"""Shop-scoped ready-to-ship work queue and label-first scan entrypoint."""

from datetime import datetime, timedelta

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import aliased, joinedload, load_only, noload, selectinload

from app import db
from app.models import AfterSale, Order, OrderItem, ShippingParcel, Shop
from app.services.purchase_service import attach_procurement_status
from app.services.shipping_service import find_parcel_by_code, normalize_label_code, scan_outbound
from app.services.after_sale_label_service import resolve_after_sale_label
from app.utils.errors import ForbiddenError, NotFoundError, ValidationError
from app.utils.security import can_access_shop


def _shop_id(raw):
    if isinstance(raw, bool):
        raise ValidationError("请选择具体店铺")
    try:
        shop_id = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValidationError("请选择具体店铺") from exc
    if shop_id <= 0:
        raise ValidationError("请选择具体店铺")
    if not can_access_shop(shop_id):
        raise ForbiddenError("无权操作该店铺的发货订单")
    if db.session.get(Shop, shop_id) is None:
        raise NotFoundError("店铺不存在")
    return shop_id


def ready_rows(shop_id, search="", *, pool=None):
    """All currently stocked orders, independent of Shopify fulfillment and order date."""
    if pool is None:
        from app.services.inventory_pool_service import build_pool_plan
        pool = build_pool_plan(shop_id, respect_procurement_exclusion=False)
    # Pool planning may have loaded these same identities with noload(parcels).
    # Refresh relationships so repeated queue checks see real registered labels.
    query = Order.query.populate_existing().options(
        load_only(Order.id, Order.shop_id, Order.order_number, Order.customer_name,
                  Order.order_date, Order.financial_status, Order.warehouse_status, Order.expected_parcel_count),
        selectinload(Order.parcels).load_only(ShippingParcel.id, ShippingParcel.order_id, ShippingParcel.scanned_at),
        selectinload(Order.items).load_only(OrderItem.id, OrderItem.order_id, OrderItem.quantity,
            OrderItem.title, OrderItem.shopify_product_id, OrderItem.shopify_variant_id,
            OrderItem.sku, OrderItem.variant_title),
        noload(Order.after_sales),
    ).filter(
        Order.shop_id == shop_id,
        Order.warehouse_status == "pending",
        Order.cancelled_at.is_(None),
        Order.financial_status.in_(("paid", "partially_refunded")),
    )
    if search:
        query = query.filter(Order.order_number.ilike(f"%{search}%"))
    orders = query.order_by(Order.order_date.asc(), Order.id.asc()).all()
    rows = [{
        "id": order.id,
        "shop_id": order.shop_id,
        "order_number": order.order_number,
        "customer_name": order.customer_name,
        "order_date": order.order_date.isoformat(),
        "financial_status": order.financial_status,
        "warehouse_status": order.warehouse_status,
        "expected_parcel_count": order.expected_parcel_count,
        "registered_parcel_count": len(order.parcels),
        "scanned_parcel_count": sum(parcel.scanned_at is not None for parcel in order.parcels),
    } for order in orders]
    attach_procurement_status(rows, order_items={order.id: order.items for order in orders},
                              pools={shop_id: pool} if pool is not None else None)
    ready = [row for row in rows if row["procurement_status"] == "ready_to_ship"]
    for row in ready:
        row["label_state"] = (
            "partially_scanned" if row["scanned_parcel_count"] else
            "awaiting_labels" if row["registered_parcel_count"] < row["expected_parcel_count"] else
            "ready_to_scan"
        )
    return ready


def list_ready_orders(args):
    """Ready means fully stocked now, regardless of Shopify fulfillment or order date."""
    shop_id = _shop_id(args.get("shop_id"))
    page = max(args.get("page", 1, type=int) or 1, 1)
    per_page = min(max(args.get("per_page", 20, type=int) or 20, 1), 100)
    search = str(args.get("search") or "").strip()
    from app.services.inventory_pool_service import build_pool_plan
    pool = build_pool_plan(shop_id, respect_procurement_exclusion=False)
    ready = ready_rows(shop_id, search, pool=pool)
    ready_sale_ids = pool["cf_ready_ids"]
    after_sales = AfterSale.query.options(joinedload(AfterSale.order)).filter(
        AfterSale.shop_id == shop_id,
        AfterSale.id.in_(ready_sale_ids),
        AfterSale.deleted_at.is_(None),
    ).order_by(AfterSale.created_at.asc(), AfterSale.id.asc()).all()
    if search:
        after_sales = [record for record in after_sales if search.casefold() in record.customer_reference.casefold()]
    total = len(ready)
    return {
        "orders": ready[(page - 1) * per_page:page * per_page],
        "after_sales": [{
            "id": record.id, "customer_reference": record.customer_reference,
            "order_id": record.order_id, "order_number": record.order.order_number,
            "tracking_number": record.tracking_number, "label_barcode": record.label_barcode,
            "created_at": record.created_at.isoformat(),
        } for record in after_sales],
        "summary": {
            "ready_orders": total,
            "awaiting_labels": sum(row["label_state"] == "awaiting_labels" for row in ready),
            "ready_to_scan": sum(row["label_state"] == "ready_to_scan" for row in ready),
            "partially_scanned": sum(row["label_state"] == "partially_scanned" for row in ready),
            "after_sale_ready": len(after_sales),
        },
        "pagination": {
            "page": page, "per_page": per_page, "total": total,
            "pages": (total + per_page - 1) // per_page,
        },
    }


def scan_by_label(payload):
    shop_id = _shop_id(payload.get("shop_id"))
    number = normalize_label_code(payload.get("shipping_label_number"))
    if not number:
        raise ValidationError("请扫描物流面单号")
    context = {}
    try:
        if len(number) > 120:
            raise ValidationError("物流面单条码过长，请重新扫描", extra={"scan_category": "label_error"})
        # CF labels take precedence over historical original-order labels.
        record = resolve_after_sale_label(shop_id, number)
        if record is not None:
            from app.services.after_sale_service import scan_outbound as scan_after_sale_outbound
            context = {"order_id": record.order_id, "after_sale_id": record.id,
                       "order_number": record.customer_reference}
            result = scan_after_sale_outbound({"shop_id": shop_id, "label_barcode": number})
            return {"order_id": result["order_id"], "order_number": result["customer_reference"],
                    "warehouse_status": "shipped", "expected_parcel_count": 1,
                    "scanned_parcel_count": 1, "after_sale_id": result["id"]}
        parcel = find_parcel_by_code(shop_id, number)
        if parcel is None:
            raise ValidationError("该店铺找不到此物流面单；请核对店铺与单号")
        context = {"order_id": parcel.order_id}
        result = scan_outbound(parcel.order_id, {"shipping_label_number": number})
        result["order_number"] = parcel.order.order_number
        return result
    except ForbiddenError:
        db.session.rollback()
        raise
    except Exception as error:
        from app.services.outbound_scan_failure_service import record_failure
        category, record_id = record_failure(shop_id, number, error, **context)
        db.session.rollback()
        if isinstance(error, (ValidationError, NotFoundError)):
            error.extra.update(scan_category=category, scan_failure_recorded=record_id is not None)
        raise


def list_recent_outbound(args):
    """Paginate all outbound history; deduplicate before filtering or paging."""
    from app.models import ACTION_SCAN, ACTION_UNDO, OutboundEvent

    shop_id = _shop_id(args.get("shop_id"))
    today = today_outbound_summary(shop_id)
    def positive(raw, default):
        try:
            return max(int(raw), 1)
        except (TypeError, ValueError):
            return default

    page = positive(args.get("page"), 1)
    per_page = min(positive(args.get("per_page", args.get("limit")), 20), 100)
    search = str(args.get("search") or "").strip()[:120]
    latest_scans = db.session.query(func.max(OutboundEvent.id).label("id")).filter(
        OutboundEvent.shop_id == shop_id, OutboundEvent.action == ACTION_SCAN,
    ).group_by(OutboundEvent.order_id, OutboundEvent.after_sale_id).subquery()
    query = OutboundEvent.query.join(latest_scans, OutboundEvent.id == latest_scans.c.id)
    if search:
        pattern = f"%{search.replace('/', '//').replace('%', '/%').replace('_', '/_')}%"
        # Searching an old scan must not resurrect a superseded scan for the same target.
        query = query.filter(or_(
            OutboundEvent.order_number.ilike(pattern, escape="/"),
            OutboundEvent.code.ilike(pattern, escape="/"),
        ))
    total = query.count()
    page = min(page, max((total + per_page - 1) // per_page, 1))
    undo_event = aliased(OutboundEvent)
    undone = db.session.query(undo_event.id).filter(
        undo_event.shop_id == shop_id, undo_event.action == ACTION_UNDO,
        undo_event.order_id == OutboundEvent.order_id,
        undo_event.after_sale_id.is_not_distinct_from(OutboundEvent.after_sale_id),
        undo_event.id > OutboundEvent.id,
    ).exists()
    scans = query.with_entities(OutboundEvent, undone.label("undone")).order_by(
        OutboundEvent.id.desc(),
    ).offset((page - 1) * per_page).limit(per_page).all()
    rows = []
    for scan, was_undone in scans:
        rows.append({
            "id": scan.id,
            "order_id": scan.order_id,
            "after_sale_id": scan.after_sale_id,
            "order_number": scan.order_number,
            "code": scan.code,
            "created_at": scan.created_at.isoformat() if scan.created_at else None,
            "user_id": scan.user_id,
            # 之后没有更晚的撤回记录 = 现在还是出库状态，可以撤回
            "undoable": not was_undone,
        })

    order_ids = {row["order_id"] for row in rows if row["after_sale_id"] is None}
    after_sale_ids = {row["after_sale_id"] for row in rows if row["after_sale_id"] is not None}
    orders = {
        order.id: order
        for order in Order.query.options(noload("*"), load_only(Order.id, Order.order_number, Order.customer_name,
                                                   Order.warehouse_status)).filter(Order.id.in_(order_ids)).all()
    } if order_ids else {}
    after_sales = {
        record.id: record
        for record in AfterSale.query.options(noload("*"), load_only(AfterSale.id, AfterSale.customer_reference,
            AfterSale.status, AfterSale.deleted_at)).filter(AfterSale.id.in_(after_sale_ids)).all()
    } if after_sale_ids else {}

    for row in rows:
        if row["after_sale_id"] is not None:
            record = after_sales.get(row["after_sale_id"])
            row["kind"] = "after_sale"
            row["title"] = record.customer_reference if record else row["order_number"]
            row["subtitle"] = "售后补发"
            row["status"] = record.status if record else "unknown"
            row["undoable"] = row["undoable"] and record is not None and record.deleted_at is None
            continue
        order = orders.get(row["order_id"])
        row["kind"] = "order"
        row["title"] = order.order_number if order else row["order_number"]
        row["subtitle"] = (order.customer_name if order else "") or ""
        row["status"] = order.warehouse_status if order else "unknown"
        row["undoable"] = row["undoable"] and order is not None
    return {"events": rows, "today": today, "pagination": {
        "page": page, "per_page": per_page, "total": total,
        "pages": (total + per_page - 1) // per_page,
    }}


def today_outbound_summary(shop_id, now=None):
    """Completed local outbound goods, Beijing day; undo and partial parcels excluded."""
    from app.models import ACTION_SCAN, AfterSaleItem, OutboundEvent
    from app.utils.order_lines import is_tip_line

    now = now or datetime.utcnow()
    day = (now + timedelta(hours=8)).date()
    start = datetime.combine(day, datetime.min.time()) - timedelta(hours=8)
    end = start + timedelta(days=1)
    orders = Order.query.with_entities(Order.id).filter(
        Order.shop_id == shop_id, Order.warehouse_status == "shipped",
        Order.shipped_at >= start, Order.shipped_at < end,
        db.session.query(OutboundEvent.id).filter(
            OutboundEvent.order_id == Order.id, OutboundEvent.after_sale_id.is_(None),
            OutboundEvent.action == ACTION_SCAN,
        ).exists(),
    ).subquery()
    after_sales = AfterSale.query.with_entities(AfterSale.id).filter(
        AfterSale.shop_id == shop_id, AfterSale.status == "shipped",
        AfterSale.deleted_at.is_(None), AfterSale.shipped_at >= start, AfterSale.shipped_at < end,
        db.session.query(OutboundEvent.id).filter(
            OutboundEvent.after_sale_id == AfterSale.id, OutboundEvent.action == ACTION_SCAN,
        ).exists(),
    ).subquery()
    quantity = 0
    for model, key, targets in ((OrderItem, OrderItem.order_id, orders),
                                (AfterSaleItem, AfterSaleItem.after_sale_id, after_sales)):
        for item in db.session.query(
            model.title, model.quantity, model.shopify_product_id, model.shopify_variant_id,
            model.sku, model.variant_title,
        ).join(targets, key == targets.c.id):
            if not is_tip_line(item):
                quantity += max(int(item.quantity or 0), 0)
    order_count = db.session.query(func.count()).select_from(orders).scalar()
    after_sale_count = db.session.query(func.count()).select_from(after_sales).scalar()
    return {"date": day.isoformat(), "quantity": quantity,
            "order_count": order_count, "after_sale_count": after_sale_count,
            "total_orders": order_count + after_sale_count}


def undo_outbound(payload):
    """撤回一次扫码出库：按记录类型分发到普通订单或售后补发。"""
    shop_id = _shop_id(payload.get("shop_id"))

    after_sale_id = payload.get("after_sale_id")
    if after_sale_id:
        record = AfterSale.query.filter_by(id=int(after_sale_id), shop_id=shop_id).first()
        if record is None:
            raise NotFoundError("售后单不存在")
        from app.services.after_sale_service import undo_outbound as undo_after_sale

        result = undo_after_sale(record.id, payload)
        return {
            "after_sale_id": result.get("id"),
            "order_id": result.get("order_id"),
            "order_number": result.get("customer_reference"),
            "warehouse_status": "pending",
        }

    order_id = payload.get("order_id")
    if not order_id:
        raise ValidationError("请选择要撤回的出库记录")
    order = Order.query.filter_by(id=int(order_id), shop_id=shop_id).first()
    if order is None:
        raise NotFoundError("订单不存在")

    from app.services.shipping_service import undo_outbound as undo_order

    return undo_order(order.id, payload)
