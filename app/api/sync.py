"""Shopify sync endpoints.

Thin routing layer over app.services.sync_service. Without ``shop_id`` the
call applies to every shop the caller may access.
"""

from flask import request

from app.api import bp
from app.services import sync_service
from app.utils.responses import ok


@bp.route("/sync/test", methods=["GET"])
def test_sync_connection():
    """Test the Shopify connection of one or all accessible shops."""
    result = sync_service.test_connections({"shop_id": request.args.get("shop_id")})
    message = "Shopify连接成功" if result["all_connected"] else "部分店铺连接失败"
    return ok(result["shops"], message=message, status=200 if result["all_connected"] else 400)


@bp.route("/sync/orders", methods=["POST"])
def sync_orders():
    """Sync orders for one or all accessible shops."""
    result = sync_service.sync_orders(request.get_json(silent=True) or {})
    fees = result.get("fees") or []
    fee_note = ""
    if fees:
        settlement = sum(item.get("settlement_updated", 0) for item in fees)
        paypal = sum(item.get("paypal_updated", 0) for item in fees)
        fee_note = f"，并补取真实手续费（结算 {settlement} 单 / PayPal {paypal} 单）"
    return ok(
        result["shops"],
        stats=result["stats"],
        fees=fees,
        message=f"已完成 {result['shop_count']} 个店铺的订单同步{fee_note}",
    )


@bp.route("/sync/products", methods=["POST"])
def sync_products():
    """Sync products for one or all accessible shops."""
    result = sync_service.sync_products(request.get_json(silent=True) or {})
    return ok(
        result["shops"], stats=result["stats"], message=f"已完成 {result['shop_count']} 个店铺的商品同步"
    )


@bp.route("/sync/fulfillment-status", methods=["POST"])
def sync_fulfillment_status():
    """Reconcile the order-level fulfilment status with Shopify."""
    result = sync_service.reconcile_fulfillment_status(request.get_json(silent=True) or {})
    updated = result["totals"]["updated"]
    return ok(
        result["shops"],
        stats=result["totals"],
        message=f"发货状态校准完成，修正 {updated} 单",
    )


@bp.route("/sync/recent", methods=["POST"])
def sync_recent_orders():
    """Sync recently updated orders."""
    result = sync_service.sync_recent_orders(request.get_json(silent=True) or {})
    return ok(result["shops"], message=f"已完成 {result['shop_count']} 个店铺的最近订单同步")


@bp.route("/sync/status", methods=["GET"])
def get_sync_status():
    """Sync counters within the caller's shop scope."""
    return ok(sync_service.sync_status())


@bp.route("/sync/gateway-fees", methods=["POST"])
def sync_gateway_fees():
    """Pull real gateway fees (Shopify Payments settlement + PayPal receipts)."""
    result = sync_service.sync_gateway_fees(request.get_json(silent=True) or {})
    return ok(result["shops"], stats=result["stats"], message="网关真实手续费同步完成")


@bp.route("/sync/shop-info", methods=["GET"])
def get_shop_info():
    """Remote shop information."""
    return ok(sync_service.shop_info({"shop_id": request.args.get("shop_id")}))
