"""Account endpoints (accounts / recharges / consumptions).

Thin routing layer over app.services.account_service. Responses keep the
legacy keys (``account`` / ``recharge`` / ``consumption`` / ``platforms`` ...)
for the existing frontend, and additionally expose the standard ``data`` field.
"""

from flask import request

from app.api import bp
from app.services import account_service
from app.utils.responses import ok


@bp.route("/accounts", methods=["GET"])
def get_accounts():
    """Account list (paginated, optionally filtered by platform/status)."""
    result = account_service.list_accounts(request.args)
    return ok(result["accounts"], pagination=result["pagination"])


@bp.route("/accounts", methods=["POST"])
def create_account():
    """Create an account."""
    account = account_service.create_account(request.get_json(silent=True) or {})
    return ok(account, message="账户创建成功", status=201, account=account)


@bp.route("/accounts/platforms", methods=["GET"])
def get_platforms():
    """Supported platform list."""
    platforms = account_service.platforms()
    return ok(platforms, platforms=platforms)


@bp.route("/accounts/summary", methods=["GET"])
def get_accounts_summary():
    """Account totals and current-period cash flow."""
    return ok(account_service.summary(request.args))


@bp.route("/accounts/<int:account_id>", methods=["GET"])
def get_account(account_id):
    """Account detail including recharge/consumption totals."""
    account = account_service.get_account(account_id)
    return ok(account, account=account)


@bp.route("/accounts/<int:account_id>", methods=["PUT"])
def update_account(account_id):
    """Update account fields."""
    account = account_service.update_account(account_id, request.get_json(silent=True) or {})
    return ok(account, message="账户更新成功", account=account)


@bp.route("/accounts/<int:account_id>", methods=["DELETE"])
def delete_account(account_id):
    """Delete an account (records must be force-deleted)."""
    force = request.args.get("force", "false").lower() == "true"
    result = account_service.delete_account(account_id, force=force)
    return ok(message="账户删除成功", **result)


@bp.route("/accounts/<int:account_id>/recharges", methods=["GET"])
def get_account_recharges(account_id):
    """Recharge history of one account."""
    result = account_service.list_recharges(account_id, request.args)
    return ok(result["recharges"], recharges=result["recharges"], pagination=result["pagination"])


@bp.route("/accounts/<int:account_id>/recharges", methods=["POST"])
def create_recharge(account_id):
    """Create a recharge (only completed recharges change the balance)."""
    recharge = account_service.create_recharge(account_id, request.get_json(silent=True) or {})
    return ok(recharge, message="充值记录创建成功", status=201, recharge=recharge)


@bp.route("/recharges/<int:recharge_id>/confirm", methods=["POST"])
def confirm_recharge(recharge_id):
    """Confirm a pending recharge."""
    recharge = account_service.confirm_recharge(recharge_id)
    return ok(recharge, message="充值确认成功", recharge=recharge)


@bp.route("/recharges/methods", methods=["GET"])
def get_recharge_methods():
    """Supported recharge methods."""
    methods = account_service.recharge_methods()
    return ok(methods, methods=methods)


@bp.route("/accounts/recharge", methods=["POST"])
def create_account_recharge():
    """Generic recharge entry point (account id comes from the body)."""
    recharge = account_service.create_account_recharge(request.get_json(silent=True) or {})
    return ok(recharge, message="充值记录创建成功", status=201, recharge=recharge)


@bp.route("/accounts/<int:account_id>/consumptions", methods=["GET"])
def get_account_consumptions(account_id):
    """Consumption history of one account."""
    result = account_service.list_consumptions(account_id, request.args)
    return ok(result["consumptions"], consumptions=result["consumptions"], pagination=result["pagination"])


@bp.route("/accounts/<int:account_id>/consumptions", methods=["POST"])
def create_consumption(account_id):
    """Create a consumption and deduct the balance."""
    consumption = account_service.create_consumption(account_id, request.get_json(silent=True) or {})
    return ok(consumption, message="消耗记录创建成功", status=201, consumption=consumption)


@bp.route("/consumptions/types", methods=["GET"])
def get_consumption_types():
    """Supported consumption types."""
    types = account_service.consumption_types()
    return ok(types, types=types)
