"""Order cost domain service.

Owns the order-cost lifecycle (create / update / confirm / batch) plus the
account deductions that confirmation triggers. Confirmation validates every
involved account *before* mutating anything and commits once, so a rejected
confirmation can never leave a half-charged account behind.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import desc, false, func
from sqlalchemy.orm import joinedload

from app import db
from app.models import Account, Consumption, Order, OrderCost, OrderCostBatch
from app.utils.errors import ForbiddenError, NotFoundError, ValidationError
from app.utils.security import (
    allowed_shop_ids,
    apply_shop_scope,
    can_access_shop,
    resolve_write_shop_id,
)

DEFAULT_PER_PAGE = 20
MAX_PER_PAGE = 100


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


def _get_cost(cost_id, action="查看"):
    order_cost = db.session.get(OrderCost, cost_id)
    if order_cost is None:
        raise NotFoundError("订单费用记录不存在")
    if not can_access_shop(order_cost.shop_id):
        raise ForbiddenError(f"无权{action}该订单费用")
    return order_cost


def _decimal(value, field="金额"):
    try:
        return Decimal(str(value or 0))
    except Exception as exc:  # noqa: BLE001 - Decimal raises several types
        raise ValidationError(f"{field}格式不正确") from exc


def list_order_costs(args):
    page = args.get("page", 1, type=int) or 1
    per_page = min(args.get("per_page", DEFAULT_PER_PAGE, type=int) or DEFAULT_PER_PAGE, MAX_PER_PAGE)

    query = apply_shop_scope(OrderCost.query, OrderCost).options(
        joinedload(OrderCost.shipping_account), joinedload(OrderCost.fangguo_account),
    )

    order_number = args.get("order_number")
    if order_number:
        query = query.filter(OrderCost.order_number.like(f"%{order_number}%"))
    status = args.get("status")
    if status:
        query = query.filter_by(status=status)
    batch_id = args.get("batch_id")
    if batch_id:
        query = query.filter_by(batch_id=batch_id)
    start_date = args.get("start_date")
    if start_date:
        query = query.filter(OrderCost.cost_date >= _parse_date(start_date, "开始日期"))
    end_date = args.get("end_date")
    if end_date:
        query = query.filter(OrderCost.cost_date <= _parse_date(end_date, "结束日期"))

    pagination = query.order_by(desc(OrderCost.created_at)).paginate(
        page=page, per_page=per_page, error_out=False
    )
    return {
        "order_costs": [cost.to_dict() for cost in pagination.items],
        "pagination": _pagination(pagination),
    }


def get_order_cost(cost_id):
    return _get_cost(cost_id).to_dict()


def by_order_numbers(order_numbers):
    if not order_numbers:
        raise ValidationError("订单号列表不能为空")
    costs = apply_shop_scope(
        OrderCost.query.options(
            joinedload(OrderCost.shipping_account), joinedload(OrderCost.fangguo_account),
        ).filter(OrderCost.order_number.in_(order_numbers)), OrderCost
    ).all()
    return [cost.to_dict() for cost in costs]


def list_batches(args):
    page = args.get("page", 1, type=int) or 1
    per_page = min(args.get("per_page", DEFAULT_PER_PAGE, type=int) or DEFAULT_PER_PAGE, MAX_PER_PAGE)

    query = OrderCostBatch.query
    allowed = allowed_shop_ids()
    if allowed is not None:
        if not allowed:
            query = query.filter(false())
        else:
            batch_ids = db.session.query(OrderCost.batch_id).filter(OrderCost.shop_id.in_(allowed)).distinct()
            query = query.filter(OrderCostBatch.batch_id.in_(batch_ids))

    pagination = query.order_by(desc(OrderCostBatch.created_at)).paginate(
        page=page, per_page=per_page, error_out=False
    )
    return {
        "batches": [batch.to_dict() for batch in pagination.items],
        "pagination": _pagination(pagination),
    }


def get_batch(batch_id):
    batch = OrderCostBatch.query.filter_by(batch_id=batch_id).first()
    if batch is None:
        raise NotFoundError("批次不存在")
    data = batch.to_dict()
    data["order_costs"] = [cost.to_dict() for cost in OrderCost.get_by_batch(batch_id)]
    return data


def summary(args):
    start_date = args.get("start_date")
    end_date = args.get("end_date")
    return OrderCost.get_cost_summary(
        start_date=_parse_date(start_date, "开始日期") if start_date else None,
        end_date=_parse_date(end_date, "结束日期") if end_date else None,
        status=args.get("status"),
        shop_ids=allowed_shop_ids(),
    )


def _cost_fields(cost):
    """Total and per-type amounts, used by confirmation."""
    return {
        "shipping": Decimal(str(cost.shipping_cost or 0)),
        "fangguo": Decimal(str(cost.fangguo_cost or 0)),
        "other": Decimal(str(cost.other_cost or 0)),
    }


def _create_cost(payload, shop_id, batch_id=None, batch_notes=None):
    return OrderCost(
        shop_id=shop_id,
        order_id=payload["_order_id"],
        order_number=payload["order_number"],
        shipping_cost=float(payload.get("shipping_cost", 0)),
        shipping_account_id=payload.get("shipping_account_id"),
        shipping_reference=payload.get("shipping_reference"),
        shipping_notes=payload.get("shipping_notes") or payload.get("notes"),
        fangguo_cost=float(payload.get("fangguo_cost", 0)),
        fangguo_account_id=payload.get("fangguo_account_id"),
        fangguo_reference=payload.get("fangguo_reference"),
        fangguo_notes=payload.get("fangguo_notes"),
        other_cost=float(payload.get("other_cost", 0)),
        other_description=payload.get("other_description") or payload.get("other_reference"),
        cost_date=_parse_date(payload["cost_date"], "费用日期"),
        entry_user=payload.get("entry_user") or payload.get("created_by", "system"),
        status=payload.get("status", "pending"),
        batch_id=batch_id,
        batch_notes=batch_notes,
    )


def _resolve_order(order_number, shop_id):
    """Order costs must link to a real order (order_id is NOT NULL)."""
    query = Order.query.filter_by(order_number=order_number)
    if shop_id is not None:
        query = query.filter(Order.shop_id == shop_id)
    order = query.first()
    if order is None:
        raise ValidationError(f"订单 {order_number} 不存在，无法录入费用")
    return order


def create_order_cost(payload):
    payload = payload or {}
    if not payload.get("order_number"):
        raise ValidationError("缺少必填字段: order_number")
    if not payload.get("cost_date"):
        raise ValidationError("缺少必填字段: cost_date")
    if not any([payload.get("shipping_cost"), payload.get("fangguo_cost"), payload.get("other_cost")]):
        raise ValidationError("至少需要填写一种费用")

    shop_id, shop_error = resolve_write_shop_id(payload.get("shop_id"))
    if shop_error:
        raise ForbiddenError(shop_error)

    order = _resolve_order(payload["order_number"], shop_id)
    # 未显式指定店铺时归属到订单所在店铺
    effective_shop_id = shop_id if shop_id is not None else order.shop_id
    duplicate = OrderCost.query.filter_by(
        order_number=payload["order_number"], shop_id=effective_shop_id
    ).first()
    if duplicate:
        raise ValidationError(f"订单 {payload['order_number']} 已存在费用记录")

    payload["_order_id"] = order.id
    order_cost = _create_cost(payload, effective_shop_id)
    db.session.add(order_cost)
    db.session.commit()
    return order_cost.to_dict()


def create_batch_order_costs(payload):
    payload = payload or {}
    order_numbers = payload.get("order_numbers")
    if not order_numbers or not isinstance(order_numbers, list):
        raise ValidationError("订单号列表不能为空")
    if not payload.get("cost_date"):
        raise ValidationError("费用日期不能为空")
    if not any([payload.get("shipping_cost"), payload.get("fangguo_cost"), payload.get("other_cost")]):
        raise ValidationError("至少需要填写一种费用")

    shop_id, shop_error = resolve_write_shop_id(payload.get("shop_id"))
    if shop_error:
        raise ForbiddenError(shop_error)

    missing = []
    orders = {}
    for order_number in order_numbers:
        try:
            orders[order_number] = _resolve_order(order_number, shop_id)
        except ValidationError:
            missing.append(order_number)
    if missing:
        raise ValidationError(f'以下订单不存在: {", ".join(missing)}')

    def effective_shop(order):
        return shop_id if shop_id is not None else order.shop_id

    shop_ids_by_number = {number: effective_shop(order) for number, order in orders.items()}
    duplicates = [
        cost.order_number
        for cost in OrderCost.query.filter(OrderCost.order_number.in_(order_numbers)).all()
        if cost.shop_id == shop_ids_by_number.get(cost.order_number)
    ]
    if duplicates:
        raise ValidationError(f'以下订单已存在费用记录: {", ".join(duplicates)}')

    batch_id = str(uuid.uuid4())[:8]
    created = []
    for order_number in order_numbers:
        order_cost = _create_cost(
            {**payload, "order_number": order_number, "_order_id": orders[order_number].id},
            shop_ids_by_number[order_number],
            batch_id=batch_id,
            batch_notes=payload.get("notes"),
        )
        db.session.add(order_cost)
        created.append(order_cost)

    # 批次元信息（此前从未写入，导致批次列表始终为空）
    order_count = len(created)
    db.session.add(
        OrderCostBatch(
            batch_id=batch_id,
            batch_name=payload.get("batch_name") or f"批量录入 {datetime.now():%Y-%m-%d %H:%M}",
            description=payload.get("notes"),
            order_count=order_count,
            total_shipping_cost=float(payload.get("shipping_cost") or 0) * order_count,
            total_fangguo_cost=float(payload.get("fangguo_cost") or 0) * order_count,
            total_other_cost=float(payload.get("other_cost") or 0) * order_count,
            status="pending",
            created_by=payload.get("created_by") or payload.get("entry_user") or "system",
        )
    )

    db.session.commit()
    return {
        "order_costs": [cost.to_dict() for cost in created],
        "batch_id": batch_id,
        "count": len(created),
    }


UPDATABLE_FIELDS = (
    "shipping_cost",
    "fangguo_cost",
    "other_cost",
    "shipping_account_id",
    "fangguo_account_id",
    "shipping_reference",
    "fangguo_reference",
    "other_description",
    "shipping_notes",
    "fangguo_notes",
    "entry_user",
)


def update_order_cost(cost_id, payload):
    order_cost = _get_cost(cost_id, "修改")
    if order_cost.status == "confirmed":
        raise ValidationError("已确认的费用记录不允许修改")

    payload = payload or {}
    for field in UPDATABLE_FIELDS:
        if field in payload:
            setattr(order_cost, field, payload[field])
    if "cost_date" in payload:
        order_cost.cost_date = _parse_date(payload["cost_date"], "费用日期")
    if "notes" in payload:
        order_cost.batch_notes = payload["notes"]

    order_cost.updated_at = datetime.utcnow()
    db.session.commit()
    return order_cost.to_dict()


def delete_order_cost(cost_id):
    order_cost = _get_cost(cost_id, "删除")
    if order_cost.status == "confirmed":
        raise ValidationError("已确认的费用记录不允许删除")

    db.session.delete(order_cost)
    db.session.commit()
    return {"id": cost_id}


def _plan_deductions(order_cost):
    """Collect (account, amount, type, description) pairs for confirmation."""
    plan = []
    if order_cost.shipping_cost and order_cost.shipping_cost > 0:
        plan.append(
            (
                order_cost.shipping_account_id,
                Decimal(str(order_cost.shipping_cost)),
                "shipping",
                f"订单{order_cost.order_number}物流费用",
                "物流账户",
            )
        )
    if order_cost.fangguo_cost and order_cost.fangguo_cost > 0:
        plan.append(
            (
                order_cost.fangguo_account_id,
                Decimal(str(order_cost.fangguo_cost)),
                "order_fee",
                f"订单{order_cost.order_number}方果下单费用",
                "方果账户",
            )
        )
    return plan


def _validate_deductions(order_cost):
    """Return (error_message, resolved_plan) without mutating anything."""
    resolved = []
    for account_id, amount, cost_type, description, label in _plan_deductions(order_cost):
        if not account_id:
            continue
        account = db.session.get(Account, account_id)
        if account is None:
            return f"{label}不存在", []
        if account.shop_id != order_cost.shop_id:
            return f"{label}不属于该订单的店铺", []
        if (account.balance or Decimal("0")) < amount:
            return f"{label}余额不足：需要{amount}，余额{account.balance}", []
        resolved.append((account, amount, cost_type, description))
    return None, resolved


def _apply_deductions(order_cost, resolved):
    """Debit the accounts and write the consumption rows (no commit)."""
    for account, amount, cost_type, description in resolved:
        account.balance = (account.balance or Decimal("0")) - amount
        account.updated_at = datetime.utcnow()
        db.session.add(
            Consumption(
                account_id=account.id,
                amount=amount,
                consumption_type=cost_type,
                description=description,
                reference_id=f"order_cost:{order_cost.id}:{cost_type}",
                consumption_date=order_cost.cost_date,
            )
        )

    order_cost.status = "confirmed"
    order_cost.updated_at = datetime.utcnow()


def confirm_order_cost(cost_id):
    """Confirm one pending order cost and charge the linked accounts."""
    order_cost = _get_cost(cost_id, "确认")
    if order_cost.status != "pending":
        raise ValidationError("只能确认待处理状态的费用记录")

    error, resolved = _validate_deductions(order_cost)
    if error:
        raise ValidationError(f"费用确认失败: {error}")

    _apply_deductions(order_cost, resolved)
    db.session.commit()
    return order_cost.to_dict()


def confirm_batch(batch_id):
    """Confirm a batch atomically: every row is validated before anything is charged."""
    order_costs = OrderCost.get_by_batch(batch_id)
    if not order_costs:
        raise NotFoundError("未找到指定批次的费用记录")
    if any(cost.status != "pending" for cost in order_costs):
        raise ValidationError("批次中存在非待处理状态的费用记录")

    failed = []
    for order_cost in order_costs:
        error, _ = _validate_deductions(order_cost)
        if error:
            failed.append({"order_number": order_cost.order_number, "error": error})

    if failed:
        raise ValidationError(
            f"批量确认失败：成功 0 条，失败 {len(failed)} 条（未产生任何扣费）",
            extra={"failed_orders": failed},
        )

    for order_cost in order_costs:
        _, resolved = _validate_deductions(order_cost)
        _apply_deductions(order_cost, resolved)

    db.session.commit()
    return {"success_count": len(order_costs)}
