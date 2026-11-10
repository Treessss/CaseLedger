"""Inventory summary and audited adjustments."""

from flask import request

from app.api import bp
from app.services import inventory_service
from app.utils.responses import created, ok


@bp.route("/inventory", methods=["GET"])
def list_inventory():
    return ok(inventory_service.list_inventory(request.args))


@bp.route("/inventory/adjustments", methods=["GET"])
def list_inventory_adjustments():
    return ok(inventory_service.list_adjustments(request.args))


@bp.route("/inventory/movements", methods=["GET"])
def list_inventory_movements():
    """每天的出入库流水（入库来自采购到货，出库来自扫码出库）。"""
    return ok(inventory_service.list_movements(request.args))


@bp.route("/inventory/adjustments", methods=["POST"])
def adjust_inventory():
    return created(inventory_service.adjust_inventory(request.get_json(silent=True) or {}))


@bp.route("/inventory/receiving/products", methods=["GET"])
def receiving_products():
    return ok(inventory_service.list_receiving_products(request.args))


@bp.route("/inventory/receiving", methods=["POST"])
def batch_receive_inventory():
    return created(inventory_service.batch_receive_inventory(request.get_json(silent=True) or {}))


@bp.route("/inventory/reservations", methods=["POST"])
def reserve_inventory():
    return created(inventory_service.reserve_for_order(request.get_json(silent=True) or {}))
