"""Shop endpoints.

Thin routing layer over app.services.shop_service. The list is visible to every
logged-in user (sub-accounts only see their assigned shops); writes are
admin-only (enforced centrally in app.__init__.ADMIN_ONLY_RULES).
"""

from flask import request

from app.api import bp
from app.services import shop_service
from app.utils.responses import ok


@bp.route("/shops", methods=["GET"])
def list_shops():
    """Shops visible to the caller, with order/expense counters."""
    return ok(shop_service.list_shops(request.args))


@bp.route("/shops", methods=["POST"])
def create_shop():
    """Create a shop."""
    shop = shop_service.create_shop(request.get_json(silent=True) or {})
    return ok(shop, message="店铺创建成功", status=201)


@bp.route("/shops/<int:shop_id>", methods=["GET"])
def get_shop(shop_id):
    """Shop detail."""
    return ok(shop_service.get_shop(shop_id))


@bp.route("/shops/<int:shop_id>", methods=["PUT"])
def update_shop(shop_id):
    """Update shop fields (blank/masked secrets keep their current value)."""
    shop = shop_service.update_shop(shop_id, request.get_json(silent=True) or {})
    return ok(shop, message="店铺更新成功")


@bp.route("/shops/<int:shop_id>", methods=["DELETE"])
def delete_shop(shop_id):
    """Delete a shop (business data requires ?force=true)."""
    force = request.args.get("force", "false").lower() == "true"
    shop_service.delete_shop(shop_id, force=force)
    return ok(message="店铺已删除")
