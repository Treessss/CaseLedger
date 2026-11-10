"""Shopify tracking writeback never substitutes for a warehouse scan."""

from app import db
from app.models import Order, ShippingParcel, Shop
from app.services import shopify_fulfillment_service as service


def test_live_tracking_lookup_ignores_cancelled_fulfillments_and_checks_order_ids(app, shop_ids, monkeypatch):
    with app.app_context():
        shop = db.session.get(Shop, shop_ids["a"])

        def fake_execute(selected_shop, query, variables):
            assert selected_shop.id == shop.id
            assert "nodes(ids: $ids)" in query
            return {"nodes": [{"id": variables["ids"][0], "fulfillments": [
                {"status": "CANCELLED", "trackingInfo": [{"number": "OLD"}]},
                {"status": "SUCCESS", "trackingInfo": [{"number": "CURRENT"}]},
            ]}]}

        monkeypatch.setattr(service.shopify_graphql, "_execute", fake_execute)
        assert service.fetch_tracking_by_order_ids(shop, ["123", "123"]) == {
            "gid://shopify/Order/123": {"CURRENT"},
        }


def _labels(app, shop_id):
    with app.app_context():
        order = Order.query.filter_by(shop_id=shop_id).first()
        order.expected_parcel_count = 2
        order.warehouse_status = "pending"
        db.session.add_all([
            ShippingParcel(shop_id=shop_id, order_id=order.id, label_number="4PX-1", source="4px"),
            ShippingParcel(shop_id=shop_id, order_id=order.id, label_number="4PX-2", source="4px"),
        ])
        db.session.commit()
        return order.id


def test_writeback_creates_one_fulfillment_with_two_tracking_numbers(app, client, admin_headers, shop_ids, monkeypatch):
    order_id = _labels(app, shop_ids["a"])
    sent = []
    monkeypatch.setattr(service.shopify_graphql, "_execute", lambda shop, query, variables: {
        "order": {"id": variables["id"], "fulfillments": [], "fulfillmentOrders": {
            "pageInfo": {"hasNextPage": False},
            "nodes": [{"id": "gid://shopify/FulfillmentOrder/1", "status": "OPEN",
                       "assignedLocation": {"location": {"id": "gid://shopify/Location/1"}},
                       "supportedActions": [{"action": "CREATE_FULFILLMENT"}]}],
        }},
    })

    def fake_mutate(shop, query, variables, key):
        sent.append((query, variables, key))
        return {"id": "gid://shopify/Fulfillment/999", "status": "SUCCESS",
                "trackingInfo": [{"company": "4PX", "number": number} for number in variables["fulfillment"]["trackingInfo"]["numbers"]]}

    monkeypatch.setattr(service, "_mutate_once", fake_mutate)
    response = client.post(f"/api/orders/{order_id}/shopify-writeback", headers=admin_headers)
    assert response.status_code == 200
    assert sent[0][2] == "fulfillmentCreate"
    assert sent[0][1]["fulfillment"]["notifyCustomer"] is True
    assert sent[0][1]["fulfillment"]["trackingInfo"]["numbers"] == ["4PX-1", "4PX-2"]
    assert response.get_json()["data"]["customer_notification_requested"] is True
    with app.app_context():
        order = db.session.get(Order, order_id)
        assert order.fulfillment_status == "fulfilled"
        assert order.warehouse_status == "pending"
        assert order.shipped_at is None
        assert order.fulfillments[0]["tracking_numbers"] == ["4PX-1", "4PX-2"]


def test_writeback_refuses_to_replace_different_remote_tracking(app, client, admin_headers, shop_ids, monkeypatch):
    order_id = _labels(app, shop_ids["a"])
    monkeypatch.setattr(service.shopify_graphql, "_execute", lambda shop, query, variables: {
        "order": {"id": variables["id"], "fulfillments": [{"id": "gid://shopify/Fulfillment/1",
            "status": "SUCCESS", "trackingInfo": [{"number": "OTHER-TRACKING"}]}]}
    })
    monkeypatch.setattr(service, "_mutate_once", lambda *args: (_ for _ in ()).throw(AssertionError("must not mutate")))
    response = client.post(f"/api/orders/{order_id}/shopify-writeback", headers=admin_headers)
    assert response.status_code == 400
    assert "不同运单号" in response.get_json()["message"]


def test_writeback_updates_empty_tracking_and_requests_customer_notification(
    app, client, admin_headers, shop_ids, monkeypatch
):
    order_id = _labels(app, shop_ids["a"])
    sent = []
    monkeypatch.setattr(service.shopify_graphql, "_execute", lambda shop, query, variables: {
        "order": {"id": variables["id"], "fulfillments": [{
            "id": "gid://shopify/Fulfillment/1", "status": "SUCCESS", "trackingInfo": [],
        }]},
    })

    def fake_mutate(shop, query, variables, key):
        sent.append((query, variables, key))
        return {"id": variables["fulfillmentId"], "status": "SUCCESS",
                "trackingInfo": [{"number": number} for number in variables["trackingInfoInput"]["numbers"]]}

    monkeypatch.setattr(service, "_mutate_once", fake_mutate)
    response = client.post(f"/api/orders/{order_id}/shopify-writeback", headers=admin_headers)
    assert response.status_code == 200
    assert sent[0][2] == "fulfillmentTrackingInfoUpdate"
    assert "notifyCustomer: true" in sent[0][0]
    assert response.get_json()["data"]["customer_notification_requested"] is True


def test_writeback_identical_remote_tracking_is_idempotent(app, client, admin_headers, shop_ids, monkeypatch):
    order_id = _labels(app, shop_ids["a"])
    monkeypatch.setattr(service.shopify_graphql, "_execute", lambda shop, query, variables: {
        "order": {"id": variables["id"], "fulfillments": [{"id": "gid://shopify/Fulfillment/1",
            "status": "SUCCESS", "trackingInfo": [{"number": "4PX-1"}, {"number": "4PX-2"}]}]}
    })
    monkeypatch.setattr(service, "_mutate_once", lambda *args: (_ for _ in ()).throw(AssertionError("must not mutate")))
    response = client.post(f"/api/orders/{order_id}/shopify-writeback", headers=admin_headers)
    assert response.status_code == 200
    assert response.get_json()["data"]["status"] == "already_fulfilled"
    assert response.get_json()["data"]["customer_notification_requested"] is False
    with app.app_context():
        assert db.session.get(Order, order_id).warehouse_status == "pending"


def test_writeback_requires_all_labels(app, client, admin_headers, shop_ids, monkeypatch):
    with app.app_context():
        order = Order.query.filter_by(shop_id=shop_ids["a"]).first()
        order.expected_parcel_count = 2
        db.session.commit()
        order_id = order.id
    monkeypatch.setattr(service.shopify_graphql, "_execute", lambda *args: (_ for _ in ()).throw(AssertionError("must not query")))
    response = client.post(f"/api/orders/{order_id}/shopify-writeback", headers=admin_headers)
    assert response.status_code == 400
    assert "尚未齐全" in response.get_json()["message"]
