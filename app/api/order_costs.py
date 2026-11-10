"""Order cost endpoints.

Thin routing layer over app.services.order_cost_service. Legacy response keys
(``order_costs`` / ``order_cost`` / ``batches`` / ``batch`` / ``summary``) are
kept for the existing frontend, together with the standard ``data`` field.
"""

from flask import request

from app.api import bp
from app.services import fourpx_cost_reconcile_service, order_cost_service
from app.utils.responses import ok


@bp.route("/order-costs", methods=["GET"])
def get_order_costs():
    """Order cost list (paginated, shop scoped)."""
    result = order_cost_service.list_order_costs(request.args)
    return ok(result["order_costs"], order_costs=result["order_costs"], pagination=result["pagination"])


@bp.route("/order-costs", methods=["POST"])
def create_order_cost():
    """Create a single order cost record."""
    order_cost = order_cost_service.create_order_cost(request.get_json(silent=True) or {})
    return ok(order_cost, message="订单费用记录创建成功", status=201, order_cost=order_cost)


@bp.route("/order-costs/fourpx/reconcile", methods=["GET"])
def fourpx_cost_reconcile():
    """4PX 真实物流费 vs 订单物流费对账（只读）。"""
    return ok(fourpx_cost_reconcile_service.reconcile(request.args))


@bp.route("/order-costs/fourpx/apply", methods=["POST"])
def fourpx_cost_apply_missing():
    """只补「4PX 有费用、订单未记物流费」的记录，不覆盖已有金额。"""
    result = fourpx_cost_reconcile_service.apply_missing(request.get_json(silent=True) or {})
    return ok(result, message=f"已补记 {result['created']} 笔物流费")


@bp.route("/order-costs/batch", methods=["POST"])
def create_batch_order_costs():
    """Create order cost records for several orders at once."""
    result = order_cost_service.create_batch_order_costs(request.get_json(silent=True) or {})
    return ok(
        result["order_costs"],
        message=f"成功创建 {result['count']} 条订单费用记录",
        status=201,
        order_costs=result["order_costs"],
        batch_id=result["batch_id"],
    )


@bp.route("/order-costs/by-orders", methods=["POST"])
def get_order_costs_by_orders():
    """Look up order costs by order numbers."""
    data = request.get_json(silent=True) or {}
    costs = order_cost_service.by_order_numbers(data.get("order_numbers"))
    return ok(costs, order_costs=costs)


@bp.route("/order-costs/summary", methods=["GET"])
def get_order_costs_summary():
    """Aggregated order cost totals."""
    summary = order_cost_service.summary(request.args)
    return ok(summary, summary=summary)


@bp.route("/order-costs/<int:cost_id>", methods=["GET"])
def get_order_cost(cost_id):
    """Order cost detail."""
    order_cost = order_cost_service.get_order_cost(cost_id)
    return ok(order_cost, order_cost=order_cost)


@bp.route("/order-costs/<int:cost_id>", methods=["PUT"])
def update_order_cost(cost_id):
    """Update a pending order cost record."""
    order_cost = order_cost_service.update_order_cost(cost_id, request.get_json(silent=True) or {})
    return ok(order_cost, message="订单费用记录更新成功", order_cost=order_cost)


@bp.route("/order-costs/<int:cost_id>", methods=["DELETE"])
def delete_order_cost(cost_id):
    """Delete a pending order cost record."""
    order_cost_service.delete_order_cost(cost_id)
    return ok(message="订单费用记录删除成功")


@bp.route("/order-costs/<int:cost_id>/confirm", methods=["POST"])
def confirm_order_cost(cost_id):
    """Confirm a record and charge the linked accounts atomically."""
    order_cost = order_cost_service.confirm_order_cost(cost_id)
    return ok(order_cost, message="费用确认成功，已自动扣费", order_cost=order_cost)


@bp.route("/order-costs/batch/<batch_id>/confirm", methods=["POST"])
def confirm_batch_order_costs(batch_id):
    """Confirm a whole batch (all rows validated before any charge)."""
    result = order_cost_service.confirm_batch(batch_id)
    return ok(message=f"批量确认成功：共确认 {result['success_count']} 条费用记录")


@bp.route("/order-cost-batches", methods=["GET"])
def get_order_cost_batches():
    """Batch list."""
    result = order_cost_service.list_batches(request.args)
    return ok(result["batches"], batches=result["batches"], pagination=result["pagination"])


@bp.route("/order-cost-batches/<batch_id>", methods=["GET"])
def get_order_cost_batch(batch_id):
    """Batch detail including its order costs."""
    batch = order_cost_service.get_batch(batch_id)
    return ok(batch, batch=batch)
