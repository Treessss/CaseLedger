#!/usr/bin/env python3
"""探测本项目用到的 REST 端点在各个 Shopify API 版本上的可用性（只读）。

背景：项目里 GraphQL 走 2026-07，REST 还停在 2023-10（已超出 Shopify 的
官方支持窗口）。升级前先确认每个端点在目标版本上真的能通，避免上线后才发现。

用法：
    python scripts/probe_rest_api_versions.py
    python scripts/probe_rest_api_versions.py --versions 2025-01,2026-07
"""

import argparse
import os
import sys

import requests
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app  # noqa: E402
from app.models import Order, Shop  # noqa: E402
from app.services.shopify_auth import get_access_token  # noqa: E402

DEFAULT_VERSIONS = ("2023-10", "2025-01", "2025-07", "2025-10", "2026-01", "2026-07")


def probe_shop(shop, versions):
    token = get_access_token(shop)
    headers = {"X-Shopify-Access-Token": token}
    domain = shop.shop_url

    # 每个店铺取一个真实订单，用来测 transactions.json
    sample = (
        Order.query.filter(Order.shop_id == shop.id, Order.shopify_order_id.isnot(None))
        .order_by(Order.id.desc())
        .first()
    )
    order_id = sample.shopify_order_id if sample else None

    endpoints = [
        ("shop.json", f"https://{domain}/admin/api/{{v}}/shop.json"),
        ("orders.json", f"https://{domain}/admin/api/{{v}}/orders.json?limit=1&status=any"),
    ]
    if order_id:
        endpoints.append(
            (
                "order.transactions",
                f"https://{domain}/admin/api/{{v}}/orders/{order_id}/transactions.json",
            )
        )
    endpoints.append(
        (
            "payments.balance",
            f"https://{domain}/admin/api/{{v}}/shopify_payments/balance/transactions.json?limit=1",
        )
    )

    print("=" * 78)
    print(f"店铺 {shop.name} / {domain}")
    for version in versions:
        results = []
        for label, template in endpoints:
            url = template.format(v=version)
            try:
                response = requests.get(url, headers=headers, timeout=30)
                code = response.status_code
            except requests.RequestException as exc:
                results.append(f"{label}=ERR({exc.__class__.__name__})")
                continue
            mark = "OK " if code == 200 else "NG "
            results.append(f"{label}={mark}{code}")
        print(f"  {version:8s} " + "  ".join(results))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--versions", default=",".join(DEFAULT_VERSIONS))
    args = parser.parse_args()
    versions = [item.strip() for item in args.versions.split(",") if item.strip()]

    app = create_app()
    with app.app_context():
        for shop in Shop.query.order_by(Shop.id).all():
            try:
                probe_shop(shop, versions)
            except Exception as exc:  # noqa: BLE001 - 探测脚本，逐店铺隔离
                print(f"店铺 {shop.name} 探测失败：{exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
