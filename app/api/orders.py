"""Order endpoints.

Routing layer only: parse input, call app.services.order_service, shape the
response. No database access and no try/except here - domain errors are raised
as ApiError and rendered by the global error handlers.
"""

from flask import request

from app.api import bp
from app.services import (
    fourpx_freight_service,
    fourpx_label_service,
    order_service,
    purchase_service,
    shipping_service,
    shopify_fulfillment_service,
)
from app.utils.responses import ok


@bp.route("/orders", methods=["GET"])
def get_orders():
    """Order list (paginated, shop scoped)."""
    return ok(order_service.list_orders(request.args))


@bp.route("/orders/recent", methods=["GET"])
def get_recent_orders():
    """Most recent orders."""
    limit = request.args.get("limit", 10, type=int)
    return ok(order_service.recent_orders(limit))


@bp.route("/orders/stats", methods=["GET"])
def get_order_stats():
    """Aggregated order statistics for the selectable period."""
    days = request.args.get("days", 30, type=int)
    return ok(order_service.order_stats(days))


@bp.route("/orders/workload", methods=["GET"])
def get_order_workload():
    """今日作业看板：待发货 / 可出库 / 待采购 / 今日已出库。"""
    return ok(order_service.workload_summary())


@bp.route("/orders/<int:order_id>/purchase-lists", methods=["GET"])
def get_order_purchase_lists(order_id):
    """该订单出现在哪些采购清单里（订单详情反向跳转用）。"""
    return ok(purchase_service.purchase_lists_for_order(order_id))


@bp.route("/orders/<int:order_id>", methods=["GET"])
def get_order(order_id):
    """Order detail including its payments."""
    return ok(order_service.get_order(order_id))


@bp.route("/orders/<int:order_id>/4px-freight", methods=["GET"])
def get_order_fourpx_freight(order_id):
    """读取已同步的 4PX 实际费用（不调用 4PX）。"""
    return ok(fourpx_freight_service.get_order_freight(order_id))


@bp.route("/orders/<int:order_id>/neighbors", methods=["GET"])
def get_order_neighbors(order_id):
    """Get adjacent orders in the default newest-first list order."""
    return ok(order_service.get_order_neighbors(order_id))


@bp.route("/orders/<int:order_id>", methods=["PUT"])
def update_order(order_id):
    """Update the whitelisted fields of an order."""
    data = request.get_json(silent=True) or {}
    return ok(order_service.update_order(order_id, data), message="订单更新成功")


@bp.route("/orders/<int:order_id>/refresh-contact", methods=["POST"])
def refresh_order_contact(order_id):
    """Refresh current order items, amount, customer and shipping details."""
    return ok(
        order_service.refresh_order_contact(order_id),
        message="已从 Shopify 更新订单详情",
    )


@bp.route("/orders/<int:order_id>/shipping-label", methods=["PUT"])
def save_shipping_label(order_id):
    return ok(shipping_service.save_shipping_label(order_id, request.get_json(silent=True) or {}),
              message="物流标签单号已保存")


@bp.route("/orders/<int:order_id>/parcel-count", methods=["PUT"])
def set_expected_parcel_count(order_id):
    return ok(shipping_service.set_expected_parcel_count(order_id, request.get_json(silent=True) or {}))


@bp.route("/orders/<int:order_id>/parcels", methods=["POST"])
def add_shipping_parcel(order_id):
    return ok(shipping_service.add_shipping_parcel(order_id, request.get_json(silent=True) or {}),
              message="包裹标签已登记")


@bp.route("/orders/<int:order_id>/parcels/<int:parcel_id>", methods=["DELETE"])
def delete_shipping_parcel(order_id, parcel_id):
    return ok(shipping_service.delete_shipping_parcel(order_id, parcel_id), message="未扫码标签已移除")


@bp.route("/orders/<int:order_id>/parcels/<int:parcel_id>/4px-label", methods=["POST"])
def get_fourpx_label(order_id, parcel_id):
    return ok(fourpx_label_service.get_parcel_label(order_id, parcel_id), message="4PX 面单已获取")


@bp.route("/orders/<int:order_id>/scan-outbound", methods=["POST"])
def scan_outbound(order_id):
    return ok(shipping_service.scan_outbound(order_id, request.get_json(silent=True) or {}),
              message="包裹扫码成功")


@bp.route("/orders/<int:order_id>/shopify-writeback", methods=["POST"])
def writeback_shopify_tracking(order_id):
    return ok(shopify_fulfillment_service.writeback_tracking(order_id),
              message="Shopify 物流单号已回填；仓库仍需扫码确认出库")
