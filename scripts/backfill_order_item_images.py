"""Fill missing order-item images from each shop's Shopify product catalog.

Usage: venv/bin/python scripts/backfill_order_item_images.py --shop-id 1
Only image_url is changed; order amounts, stock, and purchase records are untouched.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import create_app, db  # noqa: E402
from app.models import Order, OrderItem, Shop  # noqa: E402
from app.services.shopify_graphql import fetch_variant_images  # noqa: E402


def backfill(shop_id, limit=None, batch_size=20):
    shop = db.session.get(Shop, shop_id)
    if shop is None:
        raise ValueError(f"店铺 {shop_id} 不存在")

    items = OrderItem.query.join(Order, OrderItem.order_id == Order.id).filter(
        Order.shop_id == shop_id,
        OrderItem.shopify_product_id.isnot(None),
        OrderItem.shopify_variant_id.isnot(None),
    ).order_by(OrderItem.id).all()
    by_variant = {}
    for item in items:
        by_variant.setdefault(item.shopify_variant_id, []).append(item)

    # A known image is safe to reuse only for the same Shopify variant in the same shop.
    for variant_items in by_variant.values():
        known = next((item.image_url for item in variant_items if item.image_url), None)
        if known:
            for item in variant_items:
                if not item.image_url:
                    item.image_url = known
    db.session.commit()

    missing_variants = [variant_id for variant_id, variant_items in by_variant.items()
                        if any(not item.image_url for item in variant_items)]
    batches = [missing_variants[index:index + batch_size]
               for index in range(0, len(missing_variants), batch_size)]
    if limit is not None:
        batches = batches[:limit]

    updated = 0
    failed = 0
    for index, variant_ids in enumerate(batches, 1):
        try:
            images = fetch_variant_images(shop, variant_ids)
            changed = 0
            for variant_id in variant_ids:
                url = images.get(variant_id)
                if url and len(url) <= 2048:
                    for item in by_variant[variant_id]:
                        if not item.image_url:
                            item.image_url = url
                            changed += 1
            db.session.commit()
            updated += changed
            print(f"{index}/{len(batches)} variant batch: {changed} images", flush=True)
        except Exception as exc:  # Continue other products; failed ones can be retried.
            db.session.rollback()
            failed += 1
            print(f"{index}/{len(batches)} variant batch: {exc}", flush=True)
    return {"batches": len(batches), "updated_items": updated, "failed_batches": failed}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shop-id", type=int, required=True)
    parser.add_argument("--limit", type=int, default=None, help="Process only the first N variant batches")
    parser.add_argument("--batch-size", type=int, default=20, choices=(20, 50, 100), help="Variants per Shopify request")
    args = parser.parse_args()
    app = create_app()
    with app.app_context():
        print(backfill(args.shop_id, args.limit, args.batch_size))
