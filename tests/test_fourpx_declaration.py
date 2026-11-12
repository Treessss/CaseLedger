"""4PX 预申报检查：规则选择、拦截原因和只读行为。"""

from datetime import datetime
from decimal import Decimal

import pytest
from flask import current_app, g

from app import db
from app.models import Order, OrderItem, ShippingParcel, Shop
from app.services import fourpx_declaration_service
from app.services.fourpx_client import FourPxError
from app.utils.errors import ValidationError


def _order(shop_id, number, country="US", postal="01028", phone="555-0100", title="Phone Case",
           quantity=1, price="12.99", warehouse_status="pending", financial_status="paid"):
    order = Order(
        shop_id=shop_id, shopify_order_id=f"gid://shopify/Order/{number}",
        order_number=str(number), customer_name="Buyer Name", customer_phone=phone,
        shipping_address={
            "name": "Jane Doe", "phone": phone, "address1": "1 Main St", "city": "Springfield",
            "province": "MA", "zip": postal, "country": "United States", "country_code": country,
            "email": "buyer@example.com",
        },
        total_price=Decimal(price), subtotal_price=Decimal(price), currency="USD",
        financial_status=financial_status, warehouse_status=warehouse_status,
        expected_parcel_count=1, order_date=datetime.utcnow(),
    )
    db.session.add(order)
    db.session.flush()
    db.session.add(OrderItem(
        shop_id=shop_id, order_id=order.id, shopify_line_item_id=f"{number}-0",
        title=title, sku=f"SKU-{number}", quantity=quantity, price=Decimal(price),
    ))
    db.session.commit()
    return order


def _check(shop_id, **kwargs):
    args = {"shop_id": shop_id, **kwargs}
    with current_app.test_request_context():
        g.current_user = {"authority": "admin", "user_id": 1}
        return fourpx_declaration_service.check(args)


def _row(result, number):
    return next(row for row in result["orders"] if row["order_number"] == str(number))


def test_us_single_item_uses_light_small_with_estimated_weight_and_value(app, shop_ids):
    with app.app_context():
        _order(shop_ids["a"], "1001")
        row = _row(_check(shop_ids["a"]), "1001")
        assert row["country"] == "US"
        assert row["product"]["code"] == "S5058"
        assert row["weight_grams"] == 120
        assert row["declared_value"] == 1.20
        assert row["declared_currency"] == "USD"
        assert row["declared_items"] == [{
            "name_cn": "手机壳", "name_en": "Phone case",
            "quantity": 1, "unit_price": 1.20, "amount": 1.20,
        }]
        assert row["ready"] is True
        assert row["blockers"] == []
        assert row["payload_preview"]["ref_no"] == "1001"
        assert row["payload_preview"]["parcel_list"][0]["weight"] == 120


def test_us_island_postcode_uses_island_line(app, shop_ids):
    with app.app_context():
        _order(shop_ids["a"], "1002", postal="00602")
        row = _row(_check(shop_ids["a"]), "1002")
        assert row["product"]["code"] == "S5118"
        assert "岛屿" in row["product_reason"]


def test_two_pieces_of_special_model_use_standard_registered(app, shop_ids):
    with app.app_context():
        _order(shop_ids["a"], "1003", quantity=2,
               title="Gradient Magnetic iPhone Case with Matching Stand", price="6.00")
        row = _row(_check(shop_ids["a"]), "1003")
        assert row["quantity"] == 2
        assert row["special_pair"] is True
        assert row["product"]["code"] == "QC"
        assert row["weight_grams"] == 180
        assert row["declared_value"] == 2.40


def test_three_items_use_standard_registered(app, shop_ids):
    with app.app_context():
        _order(shop_ids["a"], "1004", quantity=3)
        row = _row(_check(shop_ids["a"]), "1004")
        assert row["product"]["code"] == "QC"
        assert row["weight_grams"] == 300
        assert row["declared_value"] == 3.60


def test_canada_uses_smart_with_weight_rule(app, shop_ids):
    with app.app_context():
        _order(shop_ids["a"], "1005", country="CA", postal="M5V 3L9", quantity=2)
        row = _row(_check(shop_ids["a"]), "1005")
        assert row["product"]["code"] == "S5191"
        assert row["weight_grams"] == 180
        assert row["declared_value"] == 2.00
        assert "智选" in row["product_reason"]


def test_canada_unreachable_postcode_falls_back_to_light_small(app, shop_ids):
    with app.app_context():
        _order(shop_ids["a"], "1006", country="CA", postal="G1B 0A3")
        row = _row(_check(shop_ids["a"]), "1006")
        assert row["product"]["code"] == "S5058"
        assert "智选不可达" in row["product_reason"]


@pytest.mark.parametrize(("country", "code", "postal", "state"), [
    ("GB", "S5058", "AB10 7AB", "Scotland"),
    ("AU", "S5058", "3000", "Victoria"),
    ("NZ", "QC", "1010", "Auckland"),
    ("NL", "QC", "1012 AB", "North Holland"),
])
def test_country_default_products(app, shop_ids, country, code, postal, state):
    with app.app_context():
        number = f"1007{country}"
        order = _order(shop_ids["a"], number, country=country, postal=postal)
        order.shipping_address = {**order.shipping_address, "province": state, "province_code": ""}
        db.session.commit()
        row = _row(_check(shop_ids["a"]), number)
        assert row["product"] is not None and row["product"]["code"] == code


def test_unsupported_country_is_blocked_but_visible(app, shop_ids):
    with app.app_context():
        _order(shop_ids["a"], "1008", country="DE", postal="10115")
        row = _row(_check(shop_ids["a"]), "1008")
        assert row["ready"] is False
        assert row["product"] is None
        assert any("不在发货规则内" in reason for reason in row["blockers"])


def test_missing_phone_blocks_the_order(app, shop_ids):
    with app.app_context():
        _order(shop_ids["a"], "1009", phone="")
        row = _row(_check(shop_ids["a"]), "1009")
        assert row["ready"] is False
        assert any("缺少收货电话" in reason for reason in row["blockers"])


def test_orders_with_parcels_or_not_pending_are_not_candidates(app, shop_ids):
    with app.app_context():
        order = _order(shop_ids["a"], "1010")
        db.session.add(ShippingParcel(shop_id=shop_ids["a"], order_id=order.id,
                                      label_number="4PX1", source="4px"))
        db.session.commit()
        _order(shop_ids["a"], "1011", warehouse_status="shipped")
        numbers = {row["order_number"] for row in _check(shop_ids["a"])["orders"]}
        assert "1010" not in numbers
        assert "1011" not in numbers


def test_check_does_not_create_parcels_or_orders(app, shop_ids):
    with app.app_context():
        _order(shop_ids["a"], "1012")
        before = (Order.query.count(), ShippingParcel.query.count())
        _check(shop_ids["a"])
        assert (Order.query.count(), ShippingParcel.query.count()) == before


def test_api_endpoint_returns_rule_check_payload(app, client, admin_headers, shop_ids):
    with app.app_context():
        _order(shop_ids["a"], "1013")
    response = client.get(
        f"/api/shipping/4px/declaration/check?shop_id={shop_ids['a']}&search=1013",
        headers=admin_headers)
    assert response.status_code == 200
    payload = response.get_json()["data"]
    assert payload["summary"]["candidates"] == 1
    assert payload["orders"][0]["order_number"] == "1013"
    assert payload["orders"][0]["product"]["code"] == "S5058"
    assert payload["rules"]["products"]["smart"]["code"] == "S5191"

def test_wallet_and_screen_protector_use_their_own_declared_names(app, shop_ids):
    with app.app_context():
        _order(shop_ids["b"], "1014", country="US", price="19.99",
               title="Men's Slim RFID Bifold Wallet with Pop-Up Card Holder")
        _order(shop_ids["a"], "1015", country="GB", price="15.00",
               title="Titan 2026 Dust-Removal Easy-App Screen Protector")
        wallet = _row(_check(shop_ids["b"]), "1014")
        film = _row(_check(shop_ids["a"]), "1015")
        assert wallet["declared_items"][0]["name_en"] == "Card pack"
        assert wallet["declared_value"] == 1.20
        assert film["declared_items"][0]["name_en"] == "Mobile phone protective film"
        assert film["declared_value"] == 1.20


def test_check_paginates_and_reports_total(app, shop_ids):
    with app.app_context():
        for index in range(3):
            _order(shop_ids["b"], f"200{index}")
        full = _check(shop_ids["b"], per_page=100)
        total = full["pagination"]["total"]
        assert total >= 3
        first = _check(shop_ids["b"], page=1, per_page=1)
        second = _check(shop_ids["b"], page=2, per_page=1)
        assert len(first["orders"]) == 1
        assert first["pagination"]["total"] == total
        assert first["pagination"]["pages"] == total
        assert first["orders"][0]["order_id"] != second["orders"][0]["order_id"]


def test_export_csv_contains_rows_and_headers(app, shop_ids):
    with app.app_context():
        _order(shop_ids["a"], "3001")
        with current_app.test_request_context():
            g.current_user = {"authority": "admin", "user_id": 1}
            content, filename = fourpx_declaration_service.export_csv({"shop_id": shop_ids["a"]})
        text = content.decode("utf-8-sig")
        assert filename.startswith("4px-declaration-check-") and filename.endswith(".csv")
        assert "订单号" in text and "建议产品代码" in text
        assert "3001" in text


def test_batch_plan_groups_ready_orders_and_lists_invalid_ids(app, shop_ids):
    with app.app_context():
        first = _order(shop_ids["a"], "4001")
        second = _order(shop_ids["a"], "4002", postal="00602")
        blocked = _order(shop_ids["a"], "4003", country="DE", postal="10115")
        with current_app.test_request_context():
            g.current_user = {"authority": "admin", "user_id": 1}
            plan = fourpx_declaration_service.plan_batch({
                "shop_id": shop_ids["a"],
                "order_ids": [first.id, second.id, blocked.id, 999999],
            })
        assert plan["summary"] == {"requested": 4, "matched": 3, "eligible": 2, "blocked": 1, "invalid": 1}
        assert plan["invalid_order_ids"] == [999999]
        codes = {group["product"]["code"] for group in plan["groups"]}
        assert codes == {"S5058", "S5118"}
        assert sum(group["order_count"] for group in plan["groups"]) == 2
        assert sum(group["declared_value"] for group in plan["groups"]) == 2.40


def test_batch_plan_rejects_empty_selection(app, shop_ids):
    with app.app_context():
        with current_app.test_request_context():
            g.current_user = {"authority": "admin", "user_id": 1}
            with pytest.raises(Exception):
                fourpx_declaration_service.plan_batch({"shop_id": shop_ids["a"], "order_ids": []})


class FakeCreateClient:
    """记录建单请求的假 4PX 客户端，不产生网络请求。"""
    created = []
    existing = {}

    def __init__(self, app_key, app_secret, *, environment):
        pass

    def create_order(self, payload):
        FakeCreateClient.created.append(payload)
        seq = len(FakeCreateClient.created)
        return {"4px_tracking_no": f"4PX-TEST-{seq:04d}", "label_barcode": f"BC-{seq:04d}"}

    def get_order(self, ref_no):
        return FakeCreateClient.existing.get(ref_no, [])


def _set_creds(shop_id):
    shop = db.session.get(Shop, shop_id)
    shop.set_fourpx_credentials("test-key", "test-secret")
    db.session.commit()


def _create(shop_id, order_ids, confirm=True, client_factory=FakeCreateClient):
    with current_app.test_request_context():
        g.current_user = {"authority": "admin", "user_id": 1}
        return fourpx_declaration_service.create_declarations(
            {"shop_id": shop_id, "order_ids": order_ids, "confirm": confirm},
            client_factory=client_factory,
        )


def test_create_declarations_builds_payload_and_saves_parcel(app, shop_ids):
    with app.app_context():
        FakeCreateClient.created = []
        _set_creds(shop_ids["a"])
        order = _order(shop_ids["a"], "5001")
        result = _create(shop_ids["a"], [order.id])
        assert result["summary"] == {"requested": 1, "created": 1, "failed": 0}
        payload = FakeCreateClient.created[0]
        assert payload["ref_no"] == "5001"
        assert payload["logistics_service_info"]["logistics_product_code"] == "S5058"
        assert payload["sender"]["first_name"] == "MAI"
        assert payload["sender"]["state"] == "Guangdong"
        assert payload["sender"]["city"] == "Shenzhen"
        assert payload["return_info"]["domestic_return_addr"]["first_name"] == "LIU"
        assert payload["deliver_type_info"] == {"deliver_type": "2"}
        declared = payload["parcel_list"][0]["declare_product_info"][0]
        assert declared["declare_product_name_en"] == "Phone case"
        assert declared["declare_unit_price_import"] == 1.2
        product = payload["parcel_list"][0]["product_list"][0]
        assert product["product_name"] == "Phone case"
        assert product["product_unit_price"] == 1.2
        assert product["currency"] == "USD"
        assert payload["parcel_list"][0]["parcel_value"] == 1.2
        parcel = ShippingParcel.query.filter_by(order_id=order.id).one()
        assert parcel.label_number == "4PX-TEST-0001"
        assert parcel.label_barcode == "BC-0001"
        assert parcel.source == "4px"


def test_create_declarations_skips_orders_with_parcels_and_blocked(app, shop_ids):
    with app.app_context():
        FakeCreateClient.created = []
        _set_creds(shop_ids["a"])
        bound = _order(shop_ids["a"], "5002")
        db.session.add(ShippingParcel(shop_id=shop_ids["a"], order_id=bound.id,
                                      label_number="4PX-OLD", source="4px"))
        db.session.commit()
        blocked = _order(shop_ids["a"], "5003", country="DE", postal="10115")
        result = _create(shop_ids["a"], [bound.id, blocked.id])
        assert FakeCreateClient.created == []
        assert [row["status"] for row in result["results"]] == ["skipped", "skipped"]


def test_create_declarations_requires_confirm(app, shop_ids):
    with app.app_context():
        _set_creds(shop_ids["a"])
        order = _order(shop_ids["a"], "5004")
        with current_app.test_request_context():
            g.current_user = {"authority": "admin", "user_id": 1}
            with pytest.raises(Exception):
                fourpx_declaration_service.create_declarations(
                    {"shop_id": shop_ids["a"], "order_ids": [order.id]}, client_factory=FakeCreateClient)


def test_create_declarations_adopts_existing_4px_consignment_without_creating_again(app, shop_ids):
    with app.app_context():
        class ExistingClient(FakeCreateClient):
            created_calls = 0

            def create_order(self, payload):
                ExistingClient.created_calls += 1
                return {"4px_tracking_no": "SHOULD-NOT-BE-USED"}

            def get_order(self, ref_no):
                return [{"consignment_info": {"4px_tracking_no": "4PX-EXIST-01", "ref_no": ref_no}}]

        _set_creds(shop_ids["a"])
        order = _order(shop_ids["a"], "5005")
        with current_app.test_request_context():
            g.current_user = {"authority": "admin", "user_id": 1}
            result = fourpx_declaration_service.create_declarations(
                {"shop_id": shop_ids["a"], "order_ids": [order.id], "confirm": True},
                client_factory=ExistingClient)
        assert result["results"][0]["status"] == "already_exists"
        assert ExistingClient.created_calls == 0
        assert ShippingParcel.query.filter_by(order_id=order.id).one().label_number == "4PX-EXIST-01"


def test_create_declarations_supports_australia_with_configured_sender(app, shop_ids):
    with app.app_context():
        FakeCreateClient.created = []
        _set_creds(shop_ids["a"])
        order = _order(shop_ids["a"], "5006", country="AU", postal="3000")
        result = _create(shop_ids["a"], [order.id])
        assert result["results"][0]["status"] == "created"
        assert FakeCreateClient.created[0]["sender"]["first_name"] == "MAI"
        assert ShippingParcel.query.filter_by(order_id=order.id).first() is not None


def test_check_filters_by_order_number_range(app, shop_ids):
    with app.app_context():
        for number in ("6001", "6002", "6003"):
            _order(shop_ids["b"], number)
        result = _check(shop_ids["b"], order_from="6002", order_to="6002")
        assert [row["order_number"] for row in result["orders"]] == ["6002"]
        assert result["pagination"]["total"] == 1
        wide = _check(shop_ids["b"], order_from="6001", order_to="6003")
        assert {row["order_number"] for row in wide["orders"]} == {"6001", "6002", "6003"}


def test_create_declarations_can_select_orders_by_number_range(app, shop_ids):
    with app.app_context():
        FakeCreateClient.created = []
        _set_creds(shop_ids["a"])
        first = _order(shop_ids["a"], "6101")
        second = _order(shop_ids["a"], "6102")
        third = _order(shop_ids["a"], "6103")
        with current_app.test_request_context():
            g.current_user = {"authority": "admin", "user_id": 1}
            result = fourpx_declaration_service.create_declarations(
                {"shop_id": shop_ids["a"], "order_from": "6102", "order_to": "6103",
                 "confirm": True},
                client_factory=FakeCreateClient)
        assert [row["order_number"] for row in result["results"]] == ["6102", "6103"]
        assert result["summary"]["created"] == 2
        assert FakeCreateClient.created[0]["ref_no"] == "6102"
        assert ShippingParcel.query.filter_by(order_id=first.id).first() is None
        assert ShippingParcel.query.filter_by(order_id=second.id).first() is not None
        assert ShippingParcel.query.filter_by(order_id=third.id).first() is not None


def test_address_without_street_name_is_blocked_as_address_issue(app, shop_ids):
    with app.app_context():
        order = _order(shop_ids["a"], "7001")
        order.shipping_address = {**order.shipping_address, "address1": "81"}
        db.session.commit()
        row = _row(_check(shop_ids["a"]), "7001")
        assert row["ready"] is False
        assert row["reason_group"] == "address"
        assert any("门牌号" in reason or "街道" in reason for reason in row["blockers"])
        blocked = _check(shop_ids["a"], blocked_only="true")
        numbers = {item["order_number"] for item in blocked["orders"]}
        assert "7001" in numbers
        assert all(item["ready"] is False for item in blocked["orders"])
        assert blocked["summary"]["groups"].get("address", 0) >= 1


def test_uk_order_without_email_is_blocked_as_contact_issue(app, shop_ids):
    with app.app_context():
        order = _order(shop_ids["a"], "7002", country="GB", postal="AB10 7AB")
        order.shipping_address = {**order.shipping_address, "email": None}
        order.customer_email = None
        db.session.commit()
        row = _row(_check(shop_ids["a"]), "7002")
        assert row["ready"] is False
        assert row["reason_group"] == "contact"
        assert any("邮箱" in reason for reason in row["blockers"])


def test_address_validation_flags_fake_postcode_and_state_mismatch(app, shop_ids):
    with app.app_context():
        mismatched = _order(shop_ids["a"], "7101", postal="10001")
        mismatched.shipping_address = {**mismatched.shipping_address, "province": "California", "province_code": "CA"}
        fake = _order(shop_ids["a"], "7102", postal="00000")
        db.session.commit()
        first = _row(_check(shop_ids["a"]), "7101")
        second = _row(_check(shop_ids["a"]), "7102")
        assert first["ready"] is False and any("邮编与州不一致" in reason for reason in first["blockers"])
        assert second["ready"] is False and any("邮编" in reason for reason in second["blockers"])
        assert first["reason_group"] == "address"


def test_address_validation_accepts_shared_australia_postcode(app, shop_ids):
    with app.app_context():
        number = "7103"
        order = _order(shop_ids["a"], number, country="AU", postal="3644")
        order.shipping_address = {**order.shipping_address, "province": "Victoria", "province_code": "VIC"}
        db.session.commit()
        row = _row(_check(shop_ids["a"]), number)
        assert not any("邮编与州不一致" in reason for reason in row["blockers"])


def test_address_validation_flags_placeholder_address_line(app, shop_ids):
    with app.app_context():
        order = _order(shop_ids["a"], "7104")
        order.shipping_address = {**order.shipping_address, "address2": "NA"}
        db.session.commit()
        row = _row(_check(shop_ids["a"]), "7104")
        assert row["ready"] is False
        assert any("疑似测试/占位" in reason for reason in row["blockers"])


def _add_refund(order, amount, status="success"):
    from datetime import datetime
    from decimal import Decimal
    from app.models import Payment
    db.session.add(Payment(order_id=order.id, shop_id=order.shop_id,
                           payment_method="shopify_refund", amount=Decimal(str(amount)),
                           currency="USD", status=status, payment_date=datetime.utcnow()))
    db.session.commit()


def test_fully_refunded_order_is_excluded_from_candidates(app, shop_ids):
    with app.app_context():
        refunded = _order(shop_ids["a"], "7201")
        refunded.total_price = 10
        partial = _order(shop_ids["a"], "7202")
        partial.total_price = 10
        pending_refund = _order(shop_ids["a"], "7203")
        pending_refund.total_price = 10
        db.session.commit()
        _add_refund(refunded, -10)
        _add_refund(partial, -3)
        _add_refund(pending_refund, -10, status="pending")
        result = _check(shop_ids["a"])
        numbers = {row["order_number"] for row in result["orders"]}
        assert "7201" not in numbers
        assert "7203" not in numbers
        assert "7202" in numbers
        assert _row(result, "7202")["ready"] is True


def test_create_skips_fully_refunded_order_even_if_selected(app, shop_ids):
    with app.app_context():
        FakeCreateClient.created = []
        _set_creds(shop_ids["a"])
        order = _order(shop_ids["a"], "7204")
        order.total_price = 10
        db.session.commit()
        _add_refund(order, -10)
        result = _create(shop_ids["a"], [order.id])
        assert FakeCreateClient.created == []
        assert result["results"][0]["status"] == "skipped"
        assert "退款" in result["results"][0]["error"]
        assert ShippingParcel.query.filter_by(order_id=order.id).first() is None


def test_declaration_settings_default_off_and_toggle(app, shop_ids):
    with app.app_context():
        with current_app.test_request_context():
            g.current_user = {"authority": "admin", "user_id": 1}
            assert fourpx_declaration_service.get_settings({"shop_id": shop_ids["a"]})["auto_writeback"] is False
            saved = fourpx_declaration_service.save_settings(
                {"shop_id": shop_ids["a"], "auto_writeback": True})
            assert saved["auto_writeback"] is True
            assert fourpx_declaration_service.get_settings({"shop_id": shop_ids["a"]})["auto_writeback"] is True
            # 店铺之间互相隔离
            assert fourpx_declaration_service.get_settings({"shop_id": shop_ids["b"]})["auto_writeback"] is False


def test_create_declarations_runs_auto_writeback_only_when_switch_on(app, shop_ids, monkeypatch):
    calls = []

    def fake_writeback(order_id):
        calls.append(order_id)
        return {"order_id": order_id, "status": "updated", "tracking_numbers": ["4PX-TEST-0001"],
                "warehouse_status": "pending", "customer_notification_requested": True}

    with app.app_context():
        monkeypatch.setattr("app.services.shopify_fulfillment_service.writeback_tracking", fake_writeback)
        FakeCreateClient.created = []
        _set_creds(shop_ids["a"])
        order = _order(shop_ids["a"], "7301")
        result = _create(shop_ids["a"], [order.id])
        assert calls == []
        assert result["auto_writeback"]["enabled"] is False
        assert "shopify_status" not in result["results"][0]
        with current_app.test_request_context():
            g.current_user = {"authority": "admin", "user_id": 1}
            fourpx_declaration_service.save_settings(
                {"shop_id": shop_ids["a"], "auto_writeback": True})
        order2 = _order(shop_ids["a"], "7302")
        result2 = _create(shop_ids["a"], [order2.id])
        assert calls == [order2.id]
        assert result2["auto_writeback"]["enabled"] is True
        assert result2["auto_writeback"]["updated"] == 1
        assert result2["results"][0]["shopify_status"] == "updated"


def test_product_list_uses_declared_lines_not_order_price(app, shop_ids):
    with app.app_context():
        FakeCreateClient.created = []
        _set_creds(shop_ids["a"])
        order = _order(shop_ids["a"], "7401", title="Phone Case", price="22.60")
        db.session.add(OrderItem(
            shop_id=shop_ids["a"], order_id=order.id, shopify_line_item_id="7401-1",
            title="Titan Screen Protector", sku="SKU-FILM", quantity=1, price=Decimal("9.90")))
        db.session.commit()
        _create(shop_ids["a"], [order.id])
        payload = FakeCreateClient.created[0]
        products = payload["parcel_list"][0]["product_list"]
        by_name = {item["product_name"]: item for item in products}
        assert set(by_name) == {"Phone case", "Mobile phone protective film"}
        assert by_name["Phone case"]["product_unit_price"] == 1.2
        assert by_name["Mobile phone protective film"]["product_unit_price"] == 1.2
        assert sum(item["product_unit_price"] * item["qty"] for item in products) == 2.4
        assert payload["parcel_list"][0]["parcel_value"] == 2.4


def test_create_does_not_fetch_label_or_store_placeholder_barcode(app, shop_ids, monkeypatch):
    class SameBarcodeClient(FakeCreateClient):
        def create_order(self, payload):
            SameBarcodeClient.created.append(payload)
            seq = len(SameBarcodeClient.created)
            tracking = f"4PX-SAME-{seq:04d}"
            return {"4px_tracking_no": tracking, "label_barcode": tracking}

    fetched = []
    monkeypatch.setattr("app.services.fourpx_label_service.fetch_label_data",
                        lambda *args, **kwargs: fetched.append(args) or {"label_barcode": "X", "pdf_url": ""})
    with app.app_context():
        SameBarcodeClient.created = []
        _set_creds(shop_ids["a"])
        order = _order(shop_ids["a"], "7501")
        result = _create(shop_ids["a"], [order.id], client_factory=SameBarcodeClient)
        assert result["barcode_sync"] == {"updated": 0, "failed": 0}
        assert fetched == []
        parcel = ShippingParcel.query.filter_by(order_id=order.id).one()
        assert parcel.label_number == "4PX-SAME-0001"
        assert parcel.label_barcode is None


def _add_line(shop_id, order, title, variant_title, quantity=1, price="25.00"):
    db.session.add(OrderItem(
        shop_id=shop_id, order_id=order.id,
        shopify_line_item_id=f"{order.order_number}-{title[:8]}",
        title=title, variant_title=variant_title, quantity=quantity, price=Decimal(price),
    ))
    db.session.commit()


def test_stand_and_chain_pair_uses_standard_registered(app, shop_ids):
    with app.app_context():
        order = _order(shop_ids["a"], "7601", country="GB", postal="IP6 0FG",
                       title="Silicone iPhone Case with Stand & Camera Control",
                       price="25.33")
        order.items[0].variant_title = "iPhone 18 Pro Max / Cherry Purple + Stand"
        _add_line(shop_ids["a"], order,
                  "Gradient Magnetic iPhone Case with Matching Stand",
                  "iPhone 18 Pro Max / Purple Cream + Wrist Chain", price="33.77")
        row = _row(_check(shop_ids["a"]), "7601")
        assert row["special_pair"] is True
        assert row["product"]["code"] == "QC"
        assert "套装" in row["product_reason"]


def test_same_titles_without_stand_or_chain_stay_light(app, shop_ids):
    with app.app_context():
        order = _order(shop_ids["a"], "7602", country="GB", postal="IP6 0FG",
                       title="Silicone iPhone Case with Stand & Camera Control", price="25.33")
        order.items[0].variant_title = "iPhone 18 Pro Max / Cherry Purple"
        _add_line(shop_ids["a"], order,
                  "Gradient Magnetic iPhone Case with Matching Stand",
                  "iPhone 18 Pro Max / Purple Cream")
        row = _row(_check(shop_ids["a"]), "7602")
        assert row["special_pair"] is False
        assert row["product"]["code"] == "S5058"


def test_single_stand_item_stays_light(app, shop_ids):
    with app.app_context():
        order = _order(shop_ids["a"], "7603", country="GB", postal="IP6 0FG",
                       title="Silicone iPhone Case with Stand & Camera Control", price="25.33")
        order.items[0].variant_title = "iPhone 18 Pro Max / Cherry Purple + Stand"
        db.session.commit()
        row = _row(_check(shop_ids["a"]), "7603")
        assert row["special_pair"] is False
        assert row["product"]["code"] == "S5058"


def test_canada_stand_pair_uses_standard_even_when_smart_reachable(app, shop_ids):
    with app.app_context():
        order = _order(shop_ids["a"], "7701", country="CA", postal="M5V 3L9",
                       title="Silicone iPhone Case with Stand & Camera Control", price="25.33")
        order.items[0].variant_title = "iPhone 18 Pro Max / Cherry Purple + Stand"
        _add_line(shop_ids["a"], order,
                  "Gradient Magnetic iPhone Case with Matching Stand",
                  "iPhone 18 Pro Max / Purple Cream + Wrist Chain", price="33.77")
        row = _row(_check(shop_ids["a"]), "7701")
        assert row["product"]["code"] == "QC"
        assert "支架" in row["product_reason"]


def test_canada_unreachable_stand_pair_uses_standard(app, shop_ids):
    with app.app_context():
        order = _order(shop_ids["a"], "7702", country="CA", postal="G1B 0A3",
                       title="Silicone iPhone Case with Stand & Camera Control", price="25.33")
        order.items[0].variant_title = "iPhone 18 Pro Max / Cherry Purple + Stand"
        _add_line(shop_ids["a"], order,
                  "Gradient Magnetic iPhone Case with Matching Stand",
                  "iPhone 18 Pro Max / Purple Cream + Wrist Chain", price="33.77")
        row = _row(_check(shop_ids["a"]), "7702")
        assert row["product"]["code"] == "QC"


def test_canada_two_pieces_of_single_stand_model_use_standard(app, shop_ids):
    with app.app_context():
        order = _order(shop_ids["a"], "7703", country="CA", postal="G1B 0A3", quantity=2,
                       title="Silicone iPhone Case with Stand & Camera Control", price="25.33")
        order.items[0].variant_title = "iPhone 18 Pro Max / Cherry Purple + Stand"
        db.session.commit()
        row = _row(_check(shop_ids["a"]), "7703")
        assert row["product"]["code"] == "QC"


def test_canada_single_stand_item_stays_light(app, shop_ids):
    with app.app_context():
        order = _order(shop_ids["a"], "7704", country="CA", postal="G1B 0A3",
                       title="Silicone iPhone Case with Stand & Camera Control", price="25.33")
        order.items[0].variant_title = "iPhone 18 Pro Max / Cherry Purple + Stand"
        db.session.commit()
        row = _row(_check(shop_ids["a"]), "7704")
        assert row["product"]["code"] == "S5058"


def test_canada_two_plain_items_stay_light_when_smart_unreachable(app, shop_ids):
    with app.app_context():
        _order(shop_ids["a"], "7705", country="CA", postal="G1B 0A3", quantity=2)
        row = _row(_check(shop_ids["a"]), "7705")
        assert row["product"]["code"] == "S5058"


def _create_real_ids(shop_id, count, prefix="7800"):
    ids = []
    for index in range(count):
        ids.append(_order(shop_id, f"{prefix}{index:03d}").id)
    return ids


def test_declaration_job_tracks_progress_and_is_idempotent(app, shop_ids, monkeypatch):
    calls = []

    def fake_create(args, **_kwargs):
        calls.append(list(args["order_ids"]))
        return {"summary": {"requested": len(args["order_ids"]), "created": len(args["order_ids"]), "failed": 0},
                "barcode_sync": {"updated": 0, "failed": 0}, "auto_writeback": {"enabled": False},
                "results": [{"order_id": value, "order_number": str(value), "status": "created",
                             "tracking_number": f"4PX-JOB-{value}"} for value in args["order_ids"]]}

    with app.app_context():
        monkeypatch.setattr("app.services.fourpx_declaration_service.create_declarations", fake_create)
        monkeypatch.setattr("app.tasks.run_fourpx_declaration_job_task.apply_async",
                            lambda *args, **kwargs: None)
        ids = _create_real_ids(shop_ids["a"], 60)
        with current_app.test_request_context():
            g.current_user = {"authority": "admin", "user_id": 1}
            job = fourpx_declaration_service.start_declaration_job(
                {"shop_id": shop_ids["a"], "order_ids": ids, "confirm": True})
            assert job["status"] == "queued"
            assert job["requested"] == 60
            fourpx_declaration_service.process_declaration_job(job["id"])
            fourpx_declaration_service.process_declaration_job(job["id"])  # 幂等
            stored = fourpx_declaration_service.get_declaration_job(
                {"shop_id": shop_ids["a"], "job_id": job["id"]})
            assert stored["status"] == "done"
            assert stored["processed"] == 60
            assert stored["created"] == 60
            assert len(stored["results"]) == 60
            assert len(calls) == 2  # 50 + 10


def test_declaration_job_requires_confirm_and_ids(app, shop_ids):
    with app.app_context():
        with current_app.test_request_context():
            g.current_user = {"authority": "admin", "user_id": 1}
            with pytest.raises(ValidationError):
                fourpx_declaration_service.start_declaration_job(
                    {"shop_id": shop_ids["a"], "order_ids": [1], "confirm": False})
            with pytest.raises(ValidationError):
                fourpx_declaration_service.start_declaration_job(
                    {"shop_id": shop_ids["a"], "order_ids": [], "confirm": True})


class SyncClient:
    """按客户单号回查 4PX 委托单的假客户端。"""
    found = {"7901": "4PX-MANUAL-1"}

    def __init__(self, key, secret, *, environment):
        pass

    def get_order(self, request_no):
        tracking = SyncClient.found.get(request_no)
        return [{"consignment_info": {"4px_tracking_no": tracking}}] if tracking else []


def test_sync_manual_declarations_binds_existing_fourpx_orders(app, shop_ids):
    with app.app_context():
        SyncClient.found = {"7901": "4PX-MANUAL-1"}
        _set_creds(shop_ids["a"])
        order = _order(shop_ids["a"], "7901")
        missing = _order(shop_ids["a"], "7902")
        occupied = _order(shop_ids["a"], "7903")
        db.session.add(ShippingParcel(shop_id=shop_ids["a"], order_id=occupied.id,
                                      label_number="4PX-OLD-9", source="4px"))
        db.session.commit()
        with current_app.test_request_context():
            g.current_user = {"authority": "admin", "user_id": 1}
            result = fourpx_declaration_service.sync_manual_declarations(
                {"shop_id": shop_ids["a"],
                 "order_ids": [order.id, missing.id, occupied.id]},
                client_factory=SyncClient)
        assert result["summary"] == {"requested": 3, "bound": 1, "already_bound": 1,
                                     "not_found": 1, "failed": 0}
        statuses = {row["order_number"]: row["status"] for row in result["results"]}
        assert statuses["7901"] == "bound" and statuses["7902"] == "not_found" and statuses["7903"] == "already_bound"
        parcel = ShippingParcel.query.filter_by(order_id=order.id).one()
        assert parcel.label_number == "4PX-MANUAL-1"
        assert parcel.label_barcode is None
        assert parcel.source == "4px"


def test_recipient_uses_full_name_in_first_name_to_keep_label_order(app, shop_ids):
    with app.app_context():
        order = _order(shop_ids["a"], "7605")
        row = _row(_check(shop_ids["a"]), "7605")
        assert row["recipient"]["first_name"] == "Jane Doe"
        assert row["recipient"]["last_name"] == ""
        payload = fourpx_declaration_service._create_payload(order, row)
        assert payload["recipient_info"]["first_name"] == "Jane Doe"
        assert payload["recipient_info"]["last_name"] == ""


class SmartRejectClient(FakeCreateClient):
    """加拿大智选按邮编拒绝，轻小件成功。"""

    def create_order(self, payload):
        code = payload["logistics_service_info"]["logistics_product_code"]
        if code == "S5191":
            error = FourPxError("4PX 未确认操作成功")
            error.details = "recipient_info.post_code,该邮编所在地区无法提供派送服务;"
            raise error
        return super().create_order(payload)


def test_canada_smart_rejected_postcode_falls_back_to_light_and_caches(app, shop_ids):
    with app.app_context():
        SmartRejectClient.created = []
        _set_creds(shop_ids["a"])
        order = _order(shop_ids["a"], "7900", country="CA", postal="V1L 6M6")
        result = _create(shop_ids["a"], [order.id], client_factory=SmartRejectClient)
        row = result["results"][0]
        assert row["status"] == "created_fallback"
        assert "轻小件" in row["fallback_reason"]
        parcel = ShippingParcel.query.filter_by(order_id=order.id).one()
        assert parcel.label_number.startswith("4PX-TEST-")
        from app.models import FourPxSmartRejection
        cached = FourPxSmartRejection.query.filter_by(shop_id=shop_ids["a"], postal_code="V1L6M6").one()
        assert cached.product_code == "S5191"
        # 下一次检查直接建议轻小件，不再建议智选（订单已有包裹，不再出现在候选里）
        checked = fourpx_declaration_service._build_row(order)
        assert checked["product"]["code"] == "S5058"
        assert "智选不可达" in checked["product_reason"]


def test_recipient_street_merges_shopify_address_lines(app):
    from types import SimpleNamespace
    from app.services.fourpx_declaration_service import _recipient

    order = SimpleNamespace(
        shipping_address={"name": "Test User", "phone": "123", "email": "t@example.com",
                          "post_code": "99163", "country_code": "US", "province": "WA",
                          "city": "Pullman", "address1": "1535 NORTHEAST MERMAN DRIVE AP",
                          "address2": "Apt E4"},
        customer_name="Test User", customer_phone="", customer_email="",
    )
    recipient = _recipient(order)
    assert recipient["street"] == "1535 NORTHEAST MERMAN DRIVE AP, Apt E4"

    duplicate = SimpleNamespace(
        shipping_address={"name": "T", "address1": "79 Andrew Crescent", "address2": "79 Andrew Crescent"},
        customer_name="T", customer_phone="", customer_email="",
    )
    assert _recipient(duplicate)["street"] == "79 Andrew Crescent"


def test_wallet_case_is_caseease_only_and_men_wallet_is_eranoir_only(app, shop_ids):
    from app.services.fourpx_declaration_service import declaration_category
    assert declaration_category("iPhone Wallet Case with 2-Card Holder & 360° Ring Stand", shop_ids["a"])["en"] == "Phone case"
    assert declaration_category("Men's Slim RFID Bifold Wallet with Pop-Up Card Holder", shop_ids["b"])["en"] == "Card pack"
    assert declaration_category("Men's Slim RFID Bifold Wallet with Pop-Up Card Holder", shop_ids["a"])["en"] == "Phone case"
