#!/usr/bin/env python3
"""回填订单的客户信息、收货地址与商品明细（会写数据库）。

Shopify 的订单列表接口一次就返回 customer / shipping_address / line_items，
所以全量回填只需要几十次请求（每页 250 单），比逐单拉取快得多。

用法：
    python scripts/backfill_order_details.py                 # 全量
    python scripts/backfill_order_details.py --limit 500     # 先试一部分
    python scripts/backfill_order_details.py --with-cost     # 同时刷新商品成本（较慢，会逐单同步商品）
"""

import argparse
import os
import sys

import requests
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app, db  # noqa: E402
from app.models import Order, OrderItem, Shop  # noqa: E402
from app.services.shopify_auth import API_VERSION, get_access_token
from app.models.shop import Shop as ShopModel  # noqa: E402
from app.utils.shopify_resources import extract_customer_info, extract_line_items  # noqa: E402

PAGE_LIMIT = 250


def fetch_orders(shop, limit=None):
    """按 Link 头翻页拉订单（含客户、地址、商品明细）。"""
    shop_url = ShopModel.normalize_shop_url(shop.shop_url)
    url = f"https://{shop_url}/admin/api/{API_VERSION}/orders.json"
    headers = {"X-Shopify-Access-Token": get_access_token(shop)}
    params = {"status": "any", "limit": PAGE_LIMIT}

    fetched = 0
    while url:
        response = None
        for attempt in range(4):
            try:
                response = requests.get(url, headers=headers, params=params, timeout=90)
            except requests.RequestException:
                if attempt == 3:
                    raise
                continue
            if response.status_code in (429, 500, 502, 503, 504):
                if attempt == 3:
                    raise RuntimeError(f"HTTP {response.status_code}")
                continue
            break
        if response.status_code != 200:
            raise RuntimeError(f"HTTP {response.status_code}: {response.text[:200]}")

        page = response.json().get("orders", [])
        if not page:
            return
        yield page
        fetched += len(page)
        if limit and fetched >= limit:
            return

        next_url = None
        for part in response.headers.get("Link", "").split(","):
            if 'rel="next"' in part:
                next_url = part.split(";")[0].strip().strip("<>")
                break
        if not next_url:
            return
        url, _, query = next_url.partition("?")
        params = dict(item.split("=", 1) for item in query.split("&") if "=" in item)


def apply_details(order, remote):
    """把一条远程订单的客户信息与商品明细写进本地订单。"""
    info = extract_customer_info(remote)
    if info["email"]:
        order.customer_email = info["email"]
    if info["name"]:
        order.customer_name = info["name"]
    if info["phone"]:
        order.customer_phone = info["phone"]
    if info["shipping_address"]:
        order.shipping_address = info["shipping_address"]

    rows = extract_line_items(remote)
    existing = {item.shopify_line_item_id: item for item in order.items}
    created = 0
    for row in rows:
        item = existing.get(row["shopify_line_item_id"])
        if item is None:
            item = OrderItem(
                shop_id=order.shop_id,
                shopify_line_item_id=row["shopify_line_item_id"],
            )
            order.items.append(item)
            created += 1
        item.shopify_product_id = row["shopify_product_id"]
        item.shopify_variant_id = row["shopify_variant_id"]
        item.title = row["title"]
        item.variant_title = row["variant_title"]
        item.sku = row["sku"]
        item.vendor = row["vendor"]
        item.quantity = row["quantity"]
        item.price = row["price"]
        item.total_discount = row["total_discount"]
    return len(rows), created


def main():
    parser = argparse.ArgumentParser(description="Backfill order customer/address/line items")
    parser.add_argument("--limit", type=int, default=None, help="最多处理多少单（调试用）")
    parser.add_argument("--shop-id", type=int, default=None, help="指定店铺")
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        query = Shop.query
        if args.shop_id:
            query = query.filter(Shop.id == args.shop_id)

        for shop in query.order_by(Shop.id).all():
            print(f"=== 店铺 {shop.name} ===")
            stats = {"seen": 0, "updated": 0, "missing": 0, "items": 0, "items_created": 0}

            for page in fetch_orders(shop, args.limit):
                for remote in page:
                    stats["seen"] += 1
                    order = Order.query.filter_by(shopify_order_id=str(remote["id"])).first()
                    if order is None:
                        stats["missing"] += 1
                        continue
                    rows, created = apply_details(order, remote)
                    stats["items"] += rows
                    stats["items_created"] += created
                    stats["updated"] += 1
                db.session.commit()
                print(
                    f"    已处理 {stats['seen']} 单"
                    f"，更新 {stats['updated']}"
                    f"，商品明细 {stats['items']} 行"
                    f"，库内缺失 {stats['missing']}"
                )

            print(
                f"完成：扫描 {stats['seen']} 单，更新 {stats['updated']} 单，"
                f"商品明细 {stats['items']} 行（新建 {stats['items_created']}），"
                f"库内缺失 {stats['missing']} 单"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
