"""Apply authenticated Shopify webhook payloads without committing the transaction."""

from datetime import datetime
from decimal import Decimal

from flask import current_app

from app import db
from app.models import Product
from app.services.shopify_service import ShopifyService
from app.utils.shopify_resources import WebhookResource


def _process_order(shop, topic, payload):
    service = ShopifyService()
    service.app = current_app
    service.shop_id = shop.id
    order = service._process_order(WebhookResource(payload))
    if topic == "orders/paid":
        order.financial_status = payload.get("financial_status") or "paid"
    elif topic == "orders/cancelled":
        order.financial_status = "cancelled"
        order.fulfillment_status = "cancelled"
        order.cancelled_at = order.cancelled_at or datetime.utcnow()
    order.updated_at = datetime.utcnow()
    db.session.flush()
    return {"order_id": order.id}


def _process_product(shop, topic, payload):
    product_id = str(payload["id"])
    if topic == "products/delete":
        count = Product.query.filter_by(shop_id=shop.id, shopify_product_id=product_id).delete()
        return {"deleted_variants": count}

    variants = payload.get("variants") or []
    from app.services.product_naming_service import record_catalog_names
    record_catalog_names(shop.id, {product_id: payload.get('title') or 'Untitled'})
    for variant in variants:
        variant_id = variant.get("id")
        if variant_id is None:
            continue
        item = Product.query.filter_by(
            shop_id=shop.id, shopify_product_id=product_id, shopify_variant_id=str(variant_id)
        ).first()
        if item is None:
            item = Product(shop_id=shop.id, shopify_product_id=product_id, shopify_variant_id=str(variant_id))
            db.session.add(item)
        item.title = payload.get("title") or "Untitled"
        item.variant_title = variant.get("title")
        item.sku = variant.get("sku")
        item.price = Decimal(str(variant.get("price") or 0))
        item.vendor = payload.get("vendor")
        item.inventory_quantity = variant.get("inventory_quantity") or 0
        item.product_type = payload.get("product_type")
        item.status = payload.get("status") or "active"
        item.updated_at = datetime.utcnow()
    return {"product_id": product_id, "variants": len(variants)}


def process_webhook(shop, topic, payload):
    if topic.startswith("orders/"):
        return _process_order(shop, topic, payload)
    return _process_product(shop, topic, payload)
