"""Persist scan failures only after rolling back any incomplete outbound transaction."""
from datetime import date, datetime

from flask import current_app
from sqlalchemy import func, or_

from app import db
from app.models import Order, OutboundScanFailure, User
from app.utils.errors import ApiError, ValidationError
from app.utils.security import current_user
from app.utils.timezone import beijing_date_from_utc

CATEGORIES = {
    "stock_shortage": "库存不足",
    "not_found": "未找到面单",
    "label_error": "面单不齐或不匹配",
    "order_status": "订单状态不允许",
    "changed_items": "商品或出库流水需核对",
    "system_error": "系统异常",
    "other": "其他限制",
}
DUPLICATE_MESSAGES = {
    "该包裹已扫码，禁止重复出库", "该售后包裹已扫码出库，请勿重复操作",
    "订单已完成出库，请勿重复扫码", "售后包裹已出库", "订单已有有效出库扣减，禁止重复扣减",
}


def classify(error):
    explicit = getattr(error, "extra", {}).get("scan_category")
    if explicit in CATEGORIES or explicit == "duplicate":
        return explicit
    reason = str(error)
    if reason in DUPLICATE_MESSAGES:
        return "duplicate"
    if not isinstance(error, ApiError):
        return "system_error"
    if "库存不足" in reason or "未到齐" in reason or "未备齐" in reason:
        return "stock_shortage"
    if "找不到此物流面单" in reason or "未找到" in reason:
        return "not_found"
    if "商品发生变化" in reason or "快照" in reason or "扣减流水" in reason:
        return "changed_items"
    if "标签" in reason or "条码" in reason or "面单" in reason or "4PX 单号" in reason:
        return "label_error"
    if "状态" in reason or "付款" in reason or "付清" in reason or "退款" in reason or "取消" in reason:
        return "order_status"
    return "other"


def record_failure(shop_id, number, error, *, order_id=None, after_sale_id=None, order_number=None):
    category = classify(error)
    if category == "duplicate":
        return category, None
    # This rollback releases outbound locks and prevents accidental stock/status commits.
    db.session.rollback()
    now = datetime.utcnow()
    values = {
        "shop_id": shop_id, "business_date": beijing_date_from_utc(now),
        "scan_code": number[:256], "category": category,
        "reason": (str(error) if isinstance(error, ApiError) else "系统处理失败，请核对出库状态后重试")[:1000],
        "order_id": order_id, "after_sale_id": after_sale_id, "order_number": order_number,
        "user_id": (current_user() or {}).get("user_id"), "attempts": 1,
        "created_at": now, "last_seen_at": now,
    }
    try:
        if order_id and not order_number:
            order_number = db.session.query(Order.order_number).filter_by(id=order_id, shop_id=shop_id).scalar()
            values["order_number"] = order_number
        if db.engine.dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import insert
        else:
            from sqlalchemy.dialects.sqlite import insert
        statement = insert(OutboundScanFailure).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=["shop_id", "business_date", "scan_code", "category"],
            set_={"attempts": OutboundScanFailure.attempts + 1, "last_seen_at": now,
                  "reason": values["reason"], "user_id": values["user_id"],
                  "order_id": order_id, "after_sale_id": after_sale_id, "order_number": order_number},
        ).returning(OutboundScanFailure.id)
        record_id = db.session.execute(statement).scalar_one()
        db.session.commit()
        return category, record_id
    except Exception:
        db.session.rollback()
        current_app.logger.exception("保存扫码出库异常记录失败")
        return category, None


def list_failures(args):
    from app.services.shipping_queue_service import _shop_id
    shop_id = _shop_id(args.get("shop_id"))
    try:
        day = date.fromisoformat(args.get("date")) if args.get("date") else beijing_date_from_utc(datetime.utcnow())
        page = max(int(args.get("page") or 1), 1)
        per_page = min(max(int(args.get("per_page") or 20), 1), 100)
    except (ValueError, TypeError) as exc:
        raise ValidationError("请提供有效的北京时间日期和分页参数") from exc
    category = str(args.get("category") or "")
    if category and category not in CATEGORIES:
        raise ValidationError("异常分类不存在")
    query = OutboundScanFailure.query.filter_by(shop_id=shop_id, business_date=day)
    search = str(args.get("search") or "").strip()[:256]
    if search:
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.filter(or_(OutboundScanFailure.scan_code.ilike(f"%{escaped}%", escape="\\"),
                                OutboundScanFailure.order_number.ilike(f"%{escaped}%", escape="\\")))
    summary = db.session.query(OutboundScanFailure.category, func.count(), func.sum(OutboundScanFailure.attempts)).filter(
        OutboundScanFailure.id.in_(query.with_entities(OutboundScanFailure.id))
    ).group_by(OutboundScanFailure.category).all()
    if category:
        query = query.filter_by(category=category)
    total = query.count()
    records = query.order_by(OutboundScanFailure.last_seen_at.desc(), OutboundScanFailure.id.desc()).offset(
        (page - 1) * per_page).limit(per_page).all()
    users = {row.id: row.display_name or row.username for row in db.session.query(User.id, User.display_name, User.username).filter(
        User.id.in_({record.user_id for record in records if record.user_id})
    ).all()} if records else {}
    return {"records": [record.to_dict() | {"operator": users.get(record.user_id, "")} for record in records], "date": day.isoformat(),
            "categories": CATEGORIES,
            "summary": {key: {"records": count, "attempts": int(attempts)} for key, count, attempts in summary},
            "pagination": {"page": page, "per_page": per_page, "total": total}}
