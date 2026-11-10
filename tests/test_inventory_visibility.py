"""Only stocked variants appear in the main inventory view; manual additions stay shop-scoped."""

from datetime import datetime

from app import db
from app.models import Order, OrderItem
from app.services.purchase_service import _group_key
from json import dumps


def _item(shop_id, suffix, *, product_id=None, title=None, variant_title="Blue / iPhone 18 Pro"):
    order = Order(
        shop_id=shop_id,
        shopify_order_id=f"inventory-{suffix}",
        order_number=f"#INV-{suffix}",
        total_price=10,
        subtotal_price=10,
        currency="USD",
        financial_status="paid",
        fulfillment_status="unfulfilled",
        order_date=datetime.utcnow(),
    )
    item = OrderItem(
        shop_id=shop_id,
        shopify_line_item_id=f"line-{suffix}",
        shopify_product_id=product_id or f"product-{suffix}",
        shopify_variant_id=f"variant-{suffix}",
        title=title or f"Case {suffix}",
        variant_title=variant_title,
        quantity=1,
    )
    order.items.append(item)
    db.session.add(order)
    db.session.commit()
    return dumps(_group_key(item), ensure_ascii=False)


def test_manual_stock_addition_and_default_stock_filter(client, app, admin_headers, shop_ids):
    with app.app_context():
        key_a = _item(shop_ids["a"], "a")
        _item(shop_ids["b"], "b")

    url = f'/api/inventory?shop_id={shop_ids["a"]}'
    empty = client.get(url, headers=admin_headers).get_json()["data"]
    assert empty["items"][0]["purchase_needed_quantity"] == 1
    assert empty["summary"]["variant_count"] == 1

    catalog = client.get(f"{url}&include_zero=true", headers=admin_headers).get_json()["data"]
    assert len(catalog["items"]) == 1
    assert catalog["items"][0]["group_key"] == key_a

    added = client.post(
        "/api/inventory/adjustments",
        json={"shop_id": shop_ids["a"], "group_key": key_a, "quantity_delta": 3, "reason": "手动入库"},
        headers=admin_headers,
    )
    assert added.status_code == 201

    stocked = client.get(url, headers=admin_headers).get_json()["data"]
    assert stocked["summary"]["variant_count"] == 1
    assert stocked["items"][0]["on_hand_quantity"] == 3
    other = client.get(f'/api/inventory?shop_id={shop_ids["b"]}', headers=admin_headers).get_json()["data"]
    assert all(row["group_key"] != key_a for row in other["items"])
    assert other["summary"]["on_hand_quantity"] == 0

    removed = client.post(
        "/api/inventory/adjustments",
        json={"shop_id": shop_ids["a"], "group_key": key_a, "quantity_delta": -3, "reason": "盘点扣减"},
        headers=admin_headers,
    )
    assert removed.status_code == 201
    remaining = client.get(url, headers=admin_headers).get_json()["data"]
    assert remaining["summary"]["on_hand_quantity"] == 0
    assert remaining["summary"]["purchase_needed_quantity"] == 1


def test_stock_is_grouped_by_product_then_color_and_model(client, app, admin_headers, shop_ids):
    with app.app_context():
        keys = [
            _item(shop_ids["a"], "blue-16", product_id="same-product", title="Phone Case", variant_title="Blue / iPhone 16 Pro"),
            _item(shop_ids["a"], "blue-18", product_id="same-product", title="Phone Case", variant_title="Blue / iPhone 18 Pro"),
            _item(shop_ids["a"], "red-17", product_id="same-product", title="Phone Case", variant_title="Red / iPhone 17 Pro"),
            _item(shop_ids["a"], "other", title="Other Case"),
        ]
    for key in keys:
        assert client.post(
            "/api/inventory/adjustments",
            json={"shop_id": shop_ids["a"], "group_key": key, "quantity_delta": 2, "reason": "手动入库"},
            headers=admin_headers,
        ).status_code == 201
    # 入库后系统自动把货备给对应的缺货订单（各 1 件），不需要手工再分配

    url = f'/api/inventory?shop_id={shop_ids["a"]}&group_by_product=true'
    data = client.get(url, headers=admin_headers).get_json()["data"]
    assert data["pagination"]["total"] == 2
    assert data["summary"]["product_count"] == 2
    assert data["summary"]["variant_count"] == 4
    grouped = next(product for product in data["products"] if product["title"] == "Phone Case")
    assert grouped["variant_count"] == 3
    assert grouped["on_hand_quantity"] == 6
    # 三个变体入库后都自动备给了各自的缺货订单
    assert grouped["reserved_quantity"] == 0
    assert {order["order_number"]: order["quantity"] for order in grouped["occupied_orders"]} == {
        "#INV-blue-16": 1, "#INV-blue-18": 1, "#INV-red-17": 1,
    }
    assert [color["name"] for color in grouped["colors"]] == ["Blue", "Red"]
    assert [variant["model"] for variant in grouped["colors"][0]["variants"]] == ["iPhone 18 Pro", "iPhone 16 Pro"]

    first_page = client.get(f"{url}&page=1&per_page=1", headers=admin_headers).get_json()["data"]
    second_page = client.get(f"{url}&page=2&per_page=1", headers=admin_headers).get_json()["data"]
    assert len(first_page["products"]) == len(second_page["products"]) == 1
    assert {first_page["products"][0]["title"], second_page["products"][0]["title"]} == {"Phone Case", "Other Case"}

    searched = client.get(f"{url}&search=iPhone%2016", headers=admin_headers).get_json()["data"]
    assert searched["summary"]["variant_count"] == 1
    assert searched["products"][0]["colors"][0]["variants"][0]["model"] == "iPhone 16 Pro"
