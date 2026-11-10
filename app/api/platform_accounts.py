"""Platform account (credential vault) endpoints.

Thin routing layer over app.services.platform_account_service. All endpoints
are admin-only (enforced centrally in app.__init__.ADMIN_ONLY_RULES).
"""

from flask import request

from app.api import bp
from app.services import platform_account_service
from app.utils.responses import ok


@bp.route("/platform-accounts", methods=["GET"])
def get_platform_accounts():
    """Credential list (paginated, filterable by platform / keyword)."""
    result = platform_account_service.list_accounts(request.args)
    return ok(result["accounts"], pagination=result["pagination"])


@bp.route("/platform-accounts", methods=["POST"])
def create_platform_account():
    """Store a new credential."""
    account = platform_account_service.create_account(request.get_json(silent=True) or {})
    return ok(account, message="平台账户创建成功", status=201)


@bp.route("/platform-accounts/platforms", methods=["GET"])
def get_platform_account_platforms():
    """Distinct platform names."""
    return ok(platform_account_service.platforms())


@bp.route("/platform-accounts/search", methods=["GET"])
def search_platform_accounts():
    """Keyword search over platform / username / notes."""
    return ok(platform_account_service.search(request.args.get("q", "")))


@bp.route("/platform-accounts/<int:account_id>", methods=["GET"])
def get_platform_account(account_id):
    """Credential detail (password only when explicitly requested)."""
    include_password = request.args.get("include_password", "false").lower() == "true"
    return ok(platform_account_service.get_account(account_id, include_password))


@bp.route("/platform-accounts/<int:account_id>/password", methods=["GET"])
def get_platform_account_password(account_id):
    """Reveal the decrypted password."""
    return ok({"password": platform_account_service.reveal_password(account_id)})


@bp.route("/platform-accounts/<int:account_id>", methods=["PUT"])
def update_platform_account(account_id):
    """Update a credential (optionally rotating the password)."""
    account = platform_account_service.update_account(account_id, request.get_json(silent=True) or {})
    return ok(account, message="平台账户更新成功")


@bp.route("/platform-accounts/<int:account_id>", methods=["DELETE"])
def delete_platform_account(account_id):
    """Delete a credential."""
    platform_account_service.delete_account(account_id)
    return ok(message="平台账户删除成功")
