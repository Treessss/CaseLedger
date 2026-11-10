"""Account domain service: accounts, recharges and consumptions.

Every balance mutation happens here and is committed exactly once, so this
invariant always holds:

    balance == opening_balance + sum(completed recharges) - sum(consumptions)
"""

from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import desc, func

from app import db
from app.models import Account, Consumption, Recharge, Shop
from app.utils.errors import ForbiddenError, NotFoundError, ValidationError
from app.utils.security import (
    allowed_shop_ids,
    apply_shop_scope,
    can_access_shop,
    requested_shop_id,
    resolve_write_shop_id,
)
from app.utils.timezone import beijing_day_bounds_utc

DEFAULT_PER_PAGE = 20
MAX_PER_PAGE = 100


def _to_decimal(value, field="金额"):
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError(f"{field}格式不正确") from exc
    if amount <= 0:
        raise ValidationError(f"{field}必须大于0")
    return amount


def _parse_date(value, field="日期"):
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{field}格式错误") from exc


def _pagination(page_obj):
    return {
        "page": page_obj.page,
        "pages": page_obj.pages,
        "per_page": page_obj.per_page,
        "total": page_obj.total,
        "has_next": page_obj.has_next,
        "has_prev": page_obj.has_prev,
    }


def _get_account(account_id):
    account = db.session.get(Account, account_id)
    selected_shop = requested_shop_id()
    if account is None or not can_access_shop(account.shop_id) or (
        selected_shop is not None and account.shop_id != selected_shop
    ):
        raise NotFoundError("账户不存在")
    return account


def _resolve_account_shop(payload_shop_id):
    if payload_shop_id not in (None, ""):
        try:
            payload_shop_id = int(payload_shop_id)
        except (TypeError, ValueError) as exc:
            raise ValidationError("所属店铺无效") from exc
    shop_id, error = resolve_write_shop_id(payload_shop_id)
    if error:
        raise ForbiddenError(error)
    if shop_id is None:
        allowed = allowed_shop_ids()
        candidates = allowed if allowed is not None else [shop.id for shop in Shop.query.all()]
        if len(candidates) != 1:
            raise ValidationError("请选择所属店铺")
        shop_id = candidates[0]
    if db.session.get(Shop, shop_id) is None:
        raise ValidationError("所属店铺不存在")
    return shop_id


def _credit(account, amount):
    """Increase the balance without committing (the caller owns the transaction)."""
    account.balance = (account.balance or Decimal("0")) + amount
    account.updated_at = datetime.utcnow()


def _debit(account, amount):
    if (account.balance or Decimal("0")) < amount:
        raise ValidationError(f"账户余额不足：需要{amount}，余额{account.balance}")
    account.balance = (account.balance or Decimal("0")) - amount
    account.updated_at = datetime.utcnow()


def list_accounts(args):
    page = args.get("page", 1, type=int) or 1
    per_page = min(args.get("per_page", DEFAULT_PER_PAGE, type=int) or DEFAULT_PER_PAGE, MAX_PER_PAGE)

    query = apply_shop_scope(Account.query, Account)
    platform = args.get("platform")
    if platform:
        query = query.filter_by(platform=platform)
    status = args.get("status")
    if status:
        query = query.filter_by(status=status)

    # 账户列表按创建时间筛选；业务日期由后端换算成 UTC 半开区间。
    start_raw = args.get("start_date")
    end_raw = args.get("end_date")
    if start_raw:
        start_day = _parse_date(start_raw, "开始日期")
        start_utc, _ = beijing_day_bounds_utc(start_day, start_day)
        query = query.filter(Account.created_at >= start_utc)
    if end_raw:
        end_day = _parse_date(end_raw, "结束日期")
        _, end_utc = beijing_day_bounds_utc(end_day, end_day)
        query = query.filter(Account.created_at < end_utc)
    if start_raw and end_raw and end_day < start_day:
        raise ValidationError("结束日期不能早于开始日期")

    pagination = query.order_by(Account.created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )
    return {
        "accounts": [account.to_dict() for account in pagination.items],
        "pagination": _pagination(pagination),
    }


def create_account(payload):
    payload = payload or {}
    for field in ("platform", "account_name"):
        if not payload.get(field):
            raise ValidationError(f"缺少必填字段: {field}")

    shop_id = _resolve_account_shop(payload.get("shop_id"))
    exists = Account.query.filter_by(
        shop_id=shop_id, platform=payload["platform"], account_name=payload["account_name"]
    ).first()
    if exists:
        raise ValidationError("该平台下已存在同名账户")

    opening = payload.get("balance") or 0
    account = Account(
        shop_id=shop_id,
        platform=payload["platform"],
        account_name=payload["account_name"],
        account_id=payload.get("account_id"),
        description=payload.get("description"),
        balance=Decimal(str(opening)),
        opening_balance=Decimal(str(opening)),
        currency=payload.get("currency", "CNY"),
        status=payload.get("status", "active"),
    )
    db.session.add(account)
    db.session.commit()
    return account.to_dict()


def get_account(account_id):
    account = _get_account(account_id)
    data = account.to_dict()
    data["total_recharge"] = account.get_total_recharge()
    data["total_consumption"] = account.get_total_consumption()
    return data


def update_account(account_id, payload):
    account = _get_account(account_id)
    payload = payload or {}

    if "account_name" in payload:
        duplicate = Account.query.filter(
            Account.shop_id == account.shop_id,
            Account.platform == account.platform,
            Account.account_name == payload["account_name"],
            Account.id != account_id,
        ).first()
        if duplicate:
            raise ValidationError("该平台下已存在同名账户")
        account.account_name = payload["account_name"]

    for field in ("account_id", "description", "currency", "status"):
        if field in payload:
            setattr(account, field, payload[field])

    account.updated_at = datetime.utcnow()
    db.session.commit()
    return account.to_dict()


def delete_account(account_id, force=False):
    """Delete an account; refuse when records exist unless force is set."""
    account = _get_account(account_id)
    recharge_count = Recharge.query.filter_by(account_id=account_id).count()
    consumption_count = Consumption.query.filter_by(account_id=account_id).count()

    if (recharge_count or consumption_count) and not force:
        raise ValidationError(
            "该账户有充值或消耗记录，无法删除。如需强制删除，请确认操作。",
            extra={
                "has_records": True,
                "recharge_count": recharge_count,
                "consumption_count": consumption_count,
            },
        )

    if force:
        Recharge.query.filter_by(account_id=account_id).delete()
        Consumption.query.filter_by(account_id=account_id).delete()

    db.session.delete(account)
    db.session.commit()
    return {"recharge_count": recharge_count, "consumption_count": consumption_count}


def list_recharges(account_id, args):
    _get_account(account_id)
    page = args.get("page", 1, type=int) or 1
    per_page = min(args.get("per_page", DEFAULT_PER_PAGE, type=int) or DEFAULT_PER_PAGE, MAX_PER_PAGE)

    query = Recharge.query.filter_by(account_id=account_id)
    status = args.get("status")
    if status:
        query = query.filter_by(status=status)

    pagination = query.order_by(desc(Recharge.recharge_date)).paginate(
        page=page, per_page=per_page, error_out=False
    )
    return {
        "recharges": [recharge.to_dict() for recharge in pagination.items],
        "pagination": _pagination(pagination),
    }


def _build_recharge(account, payload):
    amount = _to_decimal(payload.get("amount"), "充值金额")
    recharge_date = payload.get("recharge_date")
    return Recharge(
        account_id=account.id,
        amount=amount,
        currency=payload.get("currency", account.currency),
        recharge_method=payload.get("recharge_method"),
        transaction_id=payload.get("transaction_id"),
        description=payload.get("description"),
        status=payload.get("status", "completed"),
        recharge_date=_parse_date(recharge_date, "充值日期") if recharge_date else datetime.utcnow(),
    )


def create_recharge(account_id, payload):
    """Create a recharge. Only a completed recharge affects the balance."""
    account = _get_account(account_id)
    recharge = _build_recharge(account, payload or {})

    db.session.add(recharge)
    if recharge.status == "completed":
        _credit(account, recharge.amount)
    db.session.commit()
    return recharge.to_dict()


def confirm_recharge(recharge_id):
    """Confirm a pending recharge and credit the balance exactly once."""
    recharge = db.session.get(Recharge, recharge_id)
    if recharge is None:
        raise NotFoundError("充值记录不存在")
    if recharge.status != "pending":
        raise ValidationError("只能确认待处理状态的充值记录")

    account = _get_account(recharge.account_id)
    recharge.status = "completed"
    recharge.updated_at = datetime.utcnow()
    _credit(account, Decimal(str(recharge.amount)))
    db.session.commit()
    return recharge.to_dict()


def list_consumptions(account_id, args):
    _get_account(account_id)
    page = args.get("page", 1, type=int) or 1
    per_page = min(args.get("per_page", DEFAULT_PER_PAGE, type=int) or DEFAULT_PER_PAGE, MAX_PER_PAGE)

    query = Consumption.query.filter_by(account_id=account_id)
    consumption_type = args.get("consumption_type")
    if consumption_type:
        query = query.filter_by(consumption_type=consumption_type)

    start_date = args.get("start_date")
    if start_date:
        query = query.filter(Consumption.consumption_date >= _parse_date(start_date, "开始日期"))
    end_date = args.get("end_date")
    if end_date:
        query = query.filter(Consumption.consumption_date <= _parse_date(end_date, "结束日期"))

    pagination = query.order_by(desc(Consumption.consumption_date)).paginate(
        page=page, per_page=per_page, error_out=False
    )
    return {
        "consumptions": [consumption.to_dict() for consumption in pagination.items],
        "pagination": _pagination(pagination),
    }


def create_consumption(account_id, payload):
    """Create a consumption and deduct the balance in the same transaction."""
    account = _get_account(account_id)
    payload = payload or {}

    amount = _to_decimal(payload.get("amount"), "消耗金额")
    if not payload.get("consumption_date"):
        raise ValidationError("消耗日期不能为空")

    consumption = Consumption(
        account_id=account.id,
        amount=amount,
        currency=payload.get("currency", account.currency),
        consumption_type=payload.get("consumption_type"),
        description=payload.get("description"),
        reference_id=payload.get("reference_id"),
        consumption_date=_parse_date(payload["consumption_date"], "消耗日期"),
    )

    _debit(account, amount)
    db.session.add(consumption)
    db.session.commit()
    return consumption.to_dict()


def create_account_recharge(payload):
    """Generic recharge entry point used by the 'accounts/recharge' endpoint."""
    payload = payload or {}
    account_id = payload.get("account_id")
    if not account_id:
        raise ValidationError("账户ID不能为空")
    return create_recharge(int(account_id), payload)


def platforms():
    return Account.get_platforms()


def recharge_methods():
    return Recharge.get_recharge_methods()


def consumption_types():
    return Consumption.get_consumption_types()


def summary(args):
    """Account totals plus current-month (or custom range) cash flow."""
    query = apply_shop_scope(Account.query.filter_by(status="active"), Account)
    platform = args.get("platform")
    if platform:
        query = query.filter_by(platform=platform)
    accounts = query.all()

    total_balance = sum(float(account.balance or 0) for account in accounts)
    account_ids = [account.id for account in accounts]
    recharge_query = db.session.query(func.sum(Recharge.amount)).filter(
        Recharge.status == "completed", Recharge.account_id.in_(account_ids)
    )
    consumption_query = db.session.query(func.sum(Consumption.amount)).filter(
        Consumption.account_id.in_(account_ids)
    )

    start_date = args.get("start_date")
    end_date = args.get("end_date")
    if start_date and end_date:
        start = _parse_date(start_date, "开始日期")
        end = _parse_date(end_date, "结束日期")
        if end < start:
            raise ValidationError("结束日期不能早于开始日期")
        recharge_start, recharge_end = beijing_day_bounds_utc(start, end)
        recharge_query = recharge_query.filter(
            Recharge.recharge_date >= recharge_start, Recharge.recharge_date < recharge_end
        )
        consumption_query = consumption_query.filter(
            Consumption.consumption_date >= start, Consumption.consumption_date <= end
        )
    else:
        # 充值时间是 UTC 时间戳，月份按北京时间边界；消耗日期是原样业务日期。
        now = datetime.utcnow() + timedelta(hours=8)
        month_start = now.date().replace(day=1)
        next_month = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
        recharge_start, recharge_end = beijing_day_bounds_utc(month_start, next_month - timedelta(days=1))
        recharge_query = recharge_query.filter(
            Recharge.recharge_date >= recharge_start,
            Recharge.recharge_date < recharge_end,
        )
        consumption_query = consumption_query.filter(
            Consumption.consumption_date >= month_start,
            Consumption.consumption_date < next_month,
        )

    month_recharge = recharge_query.scalar() or 0
    month_consumption = consumption_query.scalar() or 0

    platform_summary = {}
    for account in accounts:
        bucket = platform_summary.setdefault(
            account.platform,
            {
                "count": 0,
                "total_balance": 0.00,
                "accounts": [],
            },
        )
        bucket["count"] += 1
        bucket["total_balance"] += float(account.balance or 0)
        bucket["accounts"].append(
            {
                "id": account.id,
                "account_name": account.account_name,
                "balance": float(account.balance or 0),
            }
        )

    return {
        "total_accounts": len(accounts),
        "total_balance": total_balance,
        "month_recharge": float(month_recharge),
        "month_consumption": float(month_consumption),
        "platform_summary": platform_summary,
    }
