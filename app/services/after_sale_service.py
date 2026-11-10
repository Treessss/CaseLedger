"""Shop-scoped after-sales procurement, 4PX binding and physical outbound."""

import json
import re
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import selectinload

from app import db
from app.models import (
    ACTION_SCAN, ACTION_UNDO, AfterSale, AfterSaleCost, AfterSaleItem,
    InventoryAdjustment, Order, OrderItem, Product, ShippingParcel, Shop,
)
from app.models.shop import ShopCredentialError
from app.services.fourpx_client import FourPxClient, FourPxError
from app.services.fourpx_label_service import fetch_label_data
from app.services.inventory_service import _stock_rows
from app.services.purchase_service import _group_key, _positive_int
from app.services.shipping_queue_service import _shop_id
from app.utils.errors import NotFoundError, ValidationError
from app.utils.order_lines import is_tip_line
from app.utils.security import apply_shop_scope, current_user

KINDS = {"resend", "exchange", "missing"}
COST_TYPES = {"product", "shipping", "other"}


def _get(after_sale_id, *, lock=False, include_deleted=False):
    query = AfterSale.query.filter_by(id=after_sale_id)
    if not include_deleted:
        query = query.filter(AfterSale.deleted_at.is_(None))
    if lock:
        query = query.with_for_update()
    record = query.one_or_none()
    if record is None:
        raise NotFoundError("售后单不存在")
    _shop_id(record.shop_id)
    return record


def _commit():
    try:
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        raise ValidationError("售后客户单号、物流单号或条码已存在，请刷新后核对") from exc


def _flush():
    try:
        db.session.flush()
    except IntegrityError as exc:
        db.session.rollback()
        raise ValidationError("售后客户单号、物流单号或条码已存在，请刷新后核对") from exc


def _reference(order):
    base = f"CF{str(order.order_number or '').strip().removeprefix('#')}"
    refs = {value for (value,) in db.session.query(AfterSale.customer_reference).filter(
        AfterSale.shop_id == order.shop_id,
        AfterSale.deleted_at.is_(None),
        or_(AfterSale.customer_reference == base, AfterSale.customer_reference.like(f"{base}-%")),
    ).all()}
    if base not in refs:
        return base
    number = 2
    while f"{base}-{number}" in refs:
        number += 1
    return f"{base}-{number}"


def _allocate(record):
    """Refresh readiness only; after-sales never reserve shared physical stock."""
    if record.deleted_at or record.status == 'shipped' or record.shipped_at:
        return
    for item in record.items:
        item.reserved_quantity = 0  # Kept only for backwards-compatible serialization.
    from app.services.outbound_inventory_service import quantity_plan
    try:
        quantity_plan(record)
        record.status = 'ready'
    except ValidationError:
        record.status = 'purchasing' if record.purchased_at else 'pending_purchase'


def create_after_sale(payload):
    order_id = _positive_int(payload.get("order_id"), "order_id")
    order = Order.query.filter_by(id=order_id).one_or_none()
    if order is None:
        raise NotFoundError("原订单不存在")
    _shop_id(order.shop_id)
    # All warehouse writers acquire shop first, then order/after-sale rows.
    # Using the reverse order here deadlocks against an outbound scan.
    try:
        # A long batch task can hold a shop-row key-share lock for minutes.
        # Fail fast with an actionable message instead of leaving the form
        # spinning behind a multi-minute database lock queue.
        if db.session.get_bind().dialect.name == "postgresql":
            db.session.execute(text("SET LOCAL lock_timeout = '8000ms'"))
        db.session.query(Shop).filter(Shop.id == order.shop_id).with_for_update().one()
        db.session.refresh(order, with_for_update=True)
    except OperationalError as exc:
        db.session.rollback()
        raise ValidationError("系统正在处理另一笔仓库或结算任务，请稍后重试；本次未创建售后单") from exc
    mail_id = str(payload.get("source_mail_message_id") or "").strip() or None
    if mail_id:
        from app.services.mail_scope import mailbox_scope
        from app.services.mail_service import require_message
        from app.utils.errors import ForbiddenError

        user = current_user() or {}
        if user.get("authority") != "admin" and "mail" not in (user.get("permissions") or []):
            raise ForbiddenError("关联邮件需要客服邮件权限")
        with mailbox_scope(order.shop_id):
            mail = require_message(mail_id)
            if mail.direction != "in" or not mail.customer or mail.customer.lower() != (order.customer_email or "").strip().lower():
                raise ValidationError("邮件客户邮箱与原订单不一致，请人工核对")
    if order.warehouse_status not in ("shipped", "legacy_unverified"):
        raise ValidationError("只有已出库或历史发货待核实的订单可发起售后补发")
    kind = str(payload.get("kind") or "").strip()
    if kind not in KINDS:
        raise ValidationError("请选择原品补发、换品补发或漏发补发")
    reason = str(payload.get("reason") or "").strip()
    if not reason:
        raise ValidationError("请填写售后原因")
    note = str(payload.get("note") or "").strip()
    if len(note) > 5000:
        raise ValidationError("售后备注不能超过 5000 字")
    raw_items = payload.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise ValidationError("请至少选择一个补发商品")
    # Serialize number generation for employees creating cases concurrently.
    Shop.query.filter_by(id=order.shop_id).with_for_update().one()
    record = AfterSale(
        shop_id=order.shop_id, order_id=order.id, customer_reference=_reference(order),
        kind=kind, reason=reason, note=note or None, shipping_address=order.shipping_address or {},
        source_mail_message_id=mail_id,
    )
    db.session.add(record)
    _flush()
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise ValidationError("商品明细格式错误")
        source_id = raw.get("source_order_item_id")
        source = None
        if source_id is not None:
            source = db.session.get(OrderItem, _positive_int(source_id, "source_order_item_id"))
            if source is None or source.order_id != order.id:
                raise ValidationError("原订单商品不属于当前订单")
        if kind != "exchange" and source is None:
            raise ValidationError("原品或漏发补发必须选择原订单商品")
        product_id = raw.get("catalog_product_id")
        if product_id is not None:
            if kind != "exchange":
                raise ValidationError("只有换品补发可以选择店铺其他商品")
            catalog = db.session.get(Product, _positive_int(product_id, "catalog_product_id"))
            if catalog is None or catalog.shop_id != order.shop_id or is_tip_line(catalog):
                raise ValidationError("替换商品不存在、不是实物商品或不属于当前店铺")
            catalog_order_item_id = None
        else:
            catalog_order_item_id = _positive_int(raw.get("catalog_order_item_id") or
                                                  (source.id if source else None), "catalog_order_item_id")
            catalog = db.session.get(OrderItem, catalog_order_item_id)
            if catalog is None or catalog.order.shop_id != order.shop_id:
                raise ValidationError("换品商品必须属于同一店铺")
            if is_tip_line(catalog):
                raise ValidationError("小费不是实体商品，不能发起补发")
            if kind != "exchange" and catalog.id != source.id:
                raise ValidationError("只有换品补发可以选择其他商品")
        quantity = _positive_int(raw.get("quantity"), "quantity")
        if kind == "missing" and quantity > int(source.quantity or 0):
            raise ValidationError("漏发数量不能超过原订单对应商品数量")
        image_url = getattr(catalog, "image_url", None)
        if not image_url and catalog.shopify_variant_id:
            image = OrderItem.query.filter_by(
                shop_id=order.shop_id, shopify_variant_id=catalog.shopify_variant_id,
            ).filter(OrderItem.image_url.isnot(None), OrderItem.image_url != "").order_by(OrderItem.id.desc()).first()
            image_url = image.image_url if image else None
        record.items.append(AfterSaleItem(
            source_order_item_id=source.id if source else None,
            catalog_order_item_id=catalog_order_item_id,
            catalog_product_id=catalog.id if product_id is not None else None,
            shopify_product_id=catalog.shopify_product_id,
            shopify_variant_id=catalog.shopify_variant_id,
            vendor=catalog.vendor,
            group_key=json.dumps(_group_key(catalog), ensure_ascii=False),
            title=catalog.title or "未命名商品", variant_title=catalog.variant_title,
            sku=catalog.sku, image_url=image_url, quantity=quantity,
        ))
    _flush()
    _allocate(record)
    _commit()
    # A new sale cannot already belong to a mixed purchase list. Avoid scanning
    # every saved purchase snapshot while assembling the creation response.
    return serialize_record(record, mixed_purchases=[])


def list_after_sales(args):
    shop_id = args.get("shop_id")
    query = apply_shop_scope(AfterSale.query, AfterSale, _shop_id(shop_id) if shop_id not in (None, "", "all") else None)
    query = query.filter(AfterSale.deleted_at.is_(None))
    if args.get("order_id"):
        query = query.filter(AfterSale.order_id == _positive_int(args.get("order_id"), "order_id"))
    search = str(args.get("search") or "").strip().lstrip("#")
    if search:
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        query = query.filter(or_(
            AfterSale.customer_reference.ilike(pattern, escape="\\"),
            AfterSale.tracking_number.ilike(pattern, escape="\\"),
            AfterSale.label_barcode.ilike(pattern, escape="\\"),
            AfterSale.order.has(or_(
                Order.order_number.ilike(pattern, escape="\\"),
                Order.customer_name.ilike(pattern, escape="\\"),
                Order.customer_email.ilike(pattern, escape="\\"),
            )),
        ))
    from app.services.after_sale_views_service import apply_view
    query, counts = apply_view(query, args.get('view'))
    from app.services.outbound_inventory_service import physical_context, quantity_plan
    pools = {}
    selected_status = args.get('status')
    if selected_status in ('ready', 'purchasing', 'pending_purchase'):
        active = query.options(selectinload(AfterSale.items)).filter(
            AfterSale.shipped_at.is_(None), AfterSale.status != 'shipped').all()
        by_shop = defaultdict(list)
        for record in active:
            by_shop[record.shop_id].append(record)
        ready_ids = set()
        for shop, records in by_shop.items():
            pools[shop] = physical_context(records)
            for record in records:
                try:
                    quantity_plan(record, **pools[shop])
                    ready_ids.add(record.id)
                except ValidationError:
                    pass
        query = query.filter(AfterSale.shipped_at.is_(None), AfterSale.status != 'shipped')
        query = query.filter(AfterSale.id.in_(ready_ids)) if selected_status == 'ready' else query.filter(
            AfterSale.id.notin_(ready_ids), AfterSale.purchased_at.isnot(None) if selected_status == 'purchasing'
            else AfterSale.purchased_at.is_(None))
    elif selected_status:
        query = query.filter(AfterSale.status == selected_status)
    total = query.count()
    page = max(int(args.get("page") or 1), 1)
    per_page = min(max(int(args.get("per_page") or 20), 1), 100)
    records = query.options(
        selectinload(AfterSale.order), selectinload(AfterSale.items),
        selectinload(AfterSale.costs), selectinload(AfterSale.purchase_lists),
    ).order_by(AfterSale.id.desc()).offset((page - 1) * per_page).limit(per_page).all()
    by_shop = defaultdict(list)
    for record in records:
        if not record.shipped_at and record.status != 'shipped' and not record.deleted_at:
            by_shop[record.shop_id].append(record)
    for shop, page_records in by_shop.items():
        if shop not in pools:
            pools[shop] = physical_context(page_records)
    from app.models import PurchaseList

    mixed_by_sale = {record.id: [] for record in records}
    sale_shops = {record.id: record.shop_id for record in records}
    if records:
        mixed = PurchaseList.query.filter(
            PurchaseList.shop_id.in_({record.shop_id for record in records}),
            PurchaseList.after_sale_id.is_(None), PurchaseList.status != "cancelled",
        ).all()
        for purchase in mixed:
            for sale_id in {int(row.get("after_sale_id") or 0) for row in purchase.allocations or []}:
                if sale_shops.get(sale_id) == purchase.shop_id:
                    mixed_by_sale[sale_id].append(purchase)
    return {"after_sales": [serialize_record(record, mixed_purchases=mixed_by_sale[record.id],
                                            pool=pools.get(record.shop_id)) for record in records],
            "counts": counts,
            "pagination": {"page": page, "per_page": per_page, "total": total,
                           "pages": (total + per_page - 1) // per_page}}


def get_after_sale(after_sale_id):
    return serialize_record(_get(after_sale_id))


def serialize_record(record, *, mixed_purchases=None, pool=None):
    data = record.to_dict(mixed_purchases=mixed_purchases)
    data['procurement_status'] = procurement_progress(data['purchase_lists'], record.purchased_at)
    if record.shipped_at or record.status == 'shipped':
        for item in data['items']:
            item.update(reserved_quantity=0, available_quantity=0, needed_quantity=0)
        data['stock_status'] = 'shipped'
        data['stock_missing_quantity'] = 0
        return data
    if record.deleted_at:
        return data
    from app.services.outbound_inventory_service import quantity_plan
    plan = quantity_plan(record, rows=pool['rows'] if pool else None,
        metadata=pool['metadata'] if pool else None, allow_partial=True,
        compatible_keys=pool.get('compatible_keys') if pool else None)
    coverage = Counter()
    for step in plan:
        coverage[step['item'].id] += step['quantity']
    for item in data['items']:
        item['reserved_quantity'] = 0
        item['available_quantity'] = coverage[item['id']]
        item['needed_quantity'] = max(item['quantity']-coverage[item['id']], 0)
    data['status'] = 'ready' if data['items'] and all(item['needed_quantity'] == 0 for item in data['items']) else (
        'purchasing' if record.purchased_at else 'pending_purchase')
    data['stock_status'] = 'ready' if data['status'] == 'ready' else 'missing' if data['items'] else 'invalid'
    data['stock_missing_quantity'] = sum(item['needed_quantity'] for item in data['items'])
    return data


def procurement_progress(purchases, purchased_at=None):
    """Purchase progress is historical workflow, never inferred from shared stock."""
    statuses = {purchase['status'] for purchase in purchases if purchase['status'] != 'cancelled'}
    if 'partially_received' in statuses:
        return 'partially_received'
    if 'ordered' in statuses:
        return 'ordered'
    if 'draft' in statuses:
        return 'draft'
    if 'received' in statuses:
        return 'received'
    return 'history' if purchased_at and not statuses else 'not_started'


def _release_archived_tracking(record):
    """Release unused labels, retaining their full snapshot in the append-only audit.

    Dispatched labels remain occupied: archiving must not enable a second stock
    deduction using the same physical package. An explicit undo permits reuse.
    Caller holds the shop and case locks; no commit or external request here.
    """
    if not record.deleted_at or not (record.tracking_number or record.label_barcode):
        return False
    from app.models import OutboundEvent
    last_dispatch = OutboundEvent.query.filter(
        OutboundEvent.after_sale_id == record.id,
        OutboundEvent.action.in_((ACTION_SCAN, ACTION_UNDO)),
    ).order_by(OutboundEvent.id.desc()).first()
    if (record.status == 'shipped' or record.shipped_at
            or (last_dispatch and last_dispatch.action == ACTION_SCAN)):
        return False
    from app.services.shipping_service import record_outbound_event
    record_outbound_event(record.order, action='tracking_unbind', code=record.tracking_number,
        after_sale_id=record.id, reason=json.dumps(
            [record.tracking_number, record.label_barcode], ensure_ascii=False, separators=(',', ':')))
    record.tracking_number = None
    record.label_barcode = None
    return True


def release_archived_tracking_bindings(shop_id):
    """Idempotent maintenance for archives created before label release existed."""
    shop_id = _shop_id(shop_id)
    db.session.query(Shop).filter_by(id=shop_id).with_for_update().one()
    records = AfterSale.query.filter(
        AfterSale.shop_id == shop_id, AfterSale.deleted_at.isnot(None),
        or_(AfterSale.tracking_number.isnot(None), AfterSale.label_barcode.isnot(None)),
    ).order_by(AfterSale.id).populate_existing().with_for_update().all()
    released = [record.id for record in records if _release_archived_tracking(record)]
    _commit()
    return {'released_ids': released, 'preserved_dispatched_ids': [
        record.id for record in records if record.id not in released]}


def delete_after_sale(after_sale_id):
    record = _get(after_sale_id, include_deleted=True)
    db.session.query(Shop).filter_by(id=record.shop_id).with_for_update().one()
    db.session.refresh(record, with_for_update=True)
    if not record.deleted_at:
        record.deleted_at = datetime.utcnow()
        record.deleted_by_user_id = (current_user() or {}).get("user_id")
        from app.services.packing_session_service import finish_session
        finish_session(record.shop_id, after_sale_id=record.id, state="cancelled")
    _release_archived_tracking(record)
    from app.services.purchase_edit_service import remove_deleted_sale_from_drafts
    updated_drafts = remove_deleted_sale_from_drafts(record)
    _commit()
    return {"id": record.id, "deleted": True, "history_preserved": True,
            "updated_draft_ids": updated_drafts}


def lookup_original_order(args):
    raw = str(args.get("order_number") or "").strip()
    number = raw.removeprefix("#")
    if not number or len(number) > 50 or number != number.strip():
        raise ValidationError("请输入有效的原订单号")
    shop_id = args.get("shop_id")
    if shop_id not in (None, "", "all"):
        shop_id = _shop_id(shop_id)
    else:
        shop_id = None
    query = apply_shop_scope(db.session.query(
        Order.id, Order.shop_id, Order.order_number, Order.warehouse_status, Order.note,
    ), Order, shop_id).filter(
        Order.order_number.in_((number, f"#{number}")),
    )
    matches = query.limit(2).all()
    if not matches:
        raise NotFoundError("未找到这个订单号；请确认店铺和订单号")
    if len(matches) != 1:
        raise ValidationError("多个店铺存在相同订单号，请先选择具体店铺")
    return _source_row(matches[0])


def get_original_order_source(order_id):
    from app.utils.security import can_access_shop

    row = db.session.query(
        Order.id, Order.shop_id, Order.order_number, Order.warehouse_status, Order.note,
    ).filter(Order.id == order_id).one_or_none()
    if row is None or not can_access_shop(row.shop_id):
        raise NotFoundError("原订单不存在")
    return _source_row(row)


def _source_row(row):
    items = OrderItem.query.filter(OrderItem.order_id == row.id, OrderItem.quantity > 0).order_by(OrderItem.id).all()
    return {
        "id": row.id, "shop_id": row.shop_id, "order_number": row.order_number,
        "warehouse_status": row.warehouse_status, "note": row.note,
        "line_items": [item.to_dict() for item in items if not is_tip_line(item)],
    }


def update_note(after_sale_id, payload):
    record = _get(after_sale_id, lock=True)
    if "note" not in payload:
        raise ValidationError("请提供售后备注")
    note = str(payload["note"] or "").strip()
    if len(note) > 5000:
        raise ValidationError("售后备注不能超过 5000 字")
    record.note = note or None
    _commit()
    return serialize_record(record)


def _catalog_matches(shop_id, search):
    from app.services.product_translation_service import catalog_search_conditions

    query = Product.query.filter(Product.shop_id == shop_id)
    if search:
        pattern = '%' + search.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
        query = query.filter(or_(Product.title.ilike(pattern, escape='\\'),
                                 Product.variant_title.ilike(pattern, escape='\\'),
                                 Product.sku.ilike(pattern, escape='\\'), *catalog_search_conditions(shop_id, search)))
    return query


def search_catalog_products(args):
    """Page products, not variants. Historical/inactive products are valid for reshipments."""
    shop_id = _shop_id(args.get('shop_id'))
    search = str(args.get('search') or '').strip()
    page = _positive_int(args.get('page') or 1, 'page')
    per_page = min(_positive_int(args.get('per_page') or 20, 'per_page'), 20)
    matched_ids = _catalog_matches(shop_id, search).with_entities(Product.shopify_product_id).distinct().subquery()
    groups = db.session.query(
        Product.shopify_product_id, func.min(Product.title).label('title'),
        func.count(Product.id).label('variant_count'), func.max(Product.image_url).label('image_url'),
    ).filter(Product.shop_id == shop_id, Product.shopify_product_id.in_(db.select(matched_ids.c.shopify_product_id)))
    groups = groups.group_by(Product.shopify_product_id)
    total = groups.count()
    rows = groups.order_by(func.min(Product.title), Product.shopify_product_id).offset((page - 1) * per_page).limit(per_page).all()
    return {'products': [{'shopify_product_id': row.shopify_product_id, 'title': row.title,
                          'variant_count': row.variant_count, 'image_url': row.image_url} for row in rows],
            'pagination': {'page': page, 'total': total, 'has_more': page * per_page < total}}


def search_catalog(args):
    shop_id = _shop_id(args.get("shop_id"))
    search = str(args.get("search") or "").strip()
    product_id = str(args.get('shopify_product_id') or '').strip()
    if not product_id and (not search or (len(search) < 2 and search.isascii())):
        return {"items": []}
    page = _positive_int(args.get("page") or 1, "page")
    per_page = min(_positive_int(args.get("per_page") or 200, "per_page"), 200)
    query = _catalog_matches(shop_id, '' if product_id else search)
    if product_id:
        query = query.filter(Product.shopify_product_id == product_id)
    total = query.count()
    matches = query.order_by(Product.title, Product.variant_title, Product.id).offset((page - 1) * per_page).limit(per_page).all()
    variant_ids = {item.shopify_variant_id for item in matches}
    images = {}
    if variant_ids:
        latest_images = db.session.query(func.max(OrderItem.id).label('id')).filter(
            OrderItem.shop_id == shop_id, OrderItem.shopify_variant_id.in_(variant_ids),
            OrderItem.image_url.isnot(None), OrderItem.image_url != "",
        ).group_by(OrderItem.shopify_variant_id).subquery()
        rows = OrderItem.query.filter(OrderItem.id.in_(db.select(latest_images.c.id))).all()
        for row in rows:
            images.setdefault(row.shopify_variant_id, row.image_url)
    from app.services.purchase_service import ACCESSORY_SUFFIX_RE, _color_name, _model_name

    return {"items": [{"id": item.id, "title": item.title, "variant_title": item.variant_title,
                       "status": item.status, "color": _color_name(item.variant_title),
                       "configuration": (ACCESSORY_SUFFIX_RE.search(item.variant_title or '').group(0).strip()
                                         if ACCESSORY_SUFFIX_RE.search(item.variant_title or '') else ''),
                       "model": _model_name(item.variant_title, _color_name(item.variant_title)),
                       "sku": item.sku, "image_url": item.image_url or images.get(item.shopify_variant_id)} for item in matches],
            "pagination": {"page": page, "per_page": per_page, "total": total, "has_more": page * per_page < total}}


def add_cost(after_sale_id, payload):
    record = _get(after_sale_id, lock=True)
    cost_type = str(payload.get("cost_type") or "")
    if cost_type not in COST_TYPES:
        raise ValidationError("费用类型应为采购、物流或其他")
    try:
        amount = Decimal(str(payload.get("amount_cny"))).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError) as exc:
        raise ValidationError("请填写有效的人民币费用") from exc
    if amount <= 0:
        raise ValidationError("费用必须大于零")
    db.session.add(AfterSaleCost(after_sale_id=record.id, shop_id=record.shop_id,
                                 cost_type=cost_type, amount_cny=amount,
                                 note=str(payload.get("note") or "").strip()[:255]))
    _commit()
    return serialize_record(record)


def preview_fourpx(payload, *, client_factory=FourPxClient):
    shop_id = _shop_id(payload.get("shop_id"))
    try:
        day = date.fromisoformat(str(payload.get("date") or ""))
    except ValueError as exc:
        raise ValidationError("请选择有效的北京时间日期") from exc
    start = datetime.combine(day, time.min, ZoneInfo("Asia/Shanghai")).astimezone(ZoneInfo("UTC"))
    end = start + timedelta(days=1)
    shop = db.session.get(Shop, shop_id)
    try:
        app_key, app_secret = shop.get_fourpx_credentials()
        data = client_factory(app_key, app_secret, environment=shop.fourpx_environment).get_predeclared_created_between(
            int(start.timestamp() * 1000), int(end.timestamp() * 1000))
    except (ShopCredentialError, FourPxError) as exc:
        raise ValidationError(str(exc)) from exc
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError as exc:
            raise ValidationError("4PX 返回数据格式异常") from exc
    if not isinstance(data, list):
        raise ValidationError("4PX 返回数据格式异常")
    refs = {str((entry.get("consignment_info") or {}).get("ref_no") or "").strip() for entry in data if isinstance(entry, dict)}
    records = AfterSale.query.filter(AfterSale.shop_id == shop_id, AfterSale.customer_reference.in_(refs)).order_by(
        AfterSale.deleted_at.is_(None).desc(), AfterSale.id.desc()).all()
    by_ref = {}
    for record in records:
        # An archived case with the same reusable reference never masks the live case.
        by_ref.setdefault(record.customer_reference, record)
    ref_counts = Counter(str((entry.get("consignment_info") or {}).get("ref_no") or "").strip()
                         for entry in data if isinstance(entry, dict))
    tracking_counts = Counter(str((entry.get("consignment_info") or {}).get("4px_tracking_no") or "").strip()
                              for entry in data if isinstance(entry, dict))
    rows = []
    for entry in data:
        info = entry.get("consignment_info") if isinstance(entry, dict) else None
        if not isinstance(info, dict):
            continue
        ref = str(info.get("ref_no") or "").strip()
        if not ref.upper().startswith("CF"):
            continue
        tracking = str(info.get("4px_tracking_no") or "").strip()
        record = by_ref.get(ref)
        if ref_counts[ref] != 1 or (tracking and tracking_counts[tracking] != 1):
            status = "duplicate_4px"
        elif record and record.deleted_at:
            status = "after_sale_deleted"
        elif record and record.tracking_number == tracking:
            # 已绑定的单（包括已出库/已完成的 F 状态）只显示「已绑定」，不算异常
            status = "already_bound"
        elif record and record.tracking_number:
            status = "tracking_conflict"
        elif not tracking or info.get("consignment_status") not in ("P", "V", "H", "C", "F"):
            status = "invalid_4px_status"
        elif AfterSale.query.filter_by(tracking_number=tracking).first() or ShippingParcel.query.filter_by(label_number=tracking).first():
            status = "tracking_conflict"
        else:
            status = "ready_to_bind" if record else "after_sale_not_found"
        original = record.order if record else None
        if original is None:
            parsed = re.fullmatch(r"CF([0-9]+)(?:-[0-9]+)?", ref)
            if parsed:
                matches = Order.query.filter(Order.shop_id == shop_id, Order.order_number.in_(
                    [parsed.group(1), f"#{parsed.group(1)}"],
                )).all()
                if len(matches) == 1:
                    original = matches[0]
                    if status == "after_sale_not_found":
                        status = "original_order_only"
        rows.append({"after_sale_id": record.id if record else None, "customer_reference": ref,
                     "tracking_number": tracking, "status": status,
                     "order_id": original.id if original else None,
                     "order_number": original.order_number if original else None})
    return {"date": str(day), "shop_id": shop_id, "rows": rows}


def bind_fourpx(after_sale_id, payload, *, client_factory=FourPxClient):
    record = _get(after_sale_id, lock=True)
    if record.status == "shipped":
        raise ValidationError("已出库售后单不能重新绑定物流")
    preview = preview_fourpx({"shop_id": record.shop_id, "date": payload.get("date")}, client_factory=client_factory)
    candidates = [row for row in preview["rows"] if row["after_sale_id"] == record.id]
    tracking = str(payload.get("tracking_number") or "").strip()
    matched = [row for row in candidates if row["tracking_number"] == tracking]
    if len(matched) != 1 or matched[0]["status"] not in ("ready_to_bind", "already_bound"):
        raise ValidationError("4PX 客户单号与售后单不唯一或不可绑定，请刷新对单结果")
    if record.tracking_number and record.tracking_number != tracking:
        raise ValidationError("已有不同 4PX 单号，请人工核对")
    record.tracking_number = tracking
    _commit()
    # 4PX 的「运单号」和「实体面单条码」可能不同（加拿大智选常见：运单 OLS…、面单 GV…）。
    # 绑定后顺手把实体条码带回来，否则仓库扫面单上的 GV 条码会关联不到售后单。
    _attach_physical_barcode(record, client_factory)
    return serialize_record(record)


def _attach_physical_barcode(record, client_factory):
    """绑定 4PX 单号后补登记实体面单条码；失败不阻断绑定。"""
    if record.label_barcode:
        return
    try:
        app_key, app_secret = record.shop.get_fourpx_credentials()
        label = fetch_label_data(app_key, app_secret, record.shop.fourpx_environment,
                                 record.tracking_number, client_factory=client_factory)
        barcode = str(label.get("label_barcode") or "").strip()
        if not barcode:
            return
        if AfterSale.query.filter(AfterSale.id != record.id, AfterSale.label_barcode == barcode).first():
            return
        if ShippingParcel.query.filter(or_(ShippingParcel.label_barcode == barcode,
                                           ShippingParcel.label_number == barcode)).first():
            return
        record.label_barcode = barcode
        _commit()
    except Exception:  # noqa: BLE001 - 条码是补充信息，绑定本身已成功
        db.session.rollback()


def bind_manual_tracking(after_sale_id, payload):
    """Local-only label binding; never contact a carrier or change original fulfillment."""
    if not isinstance(payload, dict):
        raise ValidationError('物流绑定参数必须为对象')
    record = _get(after_sale_id)
    db.session.query(Shop).filter_by(id=record.shop_id).with_for_update().one()
    db.session.refresh(record, with_for_update=True)
    if record.deleted_at:
        raise NotFoundError('售后单不存在')
    if record.status == 'shipped' or record.shipped_at:
        raise ValidationError('已出库售后单不能修改物流绑定')

    def label(raw, field, required=False):
        if raw is None or raw == '':
            if required:
                raise ValidationError('请输入补发物流单号')
            return None
        if not isinstance(raw, str):
            raise ValidationError(f'{field}必须是文本')
        value = raw.strip()
        if not re.fullmatch(r'[A-Za-z0-9-]{1,120}', value):
            raise ValidationError(f'{field}只能包含字母、数字和短横线，最长 120 字符')
        return value.upper()

    tracking = label(payload.get('tracking_number'), '物流单号', required=True)
    barcode = label(payload.get('label_barcode'), '实体面单条码')
    old_tracking, old_barcode = record.tracking_number, record.label_barcode
    if (payload.get('expected_tracking_number') != old_tracking
            or payload.get('expected_label_barcode') != old_barcode):
        raise ValidationError('物流绑定已被其他操作更新，请刷新售后单后重试')
    changing_tracking = bool(old_tracking and tracking != old_tracking.upper())
    if changing_tracking and barcode and old_barcode and barcode == old_barcode.upper():
        barcode = None  # A replacement waybill must not inherit the previous package's barcode.
    if not changing_tracking and barcode is None:
        barcode = old_barcode  # Leaving the optional field blank never erases a verified barcode.
    replacing = changing_tracking or bool(old_barcode and barcode and barcode != old_barcode.upper())
    if replacing and payload.get('confirm_replace') is not True:
        raise ValidationError('已有不同物流绑定，请核对并确认替换')
    if changing_tracking:
        from app.models import CfTrackingWriteback
        if CfTrackingWriteback.query.filter_by(after_sale_id=record.id).filter(
            CfTrackingWriteback.state.in_(('pending', 'uncertain', 'confirmed')),
        ).first():
            raise ValidationError('此售后运单已回填 Shopify 或结果待确认，不能在手动绑定中直接更换单号')

    codes = {tracking, barcode} - {None}
    if AfterSale.query.filter(AfterSale.id != record.id, or_(
        func.upper(AfterSale.tracking_number).in_(codes), func.upper(AfterSale.label_barcode).in_(codes),
    )).first():
        raise ValidationError('物流单号或条码已被其他有效售后单或已出库历史包裹使用，请核对')
    old_codes = {value.upper() for value in (old_tracking, old_barcode) if value}
    # Preserve legitimate pre-existing CF overwrites; never create a new collision.
    for parcel in ShippingParcel.query.filter(or_(func.upper(ShippingParcel.label_number).in_(codes),
                                                   func.upper(ShippingParcel.label_barcode).in_(codes))).all():
        matching = {value.upper() for value in (parcel.label_number, parcel.label_barcode) if value} & codes
        if not (parcel.shop_id == record.shop_id and parcel.order_id == record.order_id
                and record.order.warehouse_status in ('shipped', 'legacy_unverified') and matching <= old_codes):
            raise ValidationError('物流单号或条码已绑定原订单包裹，补发必须使用独立面单')
    for order in Order.query.filter(Order.shop_id == record.shop_id,
                                    func.upper(Order.shipping_label_number).in_(codes)).all():
        if not (order.id == record.order_id and order.warehouse_status in ('shipped', 'legacy_unverified')
                and order.shipping_label_number.upper() in old_codes):
            raise ValidationError('该单号已用于原订单，请使用补发的独立物流单号')
    references = codes | {f'#{code}' for code in codes}
    if (Order.query.filter(Order.shop_id == record.shop_id, func.upper(Order.order_number).in_(references)).first()
            or AfterSale.query.filter(AfterSale.shop_id == record.shop_id,
                                      func.upper(AfterSale.customer_reference).in_(codes)).first()):
        raise ValidationError('请输入物流面单号，不是原订单号或 CF 售后单号')
    if (tracking, barcode) != (old_tracking, old_barcode):
        record.tracking_number, record.label_barcode = tracking, barcode
        from app.services.shipping_service import record_outbound_event
        record_outbound_event(record.order, action='tracking_bind', code=tracking, after_sale_id=record.id,
            reason='原物流:' + json.dumps([old_tracking, old_barcode], ensure_ascii=False, separators=(',', ':')))
        _commit()
    return serialize_record(record)


def get_label(after_sale_id, *, client_factory=FourPxClient):
    record = _get(after_sale_id, lock=True)
    if not record.tracking_number:
        raise ValidationError("请先从 4PX 绑定补发单号")
    try:
        app_key, app_secret = record.shop.get_fourpx_credentials()
    except ShopCredentialError as exc:
        raise ValidationError(str(exc)) from exc
    # Reuse the existing validated 4PX response parser and trusted URL guard.
    label = fetch_label_data(app_key, app_secret, record.shop.fourpx_environment,
                             record.tracking_number, client_factory=client_factory)
    if record.label_barcode and record.label_barcode != label["label_barcode"]:
        raise ValidationError("4PX 实体条码发生变化，请人工核对")
    if ShippingParcel.query.filter_by(label_barcode=label["label_barcode"]).first():
        raise ValidationError("该条码已用于原订单包裹")
    record.label_barcode = label["label_barcode"]
    _commit()
    return {"after_sale_id": record.id, "tracking_number": record.tracking_number, **label}


def scan_outbound(payload):
    shop_id = _shop_id(payload.get("shop_id"))
    from app.services.shipping_service import normalize_label_code
    barcode = normalize_label_code(payload.get("label_barcode"))
    if not barcode:
        raise ValidationError("请扫描实体面单条码")
    from app.services.after_sale_label_service import resolve_after_sale_label

    # Same lock order as normal outbound and receipt: shop first, then parcel.
    db.session.query(Shop).filter(Shop.id == shop_id).with_for_update().one()
    resolved = resolve_after_sale_label(shop_id, barcode)
    if resolved is None:
        # 加拿大智选常见：运单 OLS…、面单 GV…；绑定后没补到实体条码时，
        # 扫 GV 条码会找不到售后单。这里先回查一小批 4PX 面单补条码再重试。
        from app.services.fourpx_barcode_sync_service import sync_after_sale_barcodes
        try:
            sync_after_sale_barcodes(shop_id, limit=10)
        except Exception:  # noqa: BLE001 - 补条码失败不影响后面的明确报错
            db.session.rollback()
        resolved = resolve_after_sale_label(shop_id, barcode)
    record = AfterSale.query.filter_by(id=resolved.id).with_for_update().one() if resolved else None
    if record is None:
        raise NotFoundError("未找到该店铺的售后面单条码")
    if record.status == "shipped" or record.shipped_at:
        raise ValidationError("该售后包裹已扫码出库，请勿重复操作")
    if not record.tracking_number:
        raise ValidationError("售后商品未到齐或未绑定 4PX 单号，不能扫码出库")
    from app.services.packing_session_service import begin_packing, finish_session
    packing = begin_packing({"shop_id": shop_id, "kind": "after_sale", "id": record.id},
                            commit=False, for_outbound=True)
    # begin_packing has already validated live quantities under the shop lock.
    for item in record.items:
        item.reserved_quantity = 0
    for line in packing['lines']:
        db.session.add(InventoryAdjustment(
            shop_id=shop_id, group_key=line['group_key'], quantity_delta=-line['quantity'],
            reason=f"售后 {record.customer_reference} 扫码出库",
        ))
    record.status = "shipped"
    record.shipped_at = datetime.utcnow()
    finish_session(shop_id, after_sale_id=record.id)
    from app.services.shipping_service import record_outbound_event

    record_outbound_event(
        record.order, action=ACTION_SCAN, code=barcode, after_sale_id=record.id
    )
    _commit()
    return serialize_record(record)


def undo_outbound(record_id, payload=None):
    """撤回售后补发的扫码出库，并把出库时扣掉的库存补回来。

    出库是「加一条负数库存调整」，撤回就对称地加一条正数调整，
    保持调整流水只增不改，事后能看出这笔库存动过两次。
    """
    payload = payload or {}
    record = AfterSale.query.filter_by(id=record_id).one_or_none()
    if record is None:
        raise NotFoundError("售后单不存在")
    _shop_id(record.shop_id)
    db.session.query(Shop).filter_by(id=record.shop_id).with_for_update().one()
    db.session.refresh(record, with_for_update=True)
    if record.status != "shipped":
        raise ValidationError("该售后单没有出库记录，不需要撤回")
    reason = str(payload.get("reason") or "").strip()
    if len(reason) > 255:
        raise ValidationError("撤回原因最长 255 字")

    from app.models import PackingSession
    session = PackingSession.query.filter_by(shop_id=record.shop_id, after_sale_id=record.id,
        state='completed').order_by(PackingSession.id.desc()).first()
    lines = session.lines if session else [{'group_key': item.group_key, 'quantity': item.quantity}
                                          for item in record.items]
    for line in lines:
        db.session.add(InventoryAdjustment(
            shop_id=record.shop_id, group_key=line['group_key'],
            quantity_delta=line['quantity'],
            reason=f"撤回售后 {record.customer_reference} 出库",
        ))
    record.status = "ready"
    record.shipped_at = None

    from app.services.shipping_service import record_outbound_event

    record_outbound_event(
        record.order, action=ACTION_UNDO, code=record.label_barcode,
        after_sale_id=record.id, reason=reason,
    )
    _commit()
    return serialize_record(record)
