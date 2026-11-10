#!/usr/bin/env python3
"""分析 PayPal 支付订单的真实手续费（只读，不写入任何数据）。

结论先行：Shopify 交易回执（transactions.receipt）里已经带了 PayPal 的
seller_receivable_breakdown，也就是真实的 paypal_fee / net_amount，
不需要额外接 PayPal API。

本脚本做四件事：
    1. 统计最近 N 单里有多少 PayPal 支付、能否解析出真实手续费
    2. 按币种汇总真实手续费与等效费率，并拟合「比例 + 固定费」
    3. 跟系统 fee_configs 的估算值逐笔对比，看偏差方向与幅度
    4. 检查支付币种与订单币种是否一致（数据口径问题）

用法：
    python scripts/analyze_paypal_fees.py                 # 最近 500 单
    python scripts/analyze_paypal_fees.py --limit 1500
    python scripts/analyze_paypal_fees.py --shop-id 1
"""

import argparse
import json
import os
import sys
import time
from collections import Counter, defaultdict

import requests
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app  # noqa: E402
from app.models import Order, Payment, Shop  # noqa: E402
from app.services.shopify_auth import API_VERSION, get_access_token
from app.models.shop import Shop as ShopModel  # noqa: E402

PAGE_LIMIT = 250
CACHE_PATH = "/tmp/paypal_fees_cache.json"


def extract_breakdown(receipt):
    """从 Shopify 交易回执里取出 PayPal 的 seller_receivable_breakdown。

    回执有两种形态：
        - capture 交易：receipt 直接就是 breakdown 所在对象
        - sale 交易：receipt 是 PayPal order 对象，breakdown 埋在 captures 里
    """
    if not isinstance(receipt, dict):
        return None

    def _pack(source, capture_id):
        breakdown = source.get("seller_receivable_breakdown")
        if not isinstance(breakdown, dict) or "paypal_fee" not in breakdown:
            return None
        return {
            "capture_id": capture_id,
            "gross": breakdown.get("gross_amount") or {},
            "fee": breakdown.get("paypal_fee") or {},
            "net": breakdown.get("net_amount") or {},
            "receivable": breakdown.get("receivable_amount") or {},
            "exchange_rate": breakdown.get("exchange_rate") or {},
        }

    direct = _pack(receipt, receipt.get("id"))
    if direct:
        return direct

    for purchase_unit in receipt.get("purchase_units") or []:
        payments = purchase_unit.get("payments") or {}
        for capture in payments.get("captures") or []:
            packed = _pack(capture, capture.get("id"))
            if packed:
                return packed
    return None


def fetch_orders(shop, limit):
    """翻页拉取订单列表（含 payment_gateway_names，不含 transactions）。"""
    shop_url = ShopModel.normalize_shop_url(shop.shop_url)
    api_root = f"https://{shop_url}/admin/api/{API_VERSION}"
    headers = {"X-Shopify-Access-Token": get_access_token(shop)}

    orders = []
    url = f"{api_root}/orders.json"
    params = {"status": "any", "limit": PAGE_LIMIT}

    while len(orders) < limit:
        response = requests.get(url, headers=headers, params=params, timeout=60)
        if response.status_code != 200:
            print(f"订单列表 HTTP {response.status_code}: {response.text[:300]}")
            break
        page = response.json().get("orders", [])
        if not page:
            break
        orders.extend(page)

        next_url = None
        for part in response.headers.get("Link", "").split(","):
            if 'rel="next"' in part:
                next_url = part.split(";")[0].strip().strip("<>")
                break
        if not next_url:
            break
        url, _, query = next_url.partition("?")
        params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)

    return api_root, headers, orders[:limit]


def fetch_transactions(api_root, headers, order_id):
    response = requests.get(f"{api_root}/orders/{order_id}/transactions.json", headers=headers, timeout=30)
    if response.status_code != 200:
        return None
    return response.json().get("transactions", []) or []


def fit_rate(pairs):
    """最小二乘拟合 fee = pct * gross + fixed，返回 (pct, fixed)。"""
    n = len(pairs)
    if n < 2:
        return None, None
    sum_x = sum(x for x, _ in pairs)
    sum_y = sum(y for _, y in pairs)
    sum_xx = sum(x * x for x, _ in pairs)
    sum_xy = sum(x * y for x, y in pairs)
    denominator = n * sum_xx - sum_x * sum_x
    if abs(denominator) < 1e-9:
        return None, None
    slope = (n * sum_xy - sum_x * sum_y) / denominator
    intercept = (sum_y - slope * sum_x) / n
    return slope, intercept


def main():
    parser = argparse.ArgumentParser(description="Analyze real PayPal fees from Shopify receipts")
    parser.add_argument("--limit", type=int, default=500, help="扫描最近多少个订单")
    parser.add_argument("--shop-id", type=int, default=None, help="指定店铺 ID（默认第一个）")
    parser.add_argument("--pause", type=float, default=0.35, help="每次请求间隔秒数（避免触发限流）")
    parser.add_argument("--use-cache", action="store_true", help="复用上次抓取的样本，不再请求 Shopify")
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        shop = (
            Shop.query.filter_by(id=args.shop_id).first()
            if args.shop_id
            else Shop.query.order_by(Shop.id).first()
        )
        if shop is None or not get_access_token(shop):
            print("店铺不存在或没有配置 Access Token。")
            return 1

        order_meta = {}
        if args.use_cache and os.path.exists(CACHE_PATH):
            with open(CACHE_PATH, encoding="utf-8") as handle:
                cached = json.load(handle)
            records = cached["records"]
            kkinds = Counter(cached["kinds"])
            failure = Counter(cached["failure"])
            mismatch_note = cached.get("scanned_orders")
            print(f"店铺: {shop.name} ({shop.shop_url})")
            print(f"（复用缓存，上次扫描 {mismatch_note} 单）\n")
        else:
            print(f"店铺: {shop.name} ({shop.shop_url})")
            api_root, headers, orders = fetch_orders(shop, args.limit)
            print(f"扫描订单: {len(orders)} 单\n")

            for order in orders:
                order_meta[str(order["id"])] = order

            paypal_orders = [
                o
                for o in orders
                if any("paypal" in str(g).lower() for g in (o.get("payment_gateway_names") or []))
            ]
            print(f"其中 PayPal 订单: {len(paypal_orders)} 单，开始逐单拉取交易明细…\n")

            records = []
            kkinds = Counter()
            failure = Counter()

            for index, order in enumerate(paypal_orders, 1):
                transactions = fetch_transactions(api_root, headers, order["id"])
                if transactions is None:
                    failure["交易接口失败"] += 1
                    continue

                seen_captures = set()
                for tx in transactions:
                    if "paypal" not in str(tx.get("gateway", "")).lower():
                        continue
                    kkinds[tx.get("kind")] += 1
                    breakdown = extract_breakdown(tx.get("receipt"))
                    if not breakdown:
                        failure[f"无明细({tx.get('kind')})"] += 1
                        continue
                    capture_id = breakdown["capture_id"]
                    if capture_id in seen_captures:
                        continue
                    seen_captures.add(capture_id)

                    try:
                        gross = float(breakdown["gross"].get("value"))
                        fee = float(breakdown["fee"].get("value"))
                        net = float(breakdown["net"].get("value"))
                    except (TypeError, ValueError):
                        failure["金额解析失败"] += 1
                        continue

                    records.append(
                        {
                            "order_shopify_id": str(order["id"]),
                            "order_number": order.get("order_number") or order.get("name"),
                            "order_currency": order.get("currency"),
                            "tx_currency": tx.get("currency"),
                            "kind": tx.get("kind"),
                            "tx_id": str(tx.get("id")),
                            "gross": gross,
                            "fee": fee,
                            "net": net,
                            "fee_currency": breakdown["fee"].get("currency_code"),
                            "receivable": breakdown["receivable"].get("value"),
                            "receivable_currency": breakdown["receivable"].get("currency_code"),
                            "exchange_rate": breakdown["exchange_rate"].get("value"),
                        }
                    )
                time.sleep(args.pause)
                if index % 25 == 0:
                    print(f"    已处理 {index}/{len(paypal_orders)} 单…")

            with open(CACHE_PATH, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "records": records,
                        "kinds": dict(kkinds),
                        "failure": dict(failure),
                        "scanned_orders": len(orders),
                    },
                    handle,
                    ensure_ascii=False,
                )

        # 库里已有的支付估算，用于对比（shopify_order_id 是字符串）
        db_orders = {str(o.shopify_order_id): o for o in Order.query.all()}
        db_payments = {}
        for payment in Payment.query.filter(Payment.payment_method == "paypal").all():
            db_payments[(payment.order_id, str(payment.transaction_id))] = payment

        for record in records:
            db_order = db_orders.get(record["order_shopify_id"])
            payment = db_payments.get((db_order.id, record["tx_id"])) if db_order else None
            record["estimated_fee"] = (
                float(payment.total_fee) if payment is not None and payment.total_fee is not None else None
            )
            record["payment_id"] = payment.id if payment else None

        print(f"\n解析到真实手续费的交易: {len(records)} 笔")
        if kkinds:
            print(f"PayPal 交易类型分布: {dict(kkinds)}")
        if failure:
            print(f"未解析: {dict(failure)}")

        if not records:
            print("\n没有可分析的样本。")
            return 0

        # ---- 按币种汇总 ----
        print("\n=== 一、真实手续费（按 PayPal 扣费币种）===")
        by_currency = defaultdict(list)
        for record in records:
            by_currency[record["fee_currency"] or "?"].append(record)

        print(f"{'币种':<7}{'笔数':>5}{'毛额':>13}{'手续费':>11}{'净额':>13}{'等效费率':>10}")
        for currency, rows in sorted(by_currency.items(), key=lambda x: -len(x[1])):
            gross = sum(r["gross"] for r in rows)
            fee = sum(r["fee"] for r in rows)
            net = sum(r["net"] for r in rows)
            rate = fee / gross * 100 if gross else 0
            print(f"{currency:<7}{len(rows):>5}{gross:>13,.2f}{fee:>11,.2f}{net:>13,.2f}{rate:>9.2f}%")

            slope, intercept = fit_rate([(r["gross"], r["fee"]) for r in rows])
            if slope is not None:
                print(f"       拟合: 手续费 ≈ 毛额 × {slope * 100:.2f}% + {intercept:.2f} {currency}")

        # ---- 与系统估算对比 ----
        matched = [r for r in records if r["estimated_fee"] is not None]
        print("\n=== 二、系统估算 vs PayPal 真实扣费 ===")
        if matched:
            real_total = sum(r["fee"] for r in matched)
            est_total = sum(r["estimated_fee"] for r in matched)
            diff = est_total - real_total
            pct = diff / real_total * 100 if real_total else 0
            print(f"可比对交易: {len(matched)} 笔（其余是库里还没同步到的新订单）")
            print(f"    真实手续费合计:   {real_total:,.2f}")
            print(f"    系统估算合计:     {est_total:,.2f}")
            print(f"    差额:            {diff:,.2f}  ({pct:+.1f}%)")
            print("    注：这里按「扣费币种」的原始数字直接相加，只用于看偏差方向与量级。")

            print("\n    逐笔示例（前 10 笔）：")
            for r in matched[:10]:
                delta = r["estimated_fee"] - r["fee"]
                print(
                    f"      订单 {str(r['order_number']):>8s}"
                    f"  {r['gross']:>8.2f} {r['fee_currency']}"
                    f"  真实={r['fee']:>7.2f}"
                    f"  估算={r['estimated_fee']:>7.2f}"
                    f"  差={delta:>+7.2f}"
                )
        else:
            print("没有能和库里对上的交易。")

        # ---- 币种口径检查 ----
        print("\n=== 三、币种口径检查 ===")
        mismatch = [
            r
            for r in records
            if r["tx_currency"] and r["order_currency"] and r["tx_currency"] != r["order_currency"]
        ]
        print(f"支付币种与订单币种不一致: {len(mismatch)}/{len(records)} 笔")
        if mismatch:
            for r in mismatch[:5]:
                print(
                    f"    订单 {r['order_number']}: 订单币种={r['order_currency']}"
                    f"，实际扣款币种={r['tx_currency']}"
                    f"，毛额 {r['gross']:.2f} {r['fee_currency']}"
                )
            print("\n    → 库里 payments.currency 记的是订单币种，但金额是扣款币种，")
            print("      两者不是一回事，会导致手续费率和利润按错币种计算。")

        # ---- 换汇信息 ----
        with_rate = [r for r in records if r["exchange_rate"] and r["receivable"]]
        if with_rate:
            print("\n=== 四、到账币种与换汇 ===")
            print(f"带换汇信息的交易: {len(with_rate)} 笔（示例 3 笔）")
            for r in with_rate[:3]:
                print(
                    f"    订单 {str(r['order_number']):>8s}"
                    f"  扣款 {r['gross']:.2f} {r['fee_currency']}"
                    f" → 到账 {r['receivable']} {r['receivable_currency']}"
                    f"  汇率 {float(r['exchange_rate']):.6f}"
                )
    return 0


if __name__ == "__main__":
    sys.exit(main())
