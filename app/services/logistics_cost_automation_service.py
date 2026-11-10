"""订单物流费自动化：按订单日期归集 4PX 真实费用。

规则（2026-11 起）：
- 只处理「订单日期 >= 切换日」且已出库/有 4PX 包裹的订单；
- 订单没有物流费时，写一条 pending、source=4px 的 OrderCost，成本日期=订单日期（北京时间）；
- 绝不覆盖已有手工/账单来源的费用；
- 每天按订单日期汇总 4PX 真实费用，自动生成/更新一条物流费日账单（一天一条，人工已填则跳过）。
"""

from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from flask import current_app, g
from sqlalchemy import or_

from app import db
from app.models import (AfterSale, AfterSaleCost, Expense, FourPxAfterSaleFreight, FourPxFreight,
                       Order, OrderCost, ShippingParcel, Shop)
from app.utils.errors import ValidationError

BEIJING_OFFSET = timedelta(hours=8)
DEFAULT_CUTOVER = "2026-11-01"
AUTO_DESCRIPTION_PREFIX = "4PX自动-"


def _cutover():
    raw = current_app.config.get("LOGISTICS_COST_CUTOVER") or DEFAULT_CUTOVER
    try:
        return date.fromisoformat(str(raw))
    except ValueError:
        return date.fromisoformat(DEFAULT_CUTOVER)


def _beijing_date(value):
    return (value + BEIJING_OFFSET).date() if value else None


def _order_day_range(day):
    """北京时间自然日 → 数据库 order_date（UTC naive）范围。"""
    start = datetime.combine(day, time.min) - BEIJING_OFFSET
    return start, start + timedelta(days=1)


def _ensure_worker_user():
    if not g.get("current_user"):
        g.current_user = {"authority": "admin", "user_id": None, "permissions": []}


def _freight_order_ids(order_ids):
    if not order_ids:
        return {}
    return {row.order_id: row for row in FourPxFreight.query.filter(
        FourPxFreight.order_id.in_(order_ids)).all()}


def _cost_order_ids(order_ids):
    if not order_ids:
        return {}
    rows = OrderCost.query.filter(OrderCost.order_id.in_(order_ids)).all()
    result = {}
    for row in rows:
        current = result.get(row.order_id)
        if current is None or float(row.shipping_cost or 0) > 0:
            result[row.order_id] = row
    return result


def _candidate_orders(shop_id, limit):
    start, _ = _order_day_range(_cutover())
    query = Order.query.filter(
        Order.shop_id == shop_id,
        Order.order_date >= start,
        Order.warehouse_status.in_(("shipped", "legacy_unverified", "legacy_partial_unverified")),
        or_(Order.shipped_at.isnot(None), Order.warehouse_status == "shipped"),
    ).order_by(Order.order_date.asc(), Order.id.asc())
    parcel_order_ids = {
        row[0] for row in db.session.query(ShippingParcel.order_id).filter(
            ShippingParcel.shop_id == shop_id,
            or_(ShippingParcel.source == "4px", ShippingParcel.label_number.ilike("4PX%")),
        ).distinct().all()
    }
    orders = [order for order in query.all() if order.id in parcel_order_ids]
    costs = _cost_order_ids([order.id for order in orders])
    freight_ids = set(_freight_order_ids([order.id for order in orders]))
    min_age = timedelta(hours=int(current_app.config.get("LOGISTICS_COST_MIN_AGE_HOURS", 24)))
    fresh_cutoff = datetime.utcnow() - min_age
    candidates = []
    for order in orders:
        if order.id in costs:
            continue
        # 出库不满 N 小时且还没查到费用：先跳过，等 4PX 出账后再查
        if order.id not in freight_ids and order.shipped_at and order.shipped_at > fresh_cutoff:
            continue
        candidates.append(order)
    return candidates[:limit]


def sync_missing_costs(shop_id=None, limit=None):
    """同步未记物流费的订单；返回各店铺处理结果。"""
    if not current_app.config.get("LOGISTICS_COST_AUTO", True):
        return {"enabled": False, "shops": 0, "synced": 0, "created": 0, "failed": 0}
    from app.services.fourpx_freight_service import sync_order_freight

    _ensure_worker_user()
    limit = int(limit or current_app.config.get("LOGISTICS_COST_BATCH", 100))
    shop_query = Shop.query.filter(Shop.is_active.is_(True),
                                   Shop.fourpx_app_key_encrypted.isnot(None),
                                   Shop.fourpx_app_secret_encrypted.isnot(None))
    if shop_id:
        shop_query = shop_query.filter(Shop.id == shop_id)
    summary = {"enabled": True, "shops": 0, "synced": 0, "created": 0, "failed": 0, "updated": 0}
    for shop in shop_query.order_by(Shop.id).all():
        summary["shops"] += 1
        candidates = _candidate_orders(shop.id, limit)
        if not candidates:
            continue
        existing_freight = _freight_order_ids([order.id for order in candidates])
        for order in candidates:
            try:
                freight = existing_freight.get(order.id)
                if freight is None or freight.total_fee is None:
                    data = sync_order_freight(order.id)
                    summary["synced"] += 1
                    freight = db.session.get(FourPxFreight, data["id"]) if data.get("id") else \
                        FourPxFreight.query.filter_by(order_id=order.id).one_or_none()
                if freight is None or freight.total_fee is None:
                    summary["failed"] += 1
                    continue
                shipping = Decimal(str(freight.total_fee)).quantize(Decimal("0.01"))
                cost_day = _beijing_date(order.order_date) or date.today()
                cost = OrderCost.query.filter_by(order_id=order.id).order_by(OrderCost.id.desc()).first()
                if cost is None:
                    db.session.add(OrderCost(
                        shop_id=shop.id, order_id=order.id, order_number=order.order_number,
                        shipping_cost=shipping, shipping_reference=freight.tracking_number,
                        shipping_notes="4PX 真实费用（自动）", shipping_source="4px",
                        fourpx_tracking_no=freight.tracking_number, fourpx_synced_at=datetime.utcnow(),
                        cost_date=cost_day, entry_user="4px-auto", status="pending",
                        batch_id=f"4PX-AUTO-{cost_day.isoformat()}",
                        batch_notes="按订单日期自动归集 4PX 物流费",
                    ))
                    summary["created"] += 1
                    db.session.commit()
                elif (cost.shipping_source or "manual") == "4px" and abs(
                        float(cost.shipping_cost or 0) - float(shipping)) > 0.01:
                    cost.shipping_cost = shipping
                    cost.shipping_reference = freight.tracking_number
                    cost.fourpx_tracking_no = freight.tracking_number
                    cost.fourpx_synced_at = datetime.utcnow()
                    summary["updated"] += 1
                    db.session.commit()
            except Exception:  # noqa: BLE001 - 单笔失败不影响其它
                db.session.rollback()
                summary["failed"] += 1
    return summary


def sync_missing_after_sale_costs(shop_id=None, limit=None):
    """CF 售后补发的 4PX 真实运费：按售后出库日期 >= 切换日归集。"""
    if not current_app.config.get("LOGISTICS_COST_AUTO", True):
        return {"enabled": False, "shops": 0, "synced": 0, "failed": 0}
    from app.services.fourpx_after_sale_freight_service import sync_after_sale_freight

    _ensure_worker_user()
    limit = int(limit or current_app.config.get("LOGISTICS_COST_BATCH", 100))
    start, _ = _order_day_range(_cutover())
    shops = Shop.query.filter(Shop.is_active.is_(True),
                              Shop.fourpx_app_key_encrypted.isnot(None),
                              Shop.fourpx_app_secret_encrypted.isnot(None))
    if shop_id:
        shops = shops.filter(Shop.id == shop_id)
    summary = {"enabled": True, "shops": 0, "synced": 0, "failed": 0}
    for shop in shops.order_by(Shop.id).all():
        summary["shops"] += 1
        auto_ids = {row[0] for row in db.session.query(AfterSaleCost.after_sale_id).filter(
            AfterSaleCost.shop_id == shop.id, AfterSaleCost.source == "4px",
            AfterSaleCost.cost_type == "shipping").all()}
        candidates = AfterSale.query.filter(
            AfterSale.shop_id == shop.id, AfterSale.deleted_at.is_(None),
            AfterSale.tracking_number.isnot(None), AfterSale.shipped_at.isnot(None),
            AfterSale.shipped_at >= start,
        ).order_by(AfterSale.shipped_at.asc(), AfterSale.id.asc()).all()
        candidates = [row for row in candidates if row.id not in auto_ids][:limit]
        for record in candidates:
            try:
                sync_after_sale_freight(record.id)
                summary["synced"] += 1
            except Exception:  # noqa: BLE001 - 单笔失败不影响其它
                db.session.rollback()
                summary["failed"] += 1
    return summary


def _daily_total(shop_id, day):
    start, end = _order_day_range(day)
    rows = db.session.query(FourPxFreight.total_fee).join(
        Order, Order.id == FourPxFreight.order_id).filter(
        Order.shop_id == shop_id,
        Order.order_date >= start,
        Order.order_date < end,
    ).all()
    total = Decimal("0")
    for (fee,) in rows:
        if fee is not None:
            total += Decimal(str(fee))
    cf_rows = db.session.query(FourPxAfterSaleFreight.total_fee).join(
        AfterSale, AfterSale.id == FourPxAfterSaleFreight.after_sale_id).filter(
        AfterSale.shop_id == shop_id, AfterSale.deleted_at.is_(None),
        AfterSale.shipped_at >= start, AfterSale.shipped_at < end,
    ).all()
    for (fee,) in cf_rows:
        if fee is not None:
            total += Decimal(str(fee))
    return total.quantize(Decimal("0.01"))


def write_daily_expenses(days=14, shop_id=None, today=None):
    """按订单日期自动写/更新物流费日账单，一天一条；人工填过的不动。"""
    if not current_app.config.get("LOGISTICS_COST_AUTO", True):
        return {"enabled": False, "created": 0, "updated": 0, "skipped": 0}
    today = today or _beijing_date(datetime.utcnow()) or date.today()
    summary = {"enabled": True, "created": 0, "updated": 0, "skipped": 0}
    shops = Shop.query.filter(Shop.is_active.is_(True)).all()
    if shop_id:
        shops = [shop for shop in shops if shop.id == shop_id]
    for shop in shops:
        for offset in range(1, days + 1):
            day = today - timedelta(days=offset)
            total = _daily_total(shop.id, day)
            marker = f"{AUTO_DESCRIPTION_PREFIX}{day.isoformat()}"
            existing = Expense.query.filter(
                Expense.shop_id == shop.id, Expense.category == "shipping_cost",
                Expense.expense_date == day).all()
            manual = [row for row in existing if row.description != marker]
            auto = next((row for row in existing if row.description == marker), None)
            if manual:
                summary["skipped"] += 1
                continue
            if total <= 0:
                summary["skipped"] += 1
                continue
            if auto is None:
                db.session.add(Expense(
                    shop_id=shop.id, category="shipping_cost", description=marker,
                    amount=total, currency="CNY", expense_date=day, status="approved",
                    submitter="4px-auto", reference_id=f"4PX-AUTO-{day.isoformat()}",
                ))
                summary["created"] += 1
            elif Decimal(str(auto.amount or 0)) != total:
                auto.amount = total
                summary["updated"] += 1
            db.session.commit()
    return summary
