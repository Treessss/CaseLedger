"""GraphQL order/product sync preserves the old persistence contract."""

from datetime import datetime, timedelta

from app import db
from app.models import InventoryReservation, Order, OrderItem, Payment, Product, Shop
from app.services import order_service, shopify_graphql, shopify_service
from app.services.order_service import get_order
from app.utils.shopify_resources import extract_line_items


def test_graphql_order_sync_maps_money_items_and_transactions(app, monkeypatch):
    node = {
        "id": "gid://shopify/Order/9001", "name": "#9001",
        "createdAt": "2026-09-25T10:00:00Z", "updatedAt": "2026-09-25T10:01:00Z",
        "currencyCode": "USD", "presentmentCurrencyCode": "HKD",
        "displayFinancialStatus": "PAID", "displayFulfillmentStatus": "UNFULFILLED",
        "note": "门口放置", "email": "buyer@example.com", "phone": None,
        "shippingAddress": None, "customer": None,
        "currentTotalPriceSet": {"shopMoney": {"amount": "20.00", "currencyCode": "USD"}},
        "currentSubtotalPriceSet": {"shopMoney": {"amount": "20.00", "currencyCode": "USD"}},
        "currentTotalTaxSet": {"shopMoney": {"amount": "0.00", "currencyCode": "USD"}},
        "totalShippingPriceSet": {"shopMoney": {"amount": "0.00", "currencyCode": "USD"}},
        "lineItems": {"nodes": [{
            "id": "gid://shopify/LineItem/12", "title": "Case", "variantTitle": "Blue",
            "sku": "CASE-BLUE", "vendor": "CaseLedger", "quantity": 1,
            "originalUnitPriceSet": {"shopMoney": {"amount": "20.00", "currencyCode": "USD"}},
            "discountedTotalSet": {"shopMoney": {"amount": "20.00", "currencyCode": "USD"}},
            "variant": {"id": "gid://shopify/ProductVariant/22", "product": {"id": "gid://shopify/Product/21"}},
        }]},
        "transactions": [{
            "id": "gid://shopify/OrderTransaction/30", "kind": "SALE", "status": "SUCCESS",
            "gateway": "shopify_payments", "processedAt": "2026-09-25T10:00:30Z",
            "amountSet": {
                "shopMoney": {"amount": "20.00", "currencyCode": "USD"},
                "presentmentMoney": {"amount": "171.00", "currencyCode": "HKD"},
            },
        }],
        "fulfillments": [],
    }
    assert shopify_service.graphql_order_to_rest({**node, "cancelledAt": "2026-09-25T10:02:00Z"})["cancelled_at"] == "2026-09-25T10:02:00Z"
    monkeypatch.setattr(shopify_service, "fetch_orders_page", lambda *args, **kwargs: ([node], {"hasNextPage": False}))
    monkeypatch.setattr(shopify_service.ShopifyService, "_enrich_order_details", lambda *args: None)

    with app.app_context():
        shop = Shop.query.filter_by(shop_url="shop-a.myshopify.com").one()
        service = shopify_service.ShopifyService()
        service.shop_id = shop.id
        service.app = app
        result = service.sync_orders(days_back=2, limit=1)
        assert result == {"total_fetched": 1, "new_orders": 1, "updated_orders": 0, "errors": 0}
        order = Order.query.filter_by(shop_id=shop.id, shopify_order_id="9001").one()
        assert order.note == "门口放置"
        assert order.currency == "USD"
        assert order.presentment_currency == "HKD"
        assert order.order_date == datetime(2026, 9, 25, 10)
        assert order.created_at == datetime(2026, 9, 25, 10)
        assert len(order.items) == 1
        payment = Payment.query.filter_by(order_id=order.id).one()
        assert payment.currency == "HKD"

        # Historical imports can contain Beijing wall-clock timestamps. The
        # scheduled updated-order sync must restore Shopify's UTC timestamp,
        # not move an already repaired order forward by another eight hours.
        order.order_date += timedelta(hours=8)
        order.created_at += timedelta(hours=8)
        db.session.commit()
        normalized = service._normalize_graphql_orders(shop, [node])
        monkeypatch.setattr(service, "_fetch_updated_graphql_orders", lambda _since: normalized)
        assert service.sync_recent_orders(hours=24) == {
            "total_fetched": 1, "new_orders": 0, "updated_orders": 1, "errors": 0,
        }
        db.session.refresh(order)
        assert order.order_date == datetime(2026, 9, 25, 10)
        assert order.created_at == datetime(2026, 9, 25, 10)

        service.sync_recent_orders(hours=24)
        db.session.refresh(order)
        assert order.order_date == datetime(2026, 9, 25, 10)

        node["cancelledAt"] = "2026-09-25T10:03:00Z"
        normalized = service._normalize_graphql_orders(shop, [node])
        monkeypatch.setattr(service, "_fetch_updated_graphql_orders", lambda _since: normalized)
        service.sync_recent_orders(hours=24)
        db.session.refresh(order)
        assert order.financial_status == "paid"
        assert order.cancelled_at == datetime(2026, 9, 25, 10, 3)


def test_order_sync_uses_variant_image_then_own_product_image():
    variant = {
        "id": "gid://shopify/ProductVariant/22",
        "media": {"nodes": [{"preview": {"image": {"url": "https://cdn.shopify.com/variant.jpg"}}}]},
        "product": {"id": "gid://shopify/Product/21", "featuredMedia": {"preview": {"image": {"url": "https://cdn.shopify.com/product.jpg"}}}},
    }
    assert shopify_graphql.line_item_image(variant) == "https://cdn.shopify.com/variant.jpg"
    variant["media"] = {"nodes": []}
    assert shopify_graphql.line_item_image(variant) == "https://cdn.shopify.com/product.jpg"
    mapped = shopify_service.graphql_order_to_rest({
        "lineItems": {"nodes": [{"id": "gid://shopify/LineItem/12", "variant": variant}]}
    })
    assert mapped["line_items"][0]["image_url"] == "https://cdn.shopify.com/product.jpg"


def test_product_image_backfill_paginates_and_falls_back_to_product(monkeypatch):
    calls = []

    def fake_execute(_shop, _query, variables):
        calls.append(variables)
        if variables["after"] is None:
            return {"product": {
                "featuredMedia": {"preview": {"image": {"url": "https://cdn.shopify.com/product.jpg"}}},
                "variants": {
                    "nodes": [{"id": "gid://shopify/ProductVariant/22", "media": {"nodes": []}}],
                    "pageInfo": {"hasNextPage": True, "endCursor": "next"},
                },
            }}
        return {"product": {
            "featuredMedia": {"preview": {"image": {"url": "https://cdn.shopify.com/product.jpg"}}},
            "variants": {
                "nodes": [{"id": "gid://shopify/ProductVariant/23", "media": {"nodes": [{"preview": {"image": {"url": "https://cdn.shopify.com/variant.jpg"}}}]}}],
                "pageInfo": {"hasNextPage": False},
            },
        }}

    monkeypatch.setattr(shopify_graphql, "_execute", fake_execute)
    assert shopify_graphql.fetch_product_images(object(), "21") == {
        "22": "https://cdn.shopify.com/product.jpg",
        "23": "https://cdn.shopify.com/variant.jpg",
    }
    assert calls == [
        {"id": "gid://shopify/Product/21", "after": None},
        {"id": "gid://shopify/Product/21", "after": "next"},
    ]


def test_variant_image_batch_ignores_deleted_variants(monkeypatch):
    def fake_execute(_shop, _query, variables):
        assert variables == {"ids": ["gid://shopify/ProductVariant/22", "gid://shopify/ProductVariant/23"]}
        return {"nodes": [
            {"id": "gid://shopify/ProductVariant/22", "media": {"nodes": []},
             "product": {"featuredMedia": {"preview": {"image": {"url": "https://cdn.shopify.com/product.jpg"}}}}},
            None,
        ]}

    monkeypatch.setattr(shopify_graphql, "_execute", fake_execute)
    assert shopify_graphql.fetch_variant_images(object(), ["22", "23"]) == {
        "22": "https://cdn.shopify.com/product.jpg",
    }

def test_graphql_product_sync_keeps_existing_cost(app, monkeypatch):
    node = {
        "id": "gid://shopify/Product/200", "title": "Case", "productType": "phone_case",
        "vendor": "CaseLedger", "status": "ACTIVE",
        "variants": {"nodes": [{
            "id": "gid://shopify/ProductVariant/201", "title": "Blue", "sku": "B",
            "price": "12.50", "inventoryQuantity": 4,
        }]},
    }
    monkeypatch.setattr(shopify_service, "fetch_products_page", lambda *args, **kwargs: ([node], {"hasNextPage": False}))
    with app.app_context():
        shop = Shop.query.filter_by(shop_url="shop-a.myshopify.com").one()
        service = shopify_service.ShopifyService()
        service.shop_id = shop.id
        result = service.sync_products(limit=1)
        assert result["new_products"] == 1
        product = Product.query.filter_by(shop_id=shop.id, shopify_variant_id="201").one()
        product.cost = 4
        from app import db
        db.session.commit()
        result = service.sync_products(limit=1)
        assert result["updated_products"] == 1
        assert product.cost == 4


def test_product_sync_default_reads_beyond_250_products(app, monkeypatch):
    calls = []

    def page(shop, first, after):
        offset = int(after or 0)
        calls.append(offset)
        nodes = [{"id": f"gid://shopify/Product/{i}", "title": f"Case {i}",
                  "status": "ACTIVE", "variants": {"nodes": []}}
                 for i in range(offset, min(offset + first, 251))]
        end = offset + len(nodes)
        return nodes, {"hasNextPage": end < 251, "endCursor": str(end)}

    monkeypatch.setattr(shopify_service, 'fetch_products_page', page)
    with app.app_context():
        service = shopify_service.ShopifyService()
        service.shop_id = Shop.query.first().id
        assert service.sync_products()['total_fetched'] == 251
        assert calls[-1] == 250


def test_edited_order_uses_current_items_and_releases_removed_reservation(app, monkeypatch):
    node = {
        "id": "gid://shopify/Order/10942", "name": "#10942",
        "lineItems": {"nodes": [
            {
                "id": "gid://shopify/LineItem/old", "title": "Case",
                "variantTitle": "Mirror Lime Pink / iPhone 17 Pro",
                "quantity": 1, "currentQuantity": 0,
                "originalUnitPriceSet": {"shopMoney": {"amount": "29.99"}},
                "discountedTotalSet": {"shopMoney": {"amount": "29.99"}},
                "priceAfterAllDiscountsBeforeTaxesSet": {"shopMoney": {"amount": "0.00"}},
            },
            {
                "id": "gid://shopify/LineItem/new", "title": "Case",
                "variantTitle": "Mirror Mint Lilac / iPhone 16 Pro",
                "quantity": 1, "currentQuantity": 1,
                "originalUnitPriceSet": {"shopMoney": {"amount": "29.99"}},
                "discountedTotalSet": {"shopMoney": {"amount": "29.99"}},
                "priceAfterAllDiscountsBeforeTaxesSet": {"shopMoney": {"amount": "29.99"}},
            },
        ]},
    }
    rest = shopify_service.graphql_order_to_rest(node)
    assert [(row["id"], row["quantity"]) for row in rest["line_items"]] == [("old", 0), ("new", 1)]

    with app.app_context():
        shop = Shop.query.filter_by(shop_url="shop-a.myshopify.com").one()
        order = Order.query.filter_by(shop_id=shop.id).first()
        old = OrderItem(shop_id=shop.id, order_id=order.id, shopify_line_item_id="old", quantity=1)
        gone = OrderItem(shop_id=shop.id, order_id=order.id, shopify_line_item_id="gone", quantity=1)
        db.session.add_all([old, gone])
        db.session.flush()
        db.session.add(InventoryReservation(
            shop_id=shop.id, order_id=order.id, order_item_id=old.id,
            group_key='["variant", "old"]', quantity=1,
        ))
        db.session.commit()

        service = shopify_service.ShopifyService()
        service.shop_id = shop.id
        service._sync_order_items(order, extract_line_items(rest))
        db.session.commit()

        assert {item.shopify_line_item_id: item.quantity for item in order.items} == {
            "old": 0, "gone": 0, "new": 1,
        }
        assert InventoryReservation.query.filter_by(order_item_id=old.id).count() == 0
        monkeypatch.setattr(order_service, "can_access_shop", lambda _shop_id: True)
        assert [item["variant_title"] for item in get_order(order.id)["line_items"]] == [
            "Mirror Mint Lilac / iPhone 16 Pro"
        ]


def test_recent_sync_uses_updated_orders_instead_of_created_orders(app, monkeypatch):
    seen = []

    def execute(_shop, query, variables):
        seen.append((query, variables))
        return {"orders": {"nodes": [], "pageInfo": {"hasNextPage": False}}}

    monkeypatch.setattr(shopify_graphql, "_execute", execute)
    with app.app_context():
        shop = Shop.query.filter_by(shop_url="shop-a.myshopify.com").one()
        service = shopify_service.ShopifyService()
        service.shop_id = shop.id
        service.app = app
        result = service.sync_recent_orders(hours=24)
    assert result["total_fetched"] == 0
    assert "sortKey: UPDATED_AT" in seen[0][0]
    assert seen[0][1]["query"].startswith("updated_at:>=")
    assert seen[0][1]["query"].endswith("Z")
