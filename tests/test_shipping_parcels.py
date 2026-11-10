"""A multi-parcel order completes only after every registered label is scanned."""

from app import db
from app.models import Order, OrderItem, ShippingParcel


def _ready_order(app, client, headers, shop_id):
    with app.app_context():
        order = Order.query.filter_by(shop_id=shop_id).order_by(Order.id).first()
        order.fulfillment_status = "unfulfilled"
        db.session.add(OrderItem(
            shop_id=shop_id, order_id=order.id, shopify_line_item_id="parcel-test-line",
            shopify_variant_id="parcel-test-variant", title="Case", variant_title="Blue",
            quantity=1,
        ))
        db.session.commit()
        order_id = order.id
    created = client.post("/api/purchase-lists", headers=headers,
                          json={"shop_id": shop_id, "order_ids": [order_id]}).get_json()["data"]
    assert client.post(f"/api/purchase-lists/{created['id']}/confirm", headers=headers).status_code == 200
    assert client.post(f"/api/purchase-lists/{created['id']}/receive", headers=headers,
                       json={"items": [{"group_key": created["items"][0]["group_key"],
                                        "received_quantity": 1, "damaged_quantity": 0}]}).status_code == 200
    return order_id


def test_two_parcels_require_two_distinct_scans(app, client, admin_headers, shop_ids):
    order_id = _ready_order(app, client, admin_headers, shop_ids["a"])
    base = f"/api/orders/{order_id}"
    assert client.put(f"{base}/parcel-count", headers=admin_headers,
                      json={"expected_parcel_count": 2}).status_code == 200
    assert client.post(f"{base}/parcels", headers=admin_headers,
                       json={"shipping_label_number": "4PX-A"}).status_code == 200
    assert client.post(f"{base}/scan-outbound", headers=admin_headers,
                       json={"shipping_label_number": "4PX-A"}).status_code == 400
    second = client.post(f"{base}/parcels", headers=admin_headers,
                         json={"shipping_label_number": "4PX-B"})
    assert second.status_code == 200
    parcels = second.get_json()["data"]["parcels"]
    assert len(parcels) == 2
    assert client.post(f"{base}/parcels", headers=admin_headers,
                       json={"shipping_label_number": "4PX-C"}).status_code == 400
    assert client.post(f"{base}/scan-outbound", headers=admin_headers,
                       json={"shipping_label_number": "4PX-WRONG"}).status_code == 400
    first_scan = client.post(f"{base}/scan-outbound", headers=admin_headers,
                             json={"shipping_label_number": "4PX-A"})
    assert first_scan.status_code == 200
    assert first_scan.get_json()["data"]["warehouse_status"] == "pending"
    assert first_scan.get_json()["data"]["scanned_parcel_count"] == 1
    assert client.post(f"{base}/scan-outbound", headers=admin_headers,
                       json={"shipping_label_number": "4PX-A"}).status_code == 400
    assert client.put(f"{base}/parcel-count", headers=admin_headers,
                      json={"expected_parcel_count": 1}).status_code == 400
    assert client.delete(f"{base}/parcels/{parcels[0]['id']}", headers=admin_headers).status_code == 400
    assert client.get(base, headers=admin_headers).get_json()["data"]["shipped_at"] is None
    last_scan = client.post(f"{base}/scan-outbound", headers=admin_headers,
                            json={"shipping_label_number": "4PX-B"})
    assert last_scan.status_code == 200
    assert last_scan.get_json()["data"]["warehouse_status"] == "shipped"
    assert last_scan.get_json()["data"]["scanned_parcel_count"] == 2
    with app.app_context():
        saved = ShippingParcel.query.filter_by(order_id=order_id).all()
        assert all(parcel.scanned_at and parcel.scanned_by_user_id for parcel in saved)


def test_single_label_compatibility_and_shop_scoped_uniqueness(app, client, admin_headers, shop_ids):
    order_id = _ready_order(app, client, admin_headers, shop_ids["a"])
    other_id = _ready_order(app, client, admin_headers, shop_ids["b"])
    base = f"/api/orders/{order_id}"
    assert client.put(f"{base}/shipping-label", headers=admin_headers,
                      json={"shipping_label_number": "4PX-ONE"}).status_code == 200
    assert client.put(f"/api/orders/{other_id}/shipping-label", headers=admin_headers,
                      json={"shipping_label_number": "4PX-ONE"}).status_code == 200
    assert client.post(f"{base}/scan-outbound", headers=admin_headers,
                       json={"shipping_label_number": "4PX-ONE"}).status_code == 200
    assert client.post(f"{base}/scan-outbound", headers=admin_headers,
                       json={"shipping_label_number": "4PX-ONE"}).status_code == 400


def test_shipping_queue_uses_local_stock_and_scans_by_label(app, client, admin_headers, sub_a_headers, shop_ids):
    order_id = _ready_order(app, client, admin_headers, shop_ids["a"])
    with app.app_context():
        db.session.get(Order, order_id).fulfillment_status = "fulfilled"
        db.session.commit()
    ready = client.get(f"/api/shipping/ready?shop_id={shop_ids['a']}", headers=sub_a_headers)
    assert ready.status_code == 200
    data = ready.get_json()["data"]
    assert data["summary"]["ready_orders"] == 1
    assert data["summary"]["awaiting_labels"] == 1
    assert data["orders"][0]["id"] == order_id
    assert client.get(f"/api/shipping/ready?shop_id={shop_ids['b']}", headers=sub_a_headers).status_code == 403
    assert client.post(f"/api/orders/{order_id}/parcels", headers=admin_headers,
                       json={"shipping_label_number": "QUEUE-1"}).status_code == 200
    assert client.post("/api/shipping/scan", headers=sub_a_headers,
                       json={"shop_id": shop_ids["b"], "shipping_label_number": "QUEUE-1"}).status_code == 403
    assert client.post("/api/shipping/scan", headers=sub_a_headers,
                       json={"shop_id": shop_ids["a"], "shipping_label_number": "WRONG"}).status_code == 400
    result = client.post("/api/shipping/scan", headers=sub_a_headers,
                         json={"shop_id": shop_ids["a"], "shipping_label_number": "QUEUE-1"})
    assert result.status_code == 200
    assert result.get_json()["data"]["warehouse_status"] == "shipped"
    assert result.get_json()["data"]["order_number"]
    assert client.get(f"/api/shipping/ready?shop_id={shop_ids['a']}",
                      headers=sub_a_headers).get_json()["data"]["summary"]["ready_orders"] == 0


def test_fourpx_scan_accepts_printed_barcode(app, client, admin_headers, shop_ids):
    """老格式 4PX 面单：运单号和条码是两串编号，扫实体条码要能出库。"""
    order_id = _ready_order(app, client, admin_headers, shop_ids["a"])
    with app.app_context():
        db.session.add(ShippingParcel(shop_id=shop_ids["a"], order_id=order_id,
                                      label_number="4PX-TRACKING", label_barcode="BARCODE-PRINTED",
                                      source="4px"))
        db.session.commit()
    base = {"shop_id": shop_ids["a"]}
    right = client.post("/api/shipping/scan", headers=admin_headers,
                        json={**base, "shipping_label_number": "BARCODE-PRINTED"})
    assert right.status_code == 200
    assert right.get_json()["data"]["warehouse_status"] == "shipped"


def test_fourpx_scan_accepts_tracking_number_when_label_has_one_code(app, client, admin_headers, shop_ids):
    """多数 4PX 面单只有一个一维码，而且就是运单号——扫这个码必须能出库。

    以前只认 label_barcode，导致「预申报建了面单、还没下载过 PDF」的订单
    （label_barcode 为空）永远扫不出库。实测线上有 135 张待发货订单卡在这里。
    """
    order_id = _ready_order(app, client, admin_headers, shop_ids["a"])
    with app.app_context():
        db.session.add(ShippingParcel(
            shop_id=shop_ids["a"], order_id=order_id,
            label_number="4PX3003222062235CN", label_barcode=None, source="4px",
        ))
        db.session.commit()

    lookup = client.get(
        f"/api/shipping/packing-lookup?shop_id={shop_ids['a']}&code=4PX3003222062235CN",
        headers=admin_headers,
    ).get_json()["data"]
    assert lookup["outbound"]["code_matches_label"] is True

    scan = client.post("/api/shipping/scan", headers=admin_headers,
                       json={"shop_id": shop_ids["a"], "shipping_label_number": "4PX3003222062235CN"})
    assert scan.status_code == 200
    assert scan.get_json()["data"]["warehouse_status"] == "shipped"
