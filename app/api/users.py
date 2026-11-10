"""Account endpoints: current profile and sub-account management.

Thin routing layer over app.services.user_service. Everything except
``/api/users/me/password`` is admin-only (enforced centrally in
app.__init__.ADMIN_ONLY_RULES).
"""

from flask import request

from app.api import bp
from app.services import user_service
from app.utils.responses import ok


@bp.route("/currentUser", methods=["GET"])
def get_current_user():
    """Profile of the logged-in user (role + visible shops)."""
    return ok(user_service.current_user_payload())


@bp.route("/users", methods=["GET"])
def list_users():
    """Account list."""
    return ok(user_service.list_users(request.args))


@bp.route("/users", methods=["POST"])
def create_user():
    """Create a sub-account, optionally assigning shops."""
    user = user_service.create_user(request.get_json(silent=True) or {})
    return ok(user, message="子账户创建成功", status=201)


@bp.route("/permissions", methods=["GET"])
def list_permissions():
    """Permission catalog used by the sub-account admin UI (admin-only)."""
    return ok(user_service.permission_catalog())


@bp.route("/users/me/password", methods=["POST"])
def change_own_password():
    """Rotate the caller's own password."""
    data = request.get_json(silent=True) or {}
    user_service.change_own_password(data.get("old_password"), data.get("new_password"))
    return ok(message="密码修改成功，请重新登录")


@bp.route("/users/<int:user_id>", methods=["GET"])
def get_user(user_id):
    """Account detail."""
    return ok(user_service.get_user(user_id))


@bp.route("/users/<int:user_id>", methods=["PUT"])
def update_user(user_id):
    """Update an account (including optional password reset)."""
    user = user_service.update_user(user_id, request.get_json(silent=True) or {})
    return ok(user, message="账号更新成功")


@bp.route("/users/<int:user_id>/shops", methods=["PUT"])
def assign_user_shops(user_id):
    """Assign one or more shops to a sub-account (takes effect immediately)."""
    user = user_service.assign_shops(user_id, (request.get_json(silent=True) or {}).get("shop_ids"))
    return ok(user, message="店铺分配成功")


@bp.route("/users/<int:user_id>/permissions", methods=["PUT"])
def assign_user_permissions(user_id):
    """Grant the功能 modules a sub-account may access (takes effect immediately)."""
    user = user_service.assign_permissions(user_id, (request.get_json(silent=True) or {}).get("permissions"))
    return ok(user, message="功能权限设置成功")


@bp.route("/users/<int:user_id>", methods=["DELETE"])
def delete_user(user_id):
    """Delete a sub-account."""
    user_service.delete_user(user_id)
    return ok(message="账号已删除")
