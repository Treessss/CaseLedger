#!/usr/bin/env python3
"""把存量订单的收货地址 / 客户信息从 Shopify 补齐。

历史订单是走 REST 同步的，地址只存了国家+省份。配置好每个店铺的
Partner 应用凭据后，用这个脚本按 Admin GraphQL 回填。

用法：
    python scripts/backfill_shipping_addresses.py --dry-run          # 先看有多少要补
    python scripts/backfill_shipping_addresses.py --limit 50         # 小批量试跑
    python scripts/backfill_shipping_addresses.py --shop-id 1        # 只补某个店铺
    python scripts/backfill_shipping_addresses.py                    # 全量（较慢，注意限流）

Shopify 的 GraphQL 有调用成本配额，脚本默认每单停 0.5 秒；被限流会自动退避重试。
"""

import argparse
import os
import sys
import time

from dotenv import load_dotenv
from sqlalchemy import or_

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app, db  # noqa: E402
from app.models import Order, Shop  # noqa: E402
from app.services.shopify_graphql import ShopifyGraphQLError, fetch_order_contact  # noqa: E402


def _needs_backfill(shop_id):
    """地址里没有 address1 的订单都算缺详情。"""
    query = Order.query.filter(Order.shopify_order_id.isnot(None))
    if shop_id:
        query = query.filter(Order.shop_id == shop_id)
    return query.filter(
        or_(
            Order.shipping_address.is_(None),
            Order.customer_phone.is_(None),
        )
    ).order_by(Order.id.desc())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--shop-id", type=int, default=None)
    parser.add_argument("--limit", type=int, default=None, help="最多处理多少单")
    parser.add_argument("--pause", type=float, default=0.5, help="每单之间的间隔秒数")
    parser.add_argument("--dry-run", action="store_true", help="只统计，不写库")
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        pending = _needs_backfill(args.shop_id)
        total = pending.count()
        print(f"待回填订单：{total} 单" + (f"（最多处理 {args.limit}）" if args.limit else ""))
        if args.dry_run or total == 0:
            return 0

        shops = {shop.id: shop for shop in Shop.query.all()}
        orders = pending.limit(args.limit).all() if args.limit else pending.all()

        stats = {"updated": 0, "skipped": 0, "failed": 0}
        for index, order in enumerate(orders, start=1):
            shop = shops.get(order.shop_id)
            if shop is None:
                stats["skipped"] += 1
                continue
            if not shop.has_oauth_credentials():
                stats["skipped"] += 1
                continue

            try:
                contact = fetch_order_contact(shop, order.shopify_order_id)
            except ShopifyGraphQLError as exc:
                stats["failed"] += 1
                print(f"  [{index}/{len(orders)}] 订单 {order.id} 失败：{exc}")
                # 限流就多等一会儿
                if "限流" in str(exc):
                    time.sleep(5)
                continue

            if contact.get("email"):
                order.customer_email = contact["email"]
            if contact.get("name"):
                order.customer_name = contact["name"]
            if contact.get("phone"):
                order.customer_phone = contact["phone"]
            if contact.get("shipping_address"):
                order.shipping_address = contact["shipping_address"]

            stats["updated"] += 1
            if index % 25 == 0:
                db.session.commit()
                print(f"  [{index}/{len(orders)}] 已提交，成功 {stats['updated']}")

            time.sleep(args.pause)

        db.session.commit()
        print(
            f"完成：更新 {stats['updated']}，跳过 {stats['skipped']}，失败 {stats['failed']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
