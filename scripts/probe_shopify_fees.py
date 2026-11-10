#!/usr/bin/env python3
"""探测 Shopify 订单交易是否返回真实手续费（只读，不写入任何数据）。

用途：判断当前店铺的收款方式能否直接拿到「扣手续费后到账多少」。
    - 交易里 fee / net 有值  -> 可以直接用 Shopify 的真实净到账
    - fee 为 None、net=amount -> Shopify 未上报手续费，需要走网关（PayPal/Stripe）账单

用法：
    python scripts/probe_shopify_fees.py                # 默认看最近 10 单
    python scripts/probe_shopify_fees.py --limit 20
    python scripts/probe_shopify_fees.py --shop-id 1
"""

import argparse
import json
import os
import sys

import shopify
import requests
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app  # noqa: E402
from app.models import Shop  # noqa: E402
from app.services.shopify_auth import API_VERSION, get_access_token
from app.services.shopify_service import ShopifyService  # noqa: E402



def _value(obj, name, default=None):
    """Shopify 资源对象取值（缺失字段返回默认值）。"""
    try:
        value = getattr(obj, name)
    except Exception:
        return default
    return default if value is None else value


def probe_balance_transactions(shop, limit=10):
    """探测 Shopify Payments 结算流水（含真实手续费）。

    GET /admin/api/{version}/shopify_payments/balance/transactions.json
    需要应用具备 read_shopify_payments 权限。
    """
    url = (
        f"https://{shop.shop_url}/admin/api/{API_VERSION}"
        f"/shopify_payments/balance/transactions.json?limit={limit}"
    )
    try:
        response = requests.get(
            url,
            headers={"X-Shopify-Access-Token": get_access_token(shop)},
            timeout=30,
        )
    except Exception as exc:
        print(f"请求失败: {exc}")
        return None

    if response.status_code != 200:
        print(f"HTTP {response.status_code}: {response.text[:200]}")
        if response.status_code in (401, 403):
            print("→ 大概率是应用缺少 read_shopify_payments 权限，需要在 Shopify 后台给应用补权限。")
        return None

    return response.json().get("transactions", [])


def main():
    parser = argparse.ArgumentParser(description="Probe Shopify transaction fee/net fields")
    parser.add_argument("--limit", type=int, default=10, help="检查最近多少个订单")
    parser.add_argument("--shop-id", type=int, default=None, help="指定店铺 ID（默认第一个）")
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        shop = (
            Shop.query.filter_by(id=args.shop_id).first()
            if args.shop_id
            else Shop.query.order_by(Shop.id).first()
        )
        if shop is None:
            print("没有找到店铺记录，请先在「店铺管理」中配置店铺。")
            return 1

        print(f"店铺: {shop.name} ({shop.shop_url})")
        if not get_access_token(shop):
            print("该店铺没有配置 Access Token，无法探测。")
            return 1

        service = ShopifyService()
        service.init_shop(shop, app)

        try:
            orders = shopify.Order.find(status="any", limit=args.limit)
        except Exception as exc:
            print(f"调用 Shopify 失败: {exc}")
            print("请检查 Access Token 是否有效、是否具备 read_orders 权限。")
            return 2

        print(f"取到 {len(orders)} 个订单\n")

        stat = {"transactions": 0, "with_fee": 0, "gateways": {}}

        for order in orders:
            currency = _value(order, "currency", "")
            print(
                f"订单 {_value(order, 'order_number', '?')}"
                f"  总额 {_value(order, 'total_price', '?')} {currency}"
            )

            transactions = _value(order, "transactions", []) or []
            # shopify-python 库对关联对象返回的是方法（如 order.transactions()）
            if callable(transactions):
                transactions = transactions()
            if not transactions:
                print("    (无交易记录)")
                continue

            for tx in transactions:
                stat["transactions"] += 1
                gateway = _value(tx, "gateway", "unknown")
                stat["gateways"][gateway] = stat["gateways"].get(gateway, 0) + 1

                fee = _value(tx, "fee")
                net = _value(tx, "net")
                if fee is not None:
                    stat["with_fee"] += 1

                print(
                    f"    {_value(tx, 'kind', '?'):<13s}"
                    f" 网关={gateway:<20s}"
                    f" 状态={_value(tx, 'status', '?'):<10s}"
                    f" 金额={_value(tx, 'amount', '?'):>10s}"
                    f" 手续费={str(fee):>8s}"
                    f" 净到账={str(net):>10s}"
                    f" {_value(tx, 'currency', currency)}"
                )

        print("\n=== 汇总 ===")
        print(f"交易总数: {stat['transactions']}")
        print(f"返回了手续费的交易: {stat['with_fee']}")
        print(f"涉及网关: {stat['gateways']}")

        if stat["transactions"] == 0:
            print("\n结论: 取不到交易明细，无法判断。")
        elif stat["with_fee"] == stat["transactions"]:
            print("\n结论: ✅ 所有交易都返回了 fee/net，可直接用 Shopify 的真实净到账。")
        elif stat["with_fee"] == 0:
            print(
                "\n结论: ❌ 交易里没有 fee（net 等于 amount），"
                "Shopify 未上报手续费，需走 PayPal/Stripe 账单或本地费率表。"
            )
        else:
            print(
                f"\n结论: ⚠️ 部分交易有手续费（{stat['with_fee']}/{stat['transactions']}），"
                "建议按网关分别处理：有 fee 用真实值，没有的回落本地费率表。"
            )

        # ---- 再探一层：Shopify Payments 结算流水（真实手续费在这里） ----
        print("\n=== Shopify Payments 结算流水（balance transactions）===")
        transactions = probe_balance_transactions(shop, limit=10)
        if transactions is None:
            print("无法获取（见上方错误）。")
        elif not transactions:
            print("接口可用，但没有返回结算流水（该店铺可能未使用 Shopify Payments 收单）。")
        else:
            print(f"取到 {len(transactions)} 条：")
            for item in transactions:
                print(
                    f"    {str(item.get('type')):<10s}"
                    f" 金额={str(item.get('amount')):>10s}"
                    f" 手续费={str(item.get('fee')):>8s}"
                    f" 净到账={str(item.get('net')):>10s}"
                    f" {item.get('currency')}"
                    f" 订单={item.get('source_order_id')}"
                    f" 打款批次={item.get('payout_id')}"
                    f" 到账状态={item.get('payout_status')}"
                )
            print("\n→ 这里的 fee/net 是 Shopify Payments 的真实手续费与净到账，")
            print("   source_order_id 可以直接对应到具体订单。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
