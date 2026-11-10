"""Order domain service.

All order queries and mutations live here so the route layer stays thin
(param parsing + response shaping only). This module is the reference
implementation for moving the other domains out of app/api/*.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import and_, case, desc, func, or_
from sqlalchemy.orm import joinedload, load_only, noload, selectinload

from app import db
from app.models import AfterSale, AfterSaleCost, Expense, Order, OrderCost, OrderItem, Payment, Shop
from app.models.shipping_parcel import ShippingParcel
from app.models.expense import expense_order_association
from app.services.shopify_graphql import ShopifyGraphQLError, fetch_order_details
from app.utils.errors import ForbiddenError, NotFoundError, ValidationError
from app.utils.security import apply_shop_scope, can_access_shop
from app.utils.timezone import beijing_day_bounds_utc

# Fields a client is allowed to change through the API.
UPDATABLE_FIELDS = ("total_cost", "shipping_cost", "other_fees", "notes", "tags")

MAX_PER_PAGE = 100

# 「今天要处理什么」看板里，待发货的口径与发货中心队列保持一致
PENDING_SHIP_STATUSES = ("paid", "partially_refunded")


def _parse_date(value, field_name):
    if not value:
        return None, False
    try:
        cleaned = str(value).strip('"')
        if len(cleaned) == 10:
            day = date.fromisoformat(cleaned)
            return beijing_day_bounds_utc(day, day), True
        timestamp = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
        if timestamp.tzinfo is not None:
            timestamp = timestamp.astimezone(timezone.utc).replace(tzinfo=None)
        return timestamp, False
    except ValueError as exc:
        raise ValidationError(f"{field_name}格式错误") from exc


def list_orders(args):
    """Return one page of orders visible to the current user."""
    page = args.get("page", 1, type=int) or 1
    per_page = min(args.get("per_page", 20, type=int) or 20, MAX_PER_PAGE)

    # The list is a projection, not a collection of order details. In particular,
    # do not hydrate addresses, parcels, item images or all related expenses.
    query = apply_shop_scope(Order.query, Order).options(
        load_only(
            Order.id, Order.shop_id, Order.order_number, Order.customer_name,
            Order.total_price, Order.currency, Order.actual_received,
            Order.settlement_amount, Order.settlement_currency, Order.payment_method,
            Order.payment_fee, Order.product_cost, Order.financial_status,
            Order.cancelled_at, Order.fulfillment_status, Order.warehouse_status,
            Order.shipping_label_number, Order.fulfillments, Order.order_date,
            Order.created_at,
        ),
        joinedload(Order.shop).load_only(Shop.id, Shop.name),
        noload(Order.parcels),
        noload(Order.items),
        noload(Order.after_sales),
    )

    financial_status = args.get("financial_status")
    if financial_status:
        query = query.filter(Order.financial_status == financial_status)

    fulfillment_status = args.get("fulfillment_status")
    if fulfillment_status:
        query = query.filter(Order.fulfillment_status == fulfillment_status)

    warehouse_status = args.get("warehouse_status")
    if warehouse_status:
        if warehouse_status not in ("pending", "shipped", "legacy_unverified", "legacy_partial_unverified"):
            raise ValidationError("仓库状态不支持")
        query = query.filter(Order.warehouse_status == warehouse_status)

    payment_method = args.get("payment_method")
    if payment_method:
        query = query.filter(Order.payment_method == payment_method)

    start_bound, start_is_day = _parse_date(args.get("start_date"), "开始日期")
    start_dt = start_bound[0] if start_is_day else start_bound
    if start_dt:
        query = query.filter(Order.order_date >= start_dt)

    end_bound, end_is_day = _parse_date(args.get("end_date"), "结束日期")
    if end_bound:
        if end_is_day:
            query = query.filter(Order.order_date < end_bound[1])
        else:
            query = query.filter(Order.order_date <= end_bound)

    search = args.get("search")
    if search:
        pattern = f"%{search}%"
        query = query.filter(
            db.or_(
                Order.order_number.ilike(pattern),
                Order.customer_email.ilike(pattern),
                Order.customer_name.ilike(pattern),
            )
        )

    sort_by = args.get("sort_by", "created_at")
    if not hasattr(Order, sort_by):
        sort_by = "created_at"
    sort_column = getattr(Order, sort_by)
    query = query.order_by(desc(sort_column) if args.get("sort_order", "desc") == "desc" else sort_column)

    pagination = query.paginate(page=page, per_page=per_page, error_out=False)

    page_orders = pagination.items
    orders, payment_by_order, items_by_order, refresh_currencies = _serialize_order_page(page_orders)
    _attach_fee_source(orders, payment_by_order=payment_by_order)
    from app.services.purchase_service import attach_procurement_status

    attach_procurement_status(orders, order_items=items_by_order)

    return {
        "orders": orders,
        "exchange_rate_refresh_currencies": sorted(refresh_currencies),
        "pagination": {
            "page": pagination.page,
            "per_page": pagination.per_page,
            "total": pagination.total,
            "pages": pagination.pages,
            "has_prev": pagination.has_prev,
            "has_next": pagination.has_next,
        },
    }


def _cny(amount, currency, rates, refresh_currencies):
    if not amount or not currency:
        return Decimal("0")
    value = Decimal(str(amount))
    if currency == "CNY":
        return value.quantize(Decimal("0.01"))
    if currency not in rates:
        from app.services.exchange_rate_service import exchange_rate_service

        rates[currency], needs_refresh = exchange_rate_service.get_exchange_rate_for_list(currency, "CNY")
        if needs_refresh:
            refresh_currencies.add(currency)
    return (value * rates[currency]).quantize(Decimal("0.01"))


def _page_costs(order_ids):
    cost_rows = db.session.query(
        OrderCost.order_id,
        func.sum(OrderCost.shipping_cost),
        func.sum(OrderCost.fangguo_cost),
    ).filter(OrderCost.order_id.in_(order_ids)).group_by(OrderCost.order_id).all()
    after_sale_rows = db.session.query(
        AfterSale.order_id, func.sum(AfterSaleCost.amount_cny)
    ).join(AfterSaleCost, AfterSaleCost.after_sale_id == AfterSale.id).filter(
        AfterSale.order_id.in_(order_ids)
    ).group_by(AfterSale.order_id).all()

    # Expense allocation is based on the number of linked orders, not the
    # number on this page. Aggregate that count in SQL instead of loading every
    # order attached to every expense into the ORM identity map.
    association = expense_order_association
    counts = db.session.query(
        association.c.expense_id.label("expense_id"),
        func.count(association.c.order_id).label("order_count"),
    ).group_by(association.c.expense_id).subquery()
    expense_rows = db.session.query(
        association.c.order_id, Expense.category,
        func.sum(Expense.amount / counts.c.order_count),
    ).join(Expense, Expense.id == association.c.expense_id).join(
        counts, counts.c.expense_id == Expense.id
    ).filter(
        association.c.order_id.in_(order_ids),
        Expense.category.in_(("product_cost", "shipping_cost")),
    ).group_by(association.c.order_id, Expense.category).all()
    allocated = {}
    for order_id, category, amount in expense_rows:
        allocated.setdefault(order_id, {})[category] = float(amount or 0)
    return (
        {order_id: (Decimal(str(shipping or 0)), Decimal(str(fangguo or 0)))
         for order_id, shipping, fangguo in cost_rows},
        {order_id: Decimal(str(amount or 0)) for order_id, amount in after_sale_rows},
        allocated,
    )


def _serialize_order_page(page_orders):
    if not page_orders:
        return [], {}, {}, set()
    order_ids = [order.id for order in page_orders]
    payments = {order_id: [] for order_id in order_ids}
    for payment in Payment.query.options(load_only(
        Payment.id, Payment.order_id, Payment.fee_source, Payment.payment_method,
        Payment.status, Payment.amount, Payment.currency,
        Payment.amount_in_order_currency, Payment.net_amount,
    )).filter(Payment.order_id.in_(order_ids)).all():
        payments[payment.order_id].append(payment)

    items = {order_id: [] for order_id in order_ids}
    for item in db.session.query(
        OrderItem.id, OrderItem.order_id, OrderItem.quantity, OrderItem.title,
        OrderItem.shopify_product_id, OrderItem.shopify_variant_id, OrderItem.sku, OrderItem.variant_title
    ).filter(OrderItem.order_id.in_(order_ids)).all():
        # Keep named fields intact for procurement's non-merchandise checks;
        # positional unpacking breaks whenever the projection is extended.
        items[item.order_id].append(item)

    costs, after_sale_costs, allocated = _page_costs(order_ids)
    shipping_sources = {}
    for order_id, source in db.session.query(OrderCost.order_id, OrderCost.shipping_source).filter(
            OrderCost.order_id.in_(order_ids), OrderCost.shipping_cost > 0).order_by(OrderCost.id).all():
        shipping_sources[order_id] = source or "manual"
    rates = {}
    refresh_currencies = set()
    rows = []
    for order in page_orders:
        order._list_payments = payments[order.id]
        try:
            received_source = order.actual_received_source()
        finally:
            del order._list_payments
        shipping, fangguo = costs.get(order.id, (Decimal("0"), Decimal("0")))
        after_sale = after_sale_costs.get(order.id, Decimal("0"))
        income = order.settlement_amount if order.settlement_amount is not None else order.actual_received
        income_currency = order.settlement_currency if order.settlement_amount is not None else order.currency
        gross_profit = (
            _cny(income, income_currency, rates, refresh_currencies)
            - _cny(order.product_cost, order.currency, rates, refresh_currencies)
            - shipping - fangguo - after_sale
        )
        settlement_rate = None
        settlement_cny = None
        if order.settlement_amount is not None and order.settlement_currency:
            settlement_cny = _cny(order.settlement_amount, order.settlement_currency, rates, refresh_currencies)
            settlement_rate = rates.get(order.settlement_currency, Decimal("1"))
        tracking_numbers = [number for fulfillment in (order.fulfillments or [])
                            for number in (fulfillment.get("tracking_numbers") or [])]
        rows.append({
            "id": order.id,
            "shop_id": order.shop_id,
            "shop_name": order.shop.name if order.shop else None,
            "order_number": order.order_number,
            "customer_name": order.customer_name,
            "total_price": float(order.total_price or 0),
            "currency": order.currency,
            "actual_received": float(order.actual_received or 0),
            "actual_received_source": received_source,
            "settlement_amount": float(order.settlement_amount) if order.settlement_amount is not None else None,
            "settlement_currency": order.settlement_currency,
            "settlement_amount_cny": float(settlement_cny) if settlement_cny is not None else None,
            "settlement_exchange_rate": float(settlement_rate) if settlement_rate is not None else None,
            "payment_method": order.payment_method,
            "payment_fee": float(order.payment_fee or 0),
            "gross_profit_cny": float(gross_profit),
            "shipping_cost_cny": float(shipping) if shipping and shipping > 0 else None,
            "shipping_cost_source": shipping_sources.get(order.id),
            "allocated_product_cost": allocated.get(order.id, {}).get("product_cost", 0),
            "allocated_shipping_cost": allocated.get(order.id, {}).get("shipping_cost", 0),
            "financial_status": order.financial_status,
            "cancelled_at": order.cancelled_at.isoformat() if order.cancelled_at else None,
            "fulfillment_status": order.fulfillment_status,
            "warehouse_status": order.warehouse_status,
            "shipping_label_number": order.shipping_label_number,
            "shopify_tracking_numbers": tracking_numbers,
            "order_date": order.order_date.isoformat() if order.order_date else None,
            "created_at": order.created_at.isoformat() if order.created_at else None,
        })
    return rows, payments, items, refresh_currencies


def _attach_fee_source(orders, *, payment_by_order=None):
    """给列表里的订单标上手续费来源（真实/估算）。

    整页只查一次（按 order_id 聚合），避免逐单查支付记录造成 N+1。
    """
    if not orders:
        return

    sources = {}
    if payment_by_order is not None:
        for order_id, payments in payment_by_order.items():
            sources[order_id] = {payment.fee_source or "estimated" for payment in payments}
    else:
        order_ids = [order["id"] for order in orders]
        rows = db.session.query(Payment.order_id, Payment.fee_source).filter(Payment.order_id.in_(order_ids)).all()
        for order_id, fee_source in rows:
            sources.setdefault(order_id, set()).add(fee_source or "estimated")

    for order in orders:
        order_sources = sources.get(order["id"])
        if not order_sources:
            order["fee_source"] = None
        elif order_sources == {"gateway"}:
            order["fee_source"] = "gateway"
        else:
            order["fee_source"] = "estimated"


def get_order(order_id, with_payments=True):
    """Fetch a single order or raise a domain error."""
    order = db.session.get(Order, order_id)
    if order is None:
        raise NotFoundError("订单不存在")
    if not can_access_shop(order.shop_id):
        raise ForbiddenError("无权查看该订单")

    from app.services.exchange_rate_service import exchange_rate_service

    with exchange_rate_service.use_verified_snapshot() as stale_currencies:
        data = order.to_dict()
    data["exchange_rate_refresh_currencies"] = sorted(stale_currencies)
    from app.services.purchase_service import attach_procurement_status

    attach_procurement_status([data], order_items={order.id: order.items})
    data["line_items"] = [item.to_dict() for item in order.items if int(item.quantity or 0) > 0]
    from app.services.fourpx_freight_service import serialize_for_order
    data["fourpx_freight"] = serialize_for_order(order.id)
    if with_payments:
        payments = Payment.query.filter_by(order_id=order_id).all()
        data["payments"] = [payment.to_dict() for payment in payments]
    return data


def get_order_neighbors(order_id):
    """返回订单列表默认倒序中的上一条（较新）和下一条（较旧）。"""
    order = db.session.get(Order, order_id)
    if order is None:
        raise NotFoundError("订单不存在")
    if not can_access_shop(order.shop_id):
        raise ForbiddenError("无权查看该订单")

    same_shop = Order.query.filter(Order.shop_id == order.shop_id)
    previous_order = (
        same_shop.filter(
            or_(
                Order.created_at > order.created_at,
                and_(Order.created_at == order.created_at, Order.id > order.id),
            )
        )
        .order_by(Order.created_at.asc(), Order.id.asc())
        .first()
    )
    next_order = (
        same_shop.filter(
            or_(
                Order.created_at < order.created_at,
                and_(Order.created_at == order.created_at, Order.id < order.id),
            )
        )
        .order_by(Order.created_at.desc(), Order.id.desc())
        .first()
    )

    def summary(candidate):
        if candidate is None:
            return None
        return {"id": candidate.id, "order_number": candidate.order_number}

    return {"previous": summary(previous_order), "next": summary(next_order)}


def update_order(order_id, payload):
    """Update whitelisted fields and recalculate profit."""
    order = db.session.get(Order, order_id)
    if order is None:
        raise NotFoundError("订单不存在")
    if not can_access_shop(order.shop_id):
        raise ForbiddenError("无权修改该订单")

    payload = payload or {}
    for field in UPDATABLE_FIELDS:
        if field in payload:
            setattr(order, field, payload[field])

    order.calculate_profit()
    db.session.commit()
    return order.to_dict()


def restore_refunded_item_quantities(order):
    """退款订单的本地商品数量被 Shopify current_quantity 同步成 0 时，回源恢复原始下单数量。

    只在出库补记遇到「订单没有可出库商品」时调用，用于退款但实际已发货的订单扣库存。
    返回恢复的商品行数；非 Shopify 订单或拉取失败返回 0。
    """
    if order is None or not order.shopify_order_id:
        return 0
    shop = db.session.get(Shop, order.shop_id) if order.shop_id else None
    if shop is None:
        return 0
    try:
        from app.services import shopify_graphql
        query = """
        query($id: ID!) { order(id: $id) { lineItems(first: 100) { nodes { id quantity } } } }
        """
        data = shopify_graphql._execute(shop, query, {
            "id": shopify_graphql.gid_for_order(order.shopify_order_id),
        })
        nodes = (((data or {}).get("order") or {}).get("lineItems") or {}).get("nodes") or []
    except Exception:  # noqa: BLE001 - 恢复失败就让调用方按无商品处理
        return 0
    by_line_id = {
        str(node.get("id", "")).rsplit("/", 1)[-1]: int(node.get("quantity") or 0)
        for node in nodes if isinstance(node, dict)
    }
    restored = 0
    for item in order.items:
        original = by_line_id.get(str(item.shopify_line_item_id))
        if (item.quantity or 0) <= 0 and original and original > 0:
            item.quantity = original
            restored += 1
    if restored:
        db.session.flush()
    return restored


def refresh_order_contact(order_id):
    """从 Shopify 回源更新订单及完整详情并写回本地。

    除客户与地址外，还会补订单备注、变体图（无变体图则商品主图）和物流信息。
    """
    order = db.session.get(Order, order_id)
    if order is None:
        raise NotFoundError("订单不存在")
    if not can_access_shop(order.shop_id):
        raise ForbiddenError("无权查看该订单")
    if not order.shopify_order_id:
        raise ValidationError("该订单不是从 Shopify 同步来的，没有可回源的记录")

    shop = db.session.get(Shop, order.shop_id) if order.shop_id else None
    if shop is None:
        raise ValidationError("订单没有关联店铺，无法回源拉取客户信息")

    try:
        from flask import current_app

        from app.services.shopify_service import ShopifyService

        service = ShopifyService()
        service.init_shop(shop, current_app)
        stats = service.sync_order_by_id(order)
        if stats.get("errors"):
            raise ValidationError("订单同步失败，请稍后重试")
        details = fetch_order_details(shop, order.shopify_order_id)
    except ShopifyGraphQLError as exc:
        raise ValidationError(str(exc)) from exc

    # Shopify 没返回的字段不要覆盖库里已有的值（例如手工补过的电话）
    if details.get("email"):
        order.customer_email = details["email"]
    if details.get("name"):
        order.customer_name = details["name"]
    if details.get("phone"):
        order.customer_phone = details["phone"]
    if details.get("shipping_address"):
        order.shipping_address = details["shipping_address"]
    order.note = details.get("note")
    order.fulfillments = details.get("fulfillments") or []
    if details.get("fulfillment_status"):
        order.fulfillment_status = details["fulfillment_status"]

    item_by_shopify_id = {item.shopify_line_item_id: item for item in order.items}
    for row in details.get("line_items") or []:
        item = item_by_shopify_id.get(row.get("shopify_line_item_id"))
        if item is not None and row.get("image_url"):
            item.image_url = row["image_url"]

    db.session.commit()
    return get_order(order_id)


def workload_summary():
    """今日作业看板：一眼看出今天还剩多少活。

    口径与发货中心保持一致（同一套筛选条件），避免两个页面的数字对不上：
        - ``pending_ship``      待发货（已付款、未取消、仓库还没出库）
        - ``ready_to_ship``     其中已经备齐、可以直接扫码出库的
        - ``awaiting_purchase`` 剩下还等着采购/入库的
        - ``shipped_today``     今天实际扫码出库的包裹数
    """
    from app.services.shipping_queue_service import ready_rows
    from app.utils.security import allowed_shop_ids

    allowed = allowed_shop_ids()
    empty = {
        "pending_ship": 0,
        "ready_to_ship": 0,
        "awaiting_purchase": 0,
        "shipped_today": 0,
    }
    if allowed is not None and not allowed:
        return empty

    shop_query = Shop.query
    if allowed is not None:
        shop_query = shop_query.filter(Shop.id.in_(allowed))
    shops = shop_query.order_by(Shop.id).all()

    pending_ship = apply_shop_scope(
        Order.query.filter(
            Order.warehouse_status == "pending",
            Order.cancelled_at.is_(None),
            Order.financial_status.in_(PENDING_SHIP_STATUSES),
        ),
        Order,
    ).count()

    # 备齐与否要看采购状态，逐店铺算（单店失败不影响整体看板）
    ready_to_ship = 0
    for shop in shops:
        try:
            ready_to_ship += len(ready_rows(shop.id))
        except Exception:  # noqa: BLE001
            continue

    now = datetime.utcnow()
    today = (now + timedelta(hours=8)).date()
    today_start, today_end = beijing_day_bounds_utc(today, today)
    shipped_today = apply_shop_scope(
        ShippingParcel.query.filter(
            ShippingParcel.scanned_at.isnot(None),
            ShippingParcel.scanned_at >= today_start,
            ShippingParcel.scanned_at < today_end,
        ),
        ShippingParcel,
    ).count()

    return {
        "pending_ship": pending_ship,
        "ready_to_ship": ready_to_ship,
        "awaiting_purchase": max(pending_ship - ready_to_ship, 0),
        "shipped_today": shipped_today,
    }


def recent_orders(limit=10):
    limit = min(limit or 10, 50)
    query = apply_shop_scope(Order.query, Order).options(
        joinedload(Order.shop), selectinload(Order.order_costs),
        selectinload(Order.after_sales).selectinload(AfterSale.costs),
        selectinload(Order.expenses).selectinload(Expense.orders),
    )
    orders = query.order_by(desc(Order.created_at)).limit(limit).all()
    return [order.to_dict() for order in orders]


def order_stats(days=30):
    """Aggregate order statistics within the caller's shop scope."""
    days = max(1, min(days or 30, 365))
    now = datetime.utcnow()
    since = now - timedelta(days=days)
    today_start, _ = beijing_day_bounds_utc((now + timedelta(hours=8)).date(), (now + timedelta(hours=8)).date())

    def scoped(query):
        return apply_shop_scope(query, Order)

    today = Order.created_at >= today_start
    totals = scoped(db.session.query(
        func.count(Order.id),
        func.coalesce(func.sum(Order.total_price), 0),
        func.coalesce(func.sum(Order.gross_profit), 0),
        func.coalesce(func.sum(case((today, 1), else_=0)), 0),
        func.coalesce(func.sum(case((today, Order.total_price), else_=0)), 0),
        func.coalesce(func.sum(case((today, Order.gross_profit), else_=0)), 0),
    ).filter(Order.created_at >= since)).one()
    total_orders, total_revenue, total_profit, today_orders, today_revenue, today_profit = totals
    total_revenue, total_profit = float(total_revenue), float(total_profit)
    today_revenue, today_profit = float(today_revenue), float(today_profit)

    status_rows = (
        scoped(
            db.session.query(Order.financial_status, db.func.count(Order.id)).filter(
                Order.created_at >= since
            )
        )
        .group_by(Order.financial_status)
        .all()
    )

    fulfillment_rows = (
        scoped(
            db.session.query(Order.fulfillment_status, db.func.count(Order.id)).filter(
                Order.created_at >= since
            )
        )
        .group_by(Order.fulfillment_status)
        .all()
    )

    return {
        "period": {
            "days": days,
            "orders": total_orders,
            "revenue": total_revenue,
            "profit": total_profit,
            "profit_margin": round(total_profit / total_revenue * 100, 2) if total_revenue else 0,
        },
        "today": {
            "orders": today_orders,
            "revenue": today_revenue,
            "profit": today_profit,
            "profit_margin": round(today_profit / today_revenue * 100, 2) if today_revenue else 0,
        },
        "financial_status": dict(status_rows),
        "fulfillment_status": dict(fulfillment_rows),
    }
