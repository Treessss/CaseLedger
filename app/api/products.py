from flask import request

from app.api import bp
from app.services import product_translation_service
from app.utils.responses import ok


@bp.route("/products", methods=["GET"])
def list_managed_products():
    return ok(product_translation_service.list_products(request.args))


@bp.route("/products/translations", methods=["PUT"])
def save_product_translations():
    return ok(product_translation_service.save_names(request.get_json(silent=True) or {}), message="中文名称已保存")
