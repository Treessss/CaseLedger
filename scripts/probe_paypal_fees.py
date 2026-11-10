#!/usr/bin/env python3
"""探测 PayPal 支付订单能否拿到真实手续费（只读，不写入任何数据）。

回答两个问题：
    1. 库里有多少订单/支付是 PayPal，现在的手续费是怎么来的（估算还是真实）
    2. Shopify 订单交易里有没有 PayPal 的交易号（receipt），能否据此去 PayPal 侧查真实费用

用法：
    python scripts/probe_paypal_fees.py                 # 默认抓最近 250 单
    python scripts/probe_paypal_fees.py --limit 500
    python scripts/probe_paypal_fees.py --shop-id 1
"""

import argparse
import os
import sys
from collections import Counter

import requests
from dotenv import load_dotenv
from sqlalchemy import func

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app, db  # noqa: E402
from app.models import Order, Payment, Shop  # noqa: E402
from app.services.shopify_auth import API_VERSION, get_access_token




def probe_db():
    """库内 PayPal 支付现状（估算 vs 真实）。"""
    print("=== 一、数据库现状 ===")

    rows = db.session.query(Order.payment_method, func.count(Order.id)).group_by(Order.payment_method).all()
    total = sum(r[1] for r in rows) or 1
    print("订单按支付方式：")
    for method, count in sorted(rows, key=lambda x: -x[1]):
        print(f"    {str(method or '(空)'):<18s} {count:>5d} 单  {count / total * 100:5.1f}%")

    rows = (
        db.session.query(Payment.payment_method, func.count(Payment.id))
        .group_by(Payment.payment_method)
        .all()
    )
    print("支付记录按方式：")
    for method, count in sorted(rows, key=lambda x: -x[1]):
        print(f"    {str(method or '(空)'):<18s} {count:>5d} 笔")

    agg = (
        db.session.query(
            func.count(Payment.id),
            func.coalesce(func.sum(Payment.amount), 0),
            func.coalesce(func.sum(Payment.total_fee), 0),
            func.coalesce(func.sum(Payment.net_amount), 0),
        )
        .filter(Payment.payment_method == "paypal")
        .first()
    )
    if agg and agg[0]:
        count, amount, fee, net = float(agg[0]), float(agg[1]), float(agg[2]), float(agg[3])
        rate = fee / amount * 100 if amount else 0
        print(f"\nPayPal 支付汇总：{int(count)} 笔，金额 {amount:,.2f}，手续费 {fee:,.2f}，净额 {net:,.2f}")
        print(f"    等效费率 {rate:.2f}%")
        print("    注：手续费来自 fee_configs 配置估算，不是 PayPal 实际扣费。")
    else:
        print("\n库里没有 PayPal 支付记录。")

    currencies = (
        db.session.query(Payment.currency, func.count(Payment.id))
        .filter(Payment.payment_method == "paypal")
        .group_by(Payment.currency)
        .all()
    )
    if currencies:
        print(f"币种分布：{ {c: n for c, n in currencies} }")

    return rows


def probe_shopify(shop, app, limit):
    """Shopify 侧 PayPal 交易明细，看能否拿到 PayPal 交易号。"""
    print("\n=== 二、Shopify 订单里的 PayPal 交易 ===")

    # orders.json 列表接口不返回 transactions，需要按单再调 /transactions.json。
    # 所以先用 payment_gateway_names 把 PayPal 订单筛出来，只补拉这些单的交易。
    from app.models.shop import Shop as ShopModel

    shop_url = ShopModel.normalize_shop_url(shop.shop_url)
    api_root = f"https://{shop_url}/admin/api/{API_VERSION}"
    headers = {"X-Shopify-Access-Token": get_access_token(shop)}

    orders = []
    base_url = f"{api_root}/orders.json"
    params = {
        "status": "any",
        "limit": 250,
    }
    while len(orders) < limit:
        try:
            response = requests.get(base_url, headers=headers, params=params, timeout=60)
        except Exception as exc:
            print(f"调用 Shopify 失败: {exc}")
            return None
        if response.status_code != 200:
            print(f"HTTP {response.status_code}: {response.text[:300]}")
            return None

        page = response.json().get("orders", [])
        if not page:
            break
        orders.extend(page)

        # 用 Link 头翻页
        link = response.headers.get("Link", "")
        next_url = None
        for part in link.split(","):
            if 'rel="next"' in part:
                next_url = part.split(";")[0].strip().strip("<>")
                break
        if not next_url:
            break
        base_url, _, query = next_url.partition("?")
        params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)

    orders = orders[:limit]

    print(f"取到 {len(orders)} 个订单\n")

    gateways = Counter()
    paypal_orders = 0
    paypal_txs = 0
    with_receipt = 0
    with_fee = 0
    samples = []

    for order in orders:
        order_gateways = order.get("payment_gateway_names") or []
        if not any("paypal" in str(g).lower() for g in order_gateways):
            continue

        paypal_orders += 1
        try:
            tx_response = requests.get(
                f"{api_root}/orders/{order['id']}/transactions.json", headers=headers, timeout=30
            )
        except Exception as exc:
            print(f"    订单 {order.get('name')} 交易拉取失败: {exc}")
            continue
        if tx_response.status_code != 200:
            print(f"    订单 {order.get('name')} 交易 HTTP {tx_response.status_code}")
            continue
        transactions = tx_response.json().get("transactions", []) or []

        for tx in transactions:
            gateway = tx.get("gateway", "unknown")
            gateways[gateway] += 1
            if "paypal" not in str(gateway).lower():
                continue

            paypal_txs += 1
            receipt = tx.get("receipt")
            fee = tx.get("fee")
            net = tx.get("net")
            if receipt:
                with_receipt += 1
            if fee is not None:
                with_fee += 1

            if len(samples) < 15:
                samples.append(
                    {
                        "order": order.get("order_number") or order.get("name") or "?",
                        "currency": order.get("currency", ""),
                        "total": order.get("total_price", ""),
                        "kind": tx.get("kind", "?"),
                        "status": tx.get("status", "?"),
                        "amount": tx.get("amount", "?"),
                        "tx_id": tx.get("id", ""),
                        "receipt": receipt,
                        "fee": fee,
                        "net": net,
                        "gateway": gateway,
                        "created_at": order.get("created_at", ""),
                    }
                )

    print("交易网关分布：")
    for gateway, count in gateways.most_common():
        print(f"    {gateway:<28s} {count:>5d}")

    print(f"\nPayPal 订单数: {paypal_orders}")
    print(f"PayPal 交易数: {paypal_txs}")
    print(f"其中带 receipt（PayPal 交易号）: {with_receipt}")
    print(f"其中带 fee（真实手续费）: {with_fee}")

    if samples:
        print("\n样本明细：")
        for s in samples:
            print(
                f"    订单 {str(s['order']):>10s}"
                f"  {str(s['total']):>9s} {s['currency']}"
                f"  {str(s['kind']):<8s}/{str(s['status']):<8s}"
                f"  金额={str(s['amount']):>9s}"
                f"  手续费={str(s['fee']):>7s}"
                f"  净额={str(s['net']):>9s}"
                f"  receipt={s['receipt']}"
                f"  {s['created_at'][:10]}"
            )

    return {
        "paypal_orders": paypal_orders,
        "paypal_txs": paypal_txs,
        "with_receipt": with_receipt,
        "with_fee": with_fee,
    }


def main():
    parser = argparse.ArgumentParser(description="Probe PayPal fee availability")
    parser.add_argument("--limit", type=int, default=250, help="抓取最近多少个订单")
    parser.add_argument("--shop-id", type=int, default=None, help="指定店铺 ID（默认第一个）")
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        probe_db()

        shop = (
            Shop.query.filter_by(id=args.shop_id).first()
            if args.shop_id
            else Shop.query.order_by(Shop.id).first()
        )
        if shop is None:
            print("\n没有找到店铺记录。")
            return 1
        if not get_access_token(shop):
            print(f"\n店铺 {shop.name} 没有配置 Access Token。")
            return 1

        print(f"\n探测店铺: {shop.name} ({shop.shop_url})")
        result = probe_shopify(shop, app, args.limit)

    if not result:
        return 2

    print("\n=== 结论 ===")
    if result["paypal_txs"] == 0:
        print("最近订单里没有 PayPal 支付，无法判断。")
    elif result["with_receipt"] and not result["with_fee"]:
        print("❌ 交易里没有真实手续费，但 ✅ 带 PayPal 交易号（receipt）。")
        print("   → 可以用 receipt 里的交易号去 PayPal API 查真实手续费（需要 PayPal 商务账号的 API 凭据）。")
    elif result["with_receipt"] and result["with_fee"]:
        print("✅ 交易本身带手续费，可直接使用。")
    elif not result["with_receipt"]:
        print("⚠️ 交易里既没有真实手续费，也没有 PayPal 交易号，只能靠费率表估算或导入 PayPal 账单。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
