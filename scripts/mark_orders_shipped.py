#!/usr/bin/env python3
"""把某个订单号之前的订单批量标记为「已出库」。

用途：清理历史欠账。升级多店铺/出库流程之前的老订单在系统里还是
``legacy_unverified`` / ``pending``，其实货早就发出去了，会把待发货队列和
库存占用算错。这个脚本按订单号分界线一次性把它们改成 ``shipped``。

安全措施：
    - 只处理**同一家店铺**的订单（各店订单号体系不同，不能跨店比较）
    - 默认跳过已经是 ``shipped`` 的订单
    - 执行前把每单的原始状态写入 ``backups/``，需要时可以逐单还原
    - ``--dry-run`` 先看会改哪些单，不写库

用法：
    python scripts/mark_orders_shipped.py --before 11326 --dry-run
    python scripts/mark_orders_shipped.py --before 11326
    python scripts/mark_orders_shipped.py --before 11326 --exclude-cancelled
"""

import argparse
import csv
import os
import sys
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app, db  # noqa: E402
from app.models import Order, Shop  # noqa: E402


def _boundary_order(boundary):
    matches = Order.query.filter(Order.order_number == str(boundary)).all()
    if not matches:
        raise SystemExit(f"找不到订单号 {boundary}，无法确定分界店铺")
    if len({row.shop_id for row in matches}) > 1:
        raise SystemExit(f"订单号 {boundary} 在多店铺中都存在，请用 --shop-id 指定店铺")
    return matches[0]


def _targets(shop_id, boundary, exclude_cancelled):
    """同一店铺内、订单号小于分界号、且还不是已出库的订单。"""
    rows = (
        Order.query.filter(Order.shop_id == shop_id, Order.warehouse_status != "shipped")
        .order_by(Order.id)
        .all()
    )
    picked = []
    for order in rows:
        number = str(order.order_number or "").strip()
        if not number.isdigit() or int(number) >= boundary:
            continue
        if exclude_cancelled and order.cancelled_at is not None:
            continue
        picked.append(order)
    return picked


def _write_undo(orders, backup_dir):
    os.makedirs(backup_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(backup_dir, f"mark_shipped_undo_{stamp}.csv")
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["order_id", "order_number", "previous_warehouse_status"])
        for order in orders:
            writer.writerow([order.id, order.order_number, order.warehouse_status])
    return path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--before", required=True, help="分界订单号，例如 11326（不含该单本身）")
    parser.add_argument("--shop-id", type=int, default=None, help="默认取分界订单所在店铺")
    parser.add_argument("--exclude-cancelled", action="store_true", help="跳过已取消订单")
    parser.add_argument("--dry-run", action="store_true", help="只统计，不写库")
    args = parser.parse_args()

    boundary = int(str(args.before).lstrip("#"))
    app = create_app()

    with app.app_context():
        anchor = _boundary_order(args.before)
        shop_id = args.shop_id or anchor.shop_id
        shop = db.session.get(Shop, shop_id)
        if shop is None:
            raise SystemExit(f"店铺 {shop_id} 不存在")

        targets = _targets(shop_id, boundary, args.exclude_cancelled)
        breakdown = {}
        for order in targets:
            breakdown[order.warehouse_status] = breakdown.get(order.warehouse_status, 0) + 1

        print(f"店铺：{shop.name}（id={shop_id}）")
        print(f"分界：订单号 < {boundary}（不含 {boundary} 本身，分界单创建于 {anchor.created_at}）")
        print(f"待改单数：{len(targets)}")
        for status, count in sorted(breakdown.items(), key=lambda item: -item[1]):
            print(f"  {status:26s} {count}")

        if not targets:
            print("没有需要处理的订单")
            return 0
        if args.dry_run:
            print("\n[dry-run] 未写库")
            return 0

        undo_path = _write_undo(targets, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backups"))
        print(f"\n原始状态已保存：{undo_path}")

        for order in targets:
            order.warehouse_status = "shipped"
        db.session.commit()
        print(f"已把 {len(targets)} 单标记为 shipped")

        remain = (
            Order.query.filter(Order.shop_id == shop_id, Order.warehouse_status == "pending").count()
        )
        print(f"该店铺剩余待发货（pending）：{remain} 单")
    return 0


if __name__ == "__main__":
    sys.exit(main())
