"""After-sales endpoints; state transitions live in the service layer."""

from flask import request

from app.api import bp
from app.services import after_sale_service, cf_tracking_service
from app.utils.responses import created, ok


@bp.route("/after-sales", methods=["GET"])
def list_after_sales():
    return ok(after_sale_service.list_after_sales(request.args))


@bp.route("/after-sales", methods=["POST"])
def create_after_sale():
    return created(after_sale_service.create_after_sale(request.get_json(silent=True) or {}))


@bp.route("/after-sales/<int:after_sale_id>", methods=["GET"])
def get_after_sale(after_sale_id):
    return ok(after_sale_service.get_after_sale(after_sale_id))


@bp.route("/after-sales/<int:after_sale_id>", methods=["DELETE"])
def delete_after_sale(after_sale_id):
    return ok(after_sale_service.delete_after_sale(after_sale_id), message="售后单已隐藏，历史记录保留")


@bp.route("/after-sales/4px/shopify-preview", methods=["POST"])
def preview_cf_shopify():
    return ok(cf_tracking_service.preview(request.get_json(silent=True) or {}))


@bp.route("/after-sales/4px/shopify-writeback", methods=["POST"])
def writeback_cf_shopify():
    return ok(cf_tracking_service.writeback(request.get_json(silent=True) or {}))


@bp.route("/after-sales/original-order", methods=["GET"])
def lookup_after_sale_original_order():
    return ok(after_sale_service.lookup_original_order(request.args))


@bp.route("/after-sales/original-order/<int:order_id>", methods=["GET"])
def get_after_sale_original_order(order_id):
    return ok(after_sale_service.get_original_order_source(order_id))


@bp.route("/after-sales/<int:after_sale_id>/note", methods=["PUT"])
def update_after_sale_note(after_sale_id):
    return ok(after_sale_service.update_note(after_sale_id, request.get_json(silent=True) or {}))


@bp.route("/after-sales/catalog", methods=["GET"])
def search_after_sale_catalog():
    return ok(after_sale_service.search_catalog(request.args))


@bp.route("/after-sales/catalog/products", methods=["GET"])
def search_after_sale_catalog_products():
    return ok(after_sale_service.search_catalog_products(request.args))


@bp.route("/after-sales/<int:after_sale_id>/costs", methods=["POST"])
def add_after_sale_cost(after_sale_id):
    return created(after_sale_service.add_cost(after_sale_id, request.get_json(silent=True) or {}))


@bp.route("/after-sales/4px/preview", methods=["POST"])
def preview_after_sale_fourpx():
    return ok(after_sale_service.preview_fourpx(request.get_json(silent=True) or {}))


@bp.route("/after-sales/<int:after_sale_id>/4px/bind", methods=["POST"])
def bind_after_sale_fourpx(after_sale_id):
    return ok(after_sale_service.bind_fourpx(after_sale_id, request.get_json(silent=True) or {}))


@bp.route("/after-sales/<int:after_sale_id>/tracking", methods=["PUT"])
def bind_after_sale_tracking(after_sale_id):
    return ok(after_sale_service.bind_manual_tracking(after_sale_id, request.get_json(silent=True) or {}),
              message="售后物流已绑定；未回填 Shopify，未执行出库")


@bp.route("/after-sales/<int:after_sale_id>/label", methods=["POST"])
def get_after_sale_label(after_sale_id):
    return ok(after_sale_service.get_label(after_sale_id))
