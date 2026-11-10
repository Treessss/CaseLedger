"""Procurement list endpoints; aggregation lives in the service layer."""

from io import BytesIO

from flask import request, send_file

from app.api import bp
from app.services import purchase_edit_service, purchase_excel_service, purchase_pdf_service, purchase_service
from app.utils.responses import created, ok


@bp.route("/purchase-lists", methods=["GET"])
def list_purchase_lists():
    return ok(purchase_service.list_purchase_lists(request.args))


@bp.route("/purchase-candidates", methods=["GET"])
def list_purchase_candidates():
    return ok(purchase_service.list_purchase_candidates(request.args))


@bp.route("/purchase-lists", methods=["POST"])
def create_purchase_list():
    return created(purchase_service.create_purchase_list(request.get_json(silent=True) or {}))


@bp.route("/purchase-lists/after-sale", methods=["POST"])
def create_after_sale_purchase_list():
    return created(purchase_service.create_after_sale_purchase_list(request.get_json(silent=True) or {}))


@bp.route('/purchase-lists/manual', methods=['POST'])
def create_manual_purchase_list():
    return created(purchase_edit_service.create_manual(request.get_json(silent=True) or {}))


@bp.route('/purchase-lists/catalog/products', methods=['GET'])
def purchase_catalog_products():
    return ok(purchase_edit_service.catalog_products(request.args))


@bp.route('/purchase-lists/catalog/variants', methods=['GET'])
def purchase_catalog_variants():
    return ok(purchase_edit_service.catalog_variants(request.args))


@bp.route('/purchase-lists/<int:purchase_id>/items', methods=['PATCH'])
def edit_purchase_list_items(purchase_id):
    return ok(purchase_edit_service.update_items(purchase_id, request.get_json(silent=True) or {}))


@bp.route("/purchase-lists/<int:purchase_id>", methods=["GET"])
def get_purchase_list(purchase_id):
    return ok(purchase_service.get_purchase_list(purchase_id))


@bp.route("/purchase-lists/<int:purchase_id>", methods=["DELETE"])
def delete_purchase_list(purchase_id):
    return ok(purchase_service.delete_purchase_list(purchase_id))


@bp.route("/purchase-lists/<int:purchase_id>/confirm", methods=["POST"])
def confirm_purchase_list(purchase_id):
    return ok(purchase_service.confirm_purchase_list(purchase_id))


@bp.route("/purchase-lists/<int:purchase_id>/receive", methods=["POST"])
def receive_purchase_list(purchase_id):
    return ok(purchase_service.receive_purchase_list(purchase_id, request.get_json(silent=True) or {}))


@bp.route("/purchase-lists/<int:purchase_id>/replenish", methods=["POST"])
def create_purchase_replenishment(purchase_id):
    return created(purchase_service.create_replenishment(purchase_id))


@bp.route("/purchase-lists/<int:purchase_id>/pdf", methods=["GET"])
def export_purchase_list_pdf(purchase_id):
    content, filename = purchase_pdf_service.build_purchase_pdf(
        purchase_id, product_index=request.args.get("product_index")
    )
    return send_file(BytesIO(content), mimetype="application/pdf", as_attachment=True, download_name=filename)


@bp.route("/purchase-lists/<int:purchase_id>/excel", methods=["GET"])
def export_purchase_list_excel(purchase_id):
    content, filename = purchase_excel_service.build_purchase_excel(
        purchase_id, product_index=request.args.get('product_index')
    )
    return send_file(BytesIO(content), mimetype=purchase_excel_service.MIME, as_attachment=True, download_name=filename)
