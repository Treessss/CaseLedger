"""Expense endpoints.

Thin routing layer: parse input, delegate to app.services.expense_service,
shape the response. Domain errors are raised as ApiError and rendered by the
global error handlers.
"""

from flask import request

from app.api import bp
from app.services import expense_service
from app.utils.responses import ok


@bp.route("/expenses", methods=["GET"])
def get_expenses():
    """Expense list (paginated, shop scoped, filtered and sorted)."""
    return ok(expense_service.list_expenses(request.args))


@bp.route("/expenses/categories", methods=["GET"])
def get_expense_categories():
    """Available expense categories."""
    return ok(expense_service.expense_categories())


@bp.route("/expenses/income-categories", methods=["GET"])
def get_income_categories():
    """Categories available for extra income records."""
    return ok(expense_service.income_categories())


@bp.route("/expenses/summary", methods=["GET"])
def get_expenses_summary():
    """Expense totals grouped by category."""
    return ok(expense_service.expense_summary())


@bp.route("/expenses", methods=["POST"])
def create_expense():
    """Create an expense (optionally charging an account balance)."""
    data = request.get_json(silent=True) or {}
    return ok(expense_service.create_expense(data), message="费用添加成功")


@bp.route("/expenses/<int:expense_id>", methods=["PUT"])
def update_expense(expense_id):
    """Update an expense."""
    data = request.get_json(silent=True) or {}
    return ok(expense_service.update_expense(expense_id, data), message="费用更新成功")


@bp.route("/expenses/<int:expense_id>", methods=["DELETE"])
def delete_expense(expense_id):
    """Delete an expense and restore the linked account balance."""
    expense_service.delete_expense(expense_id)
    return ok(message="费用删除成功")


@bp.route("/expenses/batch-delete", methods=["POST"])
def batch_delete_expenses():
    """Delete several expenses at once."""
    data = request.get_json(silent=True) or {}
    result = expense_service.batch_delete_expenses(data.get("expense_ids"))
    return ok(
        message=f"成功删除 {result['deleted_count']} 条费用记录",
        deleted_count=result["deleted_count"],
    )
