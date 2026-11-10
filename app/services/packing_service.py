"""Fast, read-only packing lookup by an exact order or shipping-label code."""

from collections import OrderedDict

from sqlalchemy import String, cast, func, or_
from sqlalchemy.orm import joinedload, selectinload

from app.models import AfterSale, Order, ShippingParcel
from app.services.shipping_service import normalize_label_code, order_outbound_blockers, parcel_matches_code
from app.services.after_sale_label_service import matches_after_sale_label, resolve_after_sale_label
from app.services.shipping_queue_service import _shop_id
from app.utils.errors import NotFoundError, ValidationError
from app.utils.order_lines import is_tip_line


def _outbound_state(record, code, parcels):
    """这一枪扫完之后，能不能直接确认出库。

    打包台把「核对」和「出库」合并在一个页面里，靠的就是这里给出的判断，
    所以规则必须和 ``scan_outbound`` 完全一致（直接复用它的检查函数）。
    """
    blockers = order_outbound_blockers(record, parcels)
    target = next((parcel for parcel in parcels if parcel_matches_code(parcel, code)), None)
    already_scanned = bool(target is not None and target.scanned_at)
    ready = bool(target is not None and not already_scanned and not blockers)
    return {
        # 再扫一次同一张面单就能出库
        "ready": ready,
        # 订单层面拦住出库的原因（商品没备齐、标签不齐、未付款……）
        "blockers": blockers,
        # 本次扫的编号是不是"出库要扫的那张面单"
        "code_matches_label": target is not None,
        # 这张面单已经出过库了
        "already_scanned": already_scanned,
        "expected_parcel_count": record.expected_parcel_count or 1,
        "scanned_parcel_count": sum(parcel.scanned_at is not None for parcel in parcels),
    }


def _lines(items):
    grouped = OrderedDict()
    for item in items:
        if is_tip_line(item):
            continue
        quantity = max(int(item.quantity or 0), 0)
        if not quantity:
            continue
        key = (item.shopify_variant_id or "", item.title or "", item.variant_title or "", item.sku or "")
        row = grouped.setdefault(key, {
            "title": item.title or "未命名商品",
            "variant_title": item.variant_title or "",
            "sku": item.sku or "",
            "image_url": item.image_url or "",
            "quantity": 0,
        })
        row["quantity"] += quantity
        if not row["image_url"] and item.image_url:
            row["image_url"] = item.image_url
    return list(grouped.values())


def _tracking_numbers(order):
    """订单履约记录里的运单号（Shopify 同步下来的承运商单号）。"""
    numbers = []
    for fulfillment in order.fulfillments or []:
        if not isinstance(fulfillment, dict):
            continue
        for number in fulfillment.get("tracking_numbers") or []:
            text = str(number or "").strip()
            if text:
                numbers.append(text)
    return numbers


def lookup_packing_list(args):
    """Resolve one shop-scoped code without scanning historical orders or calling carriers."""
    shop_id = _shop_id(args.get("shop_id"))
    code = normalize_label_code(args.get("code"))
    if not code or len(code) > 120:
        raise ValidationError("请输入有效的订单号或物流单号")
    number = code.removeprefix("#")
    order_numbers = (number, f"#{number}")

    parcels = ShippingParcel.query.filter(
        ShippingParcel.shop_id == shop_id,
        or_(func.upper(ShippingParcel.label_number) == code,
            func.upper(ShippingParcel.label_barcode) == code),
    ).limit(2).all()
    sale = resolve_after_sale_label(shop_id, code, allow_reference=True)
    after_sales = [sale] if sale else []
    orders = Order.query.options(
        selectinload(Order.items), selectinload(Order.parcels),
    ).filter(
        Order.shop_id == shop_id,
        or_(Order.order_number.in_(order_numbers), func.upper(Order.shipping_label_number) == code),
    ).limit(3).all()

    # 运单号多数存在 orders.fulfillments（Shopify 履约记录）里，只看
    # shipping_label_number 会把历史单全部漏掉，等于扫真实面单查不到。
    # 这里先用文本粗筛（走 shop_id 索引），再在 Python 里做精确比对，避免子串误命中。
    tracking_candidates = Order.query.options(
        selectinload(Order.items), selectinload(Order.parcels),
    ).filter(
        Order.shop_id == shop_id,
        cast(Order.fulfillments, String).ilike(f"%{code}%"),
    ).limit(5).all()
    tracking_orders = [order for order in tracking_candidates
                       if code in {normalize_label_code(number) for number in _tracking_numbers(order)}]
    # CF tracking copied to Shopify can legitimately match both the original
    # order's remote fulfillment and its reshipment. The reshipment's own label
    # is authoritative for packing; physical original parcel collisions still fail.
    if len(after_sales) == 1:
        sale = after_sales[0]
        if code in {normalize_label_code(sale.tracking_number), normalize_label_code(sale.label_barcode)}:
            tracking_orders = [order for order in tracking_orders if order.id != sale.order_id]
            orders = [order for order in orders if order.id != sale.order_id]
            parcels = [parcel for parcel in parcels if parcel.order_id != sale.order_id]

    # 同一张单可能被多个条件命中，按 id 去重，避免误判为「匹配多张清单」
    orders = list({order.id: order for order in (*orders, *tracking_orders)}.values())

    identities = {("order", parcel.order_id) for parcel in parcels}
    identities.update(("order", order.id) for order in orders)
    identities.update(("after_sale", record.id) for record in after_sales)
    if not identities:
        raise NotFoundError("当前店铺未找到对应订单或物流单号")
    if len(identities) != 1:
        raise ValidationError("该编号匹配多张清单，请核对店铺、订单号和物流单号")

    kind, record_id = identities.pop()
    if kind == "after_sale":
        record = next(record for record in after_sales if record.id == record_id)
        lines = _lines(record.items)
        from app.services.outbound_inventory_service import quantity_plan
        pooled_ready = False
        if not record.shipped_at and record.status != 'shipped':
            try:
                quantity_plan(record)
                pooled_ready = True
            except ValidationError:
                pass
        after_sale_blockers = []
        if record.status == "shipped" or record.shipped_at:
            after_sale_blockers.append("该售后包裹已扫码出库，请勿重复操作")
        elif not pooled_ready or not record.tracking_number:
            after_sale_blockers.append("售后商品未到齐或未绑定 4PX 单号，不能扫码出库")
        matches_barcode = matches_after_sale_label(record, code)
        after_sale_scanned = record.status == "shipped" or record.shipped_at is not None
        from app.services.packing_session_service import current_session, serialize_session
        session = current_session(shop_id, after_sale_id=record.id)
        return {
            "packing": serialize_session(session) if session else None,
            "type": "after_sale", "id": record.id,
            "reference": record.customer_reference,
            "original_order_number": record.order.order_number,
            "customer_name": record.order.customer_name,
            "shipping_address": record.shipping_address,
            "status": "ready" if pooled_ready and not after_sale_scanned else record.status,
            "cancelled": False,
            "reason": record.reason,
            "note": record.note,
            "order_note": record.order.note,
            "labels": [{"tracking_number": record.tracking_number,
                        "barcode": record.label_barcode,
                        "scanned": after_sale_scanned}],
            "expected_parcel_count": 1,
            "items": lines, "total_quantity": sum(line["quantity"] for line in lines),
            "outbound": {
                "ready": bool(matches_barcode and not after_sale_scanned and not after_sale_blockers),
                "blockers": after_sale_blockers,
                "code_matches_label": matches_barcode,
                "already_scanned": after_sale_scanned,
                "expected_parcel_count": 1,
                "scanned_parcel_count": 1 if after_sale_scanned else 0,
            },
        }

    order = next((order for order in orders if order.id == record_id), None)
    if order is None:
        order = Order.query.options(selectinload(Order.items), selectinload(Order.parcels)).filter_by(
            id=record_id, shop_id=shop_id,
        ).one()
    lines = _lines(order.items)
    from app.services.packing_session_service import current_session, serialize_session
    session = current_session(shop_id, order_id=order.id)
    labels = [{"tracking_number": parcel.label_number,
               "barcode": parcel.label_barcode,
               "scanned": parcel.scanned_at is not None} for parcel in order.parcels]
    if not labels:
        # 历史单没有本系统的包裹记录，用 Shopify 履约里的运单号兜底展示
        labels = [{"tracking_number": number, "barcode": None, "scanned": False}
                  for number in _tracking_numbers(order)]
    if not labels and order.shipping_label_number:
        labels = [{"tracking_number": order.shipping_label_number, "barcode": None, "scanned": False}]
    return {
        "packing": serialize_session(session) if session else None,
        "priority": order.fulfillment_priority,
        "type": "order", "id": order.id, "reference": order.order_number,
        "original_order_number": None,
        "customer_name": order.customer_name,
        "shipping_address": order.shipping_address or {},
        "status": order.warehouse_status,
        "cancelled": order.cancelled_at is not None,
        "financial_status": order.financial_status,
        "reason": None, "note": order.note, "order_note": order.note,
        "labels": labels,
        "expected_parcel_count": order.expected_parcel_count or 1,
        "items": lines, "total_quantity": sum(line["quantity"] for line in lines),
        "outbound": _outbound_state(order, code, order.parcels),
    }
