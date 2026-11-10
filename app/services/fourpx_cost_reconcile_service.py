"""4PX 真实物流费与订单费用的对账/迁移工具。

用于从「每日账单导入/手工填费用」切换到「按订单从 4PX 取真实费用」：
- 只读对账：哪些订单缺 4PX 费用、哪些缺订单费用、哪些金额不一致；
- 补记：只给「4PX 已有真实费用、订单费用还没记」的订单写一条 pending 费用，
  不覆盖手工/账单来源的金额，也不自动确认扣账户。
"""

from datetime import date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import or_

from app import db
from app.models import FourPxFreight, Order, OrderCost, ShippingParcel, Shop
from app.utils.errors import ForbiddenError, NotFoundError, ValidationError
from app.utils.security import can_access_shop, current_user, is_admin, allowed_shop_ids


def _parse_day(value, default):
    text = str(value or "").strip()
    if not text:
        return default
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise ValidationError("日期格式应为 YYYY-MM-DD") from None


def _shop_id(raw):
    try:
        shop_id = int(raw)
    except (TypeError, ValueError):
        raise ValidationError("请指定店铺 shop_id") from None
    if not can_access_shop(shop_id):
        raise ForbiddenError("无权查看该店铺")
    if db.session.get(Shop, shop_id) is None:
        raise NotFoundError("店铺不存在")
    return shop_id


def _tracking_map(order_ids):
    if not order_ids:
        return {}
    rows = ShippingParcel.query.filter(
        ShippingParcel.order_id.in_(order_ids),
        or_(ShippingParcel.source == "4px", ShippingParcel.label_number.ilike("4PX%")),
    ).order_by(ShippingParcel.id).all()
    tracking = {}
    for row in rows:
        tracking.setdefault(row.order_id, []).append(row.label_number)
    return tracking


def _freight_map(order_ids):
    if not order_ids:
        return {}
    return {row.order_id: row for row in FourPxFreight.query.filter(FourPxFreight.order_id.in_(order_ids)).all()}


def _cost_map(order_ids):
    if not order_ids:
        return {}
    rows = OrderCost.query.filter(OrderCost.order_id.in_(order_ids)).order_by(OrderCost.id).all()
    costs = {}
    for row in rows:
        current = costs.get(row.order_id)
        if current is None or (float(row.shipping_cost or 0) > 0 and float(current.shipping_cost or 0) <= 0) \
                or row.id > current.id:
            costs[row.order_id] = row
    return costs


def _classify(order, tracking_numbers, freight, cost):
    tracking = (tracking_numbers or [None])[0]
    freight_total = float(freight.total_fee) if freight and freight.total_fee is not None else None
    cost_shipping = float(cost.shipping_cost) if cost and cost.shipping_cost is not None else 0.0
    source = (cost.shipping_source if cost else None) or ("manual" if cost else None)
    if freight is None and cost_shipping <= 0:
        status = "freight_missing" if tracking else "no_4px"
    elif freight is None:
        status = "cost_no_freight"
    elif cost_shipping <= 0:
        status = "cost_missing"
    elif abs(cost_shipping - (freight_total or 0)) > 0.01:
        status = "diff"
    else:
        status = "matched"
    return {
        "order_id": order.id,
        "order_number": order.order_number,
        "order_date": order.order_date.date().isoformat() if order.order_date else None,
        "warehouse_status": order.warehouse_status,
        "tracking_number": tracking,
        "fourpx_tracking_no": freight.tracking_number if freight else None,
        "freight_currency": freight.currency if freight else None,
        "freight_total": round(freight_total, 2) if freight_total is not None else None,
        "freight_billed_at": freight.billed_at.isoformat() if freight and freight.billed_at else None,
        "cost_id": cost.id if cost else None,
        "cost_shipping": round(cost_shipping, 2),
        "cost_source": source,
        "cost_status": cost.status if cost else None,
        "diff": round((cost_shipping - (freight_total or 0)), 2) if (cost and freight) else None,
        "status": status,
    }


def reconcile(args):
    shop_id = _shop_id(args.get("shop_id"))
    today = date.today()
    start = _parse_day(args.get("date_from"), today - timedelta(days=30))
    end = _parse_day(args.get("date_to"), today)
    if end < start:
        raise ValidationError("结束日期不能早于开始日期")
    page = max(int(args.get("page") or 1), 1)
    per_page = min(max(int(args.get("per_page") or 20), 1), 100)
    status_filter = str(args.get("status") or "").strip()

    query = Order.query.filter(
        Order.shop_id == shop_id,
        Order.warehouse_status.in_(("shipped", "legacy_unverified", "legacy_partial_unverified")),
        Order.order_date >= datetime.combine(start, time.min),
        Order.order_date < datetime.combine(end + timedelta(days=1), time.min),
    )
    search = str(args.get("search") or "").strip().lstrip("#")
    if search:
        query = query.filter(Order.order_number.ilike(f"%{search}%"))
    orders = query.order_by(Order.order_date.desc(), Order.id.desc()).all()
    order_ids = [order.id for order in orders]
    tracking, freights, costs = _tracking_map(order_ids), _freight_map(order_ids), _cost_map(order_ids)
    rows = [_classify(order, tracking.get(order.id), freights.get(order.id), costs.get(order.id))
            for order in orders]
    counts = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    summary = {
        "orders": len(rows),
        "counts": counts,
        "freight_total": round(sum(row["freight_total"] or 0 for row in rows), 2),
        "cost_total": round(sum(row["cost_shipping"] or 0 for row in rows), 2),
        "missing_cost": counts.get("cost_missing", 0),
        "diff": counts.get("diff", 0),
        "has_no_4px": counts.get("no_4px", 0),
    }
    if status_filter:
        rows = [row for row in rows if row["status"] == status_filter]
    total = len(rows)
    items = rows[(page - 1) * per_page: page * per_page]
    return {"shop_id": shop_id, "date_from": start.isoformat(), "date_to": end.isoformat(),
            "summary": summary, "items": items,
            "pagination": {"page": page, "per_page": per_page, "total": total,
                           "pages": (total + per_page - 1) // per_page if per_page else 0},
            "statuses": {
                "no_4px": "无 4PX 单号", "freight_missing": "待同步 4PX 费用",
                "cost_missing": "未记物流费", "diff": "金额不一致",
                "matched": "已记且一致", "cost_no_freight": "已记费用（非 4PX）",
            }}


def apply_missing(args):
    """只补「4PX 已有费用、订单还没有物流费」的记录，绝不覆盖已有金额。"""
    shop_id = _shop_id(args.get("shop_id"))
    raw_ids = args.get("order_ids") or []
    if not isinstance(raw_ids, list) or not raw_ids or len(raw_ids) > 100:
        raise ValidationError("order_ids 需为 1–100 个订单 id")
    try:
        order_ids = [int(value) for value in raw_ids]
    except (TypeError, ValueError):
        raise ValidationError("order_ids 格式不正确") from None
    orders = Order.query.filter(Order.shop_id == shop_id, Order.id.in_(order_ids),
                                Order.warehouse_status.in_(("shipped", "legacy_unverified",
                                                            "legacy_partial_unverified"))).all()
    if len(orders) != len(set(order_ids)):
        raise ValidationError("部分订单不存在或还未出库")
    tracking, freights, costs = _tracking_map(order_ids), _freight_map(order_ids), _cost_map(order_ids)
    user = current_user() or {}
    results, created = [], 0
    batch_id = f"4PX-{datetime.utcnow().date().isoformat()}"
    for order in orders:
        row = _classify(order, tracking.get(order.id), freights.get(order.id), costs.get(order.id))
        if row["status"] != "cost_missing":
            results.append({**row, "action": "skipped", "reason": "不是「未记物流费」状态"})
            continue
        freight = freights[order.id]
        if (freight.currency or "CNY").upper() != "CNY":
            results.append({**row, "action": "skipped", "reason": f"4PX 费用币种为 {freight.currency}，需人工换算"})
            continue
        cost_date = freight.billed_at.date() if freight.billed_at else (order.order_date.date() if order.order_date else date.today())
        cost = OrderCost(
            shop_id=shop_id, order_id=order.id, order_number=order.order_number,
            shipping_cost=Decimal(str(freight.total_fee or 0)).quantize(Decimal("0.01")),
            shipping_reference=freight.tracking_number,
            shipping_notes="4PX 真实出库费用（系统补记）",
            shipping_source="4px", fourpx_tracking_no=freight.tracking_number,
            fourpx_synced_at=datetime.utcnow(),
            cost_date=cost_date, entry_user=user.get("username") or "4px-sync",
            status="pending", batch_id=batch_id, batch_notes="4PX 真实费用回填",
        )
        db.session.add(cost)
        created += 1
        results.append({**row, "action": "created"})
    db.session.commit()
    return {"shop_id": shop_id, "created": created,
            "skipped": len(results) - created, "results": results}
