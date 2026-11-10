"""Webhook authentication, shop isolation and delivery idempotency."""

import base64
import hashlib
import hmac
import json

from app import db
from app.models import Order, Product, Shop, WebhookDelivery
from app.services.shopify_service import ShopifyService
from app.services.shopify_webhook_service import WebhookResource, process_webhook


def _send(client, *, delivery_id="delivery-1", domain="shop-a.myshopify.com", secret="shop-secret"):
    payload = json.dumps(
        {"id": 500, "title": "Case", "variants": [{"id": 501, "title": "Blue", "price": "12.50"}]}
    ).encode()
    signature = base64.b64encode(hmac.new(secret.encode(), payload, hashlib.sha256).digest()).decode()
    return client.post(
        "/webhooks/shopify/products/create",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "X-Shopify-Shop-Domain": domain,
            "X-Shopify-Hmac-Sha256": signature,
            "X-Shopify-Webhook-Id": delivery_id,
            "X-Shopify-Topic": "products/create",
        },
    )


def test_product_delivery_is_applied_once(client, app):
    with app.app_context():
        shop = Shop.query.filter_by(shop_url="shop-a.myshopify.com").one()
        shop.webhook_secret = "shop-secret"
        db.session.commit()

    first = _send(client)
    second = _send(client)
    assert first.status_code == 200, first.get_data(as_text=True)
    assert second.status_code == 200
    assert second.get_json()["status"] == "duplicate"
    with app.app_context():
        assert Product.query.filter_by(shopify_product_id="500").count() == 1
        assert WebhookDelivery.query.count() == 1


def test_unknown_shop_is_rejected(client, app):
    assert _send(client, domain="unknown.myshopify.com").status_code == 401
    with app.app_context():
        assert WebhookDelivery.query.count() == 0


def test_webhook_resource_exposes_nested_objects_and_original_json():
    row = {"id": 1, "line_items": [{"id": 2, "quantity": 3}], "customer": {"first_name": "A"}}
    resource = WebhookResource(row)
    assert resource.id == 1
    assert resource.line_items[0].quantity == 3
    assert resource.customer.first_name == "A"
    assert resource.attributes == row


def test_order_webhook_updates_order_without_duplicate(client, app, monkeypatch):
    monkeypatch.setattr(ShopifyService, "_enrich_order_details", lambda *args: None)
    payload = {
        "id": 777, "order_number": 777, "total_price": "15.00", "subtotal_price": "15.00",
        "total_tax": "0", "currency": "CNY", "financial_status": "paid",
        "fulfillment_status": "unfulfilled", "created_at": "2026-01-01T10:00:00Z",
        "updated_at": "2026-01-01T10:00:00Z", "line_items": [], "transactions": [],
        "note": "请尽快发货", "fulfillments": [],
    }
    with app.app_context():
        shop = Shop.query.filter_by(shop_url="shop-a.myshopify.com").one()
        result = process_webhook(shop, "orders/create", payload)
        db.session.commit()
        assert result["order_id"]
        order = Order.query.filter_by(shop_id=shop.id, shopify_order_id="777").one()
        assert order.note == "请尽快发货"
        payload["fulfillment_status"] = "fulfilled"
        process_webhook(shop, "orders/update", payload)
        db.session.commit()
        assert Order.query.filter_by(shop_id=shop.id, shopify_order_id="777").count() == 1
        assert order.fulfillment_status == "fulfilled"
