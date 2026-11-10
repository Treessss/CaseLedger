"""Read-only Shopify image retrieval with persistent, shop-scoped catalog caching."""

from sqlalchemy import func, or_

from app import db
from app.models import Product, Shop
from app.services.shopify_graphql import _execute, _featured_image, line_item_image
from app.services.shipping_queue_service import _shop_id

PRODUCT_MAIN_IMAGES_QUERY = """
query ProductMainImages($ids: [ID!]!) {
  nodes(ids: $ids) {
    ... on Product {
      id
      featuredMedia { preview { image { url } } }
    }
  }
}
"""

VARIANT_IMAGES_QUERY = """
query VariantImages($ids: [ID!]!) {
  nodes(ids: $ids) {
    ... on ProductVariant {
      id
      image { url }
      media(first: 1) { nodes { preview { image { url } } } }
      product { featuredMedia { preview { image { url } } } }
    }
  }
}
"""


def refresh_variant_images(shop_id, product_ids=None):
    """Repair cached main-image fallbacks with current exact variant images."""
    shop_id = _shop_id(shop_id)
    shop = db.session.get(Shop, shop_id)
    query = Product.query.filter_by(shop_id=shop_id)
    if product_ids is not None:
        query = query.filter(Product.shopify_product_id.in_(product_ids))
    ids = [row[0] for row in query.with_entities(Product.shopify_variant_id).all()]
    updated = 0
    for start in range(0, len(ids), 100):
        result = _execute(shop, VARIANT_IMAGES_QUERY, {
            "ids": [f"gid://shopify/ProductVariant/{value}" for value in ids[start:start + 100]],
        })
        for node in result.get("nodes") or []:
            image = line_item_image(node)
            if not node or not image:
                continue
            updated += Product.query.filter_by(shop_id=shop_id,
                shopify_variant_id=str(node["id"]).rsplit("/", 1)[-1]).update(
                    {"image_url": image}, synchronize_session=False)
        db.session.commit()
    return {"variants_checked": len(ids), "variants_updated": updated}


def refresh_missing_images(shop_id):
    shop_id = _shop_id(shop_id)
    shop = db.session.get(Shop, shop_id)
    ids = [row[0] for row in db.session.query(Product.shopify_product_id).filter(
        Product.shop_id == shop_id, or_(Product.image_url.is_(None), Product.image_url == ""),
    ).distinct().all()]
    updated = 0
    unavailable = 0
    for start in range(0, len(ids), 50):
        batch = ids[start:start + 50]
        result = _execute(shop, PRODUCT_MAIN_IMAGES_QUERY, {"ids": [f"gid://shopify/Product/{value}" for value in batch]})
        found = {}
        for node in result.get("nodes") or []:
            image = _featured_image(node or {})
            if node and image and image.get("url"):
                found[str(node["id"]).rsplit("/", 1)[-1]] = image["url"]
        for product_id, image_url in found.items():
            updated += Product.query.filter(
                Product.shop_id == shop_id, Product.shopify_product_id == product_id,
                or_(Product.image_url.is_(None), Product.image_url == ""),
            ).update({"image_url": image_url}, synchronize_session=False)
        unavailable += len(batch) - len(found)
        db.session.commit()
    return {"products_checked": len(ids), "variants_updated": updated, "products_without_image": unavailable}
