#!/usr/bin/env python3
"""回填网关真实手续费（会写数据库，执行前请先备份）。

    python scripts/backfill_gateway_fees.py --all                 # 全部补
    python scripts/backfill_gateway_fees.py --paypal --limit 50   # 先小范围试
    python scripts/backfill_gateway_fees.py --shopify-payments

数据来源：
    - PayPal：逐单拉 /orders/{id}/transactions.json，解析回执里的 paypal_fee
    - Shopify Payments：拉结算流水 balance/transactions，按订单聚合
"""

import argparse
import os
import sys

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app  # noqa: E402
from app.models import Shop  # noqa: E402
from app.services.gateway_fee_service import (  # noqa: E402
    refresh_paypal_payments,
    sync_shopify_payments_charges,
)


def main():
    parser = argparse.ArgumentParser(description="Backfill real gateway fees into payments")
    parser.add_argument("--all", action="store_true", help="PayPal + Shopify Payments 全补")
    parser.add_argument("--paypal", action="store_true", help="只补 PayPal")
    parser.add_argument("--shopify-payments", action="store_true", help="只补 Shopify Payments")
    parser.add_argument("--shop-id", type=int, default=None, help="指定店铺（默认全部店铺）")
    parser.add_argument("--limit", type=int, default=None, help="每个店铺最多处理多少单（调试用）")
    parser.add_argument("--pause", type=float, default=0.35, help="PayPal 每次请求间隔秒数")
    parser.add_argument("--only-missing", action="store_true", help="只补还没有真实费用的订单")
    args = parser.parse_args()

    do_paypal = args.all or args.paypal
    do_shopify = args.all or args.shopify_payments
    if not (do_paypal or do_shopify):
        parser.error("请指定 --all / --paypal / --shopify-payments")

    app = create_app()
    with app.app_context():
        query = Shop.query
        if args.shop_id:
            query = query.filter(Shop.id == args.shop_id)
        shops = query.order_by(Shop.id).all()
        if not shops:
            print("没有找到店铺记录。")
            return 1

        for shop in shops:
            print(f"\n=== 店铺 {shop.name} ({shop.shop_url}) ===")

            if do_shopify:
                print("[Shopify Payments] 拉取结算流水…")
                try:
                    stats = sync_shopify_payments_charges(shop, limit=args.limit)
                except Exception as exc:
                    print(f"    失败: {exc}")
                else:
                    print(
                        f"    流水 {stats['fetched']} 条"
                        f"，订单命中 {stats['matched']}"
                        f"，更新支付 {stats['updated']}"
                        f"，未匹配 {stats['unmatched']}"
                    )
                    print(f"    结算币种: {stats['currency']}，流水类型: {stats['types']}")

            if do_paypal:
                print("[PayPal] 逐单重拉交易回执（较慢，请耐心等）…")

                def progress(done, total):
                    print(f"    进度 {done}/{total}")

                stats = refresh_paypal_payments(
                    shop,
                    limit=args.limit,
                    pause=args.pause,
                    progress=progress,
                    only_missing=args.only_missing,
                )
                print(
                    f"    订单 {stats['orders']}"
                    f"，交易 {stats['transactions']}"
                    f"，更新 {stats['updated']}"
                    f"，跳过 {stats['skipped']}"
                    f"，失败 {stats['failed']}"
                )
    return 0


if __name__ == "__main__":
    sys.exit(main())
