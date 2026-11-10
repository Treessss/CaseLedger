"""Predeclaration preview must fail closed on ambiguous or mismatched numbers."""

from app import db
from app.models import Order, ShippingParcel, Shop
from app.services.fourpx_preview_service import (
    batch_bind_and_writeback, batch_bind_predeclared, bind_and_writeback, bind_predeclared,
    preview_predeclared,
)
from flask import g
import pytest


@pytest.fixture(autouse=True)
def mock_shopify_tracking(monkeypatch):
    monkeypatch.setattr("app.services.fourpx_preview_service.fetch_tracking_by_order_ids",
                        lambda shop, ids: {f"gid://shopify/Order/{value}": set() for value in ids})


class FakeClient:
    rows = []
    call_window = None

    def __init__(self, app_key, app_secret, *, environment):
        assert app_key == "test-key"
        assert app_secret == "test-secret"
        assert environment == "test"

    def get_predeclared_created_between(self, start_ms, end_ms):
        self.__class__.call_window = (start_ms, end_ms)
        return self.rows


def _row(ref, tracking, status="P"):
    return {"consignment_info": {
        "ref_no": ref, "4px_tracking_no": tracking, "consignment_status": status,
    }}


def test_preview_matches_only_unique_shop_orders_without_writes(app, shop_ids):
    with app.app_context():
        shop = db.session.get(Shop, shop_ids["a"])
        shop.set_fourpx_credentials("test-key", "test-secret")
        order = Order.query.filter_by(shop_id=shop.id, order_number="#A0").one()
        other_shop_order = Order.query.filter_by(shop_id=shop_ids["b"]).one()
        FakeClient.rows = [_row("A0", "4PX-ONE"), _row("B0", "4PX-TWO"),
                           _row("MISSING", "4PX-THREE"), _row("A1", "DUP"), _row("A2", "DUP")]
        with app.test_request_context():
            g.current_user = {"authority": "admin"}
            result = preview_predeclared({"shop_id": shop.id, "date": "2026-09-30"}, client_factory=FakeClient)
        assert result["total"] == 5
        assert result["ready_to_bind"] == 1
        assert [row["status"] for row in result["rows"]] == [
            "ready_to_bind", "order_not_found", "order_not_found", "duplicate_4px", "duplicate_4px"
        ]
        assert result["rows"][0]["order_id"] == order.id
        assert FakeClient.call_window[1] - FakeClient.call_window[0] == 86_400_000
        assert db.session.get(Order, order.id).shipping_label_number is None
        assert db.session.get(Order, other_shop_order.id).shipping_label_number is None


def test_preview_accepts_two_distinct_parcels_for_one_order(app, shop_ids):
    with app.app_context():
        shop = db.session.get(Shop, shop_ids["a"])
        shop.set_fourpx_credentials("test-key", "test-secret")
        order = Order.query.filter_by(shop_id=shop.id, order_number="#A0").one()
        FakeClient.rows = [_row("A0", "TRACK-1"), _row("A0", "TRACK-2")]
        with app.test_request_context():
            g.current_user = {"authority": "admin"}
            before = preview_predeclared({"shop_id": shop.id, "date": "2026-09-30"}, client_factory=FakeClient)
            assert {row["status"] for row in before["rows"]} == {"parcel_count_mismatch"}
            order.expected_parcel_count = 2
            db.session.commit()
            after = preview_predeclared({"shop_id": shop.id, "date": "2026-09-30"}, client_factory=FakeClient)
            assert {row["status"] for row in after["rows"]} == {"ready_to_bind"}


def test_preview_disables_rows_already_backfilled_in_shopify(app, shop_ids, monkeypatch):
    with app.app_context():
        shop = db.session.get(Shop, shop_ids["a"])
        shop.set_fourpx_credentials("test-key", "test-secret")
        order = Order.query.filter_by(shop_id=shop.id, order_number="#A0").one()
        FakeClient.rows = [_row("A0", "TRACK-REMOTE")]
        monkeypatch.setattr("app.services.fourpx_preview_service.fetch_tracking_by_order_ids",
                            lambda selected_shop, ids: {f"gid://shopify/Order/{order.shopify_order_id}":
                                                        {"TRACK-REMOTE"}})
        with app.test_request_context():
            g.current_user = {"authority": "admin"}
            result = preview_predeclared({"shop_id": shop.id, "date": "2026-09-30"}, client_factory=FakeClient)
            assert result["rows"][0]["status"] == "shopify_already_fulfilled"
            assert result["ready_to_bind"] == 0
            assert result["exceptions"] == 0
            selected = {"shop_id": shop.id, "date": "2026-09-30", "pairs": [
                {"customer_reference": "A0", "tracking_number": "TRACK-REMOTE"}
            ]}
            batch = batch_bind_and_writeback(selected, client_factory=FakeClient)
            assert batch["summary"]["skipped_pairs"] == 1
            assert ShippingParcel.query.filter_by(order_id=order.id).count() == 0


def test_preview_blocks_different_shopify_tracking(app, shop_ids, monkeypatch):
    with app.app_context():
        shop = db.session.get(Shop, shop_ids["a"])
        shop.set_fourpx_credentials("test-key", "test-secret")
        order = Order.query.filter_by(shop_id=shop.id, order_number="#A0").one()
        FakeClient.rows = [_row("A0", "TRACK-4PX")]
        monkeypatch.setattr("app.services.fourpx_preview_service.fetch_tracking_by_order_ids",
                            lambda selected_shop, ids: {f"gid://shopify/Order/{order.shopify_order_id}":
                                                        {"DIFFERENT"}})
        with app.test_request_context():
            g.current_user = {"authority": "admin"}
            result = preview_predeclared({"shop_id": shop.id, "date": "2026-09-30"}, client_factory=FakeClient)
        assert result["rows"][0]["status"] == "shopify_tracking_conflict"
        assert result["exceptions"] == 1


def test_preview_route_rejects_unconfigured_shop(app, client, admin_headers, shop_ids):
    response = client.post("/api/shipping/4px/preview", headers=admin_headers,
                           json={"shop_id": shop_ids["a"], "date": "2026-09-30"})
    assert response.status_code == 400
    assert "4PX" in response.get_json()["message"]


def test_bind_rechecks_pair_then_saves_local_label_only(app, shop_ids):
    with app.app_context():
        shop = db.session.get(Shop, shop_ids["a"])
        shop.set_fourpx_credentials("test-key", "test-secret")
        order = Order.query.filter_by(shop_id=shop.id, order_number="#A0").one()
        order.warehouse_status = "pending"
        db.session.commit()
        FakeClient.rows = [_row("A0", "TRACK-SAFE")]
        payload = {"shop_id": shop.id, "date": "2026-09-30",
                   "customer_reference": "A0", "tracking_number": "TRACK-SAFE"}
        with app.test_request_context():
            g.current_user = {"authority": "admin"}
            result = bind_predeclared(payload, client_factory=FakeClient)
            assert result["order_id"] == order.id
            parcel = ShippingParcel.query.filter_by(order_id=order.id).one()
            assert parcel.label_number == "TRACK-SAFE"
            assert parcel.source == "4px"
            assert parcel.scanned_at is None
            assert order.warehouse_status == "pending"
            assert order.fulfillment_status == "fulfilled"  # unchanged from fixture
            try:
                bind_predeclared(payload, client_factory=FakeClient)
            except Exception as exc:
                assert "已变化" in str(exc)
            else:
                raise AssertionError("duplicate bind must fail")


def test_one_click_bind_keeps_local_pair_when_shopify_fails(app, shop_ids, monkeypatch):
    with app.app_context():
        shop = db.session.get(Shop, shop_ids["a"])
        shop.set_fourpx_credentials("test-key", "test-secret")
        order = Order.query.filter_by(shop_id=shop.id, order_number="#A0").one()
        FakeClient.rows = [_row("A0", "TRACK-ONCE")]
        payload = {"shop_id": shop.id, "date": "2026-09-30",
                   "customer_reference": "A0", "tracking_number": "TRACK-ONCE"}

        def fail_writeback(order_id):
            assert order_id == order.id
            from app.utils.errors import ValidationError
            raise ValidationError("Shopify 暂时不可用")

        monkeypatch.setattr("app.services.shopify_fulfillment_service.writeback_tracking", fail_writeback)
        with app.test_request_context():
            g.current_user = {"authority": "admin"}
            result = bind_and_writeback(payload, client_factory=FakeClient)
        assert result["shopify_status"] == "failed"
        assert "Shopify" in result["shopify_error"]
        assert ShippingParcel.query.filter_by(order_id=order.id, label_number="TRACK-ONCE").count() == 1
        assert order.warehouse_status == "pending"


def test_one_click_waits_for_all_parcels_before_shopify(app, shop_ids, monkeypatch):
    with app.app_context():
        shop = db.session.get(Shop, shop_ids["a"])
        shop.set_fourpx_credentials("test-key", "test-secret")
        order = Order.query.filter_by(shop_id=shop.id, order_number="#A0").one()
        order.expected_parcel_count = 2
        db.session.commit()
        FakeClient.rows = [_row("A0", "TRACK-1"), _row("A0", "TRACK-2")]
        calls = []
        monkeypatch.setattr("app.services.shopify_fulfillment_service.writeback_tracking",
                            lambda order_id: calls.append(order_id) or {"status": "updated"})
        payload = {"shop_id": shop.id, "date": "2026-09-30", "customer_reference": "A0"}
        with app.test_request_context():
            g.current_user = {"authority": "admin"}
            first = bind_and_writeback({**payload, "tracking_number": "TRACK-1"}, client_factory=FakeClient)
            second = bind_and_writeback({**payload, "tracking_number": "TRACK-2"}, client_factory=FakeClient)
        assert first["shopify_status"] == "awaiting_parcels"
        assert second["shopify_status"] == "updated"
        assert calls == [order.id]


def test_batch_binds_two_parcels_then_writes_back_once(app, shop_ids, monkeypatch):
    with app.app_context():
        shop = db.session.get(Shop, shop_ids["a"])
        shop.set_fourpx_credentials("test-key", "test-secret")
        order = Order.query.filter_by(shop_id=shop.id, order_number="#A0").one()
        order.expected_parcel_count = 2
        db.session.commit()
        FakeClient.rows = [_row("A0", "BATCH-1"), _row("A0", "BATCH-2")]
        calls = []
        monkeypatch.setattr("app.services.shopify_fulfillment_service.writeback_tracking",
                            lambda order_id: calls.append(order_id) or {"status": "updated"})
        payload = {"shop_id": shop.id, "date": "2026-09-30", "pairs": [
            {"customer_reference": "A0", "tracking_number": "BATCH-1"},
            {"customer_reference": "A0", "tracking_number": "BATCH-2"},
        ]}
        with app.test_request_context():
            g.current_user = {"authority": "admin"}
            result = batch_bind_and_writeback(payload, client_factory=FakeClient)
        assert result["summary"]["newly_bound"] == 2
        assert result["summary"]["updated_orders"] == 1
        assert calls == [order.id]
        assert ShippingParcel.query.filter_by(order_id=order.id).count() == 2
        assert order.warehouse_status == "pending"


def test_batch_bind_only_returns_ready_orders_without_shopify_mutation(app, shop_ids, monkeypatch):
    with app.app_context():
        shop = db.session.get(Shop, shop_ids["a"])
        shop.set_fourpx_credentials("test-key", "test-secret")
        first = Order.query.filter_by(shop_id=shop.id, order_number="#A0").one()
        second = Order.query.filter_by(shop_id=shop.id, order_number="#A1").one()
        FakeClient.rows = [_row("A0", "BIND-A"), _row("A1", "BIND-B")]
        checked = []
        monkeypatch.setattr("app.services.fourpx_preview_service.fetch_tracking_by_order_ids",
                            lambda selected_shop, ids: checked.extend(ids) or {
                                f"gid://shopify/Order/{value}": set() for value in ids
                            })
        monkeypatch.setattr("app.services.shopify_fulfillment_service.writeback_tracking",
                            lambda order_id: (_ for _ in ()).throw(AssertionError("must not write back")))
        with app.test_request_context():
            g.current_user = {"authority": "admin"}
            result = batch_bind_predeclared({"shop_id": shop.id, "date": "2026-09-30", "pairs": [
                {"customer_reference": "A0", "tracking_number": "BIND-A"},
            ]}, client_factory=FakeClient)
        assert checked == [first.shopify_order_id]
        assert result["summary"]["ready_to_writeback_orders"] == 1
        assert result["orders"][0]["shopify_status"] == "ready_to_writeback"
        assert ShippingParcel.query.filter_by(order_id=first.id, label_number="BIND-A").count() == 1
        assert ShippingParcel.query.filter_by(order_id=second.id).count() == 0
        assert first.warehouse_status == "pending"


def test_batch_partial_failure_retains_binding_and_other_order_succeeds(app, shop_ids, monkeypatch):
    with app.app_context():
        shop = db.session.get(Shop, shop_ids["a"])
        shop.set_fourpx_credentials("test-key", "test-secret")
        first = Order.query.filter_by(shop_id=shop.id, order_number="#A0").one()
        second = Order.query.filter_by(shop_id=shop.id, order_number="#A1").one()
        FakeClient.rows = [_row("A0", "BATCH-A"), _row("A1", "BATCH-B")]

        def fake_writeback(order_id):
            if order_id == first.id:
                from app.utils.errors import ValidationError
                raise ValidationError("Shopify 结果不确定")
            assert order_id == second.id
            return {"status": "updated"}

        monkeypatch.setattr("app.services.shopify_fulfillment_service.writeback_tracking", fake_writeback)
        payload = {"shop_id": shop.id, "date": "2026-09-30", "pairs": [
            {"customer_reference": "A0", "tracking_number": "BATCH-A"},
            {"customer_reference": "A1", "tracking_number": "BATCH-B"},
        ]}
        with app.test_request_context():
            g.current_user = {"authority": "admin"}
            result = batch_bind_and_writeback(payload, client_factory=FakeClient)
        assert result["summary"]["failed_orders"] == 1
        assert result["summary"]["updated_orders"] == 1
        assert result["summary"]["newly_bound"] == 2
        assert ShippingParcel.query.filter_by(order_id=first.id).count() == 1
        assert ShippingParcel.query.filter_by(order_id=second.id).count() == 1
        assert first.warehouse_status == second.warehouse_status == "pending"


def test_batch_retries_already_bound_pair_idempotently(app, shop_ids, monkeypatch):
    with app.app_context():
        shop = db.session.get(Shop, shop_ids["a"])
        shop.set_fourpx_credentials("test-key", "test-secret")
        order = Order.query.filter_by(shop_id=shop.id, order_number="#A0").one()
        FakeClient.rows = [_row("A0", "BATCH-RETRY")]
        payload = {"shop_id": shop.id, "date": "2026-09-30", "pairs": [
            {"customer_reference": "A0", "tracking_number": "BATCH-RETRY"},
        ]}
        with app.test_request_context():
            g.current_user = {"authority": "admin"}
            bind_predeclared({**payload, "customer_reference": "A0", "tracking_number": "BATCH-RETRY"},
                             client_factory=FakeClient)
            monkeypatch.setattr("app.services.shopify_fulfillment_service.writeback_tracking",
                                lambda order_id: {"status": "already_fulfilled"})
            result = batch_bind_and_writeback(payload, client_factory=FakeClient)
        assert result["summary"]["already_bound"] == 1
        assert result["summary"]["newly_bound"] == 0
        assert result["summary"]["already_fulfilled_orders"] == 1
        assert ShippingParcel.query.filter_by(order_id=order.id).count() == 1


def test_batch_skips_stale_or_mismatched_selection(app, shop_ids, monkeypatch):
    with app.app_context():
        shop = db.session.get(Shop, shop_ids["a"])
        shop.set_fourpx_credentials("test-key", "test-secret")
        FakeClient.rows = [_row("A0", "CURRENT")]
        monkeypatch.setattr("app.services.shopify_fulfillment_service.writeback_tracking",
                            lambda order_id: (_ for _ in ()).throw(AssertionError("must not write")))
        with app.test_request_context():
            g.current_user = {"authority": "admin"}
            result = batch_bind_and_writeback({"shop_id": shop.id, "date": "2026-09-30",
                "pairs": [{"customer_reference": "A0", "tracking_number": "STALE"}]}, client_factory=FakeClient)
        assert result["summary"]["skipped_pairs"] == 1
        assert result["summary"]["newly_bound"] == 0


def test_batch_endpoint_enforces_shop_permission(app, client, sub_a_headers, shop_ids):
    response = client.post("/api/shipping/4px/batch-bind-and-writeback", headers=sub_a_headers,
                           json={"shop_id": shop_ids["b"], "date": "2026-09-30",
                                 "pairs": [{"customer_reference": "B0", "tracking_number": "X"}]})
    assert response.status_code == 403


def test_bind_only_endpoint_enforces_shop_permission(app, client, sub_a_headers, shop_ids):
    response = client.post("/api/shipping/4px/batch-bind", headers=sub_a_headers,
                           json={"shop_id": shop_ids["b"], "date": "2026-09-30",
                                 "pairs": [{"customer_reference": "B0", "tracking_number": "X"}]})
    assert response.status_code == 403
