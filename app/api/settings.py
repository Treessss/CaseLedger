"""Settings endpoints: default shop credentials and fee configuration.

Thin routing layer over app.services.settings_service. Writes are admin-only
(enforced centrally in app.__init__.ADMIN_ONLY_RULES).
"""

from flask import request

from app.api import bp
from app.services import settings_service
from app.utils.responses import ok


@bp.route("/settings/shopify", methods=["GET"])
def get_shopify_config():
    """Default shop credentials (secrets are masked)."""
    return ok(settings_service.get_shopify_config(), message="多店铺配置请在「店铺管理」页面维护")


@bp.route("/settings/shopify", methods=["POST"])
def save_shopify_config():
    """Create or update the default shop credentials."""
    settings_service.save_shopify_config(request.get_json(silent=True) or {})
    return ok(message="配置保存成功")


@bp.route("/settings/shopify/test", methods=["POST"])
def test_shopify_connection():
    """Test the default shop connection."""
    return ok(settings_service.test_shopify_connection(), message="连接测试成功")


@bp.route("/settings/shopify/sync", methods=["POST"])
def sync_shopify_orders():
    """Sync orders for the default shop."""
    data = request.get_json(silent=True) or {}
    result = settings_service.sync_shopify_orders(
        days_back=data.get("days_back", 30),
        limit=data.get("limit", 250),
    )
    return ok(result, message="订单同步完成")


@bp.route("/settings/fees", methods=["GET"])
def get_fee_configs():
    """Active fee configurations."""
    return ok(settings_service.list_fees())


@bp.route("/settings/fees", methods=["POST"])
def create_fee_config():
    """Create a fee configuration."""
    config = settings_service.create_fee(request.get_json(silent=True) or {})
    return ok(config, message="费用配置创建成功")


@bp.route("/settings/fees/<int:fee_id>", methods=["PUT"])
def update_fee_config(fee_id):
    """Update a fee configuration."""
    settings_service.update_fee(fee_id, request.get_json(silent=True) or {})
    return ok(message="费用配置更新成功")


@bp.route("/settings/fees/<int:fee_id>", methods=["DELETE"])
def delete_fee_config(fee_id):
    """Deactivate a fee configuration (soft delete)."""
    settings_service.delete_fee(fee_id)
    return ok(message="费用配置删除成功")
