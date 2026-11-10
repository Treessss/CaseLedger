"""Reconcile stored order timestamps with Shopify without touching monetary data.

Dry-run by default. ``--apply`` requires a verified database backup path.
Only an exact eight-hour offset (or an already correct value) is accepted for
order_date and created_at. An unexpected offset aborts before any writes.
"""

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import create_app, db  # noqa: E402
from app.models import Order, Payment, Shop  # noqa: E402
from app.services.shopify_graphql import _execute, require_all_orders_access  # noqa: E402
from app.utils.timezone import utc_naive_from_iso  # noqa: E402

OFFSET = timedelta(hours=8)
ORDER_TIMES_QUERY = """
query OrderTimes($first: Int!, $after: String) {
  orders(first: $first, after: $after, sortKey: CREATED_AT, reverse: true) {
    pageInfo { hasNextPage endCursor }
    nodes { id name createdAt updatedAt transactions { id processedAt } }
  }
}
"""


def fetch_shopify_order_times(shop):
    """Fetch every historical order; fail closed on stalled pagination/duplicates."""
    require_all_orders_access(shop)
    cursor = None
    result = {}
    page = 0
    while True:
        connection = _execute(shop, ORDER_TIMES_QUERY, {"first": 250, "after": cursor})["orders"]
        nodes = connection.get("nodes") or []
        for node in nodes:
            order_id = node["id"].rsplit("/", 1)[-1]
            if order_id in result:
                raise RuntimeError(f"店铺 {shop.name} 的 Shopify 订单分页出现重复 ID {order_id}")
            result[order_id] = node
        page += 1
        if page % 5 == 0 or not connection["pageInfo"]["hasNextPage"]:
            print(f"{shop.name}: 已核对 {page} 页、{len(result)} 笔 Shopify 订单", flush=True)
        if not connection["pageInfo"]["hasNextPage"]:
            return result
        next_cursor = connection["pageInfo"].get("endCursor")
        if not nodes or not next_cursor or next_cursor == cursor:
            raise RuntimeError(f"店铺 {shop.name} 的 Shopify 订单分页未前进，已停止")
        cursor = next_cursor


def plan_timestamp_update(order, remote):
    """Return a safe bulk mapping and offset categories for one existing order."""
    created_utc = utc_naive_from_iso(remote["createdAt"])
    updated_utc = utc_naive_from_iso(remote["updatedAt"])
    for field in ("order_date", "created_at"):
        value = getattr(order, field)
        if value not in (created_utc, created_utc + OFFSET):
            raise RuntimeError(
                f"订单 {order.order_number} 的 {field} 与 Shopify 相差不是 0 或 8 小时："
                f"本地 {value}，Shopify {created_utc}"
            )

    # updated_at can also be a local edit/webhook timestamp. Only repair it
    # when it is an exact Shopify timestamp + 8 hours; otherwise preserve it.
    updated_value = order.updated_at
    updated_category = "other"
    if updated_value == updated_utc + OFFSET:
        updated_value = updated_utc
        updated_category = "shifted_8h"
    elif updated_value == updated_utc:
        updated_category = "already_utc"

    order_category = "shifted_8h" if order.order_date == created_utc + OFFSET else "already_utc"
    mapping = {
        "id": order.id,
        "order_date": created_utc,
        "created_at": created_utc,
        # Explicitly supply updated_at so the model's onupdate hook cannot
        # replace Shopify/local history with the repair's wall-clock time.
        "updated_at": updated_value,
    }
    changed = (
        order.order_date != mapping["order_date"]
        or order.created_at != mapping["created_at"]
        or order.updated_at != mapping["updated_at"]
    )
    return mapping if changed else None, order_category, updated_category


def plan_payment_timestamp_update(payment, transaction):
    """Only fix payments whose Shopify transaction proves an exact +8h offset."""
    if transaction is None or not transaction.get("processedAt"):
        return None, "without_shopify_time"
    expected = utc_naive_from_iso(transaction["processedAt"])
    if payment.payment_date == expected:
        return None, "already_utc"
    if payment.payment_date != expected + OFFSET:
        return None, "other"
    return {"id": payment.id, "payment_date": expected, "updated_at": payment.updated_at}, "shifted_8h"


def build_plan(shops, *, lock_rows=False):
    order_mappings = []
    payment_mappings = []
    summaries = []
    # Finish all network reads before acquiring database row locks.
    remote_by_shop = {shop.id: fetch_shopify_order_times(shop) for shop in shops}
    for shop in shops:
        remote = remote_by_shop[shop.id]
        query = Order.query.filter_by(shop_id=shop.id)
        if lock_rows:
            query = query.with_for_update()
        local = {order.shopify_order_id: order for order in query.all()}
        payment_query = Payment.query.join(Order, Payment.order_id == Order.id).filter(Order.shop_id == shop.id)
        if lock_rows:
            payment_query = payment_query.with_for_update(of=Payment)
        payments_by_order = defaultdict(list)
        for payment in payment_query.all():
            payments_by_order[payment.order_id].append(payment)
        local_only = local.keys() - remote.keys()
        if local_only:
            raise RuntimeError(
                f"店铺 {shop.name} 有 {len(local_only)} 笔本地订单在 Shopify 全量结果中不存在；"
                "为避免误改，已停止"
            )
        offsets = Counter()
        updated_offsets = Counter()
        payment_offsets = Counter()
        for order_id in local.keys() & remote.keys():
            order = local[order_id]
            node = remote[order_id]
            mapping, category, updated_category = plan_timestamp_update(order, node)
            offsets[category] += 1
            updated_offsets[updated_category] += 1
            if mapping:
                order_mappings.append(mapping)
            transactions = {tx["id"].rsplit("/", 1)[-1]: tx for tx in node.get("transactions") or []}
            for payment in payments_by_order[order.id]:
                payment_mapping, payment_category = plan_payment_timestamp_update(
                    payment, transactions.get(payment.transaction_id)
                )
                payment_offsets[payment_category] += 1
                if payment_mapping:
                    payment_mappings.append(payment_mapping)
        summaries.append(
            {
                "shop": shop.name,
                "local_orders": len(local),
                "shopify_orders": len(remote),
                "missing_local": [remote[key]["name"] for key in sorted(remote.keys() - local.keys())],
                "order_time_offsets": dict(offsets),
                "updated_time_offsets": dict(updated_offsets),
                "payment_time_offsets": dict(payment_offsets),
            }
        )
    return order_mappings, payment_mappings, summaries, remote_by_shop


def verify_after_apply(shops, remote_by_shop):
    for shop in shops:
        remote = remote_by_shop[shop.id]
        local = Order.query.filter_by(shop_id=shop.id).all()
        payments_by_order = defaultdict(list)
        for payment in Payment.query.join(Order, Payment.order_id == Order.id).filter(Order.shop_id == shop.id):
            payments_by_order[payment.order_id].append(payment)
        for order in local:
            node = remote.get(order.shopify_order_id)
            if node is None:
                continue
            expected = utc_naive_from_iso(node["createdAt"])
            if order.order_date != expected or order.created_at != expected:
                raise RuntimeError(f"修正后订单 {order.order_number} 仍与 Shopify 创建时间不一致")
            transactions = {tx["id"].rsplit("/", 1)[-1]: tx for tx in node.get("transactions") or []}
            for payment in payments_by_order[order.id]:
                transaction = transactions.get(payment.transaction_id)
                if transaction and transaction.get("processedAt"):
                    expected_payment = utc_naive_from_iso(transaction["processedAt"])
                    if payment.payment_date == expected_payment + OFFSET:
                        raise RuntimeError(f"修正后支付记录 {payment.id} 仍比 Shopify 晚 8 小时")
        print(f"{shop.name}: 已复核 {len(local)} 笔本地订单及其匹配交易的时间", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="实际写入；默认只预演")
    parser.add_argument("--backup", type=Path, help="实际写入时必填：预先生成的完整数据库备份")
    args = parser.parse_args()
    if args.apply and (not args.backup or not args.backup.is_file() or args.backup.stat().st_size == 0):
        parser.error("--apply 必须指定已存在且非空的 --backup 文件")

    app = create_app()
    with app.app_context():
        shops = Shop.query.order_by(Shop.id).all()
        order_mappings, payment_mappings, summaries, remote_by_shop = build_plan(shops, lock_rows=args.apply)
        print(json.dumps({"mode": "apply" if args.apply else "dry_run", "order_changes": len(order_mappings), "payment_changes": len(payment_mappings), "shops": summaries}, ensure_ascii=False), flush=True)
        if not args.apply:
            db.session.rollback()
            return
        try:
            if order_mappings:
                db.session.bulk_update_mappings(Order, order_mappings)
            if payment_mappings:
                db.session.bulk_update_mappings(Payment, payment_mappings)
            db.session.commit()
            db.session.expire_all()
            verify_after_apply(shops, remote_by_shop)
        except Exception:
            db.session.rollback()
            raise


if __name__ == "__main__":
    main()
