"""Expense domain service.

Owns every expense query and mutation, including the account balance side
effects, so app/api/expenses.py stays a thin routing layer.
"""

import time
from datetime import datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import asc, desc, or_
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import selectinload

from app import db
from app.models import Account, Consumption, Expense, Order, Recharge, Shop
from app.utils.errors import ForbiddenError, NotFoundError, ValidationError
from app.utils.security import (
    allowed_shop_ids,
    apply_shop_scope,
    can_access_shop,
    resolve_write_shop_id,
)

# SQLite can return "database is locked" under concurrent writes; retry briefly.
LOCK_RETRY_ATTEMPTS = 3
LOCK_RETRY_DELAY = 0.2

DEFAULT_PER_PAGE = 10
MAX_PER_PAGE = 100

# 收入入账到账户时，用 transaction_id 关联回收入流水，便于删除时精确回退
INCOME_LEDGER_PREFIX = "income:"


def _parse_expense_date(value, field_name="日期"):
    try:
        return datetime.strptime(str(value).strip('"'), "%Y-%m-%d").date()
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{field_name}格式错误") from exc


def _order_brief(order):
    return {
        "id": order.id,
        "order_number": order.order_number,
        "customer_name": order.customer_name,
        "total_price": float(order.total_price) if order.total_price else 0,
    }


def _serialize_expense(expense, account_name=None, shop_name=None, fallback_orders=None):
    """List representation (kept compatible with the previous API)."""
    orders_info = [
        _order_brief(order) for order in expense.orders if order.shop_id == expense.shop_id
    ]

    # Backwards compatibility: fall back to the single linked order.
    if not orders_info and expense.order_id:
        order = fallback_orders.get(expense.order_id) if fallback_orders is not None else db.session.get(Order, expense.order_id)
        if order is not None and order.shop_id == expense.shop_id:
            orders_info = [_order_brief(order)]

    return {
        "id": expense.id,
        "date": expense.expense_date.strftime("%Y-%m-%d") if expense.expense_date else None,
        "record_type": expense.record_type or Expense.TYPE_EXPENSE,
        "category": expense.category,
        "amount": float(expense.amount) if expense.amount else 0,
        "description": expense.description,
        "status": getattr(expense, "status", None) or "confirmed",
        "submitter": getattr(expense, "submitter", None) or "Admin",
        "account_id": expense.account_id,
        "account_name": account_name,
        "shop_id": expense.shop_id,
        "shop_name": shop_name,
        "order_id": expense.order_id,
        "order_info": orders_info[0] if orders_info else None,
        "orders_info": orders_info,
        "created_at": expense.created_at.strftime("%Y-%m-%d %H:%M:%S") if expense.created_at else None,
        "updated_at": expense.updated_at.strftime("%Y-%m-%d %H:%M:%S") if expense.updated_at else None,
    }


def list_expenses(args):
    """Paginated expense list, scoped to the caller's shops."""
    page = args.get("page", 1, type=int) or 1
    per_page = min(
        args.get("per_page", DEFAULT_PER_PAGE, type=int) or DEFAULT_PER_PAGE,
        MAX_PER_PAGE,
    )

    query = apply_shop_scope(Expense.query, Expense).options(selectinload(Expense.orders))

    record_type = args.get("record_type")
    if record_type in Expense.RECORD_TYPES:
        query = query.filter(Expense.record_type == record_type)

    category = (args.get("category") or "").strip()
    if category:
        query = query.filter(Expense.category == category)

    status = (args.get("status") or "").strip()
    if status:
        query = query.filter(Expense.status == status)

    description = (args.get("description") or "").strip()
    if description:
        query = query.filter(Expense.description.ilike(f"%{description}%"))

    submitter = (args.get("submitter") or "").strip()
    if submitter:
        query = query.filter(Expense.submitter.ilike(f"%{submitter}%"))

    search = (args.get("search") or "").strip()
    if search:
        pattern = f"%{search}%"
        query = query.filter(
            or_(Expense.description.ilike(pattern), Expense.submitter.ilike(pattern))
        )

    start_date = args.get("start_date")
    if start_date:
        query = query.filter(Expense.expense_date >= _parse_expense_date(start_date, "开始日期"))

    end_date = args.get("end_date")
    if end_date:
        query = query.filter(Expense.expense_date <= _parse_expense_date(end_date, "结束日期"))

    sortable = {
        "date": Expense.expense_date,
        "amount": Expense.amount,
        "category": Expense.category,
        "status": Expense.status,
        "submitter": Expense.submitter,
        "created_at": Expense.created_at,
    }
    sort_column = sortable.get(args.get("sort_by"), Expense.expense_date)
    sort_direction = asc if args.get("sort_order") == "asc" else desc
    pagination = query.order_by(sort_direction(sort_column), desc(Expense.id)).paginate(
        page=page, per_page=per_page, error_out=False
    )

    account_ids = {item.account_id for item in pagination.items if item.account_id}
    account_names = {}
    if account_ids:
        account_names = {
            account.id: account.account_name
            for account in Account.query.filter(Account.id.in_(account_ids)).all()
        }

    # 店铺归属一次性批量取名字，避免逐行查询
    shop_ids = {item.shop_id for item in pagination.items if item.shop_id}
    shop_names = {}
    if shop_ids:
        shop_names = {
            shop.id: shop.name for shop in Shop.query.filter(Shop.id.in_(shop_ids)).all()
        }

    fallback_ids = {item.order_id for item in pagination.items if item.order_id and not item.orders}
    fallback_orders = {
        order.id: order for order in Order.query.filter(Order.id.in_(fallback_ids)).all()
    } if fallback_ids else {}

    return {
        "expenses": [
            _serialize_expense(
                item,
                account_names.get(item.account_id),
                shop_names.get(item.shop_id),
                fallback_orders,
            )
            for item in pagination.items
        ],
        "pagination": {
            "page": pagination.page,
            "per_page": pagination.per_page,
            "total": pagination.total,
            "pages": pagination.pages,
            "has_prev": pagination.has_prev,
            "has_next": pagination.has_next,
        },
    }


def expense_categories():
    return [{"key": key, "label": label} for key, label in Expense.get_categories().items()]


def income_categories():
    """Categories available for extra income records."""
    return [{"key": key, "label": label} for key, label in Expense.get_income_categories().items()]


def expense_summary():
    """Totals for expenses and extra income, scoped to the caller's shops.

    The first three keys keep their original meaning (expenses only) so the
    existing frontend keeps working; income figures are additive.
    """

    def breakdown(record_type):
        rows = (
            apply_shop_scope(
                db.session.query(
                    Expense.category,
                    db.func.sum(Expense.amount).label("total_amount"),
                    db.func.count(Expense.id).label("count"),
                ).filter(Expense.record_type == record_type),
                Expense,
            )
            .group_by(Expense.category)
            .all()
        )

        total = (
            apply_shop_scope(
                db.session.query(db.func.sum(Expense.amount)).filter(Expense.record_type == record_type),
                Expense,
            ).scalar()
            or 0
        )
        count = apply_shop_scope(Expense.query.filter(Expense.record_type == record_type), Expense).count()

        return (
            float(total),
            count,
            [
                {
                    "category": row.category,
                    "total_amount": float(row.total_amount or 0),
                    "count": row.count,
                }
                for row in rows
            ],
        )

    expense_total, expense_count, expense_rows = breakdown(Expense.TYPE_EXPENSE)
    income_total, income_count, income_rows = breakdown(Expense.TYPE_INCOME)

    return {
        "total_amount": expense_total,
        "total_count": expense_count,
        "type_summary": expense_rows,
        # 新增：额外收入
        "total_income": income_total,
        "income_count": income_count,
        "income_summary": income_rows,
        "net_amount": round(income_total - expense_total, 2),
    }


def _get_expense(expense_id, action="查看"):
    expense = db.session.get(Expense, expense_id)
    if expense is None:
        raise NotFoundError("费用记录不存在")
    if not can_access_shop(expense.shop_id):
        raise ForbiddenError(f"无权{action}该费用")
    return expense


def _linked_consumption(expense):
    """Find the consumption row created for this expense.

    New rows carry reference_id = expense:<id>; legacy rows are matched by the
    historical description pattern so old data still restores correctly.
    """
    if not expense.account_id:
        return None

    by_reference = Consumption.query.filter_by(
        account_id=expense.account_id,
        reference_id=f"expense:{expense.id}",
    ).first()
    if by_reference is not None:
        return by_reference

    return Consumption.query.filter(
        Consumption.account_id == expense.account_id,
        Consumption.description.like(f"%费用支出: {expense.description}%"),
    ).first()


def _linked_income_recharge(expense):
    """Recharge row created when extra income was credited into an account."""
    if not expense.account_id:
        return None
    return Recharge.query.filter_by(
        account_id=expense.account_id,
        transaction_id=f"{INCOME_LEDGER_PREFIX}{expense.id}",
    ).first()


def _credit_income_to_account(expense, amount):
    """Record received income into the linked account as a completed recharge."""
    if not expense.account_id:
        return
    account = db.session.get(Account, expense.account_id)
    if account is None:
        raise ValidationError("关联账户不存在")

    account.balance = (account.balance or 0) + amount
    account.updated_at = datetime.utcnow()
    db.session.add(
        Recharge(
            account_id=expense.account_id,
            amount=amount,
            currency=expense.currency or "CNY",
            recharge_method="income",
            transaction_id=f"{INCOME_LEDGER_PREFIX}{expense.id}",
            description=f"收入入账: {expense.description}",
            status="completed",
            recharge_date=datetime.combine(expense.expense_date, datetime.min.time()),
        )
    )


def _reverse_income_from_account(expense):
    """Undo the account credit when an income record is deleted."""
    recharge = _linked_income_recharge(expense)
    if recharge is None:
        return

    account = db.session.get(Account, expense.account_id)
    if account is not None:
        account.balance = (account.balance or 0) - recharge.amount
        account.updated_at = datetime.utcnow()
    db.session.delete(recharge)


def _restore_account_balance(expense):
    consumption = _linked_consumption(expense)
    if consumption is None:
        return

    account = db.session.get(Account, expense.account_id)
    if account is not None:
        account.balance = (account.balance or 0) + consumption.amount
        account.updated_at = datetime.utcnow()
    db.session.delete(consumption)


def _reverse_account_effect(expense):
    """撤销这条流水对账户余额的影响（收入=减回，支出=加回）。"""
    if (expense.record_type or Expense.TYPE_EXPENSE) == Expense.TYPE_INCOME:
        _reverse_income_from_account(expense)
    else:
        _restore_account_balance(expense)


def _created_payload(expense):
    return {
        "id": expense.id,
        "date": expense.expense_date.strftime("%Y-%m-%d"),
        "record_type": expense.record_type or Expense.TYPE_EXPENSE,
        "category": expense.category,
        "amount": float(expense.amount),
        "currency": expense.currency,
        "original_amount": float(expense.original_amount) if expense.original_amount else None,
        "original_currency": expense.original_currency,
        "exchange_rate": float(expense.exchange_rate) if expense.exchange_rate else 1.0,
        "description": expense.description,
        "vendor": expense.vendor,
        "reference_id": expense.reference_id,
        "submitter": expense.submitter,
        "status": expense.status,
        "shop_id": expense.shop_id,
        "shop_name": expense.shop.name if expense.shop else None,
    }


def _resolve_expense_shop(payload_shop_id):
    """费用必须归属到具体店铺，保证各店铺账目互不串。

    - 子账户：只能写自己被分配的店铺（``resolve_write_shop_id`` 把关）
    - 管理员：显式传 ``shop_id`` 就用它；没传但系统里只有一个店铺时自动归属；
      存在多个店铺时要求显式选择，避免产生「无店铺」的孤儿费用。
    """
    if payload_shop_id not in (None, ""):
        try:
            payload_shop_id = int(payload_shop_id)
        except (TypeError, ValueError) as exc:
            raise ValidationError("所属店铺无效") from exc

    shop_id, error = resolve_write_shop_id(payload_shop_id)
    if error:
        raise ForbiddenError(error)
    if shop_id is not None:
        if db.session.get(Shop, shop_id) is None:
            raise ValidationError("所属店铺不存在")
        return shop_id

    allowed = allowed_shop_ids()
    candidates = allowed if allowed is not None else [shop.id for shop in Shop.query.all()]
    if len(candidates) == 1:
        return candidates[0]
    raise ValidationError("请选择所属店铺（费用必须归属到具体店铺，才能保证各店铺账目隔离）")


def _resolve_expense_orders(order_ids, shop_id):
    """Only link orders that belong to the expense's shop."""
    if not isinstance(order_ids, list):
        raise ValidationError("关联订单必须是数组")
    try:
        ids = list(dict.fromkeys(int(order_id) for order_id in order_ids))
    except (TypeError, ValueError) as exc:
        raise ValidationError("关联订单 ID 无效") from exc
    if not ids:
        return []
    orders = Order.query.filter(Order.id.in_(ids), Order.shop_id == shop_id).all()
    if len(orders) != len(ids):
        raise ValidationError("关联订单不存在或不属于所选店铺")
    return orders


def create_expense(payload):
    """Create an expense (money out) or an extra income record (money in).

    - expense + account_id -> deduct the account balance and write a consumption
    - income  + account_id -> credit the account balance as a completed recharge
    """
    payload = payload or {}
    for field in ("date", "category", "amount", "description"):
        if not payload.get(field):
            raise ValidationError(f"缺少必需字段: {field}")

    record_type = payload.get("record_type") or Expense.TYPE_EXPENSE
    if record_type not in Expense.RECORD_TYPES:
        raise ValidationError("流水类型只能是 expense（支出）或 income（收入）")
    is_income = record_type == Expense.TYPE_INCOME

    try:
        amount_value = Decimal(str(payload["amount"]))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError("金额格式不正确") from exc
    if amount_value <= 0:
        raise ValidationError("金额必须大于0")

    amount = Decimal(str(payload["amount"]))
    shop_id = _resolve_expense_shop(payload.get("shop_id"))
    account_id = payload.get("account_id")
    account = None
    if account_id:
        account = db.session.get(Account, account_id)
        if account is None:
            raise ValidationError(f"账户不存在: {account_id}")
        if account.shop_id != shop_id:
            raise ValidationError("账户不属于所选店铺")
        # 只有支出才需要校验余额；收入是往账户里加钱
        if not is_income and (account.balance or 0) < amount:
            raise ValidationError(f'账户余额不足，当前余额: {account.balance}，需要扣除: {payload["amount"]}')

    order_ids = payload.get("order_ids") or []
    if not order_ids and payload.get("order_id") is not None:
        order_ids = [payload["order_id"]]
    orders = _resolve_expense_orders(order_ids, shop_id)

    expense_date = _parse_expense_date(payload["date"])

    for attempt in range(1, LOCK_RETRY_ATTEMPTS + 1):
        try:
            expense = Expense(
                shop_id=shop_id,
                record_type=record_type,
                expense_date=expense_date,
                category=payload["category"],
                amount=float(payload["amount"]),
                currency="CNY",
                original_amount=payload.get("original_amount"),
                original_currency=payload.get("original_currency"),
                exchange_rate=payload.get("exchange_rate", 1.0),
                description=payload["description"],
                vendor=payload.get("vendor", ""),
                reference_id=payload.get("reference_id", ""),
                submitter=payload.get("submitter", "Admin"),
                account_id=account_id,
                order_id=orders[0].id if orders else None,
                status=payload.get("status", "completed"),
                created_at=datetime.utcnow(),
            )
            db.session.add(expense)
            db.session.flush()  # need expense.id for the consumption reference

            expense.orders.extend(orders)

            if account is not None:
                if is_income:
                    _credit_income_to_account(expense, amount)
                else:
                    account.balance = (account.balance or 0) - amount
                    account.updated_at = datetime.utcnow()
                    db.session.add(
                        Consumption(
                            account_id=account_id,
                            amount=amount,
                            description=f"费用支出: {payload['description']}",
                            reference_id=f"expense:{expense.id}",
                            consumption_date=expense.expense_date,
                            created_at=datetime.utcnow(),
                        )
                    )

            db.session.commit()
            return _created_payload(expense)

        except OperationalError as exc:
            db.session.rollback()
            if "database is locked" in str(exc).lower() and attempt < LOCK_RETRY_ATTEMPTS:
                time.sleep(LOCK_RETRY_DELAY * attempt)
                continue
            raise


def update_expense(expense_id, payload):
    """Update whitelisted fields and re-link orders."""
    expense = _get_expense(expense_id, "修改")
    payload = payload or {}

    target_account_id = expense.account_id
    if "account_id" in payload:
        raw = payload['account_id']
        try:
            target_account_id = None if raw in (None, "") else int(raw)
        except (TypeError, ValueError) as exc:
            raise ValidationError("账户编号不正确") from exc
        if isinstance(raw, bool) or (target_account_id is not None and target_account_id <= 0):
            raise ValidationError("账户编号不正确")
        if target_account_id:
            account = db.session.get(Account, target_account_id)
            if account is None or account.shop_id != expense.shop_id:
                raise ValidationError("账户不存在或不属于该费用的店铺")
    try:
        final_amount = Decimal(str(payload.get('amount', expense.amount)))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError("金额格式不正确") from exc
    if not final_amount.is_finite() or final_amount <= 0:
        raise ValidationError("金额必须大于0")
    relink = target_account_id != expense.account_id or (
        bool(expense.account_id) and final_amount != expense.amount
    )
    if relink:
        # Reverse before changing the description/account: legacy ledger lookup
        # uses the original description. Removal and replacement are one transaction.
        _reverse_account_effect(expense)
        db.session.flush()

    if "date" in payload:
        expense.expense_date = _parse_expense_date(payload["date"])
    if "expense_date" in payload:
        expense.expense_date = _parse_expense_date(payload["expense_date"])
    if "category" in payload:
        expense.category = payload["category"]
    if "amount" in payload:
        expense.amount = final_amount
    if "description" in payload:
        expense.description = payload["description"]
    if "submitter" in payload:
        expense.submitter = payload["submitter"]
    if "status" in payload:
        expense.status = payload["status"]
    if "vendor" in payload:
        expense.vendor = payload["vendor"]
    if "reference_id" in payload:
        expense.reference_id = payload["reference_id"]
    if "account_id" in payload:
        expense.account_id = target_account_id

    if relink and target_account_id:
        if expense.record_type == Expense.TYPE_INCOME:
            _credit_income_to_account(expense, final_amount)
        else:
            account = db.session.get(Account, target_account_id)
            if (account.balance or 0) < final_amount:
                raise ValidationError("账户余额不足")
            account.balance = (account.balance or 0) - final_amount
            account.updated_at = datetime.utcnow()
            db.session.add(Consumption(account_id=target_account_id, amount=final_amount,
                description=f"费用支出: {expense.description}", reference_id=f"expense:{expense.id}",
                consumption_date=expense.expense_date, created_at=datetime.utcnow()))

    if "order_ids" in payload or "order_id" in payload:
        if "order_ids" in payload:
            order_ids = payload["order_ids"] or []
        else:
            order_ids = [payload["order_id"]] if payload["order_id"] not in (None, "") else []
        orders = _resolve_expense_orders(order_ids or [], expense.shop_id)
        expense.orders = orders
        expense.order_id = orders[0].id if orders else None

    expense.updated_at = datetime.utcnow()
    db.session.commit()

    return {
        "id": expense.id,
        "date": expense.expense_date.strftime("%Y-%m-%d"),
        "record_type": expense.record_type or Expense.TYPE_EXPENSE,
        "category": expense.category,
        "amount": float(expense.amount),
        "description": expense.description,
        "submitter": expense.submitter,
        "status": expense.status,
        "vendor": expense.vendor,
        "reference_id": expense.reference_id,
        "account_id": expense.account_id,
    }


def delete_expense(expense_id):
    """Delete one expense and restore the linked account balance."""
    expense = _get_expense(expense_id, "删除")
    _reverse_account_effect(expense)
    db.session.delete(expense)
    db.session.commit()
    return {"id": expense_id}


def batch_delete_expenses(expense_ids):
    """Delete several expenses (shop scoped) and restore account balances."""
    if not isinstance(expense_ids, list) or not expense_ids or len(expense_ids) > 1000:
        raise ValidationError("请选择 1 至 1000 条费用记录")
    if any(isinstance(item, bool) or not isinstance(item, int) or item <= 0 for item in expense_ids):
        raise ValidationError("费用编号必须为正整数")
    ids = list(set(expense_ids))

    expenses = apply_shop_scope(Expense.query.filter(Expense.id.in_(ids)), Expense).all()
    if len(expenses) != len(ids):
        raise NotFoundError("部分费用不存在或无权访问，未删除任何记录")

    for expense in expenses:
        _reverse_account_effect(expense)
        db.session.delete(expense)

    db.session.commit()
    return {"deleted_count": len(expenses)}
