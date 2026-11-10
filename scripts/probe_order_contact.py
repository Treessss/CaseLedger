#!/usr/bin/env python3
"""验证 Admin GraphQL 能否拿到订单收货地址与联系方式（只读，不写库）。

对比同一批订单在「本地库」和「Shopify GraphQL」里的客户信息，
用来判断受保护客户数据是否真的能取到。

用法：
    python scripts/probe_order_contact.py                 # 取本地最近 5 单
    python scripts/probe_order_contact.py --limit 10
    python scripts/probe_order_contact.py --order-id 123
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
from app.services.shopify_graphql import (  # noqa: E402
    API_VERSION,
    ShopifyGraphQLError,
    _execute,
    fetch_order_contact,
)

# 只问「非 PII」字段：用来区分「接口不通」和「客户数据没授权」
REDUCED_QUERY = """
query OrderBasics($id: ID!) {
  order(id: $id) {
    id
    name
    displayFinancialStatus
    shippingAddress {
      country
      countryCode
      province
      provinceCode
    }
  }
}
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--order-id", type=int, default=None)
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        if args.order_id:
            orders = [Order.query.get(args.order_id)]
        else:
            orders = Order.query.order_by(Order.id.desc()).limit(args.limit).all()

        print(f"GraphQL API 版本: {API_VERSION}")
        for order in orders:
            if order is None:
                continue
            shop = Shop.query.get(order.shop_id) if order.shop_id else None
            print("=" * 70)
            print(f"# 本地订单 {order.id} / Shopify {order.shopify_order_id} / 店铺 {getattr(shop, 'name', None)}")
            print(f"  本地: name={order.customer_name!r} phone={order.customer_phone!r} email={order.customer_email!r}")
            print(f"  本地地址: {json.dumps(order.shipping_address, ensure_ascii=False)}")
            if shop is None:
                print("  跳过: 订单没有关联店铺")
                continue
            try:
                contact = fetch_order_contact(shop, order.shopify_order_id)
            except ShopifyGraphQLError as exc:
                print(f"  GraphQL 失败: {exc}")
                continue
            print(f"  GraphQL: name={contact['name']!r} phone={contact['phone']!r} email={contact['email']!r}")
            print(f"  GraphQL 地址: {json.dumps(contact['shipping_address'], ensure_ascii=False)}")


def probe_reduced(app, order_id):
    """只取非 PII 字段，看接口本身是否可用。"""
    from app.services.shopify_graphql import gid_for_order

    with app.app_context():
        order = Order.query.get(order_id)
        shop = Shop.query.get(order.shop_id) if order and order.shop_id else None
        if shop is None:
            print("找不到订单或店铺")
            return
        try:
            data = _execute(shop, REDUCED_QUERY, {"id": gid_for_order(order.shopify_order_id)})
        except ShopifyGraphQLError as exc:
            print(f"非 PII 查询也失败: {exc}")
            return
        print(json.dumps(data, ensure_ascii=False, indent=2))


SHOP_QUERY = """
query ShopInfo {
  shop {
    name
    myshopifyDomain
    plan { displayName shopifyPlus partnerDevelopment }
    currencyCode
  }
}
"""


# 扫码查单工具用的原始查询：只问 order.shippingAddress，不碰 customer 对象
THEIRS_QUERY = """
query OrderForWarehouse($id: ID!) {
  order(id: $id) {
    id
    name
    note
    shippingAddress {
      name
      company
      phone
      address1
      address2
      city
      province
      zip
      country
    }
  }
}
"""


def probe_theirs(app, order_id):
    """用扫码工具的原始查询测同一张订单，判断差异在「查询字段」还是「应用授权」。"""
    from app.services.shopify_graphql import gid_for_order

    with app.app_context():
        order = Order.query.get(order_id)
        shop = Shop.query.get(order.shop_id) if order and order.shop_id else None
        if shop is None:
            print("找不到订单或店铺")
            return
        print(f"店铺: {shop.name} / {shop.shop_url}")
        try:
            data = _execute(shop, THEIRS_QUERY, {"id": gid_for_order(order.shopify_order_id)})
        except ShopifyGraphQLError as exc:
            print(f"❌ 失败: {exc}")
            return
        print("✅ 成功，返回：")
        print(json.dumps(data, ensure_ascii=False, indent=2))


def probe_shop(app):
    """看店铺套餐 —— PII 只有 Shopify/Advanced/Plus 套餐才给。"""
    with app.app_context():
        shop = Shop.query.first()
        if shop is None:
            print("没有配置店铺")
            return
        try:
            data = _execute(shop, SHOP_QUERY, {})
        except ShopifyGraphQLError as exc:
            print(f"查询失败: {exc}")
            return
        print(json.dumps(data, ensure_ascii=False, indent=2))


def probe_oauth(app, order_id):
    """用店铺配置的 Client ID / Client Secret 走 client_credentials 换票，再试查询。

    这是全项目唯一的绑定方式；换票失败就说明凭据或应用安装有问题。
    """
    import requests as _requests

    from app.models.shop import Shop as ShopModel
    from app.services.shopify_auth import credentials_for
    from app.services.shopify_graphql import gid_for_order

    with app.app_context():
        order = Order.query.get(order_id)
        shop = Shop.query.get(order.shop_id) if order and order.shop_id else None
        if shop is None:
            print("找不到订单或店铺")
            return

        credentials = credentials_for(shop)
        if not credentials:
            print(f"店铺 {shop.name} 未配置 Client ID / Client Secret，无法走 client_credentials")
            return

        client_id, client_secret = credentials
        shop_url = ShopModel.normalize_shop_url(shop.shop_url)
        print(f"店铺: {shop.name} / {shop_url}")
        print(f"client_id 长度: {len(client_id)}，client_secret 长度: {len(client_secret)}")

        resp = _requests.post(
            f"https://{shop_url}/admin/oauth/access_token",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
            },
            timeout=30,
        )
        print(f"换票 HTTP: {resp.status_code}")
        try:
            payload = resp.json()
        except ValueError:
            print(f"非 JSON 响应: {resp.text[:200]}")
            return

        if not payload.get("access_token"):
            print(f"换票失败: {payload.get('error')} / {payload.get('error_description')}")
            return

        token = payload["access_token"]
        print(f"换票成功，token 长度 {len(token)}，有效期 {payload.get('expires_in')} 秒，"
              f"scopes={payload.get('scope')}")

        graphql_resp = _requests.post(
            f"https://{shop_url}/admin/api/2026-07/graphql.json",
            headers={"X-Shopify-Access-Token": token, "Content-Type": "application/json"},
            json={"query": THEIRS_QUERY, "variables": {"id": gid_for_order(order.shopify_order_id)}},
            timeout=30,
        )
        body = graphql_resp.json()
        if body.get("errors"):
            print("查询失败:", json.dumps(body["errors"][:1], ensure_ascii=False)[:400])
            return
        print("✅ 查询成功:")
        print(json.dumps(body.get("data"), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if "--shop" in sys.argv:
        probe_shop(create_app())
    elif "--oauth" in sys.argv:
        raw = [item for item in sys.argv[2:] if item.isdigit()]
        if not raw:
            print("用法: python scripts/probe_order_contact.py --oauth <本地订单ID>")
            sys.exit(1)
        probe_oauth(create_app(), int(raw[0]))
    elif "--theirs" in sys.argv:
        raw = [item for item in sys.argv[2:] if item.isdigit()]
        if not raw:
            print("用法: python scripts/probe_order_contact.py --theirs <本地订单ID>")
            sys.exit(1)
        probe_theirs(create_app(), int(raw[0]))
    elif "--reduced" in sys.argv:
        raw = [item for item in sys.argv[2:] if item.isdigit()]
        if not raw:
            print("用法: python scripts/probe_order_contact.py --reduced <本地订单ID>")
            sys.exit(1)
        probe_reduced(create_app(), int(raw[0]))
    else:
        main()
