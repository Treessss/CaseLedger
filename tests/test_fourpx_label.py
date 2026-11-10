"""A physical outbound scan must accept whichever code is actually printed on the label.

面单形态不统一：多数只有一个一维码，而且就是运单号；老格式才是
「运单号 + 另一个打印条码」两串编号。两串都要认，否则只存了运单号的面单
（预申报建单、还没下载过 PDF）会永远扫不出库。
"""

import pytest
from flask import g

from app import db
from app.models import Order, OrderItem, ShippingParcel, Shop
from app.services.fourpx_label_service import get_parcel_label
from app.utils.errors import ValidationError


class FakeLabelClient:
    data = {
        "label_barcode": "PRINTED-BARCODE-1",
        "child_label_barcode": [],
        "label_url_info": {"logistics_label": "https://bss-fss.4px.com/labels/test.pdf"},
    }

    def __init__(self, key, secret, *, environment):
        assert (key, secret, environment) == ("test-key", "test-secret", "test")

    def get_label(self, request_no):
        assert request_no == "4PX-TRACKING-1"
        return self.data


def _prepare(shop_ids):
    shop = db.session.get(Shop, shop_ids["a"])
    shop.set_fourpx_credentials("test-key", "test-secret")
    order = Order.query.filter_by(shop_id=shop.id).first()
    parcel = ShippingParcel(shop_id=shop.id, order_id=order.id,
                            label_number="4PX-TRACKING-1", source="4px")
    db.session.add(parcel)
    db.session.commit()
    return order.id, parcel.id


def _make_ready(app, client, headers, shop_id, *, line_item_id="fourpx-ready-line"):
    """把店铺第一张订单做成「商品已备齐、可以出库」，好让扫码走到出库那一步。"""
    with app.app_context():
        order = Order.query.filter_by(shop_id=shop_id).order_by(Order.id).first()
        order.fulfillment_status = "unfulfilled"
        db.session.add(OrderItem(
            shop_id=shop_id, order_id=order.id, shopify_line_item_id=line_item_id,
            shopify_variant_id="fourpx-ready-variant", title="Case",
            variant_title="Blue", quantity=1,
        ))
        db.session.commit()
        order_id = order.id
    created = client.post("/api/purchase-lists", headers=headers,
                          json={"shop_id": shop_id, "order_ids": [order_id]}).get_json()["data"]
    client.post(f"/api/purchase-lists/{created['id']}/confirm", headers=headers)
    client.post(f"/api/purchase-lists/{created['id']}/receive", headers=headers,
                json={"items": [{"group_key": created["items"][0]["group_key"],
                                 "received_quantity": 1, "damaged_quantity": 0}]})
    return order_id


def test_fetch_label_persists_physical_barcode_not_pdf_url(app, shop_ids):
    with app.app_context():
        order_id, parcel_id = _prepare(shop_ids)
        with app.test_request_context():
            g.current_user = {"authority": "admin"}
            result = get_parcel_label(order_id, parcel_id, client_factory=FakeLabelClient)
            assert result["label_barcode"] == "PRINTED-BARCODE-1"
            assert result["pdf_url"].endswith("test.pdf")
            assert db.session.get(ShippingParcel, parcel_id).label_barcode == "PRINTED-BARCODE-1"
            assert "pdf_url" not in db.session.get(ShippingParcel, parcel_id).to_dict()
            assert get_parcel_label(order_id, parcel_id, client_factory=FakeLabelClient)["label_barcode"] == result["label_barcode"]


@pytest.mark.parametrize("bad_data", [
    {"label_barcode": "", "label_url_info": {"logistics_label": "https://bss-fss.4px.com/a.pdf"}},
    {"label_barcode": "BAR-1", "child_label_barcode": ["CHILD"],
     "label_url_info": {"logistics_label": "https://bss-fss.4px.com/a.pdf"}},
    {"label_barcode": "BAR-1", "label_url_info": {"logistics_label": "https://evil.example/a.pdf"}},
])
def test_rejects_incomplete_or_untrusted_label(app, shop_ids, bad_data):
    with app.app_context():
        order_id, parcel_id = _prepare(shop_ids)
        FakeLabelClient.data = bad_data
        try:
            with app.test_request_context():
                g.current_user = {"authority": "admin"}
                with pytest.raises(ValidationError):
                    get_parcel_label(order_id, parcel_id, client_factory=FakeLabelClient)
            assert db.session.get(ShippingParcel, parcel_id).label_barcode is None
        finally:
            FakeLabelClient.data = {
                "label_barcode": "PRINTED-BARCODE-1", "child_label_barcode": [],
                "label_url_info": {"logistics_label": "https://bss-fss.4px.com/labels/test.pdf"},
            }


def test_tracking_number_can_be_scanned_when_label_has_no_barcode(app, client, admin_headers, shop_ids):
    """只有一个一维码的 4PX 面单：那个码就是运单号，必须能扫出库。

    线上有 135 张待发货订单卡在「预申报建了面单、但没下载过 PDF」，
    面单里只有运单号、没有条码，旧规则直接判定"找不到面单"，根本扫不动。
    """
    order_id = _make_ready(app, client, admin_headers, shop_ids["a"])
    with app.app_context():
        db.session.add(ShippingParcel(shop_id=shop_ids["a"], order_id=order_id,
                                      label_number="4PX3003222062235CN", source="4px"))
        db.session.commit()

    response = client.post(f"/api/orders/{order_id}/scan-outbound", headers=admin_headers,
                           json={"shipping_label_number": "4PX3003222062235CN"})
    assert response.status_code == 200
    assert response.get_json()["data"]["warehouse_status"] == "shipped"


def test_printed_barcode_still_scans_when_it_differs_from_tracking_number(app, client, admin_headers, shop_ids):
    """老格式 4PX 面单（运单号 OLS…、条码 GV…）：扫实体条码依然要能出库。"""
    order_id = _make_ready(app, client, admin_headers, shop_ids["a"], line_item_id="fourpx-old-line")
    with app.app_context():
        db.session.add(ShippingParcel(shop_id=shop_ids["a"], order_id=order_id,
                                      label_number="OLS3060230965644",
                                      label_barcode="GV26CAA0U074350559", source="4px"))
        db.session.commit()

    response = client.post(f"/api/orders/{order_id}/scan-outbound", headers=admin_headers,
                           json={"shipping_label_number": "GV26CAA0U074350559"})
    assert response.status_code == 200
    assert response.get_json()["data"]["warehouse_status"] == "shipped"


def test_placeholder_barcode_is_replaced_at_print_time(app, shop_ids):
    with app.app_context():
        order_id, parcel_id = _prepare(shop_ids)
        parcel = db.session.get(ShippingParcel, parcel_id)
        # 建单时只写了运单号占位，不是真实打印条码
        parcel.label_barcode = parcel.label_number
        db.session.commit()
        with app.test_request_context():
            g.current_user = {"authority": "admin"}
            result = get_parcel_label(order_id, parcel_id, client_factory=FakeLabelClient)
        assert result["label_barcode"] == "PRINTED-BARCODE-1"
        db.session.expire_all()
        assert db.session.get(ShippingParcel, parcel_id).label_barcode == "PRINTED-BARCODE-1"
