"""4PX 实际出账费用同步：真实计费重、明细与总费用。"""

import json
from datetime import datetime

from flask import current_app, g

from app import db
from app.models import FourPxFreight, Order, ShippingParcel, Shop
from app.services import fourpx_freight_service, order_service
from app.utils.errors import ValidationError


class FakeFreightClient:
    payload = {
        "4px_tracking_no": "4PX3003221955217CN",
        "ref_no": "11475",
        "charge_weight": "0.092",
        "currency": "CNY",
        "create_time": "2026-10-06 01:30:25",
        "subs": [
            {"fee_name": "挂号费", "fee_amount": "21.0", "currency": "CNY"},
            {"fee_name": "速递运费", "fee_amount": "5.8", "currency": "CNY"},
        ],
        "total_fee": "26.8",
    }

    def __init__(self, key, secret, *, environment):
        pass

    def get_freight(self, request_no):
        return json.dumps(FakeFreightClient.payload, ensure_ascii=False)


def _prepare(shop_ids):
    shop = db.session.get(Shop, shop_ids["a"])
    shop.set_fourpx_credentials("test-key", "test-secret")
    order = Order.query.filter_by(shop_id=shop.id).order_by(Order.id).first()
    order.warehouse_status = "shipped"
    parcel = ShippingParcel(shop_id=shop.id, order_id=order.id,
                            label_number="4PX3003221955217CN", source="4px")
    db.session.add(parcel)
    db.session.commit()
    return order


def test_sync_order_freight_stores_real_weight_and_price(app, shop_ids):
    with app.app_context():
        order = _prepare(shop_ids)
        with current_app.test_request_context():
            g.current_user = {"authority": "admin", "user_id": 1, "shop_ids": None, "permissions": []}
            first = fourpx_freight_service.sync_order_freight(order.id, client_factory=FakeFreightClient)
            assert first["charge_weight_kg"] == 0.092
            assert first["total_fee"] == 26.8
            assert first["currency"] == "CNY"
            assert [row["fee_name"] for row in first["subs"]] == ["挂号费", "速递运费"]
            assert first["billed_at"].startswith("2026-10-06")

            # 重复同步是更新而不是新增
            FakeFreightClient.payload["total_fee"] = "27.1"
            second = fourpx_freight_service.sync_order_freight(order.id, client_factory=FakeFreightClient)
            assert second["total_fee"] == 27.1
            assert FourPxFreight.query.count() == 1

            detail = order_service.get_order(order.id)
            assert detail["fourpx_freight"]["total_fee"] == 27.1


def test_sync_freight_requires_order_parcel_and_billed_data(app, shop_ids):
    with app.app_context():
        shop = db.session.get(Shop, shop_ids["a"])
        shop.set_fourpx_credentials("test-key", "test-secret")
        order = Order.query.filter_by(shop_id=shop.id).order_by(Order.id).first()
        db.session.commit()
        with current_app.test_request_context():
            g.current_user = {"authority": "admin", "user_id": 1, "shop_ids": None, "permissions": []}
            with pytest_raises_validation():
                fourpx_freight_service.sync_order_freight(order.id, client_factory=FakeFreightClient)

        class EmptyClient(FakeFreightClient):
            def get_freight(self, request_no):
                return None

        parcel = ShippingParcel(shop_id=shop.id, order_id=order.id, label_number="4PX-EMPTY", source="4px")
        db.session.add(parcel)
        db.session.commit()
        with current_app.test_request_context():
            g.current_user = {"authority": "admin", "user_id": 1, "shop_ids": None, "permissions": []}
            with pytest_raises_validation():
                fourpx_freight_service.sync_order_freight(order.id, client_factory=EmptyClient)


class pytest_raises_validation:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return exc_type is not None and issubclass(exc_type, ValidationError)
