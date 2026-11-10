#!/usr/bin/env python3
"""校准订单级发货状态（修「已发货却显示未发货」）。

背景：订单增量同步按 **创建时间** 抓最近几天的订单，订单一发货就可能滚出
窗口，本地 ``fulfillment_status`` 会永远停在 ``unfulfilled``，
「已发货」快捷筛选因此漏单、订单号看起来不连续。

本脚本按 **updated_at** 反向捞取状态变化的订单（一次 250 条，很快），
把本地状态改成 Shopify 的订单级真实状态。

用法：
    python scripts/backfill_fulfillment_status.py --dry-run      # 只统计会改多少单
    python scripts/backfill_fulfillment_status.py                # 默认回看 30 天
    python scripts/backfill_fulfillment_status.py --days 365     # 回看一年（首次全量修）
    python scripts/backfill_fulfillment_status.py --shop-id 1
"""

import argparse
import json
import os
import sys

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app  # noqa: E402
from app.models import Order, Shop  # noqa: E402
from app.services.shopify_graphql import fetch_fulfillment_statuses  # noqa: E402


def preview(shop, days):
    """不改库，只统计有多少单的状态与 Shopify 不一致。"""
    from datetime import datetime, timedelta

    since = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    statuses = fetch_fulfillment_statuses(shop, since_iso=since)

    local = {
        order.shopify_order_id: order
        for order in Order.query.filter(
            Order.shop_id == shop.id, Order.shopify_order_id.in_(list(statuses.keys()) or [""])
        ).all()
    }
    mismatched = [
        (oid, local[oid].order_number, local[oid].fulfillment_status, remote)
        for oid, remote in statuses.items()
        if oid in local and local[oid].fulfillment_status != remote
    ]
    return statuses, mismatched


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30, help="按 updated_at 回看多少天")
    parser.add_argument("--shop-id", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true", help="只统计，不写库")
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        query = Shop.query.order_by(Shop.id)
        if args.shop_id:
            query = query.filter(Shop.id == args.shop_id)

        total_mismatch = 0
        for shop in query.all():
            statuses, mismatched = preview(shop, args.days)
            total_mismatch += len(mismatched)
            print("=" * 74)
            print(f"店铺 {shop.name}：取到 {len(statuses)} 单状态，本地不一致 {len(mismatched)} 单")
            for order_id, number, old, new in mismatched[:10]:
                print(f"   #{number}: {old} -> {new}")
            if len(mismatched) > 10:
                print(f"   ...还有 {len(mismatched) - 10} 单")

        if args.dry_run:
            print(f"\n[dry-run] 共需修正 {total_mismatch} 单，未写库")
            return 0

        from app.services.sync_service import reconcile_fulfillment_status

        result = reconcile_fulfillment_status(
            {"days_back": args.days, "shop_id": args.shop_id}
        )
        print("\n执行结果（含每店明细，失败也会显示出来）:")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
