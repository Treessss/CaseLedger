"""Shopify webhook transport: authenticate, deduplicate, then delegate domain work."""

import logging
from datetime import datetime

from flask import Blueprint, current_app, jsonify, request
from sqlalchemy.exc import IntegrityError

from app import db
from app.models import Shop, WebhookDelivery
from app.services.shopify_webhook_service import process_webhook
from app.utils.helpers import validate_shopify_webhook_signature

webhook_bp = Blueprint("webhooks", __name__)
logger = logging.getLogger(__name__)

TOPICS = {
    "orders/create": "/shopify/orders/create",
    "orders/update": "/shopify/orders/update",
    "orders/paid": "/shopify/orders/paid",
    "orders/cancelled": "/shopify/orders/cancelled",
    "products/create": "/shopify/products/create",
    "products/update": "/shopify/products/update",
    "products/delete": "/shopify/products/delete",
}


def _handle_webhook(topic):
    domain = request.headers.get("X-Shopify-Shop-Domain")
    shop = Shop.query.filter_by(shop_url=Shop.normalize_shop_url(domain)).first() if domain else None
    if shop is None:
        return jsonify({"error": "Unknown shop"}), 401

    signature = request.headers.get("X-Shopify-Hmac-Sha256")
    secret = shop.webhook_secret or current_app.config.get("SHOPIFY_WEBHOOK_SECRET")
    if not signature or not secret or not validate_shopify_webhook_signature(
        request.get_data(), signature, secret
    ):
        return jsonify({"error": "Invalid signature"}), 401

    header_topic = request.headers.get("X-Shopify-Topic")
    if header_topic and header_topic != topic:
        return jsonify({"error": "Topic mismatch"}), 400
    delivery_id = request.headers.get("X-Shopify-Webhook-Id")
    if not delivery_id or len(delivery_id) > 128:
        return jsonify({"error": "Missing or invalid delivery id"}), 400
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or payload.get("id") is None:
        return jsonify({"error": "Invalid payload"}), 400

    try:
        # Insert before processing. The unique constraint serializes concurrent retries;
        # a failed handler rolls this row back so Shopify can retry the delivery.
        db.session.add(WebhookDelivery(shop_id=shop.id, delivery_id=delivery_id, topic=topic))
        db.session.flush()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"status": "duplicate"}), 200

    try:
        result = process_webhook(shop, topic, payload)
        db.session.commit()
        return jsonify({"status": "success", **result}), 200
    except Exception:
        db.session.rollback()
        logger.exception("Webhook %s failed for shop %s", topic, shop.id)
        return jsonify({"error": "Internal server error"}), 500


for _topic, _rule in TOPICS.items():
    webhook_bp.add_url_rule(
        _rule,
        endpoint=_topic.replace("/", "_"),
        view_func=lambda topic=_topic: _handle_webhook(topic),
        methods=["POST"],
    )


@webhook_bp.route("/health", methods=["GET"])
def webhook_health():
    return jsonify(
        {"status": "healthy", "timestamp": datetime.utcnow().isoformat(), "service": "shopify-webhooks"}
    ), 200
