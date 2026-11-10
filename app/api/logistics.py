"""Thin logistics routes: authorization registry and services own all business rules."""
from flask import request

from app.api import bp
from app.services import logistics_service, logistics_outbound_service
from app.utils.responses import ok


@bp.route('/logistics', methods=['GET'])
def logistics_list():
    return ok(logistics_service.list_records(request.args))


@bp.route('/logistics/<int:binding_id>/handling', methods=['PATCH'])
def logistics_handling(binding_id):
    return ok(logistics_service.set_handling(binding_id, request.get_json(silent=True) or {}))


@bp.route('/logistics/refresh', methods=['POST'])
def logistics_refresh():
    return ok(logistics_service.enqueue(request.get_json(silent=True) or {}))


@bp.route('/logistics/batch-outbound', methods=['POST'])
def logistics_batch_outbound():
    return ok(logistics_outbound_service.batch_outbound(request.get_json(silent=True) or {}))


@bp.route('/logistics/outbound-audits', methods=['GET'])
def logistics_outbound_audits():
    return ok(logistics_service.list_audits(request.args))
