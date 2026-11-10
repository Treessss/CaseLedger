"""补齐售后补发的 4PX 实体面单条码（运单号与面单条码不同时使用）。

用法：python scripts/backfill_after_sale_barcodes.py [shop_id]
不修改物流绑定、不出库；只按已绑定的 4PX 运单号回查面单条码并保存。
"""

import sys

from app import create_app, db
from app.models import Shop
from app.services.fourpx_barcode_sync_service import sync_after_sale_barcodes


def main():
    shop_id = int(sys.argv[1]) if len(sys.argv) > 1 else None
    app = create_app()
    with app.app_context():
        shops = [db.session.get(Shop, shop_id)] if shop_id else Shop.query.order_by(Shop.id).all()
        total_updated = 0
        for shop in shops:
            if shop is None or not shop.fourpx_app_key_encrypted:
                continue
            result = sync_after_sale_barcodes(shop.id)
            total_updated += result["updated"]
            print(f"shop {shop.id} ({shop.name}): updated={result['updated']} failed={len(result['failed'])}")
            for failure in result["failed"][:10]:
                print("  failed:", failure)
        print(f"total updated: {total_updated}")


if __name__ == "__main__":
    main()
