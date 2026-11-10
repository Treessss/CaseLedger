"""A Shopify fulfillment and a saved label are not physical warehouse outbound."""

from app import db
from app.models import Order


def test_local_label_is_shop_scoped_and_does_not_ship(app, client, admin_headers, sub_a_headers, shop_ids):
    with app.app_context():
        orders = Order.query.filter_by(shop_id=shop_ids["a"]).order_by(Order.id).all()
        other = Order.query.filter_by(shop_id=shop_ids["b"]).first()
        first_id, second_id, other_id = orders[0].id, orders[1].id, other.id
        assert orders[0].fulfillment_status == "fulfilled"

    url = f"/api/orders/{first_id}/shipping-label"
    assert client.put(url, headers=admin_headers, json={"shipping_label_number": "4PX-001"}).status_code == 200
    detail = client.get(f"/api/orders/{first_id}", headers=admin_headers).get_json()["data"]
    assert detail["shipping_label_number"] == "4PX-001"
    assert detail["warehouse_status"] == "pending"
    assert detail["shipped_at"] is None

    assert client.put(
        f"/api/orders/{second_id}/shipping-label", headers=admin_headers,
        json={"shipping_label_number": "4PX-001"},
    ).status_code == 400
    assert client.put(
        f"/api/orders/{other_id}/shipping-label", headers=admin_headers,
        json={"shipping_label_number": "4PX-001"},
    ).status_code == 200
    assert client.put(
        f"/api/orders/{other_id}/shipping-label", headers=sub_a_headers,
        json={"shipping_label_number": "4PX-002"},
    ).status_code == 403

    scan_url = f"/api/orders/{first_id}/scan-outbound"
    assert client.post(scan_url, headers=admin_headers,
                       json={"shipping_label_number": "WRONG"}).status_code == 400
    assert client.post(scan_url, headers=admin_headers,
                       json={"shipping_label_number": "4PX-001"}).status_code == 400
    with app.app_context():
        assert db.session.get(Order, first_id).warehouse_status == "pending"


def test_warehouse_status_filter_is_independent_of_shopify(app, client, admin_headers, shop_ids):
    response = client.get("/api/orders?warehouse_status=pending", headers=admin_headers)
    assert response.status_code == 200
    orders = response.get_json()["data"]["orders"]
    assert any(order["shop_id"] == shop_ids["a"] and order["fulfillment_status"] == "fulfilled" for order in orders)
    assert client.get("/api/orders?warehouse_status=invalid", headers=admin_headers).status_code == 400


def test_label_lookup_and_scan_match_are_case_insensitive(app, client, admin_headers, shop_ids):
    from app.models import ShippingParcel
    from app.services.shipping_service import parcel_matches_code

    with app.app_context():
        order = Order.query.filter_by(shop_id=shop_ids["a"], warehouse_status="pending").first()
        order.shipping_label_number = "4px-lower-01"
        parcel = ShippingParcel(shop_id=shop_ids["a"], order_id=order.id,
                                label_number="4px-lower-01", label_barcode="4px-lower-barcode")
        db.session.add(parcel)
        db.session.commit()
        order_id, parcel_id = order.id, parcel.id

    for code in ("4PX-LOWER-01", "4px-lower-01"):
        response = client.get("/api/shipping/packing-lookup", headers=admin_headers,
                              query_string={"shop_id": shop_ids["a"], "code": code})
        assert response.status_code == 200, response.get_json()
        assert response.get_json()["data"]["id"] == order_id
    with app.app_context():
        parcel = db.session.get(ShippingParcel, parcel_id)
        assert parcel_matches_code(parcel, "4px-lower-barcode")
        assert parcel_matches_code(parcel, "4PX-LOWER-01")
