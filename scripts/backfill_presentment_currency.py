#!/usr/bin/env python3
"""回填订单的买家结算币种（presentment_currency），并修正支付记录的币种口径。

背景：订单表记的是店铺币种（USD），买家实际可能用 AUD / CAD / GBP 付款。
老数据的 payments.currency 被写成了订单币种，金额却是扣款币种的数字，
导致手续费率与利润率按错币种计算。

本脚本：
    1. 翻订单列表（250/页，几十次请求即可覆盖全量）取 presentment_currency
    2. 回填到 orders.presentment_currency
    3. 单笔支付的订单，把 payments.currency 修正为买家币种，
       并按订单总额比例重算 amount_in_order_currency

用法：
    python scripts/backfill_presentment_currency.py            # 全量
    python scripts/backfill_presentment_currency.py --limit 500
"""

import argparse
import os
import sys

import requests
from dotenv import load_dotenv
from decimal import Decimal

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app, db  # noqa: E402
from app.models import Order, Payment, Shop  # noqa: E402
from app.services.shopify_auth import API_VERSION, get_access_token
from app.models.shop import Shop as ShopModel  # noqa: E402
from app.services.gateway_fee_service import _money  # noqa: E402

PAGE_LIMIT = 250


def fetch_orders(shop, limit=None):
    """翻页取订单的币种字段。"""
    shop_url = ShopModel.normalize_shop_url(shop.shop_url)
    url = f"https://{shop_url}/admin/api/{API_VERSION}/orders.json"
    headers = {"X-Shopify-Access-Token": get_access_token(shop)}
    params = {
        "status": "any",
        "limit": PAGE_LIMIT,
    }

    fetched = 0
    while url:
        for attempt in range(4):
            try:
                response = requests.get(url, headers=headers, params=params, timeout=60)
                break
            except requests.RequestException:
                if attempt == 3:
                    raise
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


def main():
    parser = argparse.ArgumentParser(description="Backfill order presentment currency")
    parser.add_argument("--limit", type=int, default=None, help="最多处理多少个订单（调试用）")
    parser.add_argument("--shop-id", type=int, default=None, help="指定店铺")
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        query = Shop.query
        if args.shop_id:
            query = query.filter(Shop.id == args.shop_id)

        for shop in query.order_by(Shop.id).all():
            print(f"=== 店铺 {shop.name} ===")
            stats = {"seen": 0, "updated": 0, "missing": 0, "mismatch": 0, "payments_fixed": 0}

            for page in fetch_orders(shop, args.limit):
                for remote in page:
                    stats["seen"] += 1
                    order = Order.query.filter_by(shopify_order_id=str(remote["id"])).first()
                    if order is None:
                        stats["missing"] += 1
                        continue

                    presentment = remote.get("presentment_currency") or remote.get("currency")
                    if order.presentment_currency != presentment:
                        order.presentment_currency = presentment
                        stats["updated"] += 1

                    payments = list(order.payments)
                    if len(payments) != 1:
                        continue
                    payment = payments[0]
                    if not presentment or payment.currency == presentment or payment.amount is None:
                        continue

                    stats["mismatch"] += 1
                    payment.currency = presentment
                    # 单笔支付：订单总额本身就是这笔支付的订单币种金额
                    payment.amount_in_order_currency = _money(Decimal(str(order.total_price)))
                    stats["payments_fixed"] += 1
                db.session.commit()

            print(
                f"    订单 {stats['seen']}"
                f"，更新币种 {stats['updated']}"
                f"，库内缺失 {stats['missing']}"
                f"，支付币种不一致 {stats['mismatch']}"
                f"，已修正 {stats['payments_fixed']}"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
