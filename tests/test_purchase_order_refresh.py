"""Purchase creation always reads Shopify, not stale local line items."""

import json
from datetime import datetime, timedelta

import pytest

from app import db
from app.models import InventoryReservation, Order, OrderItem, Product, PurchaseList
from app.services import purchase_order_refresh_service as refresh
from app.services import shopify_graphql


def _seed_orders(shop_id, count=2):
    orders = []
    for index in range(count):
        order = Order(
            shop_id=shop_id, shopify_order_id=str(91000 + index), order_number=str(12000 + index),
            total_price=30, subtotal_price=30, currency="USD", financial_status="paid",
            fulfillment_status="unfulfilled", order_date=datetime(2026, 9, 19),
            created_at=datetime(2026, 9, 19),
        )
        order.items = [OrderItem(shop_id=shop_id, shopify_line_item_id=str(81000 + index),
                                 shopify_variant_id="old", title="Old product", quantity=1, price=30)]
        db.session.add(order)
        orders.append(order)
    db.session.commit()
    return [order.id for order in orders]


def _node(shopify_id, number, variant="new", quantity=2):
    money = {"shopMoney": {"amount": "30", "currencyCode": "USD"}}
    return {
        "id": f"gid://shopify/Order/{shopify_id}", "name": f"#{number}",
        "createdAt": "2026-09-19T00:00:00Z", "updatedAt": "2026-10-09T01:00:00Z",
        "currencyCode": "USD", "displayFinancialStatus": "PAID",
        "displayFulfillmentStatus": "UNFULFILLED", "note": "Latest customer note",
        "currentTotalPriceSet": money, "currentSubtotalPriceSet": money,
        "totalShippingPriceSet": money,
        "lineItems": {"pageInfo": {"hasNextPage": False}, "nodes": [{
            "id": f"gid://shopify/LineItem/{shopify_id}", "title": "Changed product",
            "variantTitle": "Green / iPhone 17", "quantity": quantity, "currentQuantity": quantity,
            "originalUnitPriceSet": money,
            "variant": {"id": f"gid://shopify/ProductVariant/{variant}", "media": {
                "nodes": [{"preview": {"image": {"url": "https://example.com/variant.jpg"}}}]}},
        }]},
    }


def _mock_single_fetch(monkeypatch, fetch):
    def fetch_batch(shop, ids):
        return [fetch(shop, shopify_id, str(int(shopify_id) - 91000 + 12000)) for shopify_id in ids]

    monkeypatch.setattr(refresh, "fetch_orders_sync_by_ids", fetch_batch)


def test_every_selected_order_is_refreshed_before_snapshot(app, client, admin_headers, shop_ids, monkeypatch):
    ids = _seed_orders(shop_ids["a"])
    calls = []

    def fetch(shop, shopify_id, number):
        calls.append((shop.id, shopify_id))
        return _node(shopify_id, number)

    _mock_single_fetch(monkeypatch, fetch)
    response = client.post("/api/purchase-lists", headers=admin_headers,
                           json={"shop_id": shop_ids["a"], "order_ids": ids})
    assert response.status_code == 201, response.get_json()
    data = response.get_json()["data"]
    assert len(calls) == 2
    assert data["total_quantity"] == 4
    assert {item["shopify_variant_id"] for item in data["items"]} == {"new"}
    for order_id in ids:
        order = db.session.get(Order, order_id)
        assert order.note == "Latest customer note"
        assert next(item for item in order.items if item.shopify_variant_id == "old").quantity == 0
        assert next(item for item in order.items if item.quantity).image_url.endswith("variant.jpg")
        assert order.created_at == datetime(2026, 9, 19)
        assert order.shopify_details_synced_at is not None


def test_existing_product_is_reused_when_graphql_only_has_quantity(
    app, client, admin_headers, shop_ids, monkeypatch,
):
    ids = _seed_orders(shop_ids["a"])
    product = Product(shop_id=shop_ids["a"], shopify_product_id="16110916960559",
                      shopify_variant_id="58448536076591", title="Existing", price=30, cost=5)
    db.session.add(product)
    db.session.commit()
    product_id = product.id
    from app.services.shopify_service import ShopifyService

    original_sync = ShopifyService._sync_product

    def assert_preloaded(service, line):
        assert (str(line.product_id), str(line.variant_id)) in service._prefetched_products
        return original_sync(service, line)

    monkeypatch.setattr(ShopifyService, "_sync_product", assert_preloaded)

    def fetch(shop, shopify_id, number):
        node = _node(shopify_id, number, variant="58448536076591")
        node["lineItems"]["nodes"][0]["variant"]["product"] = {
            "id": "gid://shopify/Product/16110916960559",
        }
        return node

    _mock_single_fetch(monkeypatch, fetch)
    response = client.post("/api/purchase-lists", headers=admin_headers,
                           json={"shop_id": shop_ids["a"], "order_ids": ids})
    assert response.status_code == 201, response.get_json()
    assert Product.query.filter_by(shopify_variant_id="58448536076591").count() == 1
    assert db.session.get(Product, product_id).cost == 5
    assert all(db.session.get(Order, order_id).product_cost == 10 for order_id in ids)


def test_product_cache_miss_checks_database_before_insert(app, shop_ids):
    from app.services.shopify_service import ShopifyService
    from app.utils.shopify_resources import WebhookResource

    product = Product(shop_id=shop_ids["a"], shopify_product_id="123",
                      shopify_variant_id="456", title="Existing", price=30, cost=7)
    db.session.add(product)
    db.session.commit()
    service = ShopifyService()
    service.shop_id = shop_ids["a"]
    service._prefetched_products = {}
    result = service._sync_product(WebhookResource({
        "product_id": "123", "variant_id": "456", "title": "Updated",
        "variant_title": "Blue", "sku": "sku", "price": "30", "vendor": "vendor",
    }))
    db.session.flush()
    assert result.id == product.id
    assert result.cost == 7
    assert service._prefetched_products[("123", "456")] is result
    assert Product.query.filter_by(shopify_variant_id="456").count() == 1


@pytest.mark.parametrize("failure", ["timeout", "wrong_id", "missing_items", "missing_cursor"])
def test_any_failed_or_incomplete_refresh_prevents_purchase(app, client, admin_headers, shop_ids, monkeypatch, failure):
    ids = _seed_orders(shop_ids["a"])

    def fetch(shop, shopify_id, number):
        node = _node(shopify_id, number)
        if number == "12001":
            if failure == "timeout":
                raise TimeoutError("Shopify timeout")
            if failure == "wrong_id":
                node["id"] = "gid://shopify/Order/other"
            if failure == "missing_items":
                del node["lineItems"]
            if failure == "missing_cursor":
                node["lineItems"]["pageInfo"] = {"hasNextPage": True}
        return node

    _mock_single_fetch(monkeypatch, fetch)
    response = client.post("/api/purchase-lists", headers=admin_headers,
                           json={"shop_id": shop_ids["a"], "order_ids": ids})
    assert response.status_code == 400
    assert "12001" in response.get_json()["message"]
    assert PurchaseList.query.count() == 0
    assert db.session.get(Order, ids[0]).items[0].title == "Old product"
    assert all(db.session.get(Order, order_id).shopify_details_synced_at is None for order_id in ids)


@pytest.mark.parametrize("status", ["REFUNDED", "cancelled"])
def test_latest_refund_or_cancellation_blocks_procurement(app, client, admin_headers, shop_ids, monkeypatch, status):
    ids = _seed_orders(shop_ids["a"], count=1)

    def fetch(shop, shopify_id, number):
        node = _node(shopify_id, number)
        if status == "cancelled":
            node["cancelledAt"] = "2026-10-09T00:00:00Z"
        else:
            node["displayFinancialStatus"] = status
        return node

    _mock_single_fetch(monkeypatch, fetch)
    response = client.post("/api/purchase-lists", headers=admin_headers,
                           json={"shop_id": shop_ids["a"], "order_ids": ids})
    assert response.status_code == 400
    assert PurchaseList.query.count() == 0


def test_cross_shop_selection_never_contacts_shopify(app, client, admin_headers, shop_ids, monkeypatch):
    ids = _seed_orders(shop_ids["b"], count=1)
    calls = []
    _mock_single_fetch(monkeypatch, lambda *_: calls.append(True))
    response = client.post("/api/purchase-lists", headers=admin_headers,
                           json={"shop_id": shop_ids["a"], "order_ids": ids})
    assert response.status_code == 403
    assert calls == []


def test_remaining_items_and_current_quantity_are_used(app, client, admin_headers, shop_ids, monkeypatch):
    ids = _seed_orders(shop_ids["a"], count=1)
    node = _node("91000", "12000", quantity=0)
    node["lineItems"]["pageInfo"] = {"hasNextPage": True, "endCursor": "next-page"}
    remaining = _node("92000", "12000", quantity=3)["lineItems"]["nodes"]
    _mock_single_fetch(monkeypatch, lambda *_: node)
    calls = []

    def execute(shop, query, variables):
        calls.append(variables)
        return {"order": {"lineItems": {"nodes": remaining, "pageInfo": {"hasNextPage": False}}}}

    monkeypatch.setattr(shopify_graphql, "_execute", execute)
    response = client.post("/api/purchase-lists", headers=admin_headers,
                           json={"shop_id": shop_ids["a"], "order_ids": ids})
    assert response.status_code == 201, response.get_json()
    assert response.get_json()["data"]["total_quantity"] == 3
    assert calls == [{"id": "gid://shopify/Order/91000", "after": "next-page"}]


def test_broken_remaining_page_blocks_purchase(app, client, admin_headers, shop_ids, monkeypatch):
    ids = _seed_orders(shop_ids["a"], count=1)
    node = _node("91000", "12000")
    node["lineItems"]["pageInfo"] = {"hasNextPage": True, "endCursor": "next-page"}
    _mock_single_fetch(monkeypatch, lambda *_: node)
    monkeypatch.setattr(shopify_graphql, "_execute", lambda *_: {"order": None})
    response = client.post("/api/purchase-lists", headers=admin_headers,
                           json={"shop_id": shop_ids["a"], "order_ids": ids})
    assert response.status_code == 400
    assert PurchaseList.query.count() == 0


def test_changed_variant_does_not_use_old_stock_or_purchase_allocations(app, client, admin_headers, shop_ids, monkeypatch):
    ids = _seed_orders(shop_ids["a"], count=1)
    item = db.session.get(Order, ids[0]).items[0]
    old_key = json.dumps(["variant", "old"])
    db.session.add(InventoryReservation(shop_id=shop_ids["a"], order_id=ids[0], order_item_id=item.id,
                                        group_key=old_key, quantity=1))
    db.session.add(PurchaseList(shop_id=shop_ids["a"], name="Old received purchase", status="received",
                                order_ids=ids, items=[], order_count=1, total_quantity=1, allocations=[{
                                    "order_item_id": item.id, "group_key": old_key,
                                    "quantity": 1, "received_quantity": 1,
                                }]))
    db.session.commit()
    node = _node("91000", "12000")
    node["lineItems"]["nodes"][0]["id"] = "gid://shopify/LineItem/81000"
    _mock_single_fetch(monkeypatch, lambda *_: node)
    response = client.post("/api/purchase-lists", headers=admin_headers,
                           json={"shop_id": shop_ids["a"], "order_ids": ids})
    assert response.status_code == 201, response.get_json()
    assert response.get_json()["data"]["total_quantity"] == 2
    assert InventoryReservation.query.count() == 0


def test_successful_details_are_reused_for_one_hour(app, client, admin_headers, shop_ids, monkeypatch):
    ids = _seed_orders(shop_ids["a"])
    calls = []

    def fetch_batch(shop, shopify_ids):
        calls.append(list(shopify_ids))
        return [_node(shopify_id, str(int(shopify_id) - 79000)) for shopify_id in shopify_ids]

    monkeypatch.setattr(refresh, "fetch_orders_sync_by_ids", fetch_batch)
    payload = {"shop_id": shop_ids["a"], "order_ids": ids}
    first = client.post("/api/purchase-lists", headers=admin_headers, json=payload)
    assert first.status_code == 201, first.get_json()
    times = [db.session.get(Order, order_id).shopify_details_synced_at for order_id in ids]
    second = client.post("/api/purchase-lists", headers=admin_headers, json=payload)
    assert second.status_code == 201, second.get_json()
    assert len(calls) == 1
    assert times == [db.session.get(Order, order_id).shopify_details_synced_at for order_id in ids]
    candidates = client.get(f"/api/purchase-candidates?shop_id={shop_ids['a']}", headers=admin_headers)
    assert all(row["shopify_details_synced_at"] for row in candidates.get_json()["data"]["orders"]
               if row["id"] in ids)


def test_only_expired_orders_are_in_remote_batches(app, client, admin_headers, shop_ids, monkeypatch):
    ids = _seed_orders(shop_ids["a"])
    fresh_time = datetime.utcnow() - timedelta(minutes=59)
    db.session.get(Order, ids[0]).shopify_details_synced_at = fresh_time
    db.session.get(Order, ids[1]).shopify_details_synced_at = datetime.utcnow() - timedelta(minutes=61)
    db.session.commit()
    calls = []

    def fetch_batch(shop, shopify_ids):
        calls.append(list(shopify_ids))
        return [_node(shopify_id, "12001") for shopify_id in shopify_ids]

    monkeypatch.setattr(refresh, "fetch_orders_sync_by_ids", fetch_batch)
    response = client.post("/api/purchase-lists", headers=admin_headers,
                           json={"shop_id": shop_ids["a"], "order_ids": ids})
    assert response.status_code == 201, response.get_json()
    assert calls == [["91001"]]
    assert response.get_json()["data"]["total_quantity"] == 3
    assert db.session.get(Order, ids[0]).shopify_details_synced_at == fresh_time


@pytest.mark.parametrize("age_seconds, expected", [(0, False), (3599, False), (3600, True), (3601, True), (-60, True)])
def test_detail_cache_expiry_boundary(age_seconds, expected):
    now = datetime(2026, 10, 9, 8)
    order = Order(shopify_details_synced_at=now - timedelta(seconds=age_seconds))
    assert refresh.needs_detail_refresh(order, now) is expected


def test_twelve_orders_use_three_batches(app, shop_ids, monkeypatch):
    ids = _seed_orders(shop_ids["a"], count=12)
    orders = Order.query.filter(Order.id.in_(ids)).order_by(Order.id).all()
    calls = []

    def fetch_batch(shop, shopify_ids):
        calls.append(list(shopify_ids))
        # The API need not return nodes in requested order.
        return [_node(shopify_id, str(int(shopify_id) - 79000)) for shopify_id in reversed(shopify_ids)]

    monkeypatch.setattr(refresh, "fetch_orders_sync_by_ids", fetch_batch)
    resources = refresh.fetch_purchase_orders(shop_ids["a"], orders)
    assert len(calls) == 3
    assert sorted(len(batch) for batch in calls) == [2, 5, 5]
    assert [order_id for order_id, _ in resources] == ids
