from copy import deepcopy
from datetime import datetime

import pytest

from app import db
from app.models import AfterSale, AfterSaleCost, CfTrackingWriteback, Order, OrderItem
from app.services import cf_tracking_service, shopify_graphql
from app.services.inventory_service import _stock_rows
from app.utils.errors import ValidationError


def test_migration_restores_unbound_recreated_reference_and_only_its_display_snapshots(app, client, admin_headers, shop_ids):
    import importlib.util
    from pathlib import Path
    from app.models import PurchaseList
    old = create_case(app, client, admin_headers, shop_ids['a'])
    client.delete(f"/api/after-sales/{old['id']}", headers=admin_headers)
    new_response = client.post('/api/after-sales', headers=admin_headers, json={
        'order_id': old['order_id'], 'kind': 'resend', 'reason': 'recreate',
        'items': [{'source_order_item_id': old['items'][0]['source_order_item_id'], 'quantity': 2}],
    })
    assert new_response.status_code == 201
    new = new_response.json['data']
    legacy = old['customer_reference'] + '-2'
    with app.app_context():
        record = db.session.get(AfterSale, new['id'])
        record.customer_reference = legacy
        key = record.items[0].group_key
        purchase = PurchaseList(shop_id=record.shop_id, name=f'售后采购 {legacy}', after_sale_id=record.id,
            order_ids=[], items=[{'group_key': key, 'quantity': 2, 'order_numbers': [legacy]}],
            allocations=[{'after_sale_id': record.id, 'group_key': key, 'quantity': 2}],
            receipt=[{'group_key': key, 'received_quantity': 1}], status='partially_received')
        db.session.add(purchase)
        db.session.commit()
        purchase_id = purchase.id
        path = Path(__file__).resolve().parents[1] / 'migrations/versions/c82fd850ab46_reuse_archived_after_sale_references_.py'
        spec = importlib.util.spec_from_file_location('reference_migration', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module._restore_unbound_recreated_references(db.session.connection())
        db.session.commit()
        db.session.expire_all()
        assert db.session.get(AfterSale, new['id']).customer_reference == old['customer_reference']
        archived = db.session.get(AfterSale, old['id'])
        assert archived.deleted_at is not None and archived.customer_reference == old['customer_reference']
        saved = db.session.get(PurchaseList, purchase_id)
        assert saved.items[0]['order_numbers'] == [old['customer_reference']]
        assert saved.items[0]['quantity'] == 2
        assert saved.receipt[0]['received_quantity'] == 1
        assert saved.allocations[0]['after_sale_id'] == new['id']
        # Already-bound cases are not automatically renamed even if the base is free.
        record = db.session.get(AfterSale, new['id'])
        record.customer_reference = legacy
        record.tracking_number = 'BOUND-LABEL'
        db.session.commit()
        module._restore_unbound_recreated_references(db.session.connection())
        db.session.commit()
        assert db.session.get(AfterSale, new['id']).customer_reference == legacy


def create_case(app, client, headers, shop_id):
    with app.app_context():
        order = Order.query.filter_by(shop_id=shop_id).first()
        order.warehouse_status = "shipped"
        item = OrderItem(shop_id=shop_id, order_id=order.id, shopify_line_item_id="archive-line",
                         shopify_variant_id="archive-variant", title="Archive case", quantity=2)
        db.session.add(item)
        db.session.commit()
        order_id, item_id = order.id, item.id
    response = client.post("/api/after-sales", headers=headers, json={
        "order_id": order_id, "kind": "resend", "reason": "test", "items": [
            {"source_order_item_id": item_id, "quantity": 2},
        ],
    })
    assert response.status_code == 201
    return response.get_json()["data"]


def test_delete_hides_and_releases_occupancy_preserving_costs_and_partial_receipts(app, client, admin_headers, shop_ids):
    sale = create_case(app, client, admin_headers, shop_ids["a"])
    purchase = client.post("/api/purchase-lists/after-sale", headers=admin_headers,
                           json={"after_sale_id": sale["id"]}).get_json()["data"]
    key = purchase["items"][0]["group_key"]
    assert client.post(f"/api/purchase-lists/{purchase['id']}/confirm", headers=admin_headers).status_code == 200
    assert client.post(f"/api/purchase-lists/{purchase['id']}/receive", headers=admin_headers, json={
        "batch_id": "one", "finalize": False, "items": [{"group_key": key, "received_quantity": 1, "damaged_quantity": 0}],
    }).status_code == 200
    assert client.post(f"/api/after-sales/{sale['id']}/costs", headers=admin_headers,
                       json={"cost_type": "shipping", "amount_cny": 12}).status_code == 201
    with app.app_context():
        assert _stock_rows(shop_ids["a"])[key]["reserved_quantity"] == 0
        assert _stock_rows(shop_ids["a"])[key]["on_hand_quantity"] == 1
        profit = db.session.get(Order, sale["order_id"]).calculate_gross_profit_cny()
    assert client.delete(f"/api/after-sales/{sale['id']}", headers=admin_headers).status_code == 200
    assert client.delete(f"/api/after-sales/{sale['id']}", headers=admin_headers).status_code == 200
    assert client.get(f"/api/after-sales/{sale['id']}", headers=admin_headers).status_code == 404
    assert client.get("/api/after-sales", headers=admin_headers).get_json()["data"]["after_sales"] == []
    assert client.post("/api/purchase-lists/after-sale", headers=admin_headers,
                       json={"after_sale_id": sale["id"]}).status_code == 400
    assert client.post(f"/api/purchase-lists/{purchase['id']}/receive", headers=admin_headers, json={
        "batch_id": "two", "finalize": True, "items": [{"group_key": key, "received_quantity": 1, "damaged_quantity": 0}],
    }).status_code == 200
    with app.app_context():
        record = db.session.get(AfterSale, sale["id"])
        assert record.deleted_at and record.status == "purchasing"
        assert record.items[0].received_quantity == 2
        assert AfterSaleCost.query.filter_by(after_sale_id=record.id).count() == 1
        assert db.session.get(Order, sale["order_id"]).calculate_gross_profit_cny() == profit
        stock = _stock_rows(shop_ids["a"])[key]
        assert stock["reserved_quantity"] == 0 and stock["on_hand_quantity"] == 2
    candidates = client.get(f"/api/purchase-candidates?shop_id={shop_ids['a']}&view=after_sale", headers=admin_headers).get_json()["data"]
    assert candidates["category_counts"]["after_sale"] == 0


def test_delete_is_shop_scoped(app, client, admin_headers, sub_a_headers, shop_ids):
    sale = create_case(app, client, admin_headers, shop_ids["b"])
    assert client.delete(f"/api/after-sales/{sale['id']}", headers=sub_a_headers).status_code == 403
    with app.app_context():
        assert db.session.get(AfterSale, sale["id"]).deleted_at is None


@pytest.fixture()
def cf_fixture(app, monkeypatch, shop_ids):
    with app.app_context():
        order = Order.query.filter_by(shop_id=shop_ids["a"]).first()
        order.warehouse_status = "shipped"
        db.session.commit()
        order_id, gid = order.id, shopify_graphql.gid_for_order(order.shopify_order_id)
    row = {"customer_reference": "CF1001", "tracking_number": "4PX-NEW", "status": "original_order_only", "order_id": order_id}
    remote = {"order": {"id": gid, "fulfillments": [{
        "id": "gid://shopify/Fulfillment/1", "status": "SUCCESS", "trackingInfo": [{"number": "OLD"}],
    }]}}

    def preview(payload):
        from app.services.shipping_queue_service import _shop_id
        _shop_id(payload["shop_id"])
        return {"shop_id": shop_ids["a"], "rows": [row]}

    monkeypatch.setattr(cf_tracking_service.after_sale_service, "preview_fourpx", preview)
    monkeypatch.setattr(shopify_graphql, "_execute", lambda *_args, **_kwargs: deepcopy(remote))
    payload = {"shop_id": shop_ids["a"], "date": "2026-10-07", "customer_reference": "CF1001",
               "tracking_number": "4PX-NEW", "confirm_overwrite": True,
               "fulfillment_id": "gid://shopify/Fulfillment/1", "expected_tracking_numbers": ["OLD"]}
    return payload, remote, order_id


def test_cf_replaces_selected_tracking_not_warehouse_and_is_idempotent(app, client, admin_headers, monkeypatch, cf_fixture):
    payload, remote, order_id = cf_fixture
    remote["order"]["fulfillments"].append({"id": "gid://shopify/Fulfillment/2", "status": "SUCCESS", "trackingInfo": [{"number": "SECOND"}]})
    calls = []

    def mutate(_shop, query, variables, key):
        assert "notifyCustomer: true" in query
        calls.append(variables)
        updated = {"id": payload["fulfillment_id"], "status": "SUCCESS", "trackingInfo": [{"company": "4PX", "number": "4PX-NEW"}]}
        remote["order"]["fulfillments"][0] = updated
        return updated

    monkeypatch.setattr(cf_tracking_service, "_mutate_once", mutate)
    response = client.post("/api/after-sales/4px/shopify-writeback", headers=admin_headers, json=payload)
    assert response.status_code == 200
    assert response.get_json()["data"]["customer_notification_requested"] is True
    assert client.post("/api/after-sales/4px/shopify-writeback", headers=admin_headers, json=payload).get_json()["data"]["status"] == "already_matched"
    assert len(calls) == 1
    with app.app_context():
        order = db.session.get(Order, order_id)
        assert order.warehouse_status == "shipped" and order.shipping_label_number is None
        assert order.fulfillments[1]["tracking_numbers"] == ["SECOND"]
        assert CfTrackingWriteback.query.one().previous_tracking_numbers == ["OLD"]


@pytest.mark.parametrize("change", [{"confirm_overwrite": False}, {"fulfillment_id": "wrong"}, {"expected_tracking_numbers": ["CHANGED"]}, {"tracking_number": "WRONG"}])
def test_cf_requires_unique_pair_explicit_confirmation_and_unchanged_tracking(client, admin_headers, monkeypatch, cf_fixture, change):
    payload, _remote, _order_id = cf_fixture
    monkeypatch.setattr(cf_tracking_service, "_mutate_once", lambda *_args: pytest.fail("must not write"))
    assert client.post("/api/after-sales/4px/shopify-writeback", headers=admin_headers, json={**payload, **change}).status_code == 400


def test_uncertain_cf_request_does_not_blindly_retry(app, client, admin_headers, monkeypatch, cf_fixture):
    payload, _remote, _order_id = cf_fixture
    calls = []

    def mutate(*_args):
        calls.append(1)
        raise ValidationError("timeout", extra={"result_uncertain": True})

    monkeypatch.setattr(cf_tracking_service, "_mutate_once", mutate)
    assert client.post("/api/after-sales/4px/shopify-writeback", headers=admin_headers, json=payload).status_code == 400
    assert client.post("/api/after-sales/4px/shopify-writeback", headers=admin_headers, json=payload).status_code == 400
    assert len(calls) == 1
    with app.app_context():
        assert CfTrackingWriteback.query.one().state == "uncertain"


def test_cf_writeback_binds_existing_sale_and_packing_uses_reshipment(app, client, admin_headers, monkeypatch, cf_fixture):
    payload, remote, order_id = cf_fixture
    with app.app_context():
        order = db.session.get(Order, order_id)
        record = AfterSale(shop_id=order.shop_id, order_id=order.id, customer_reference="CF1001",
                           kind="resend", reason="resend", shipping_address={}, status="pending_purchase")
        db.session.add(record)
        db.session.commit()
        record_id = record.id
    monkeypatch.setattr(cf_tracking_service.after_sale_service, "preview_fourpx", lambda _payload: {
        "shop_id": payload["shop_id"], "rows": [{"order_id": order_id, "after_sale_id": record_id,
            "customer_reference": "CF1001", "tracking_number": "4PX-NEW", "status": "ready_to_bind"}],
    })
    monkeypatch.setattr(cf_tracking_service, "_mutate_once", lambda *_args: {
        "id": payload["fulfillment_id"], "status": "SUCCESS", "trackingInfo": [{"number": "4PX-NEW"}],
    })
    assert client.post("/api/after-sales/4px/shopify-writeback", headers=admin_headers, json=payload).status_code == 200
    with app.app_context():
        assert db.session.get(AfterSale, record_id).tracking_number == "4PX-NEW"
    result = client.get(f"/api/shipping/packing-lookup?shop_id={payload['shop_id']}&code=4PX-NEW", headers=admin_headers)
    assert result.status_code == 200
    assert result.get_json()["data"]["type"] == "after_sale"
