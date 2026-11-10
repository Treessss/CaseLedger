from datetime import datetime

import pytest

from app import db
from app.models import AfterSale, InventoryAdjustment, Order, OutboundEvent, ShippingParcel
from app.utils.permissions import required_permission
from tests.test_after_sale_archive_and_cf import create_case


def bind(client, headers, sale, tracking='4PX12345678CN', barcode=None, **extra):
    return client.put(f"/api/after-sales/{sale['id']}/tracking", headers=headers, json={
        'tracking_number': tracking, 'label_barcode': barcode,
        'expected_tracking_number': sale.get('tracking_number'),
        'expected_label_barcode': sale.get('label_barcode'), **extra,
    })


def test_manual_tracking_only_changes_sale_and_is_idempotent(app, client, admin_headers, shop_ids, monkeypatch):
    from app.services import after_sale_service, cf_tracking_service
    def no_remote(*_args, **_kwargs):
        pytest.fail('Manual binding must not call carriers or Shopify')
    monkeypatch.setattr(after_sale_service, 'preview_fourpx', no_remote)
    monkeypatch.setattr(after_sale_service, 'fetch_label_data', no_remote)
    monkeypatch.setattr(cf_tracking_service, 'writeback', no_remote)
    sale = create_case(app, client, admin_headers, shop_ids['a'])
    with app.app_context():
        order = db.session.get(Order, sale['order_id'])
        before = (order.warehouse_status, order.fulfillment_status, order.fulfillments, order.shipping_label_number)
    response = bind(client, admin_headers, sale, tracking='  ols123456789  ')
    assert response.status_code == 200
    saved = response.json['data']
    assert saved['tracking_number'] == 'OLS123456789' and saved['label_barcode'] is None
    assert saved['procurement_status'] == sale['procurement_status']
    assert saved['status'] == sale['status'] and saved['shipped_at'] is None
    assert bind(client, admin_headers, saved, tracking='OLS123456789').status_code == 200
    looked_up = client.get(f"/api/shipping/packing-lookup?shop_id={shop_ids['a']}&code=OLS123456789",
                           headers=admin_headers).json['data']
    assert looked_up['type'] == 'after_sale' and looked_up['id'] == sale['id']
    with app.app_context():
        order = db.session.get(Order, sale['order_id'])
        assert before == (order.warehouse_status, order.fulfillment_status, order.fulfillments, order.shipping_label_number)
        assert InventoryAdjustment.query.count() == 0
        audit = OutboundEvent.query.one()
        assert audit.action == 'tracking_bind' and audit.after_sale_id == sale['id'] and audit.user_id


def test_manual_barcode_and_tracking_both_resolve_and_scan_only_after_stock(app, client, admin_headers, shop_ids):
    sale = create_case(app, client, admin_headers, shop_ids['a'])
    result = bind(client, admin_headers, sale, barcode='GV26CAA0U077777777')
    assert result.status_code == 200
    saved = result.json['data']
    for code in ('4PX12345678CN', 'GV26CAA0U077777777'):
        looked_up = client.get(f"/api/shipping/packing-lookup?shop_id={shop_ids['a']}&code={code}", headers=admin_headers)
        assert looked_up.status_code == 200 and looked_up.json['data']['id'] == sale['id']
        assert looked_up.json['data']['outbound']['code_matches_label']
    blocked = client.post('/api/shipping/scan', headers=admin_headers,
                          json={'shop_id': shop_ids['a'], 'shipping_label_number': 'GV26CAA0U077777777'})
    assert blocked.status_code == 400
    with app.app_context():
        record = db.session.get(AfterSale, sale['id'])
        db.session.add(InventoryAdjustment(shop_id=record.shop_id, group_key=record.items[0].group_key,
                                           quantity_delta=2, reason='test stock'))
        db.session.commit()
    scanned = client.post('/api/shipping/scan', headers=admin_headers,
                          json={'shop_id': shop_ids['a'], 'shipping_label_number': saved['tracking_number']})
    assert scanned.status_code == 200 and scanned.json['data']['after_sale_id'] == sale['id']
    assert bind(client, admin_headers, saved).status_code == 400


def test_replacement_needs_confirmation_cas_and_clears_old_barcode(app, client, admin_headers, shop_ids):
    sale = create_case(app, client, admin_headers, shop_ids['a'])
    saved = bind(client, admin_headers, sale, barcode='GV26CAA0U077777777').json['data']
    assert bind(client, admin_headers, sale, tracking='OLS999999999', confirm_replace=True).status_code == 400
    assert bind(client, admin_headers, saved, tracking='OLS999999999').status_code == 400
    result = bind(client, admin_headers, saved, tracking='OLS999999999',
                  barcode=saved['label_barcode'], confirm_replace=True)
    assert result.status_code == 200
    assert result.json['data']['label_barcode'] is None
    with app.app_context():
        assert OutboundEvent.query.filter_by(action='tracking_bind').count() == 2
    assert client.get(f"/api/shipping/packing-lookup?shop_id={shop_ids['a']}&code=GV26CAA0U077777777",
                      headers=admin_headers).status_code == 404
    new = result.json['data']
    augmented = bind(client, admin_headers, new, tracking=new['tracking_number'], barcode='GV26CAA0U078888888')
    assert augmented.status_code == 200
    # Optional blank input is not an implicit request to remove a verified barcode.
    assert bind(client, admin_headers, augmented.json['data'], tracking=new['tracking_number']).json['data']['label_barcode'] == 'GV26CAA0U078888888'


@pytest.mark.parametrize('source,field', [('sale', 'tracking_number'), ('sale', 'label_barcode'),
                                         ('parcel', 'label_number'), ('parcel', 'label_barcode'), ('order', 'shipping_label_number')])
def test_duplicate_labels_across_columns_are_rejected(app, client, admin_headers, shop_ids, source, field):
    sale = create_case(app, client, admin_headers, shop_ids['a'])
    with app.app_context():
        if source == 'sale':
            record = AfterSale(shop_id=shop_ids['a'], order_id=sale['order_id'], customer_reference='CF-OLD',
                               kind='resend', reason='old', shipping_address={})
        elif source == 'parcel':
            record = ShippingParcel(shop_id=shop_ids['a'], order_id=sale['order_id'], label_number='PARCEL000000')
        else:
            record = db.session.get(Order, sale['order_id'])
        setattr(record, field, 'CONFLICT000000')
        db.session.add(record)
        db.session.commit()
    assert bind(client, admin_headers, sale, tracking='CONFLICT000000').status_code == 400
    assert bind(client, admin_headers, sale, barcode='CONFLICT000000').status_code == 400
    with app.app_context():
        assert db.session.get(AfterSale, sale['id']).tracking_number is None


def test_delete_releases_both_codes_and_recreated_sale_can_rebind(app, client, admin_headers, shop_ids):
    import json
    sale = create_case(app, client, admin_headers, shop_ids['a'])
    saved = bind(client, admin_headers, sale, barcode='GV26REUSE123').json['data']
    for _ in range(2):
        assert client.delete(f"/api/after-sales/{sale['id']}", headers=admin_headers).status_code == 200
    with app.app_context():
        archived = db.session.get(AfterSale, sale['id'])
        assert archived.deleted_at and archived.tracking_number is None and archived.label_barcode is None
        audit = OutboundEvent.query.filter_by(action='tracking_unbind').one()
        assert json.loads(audit.reason) == [saved['tracking_number'], saved['label_barcode']]
        assert audit.code == saved['tracking_number'] and audit.after_sale_id == sale['id']
        assert InventoryAdjustment.query.count() == 0
    response = client.post('/api/after-sales', headers=admin_headers, json={
        'order_id': sale['order_id'], 'kind': 'resend', 'reason': 'recreated',
        'items': [{'source_order_item_id': sale['items'][0]['source_order_item_id'], 'quantity': 2}],
    })
    new = response.json['data']
    assert new['customer_reference'] == sale['customer_reference']
    result = bind(client, admin_headers, new, tracking=saved['tracking_number'], barcode=saved['label_barcode'])
    assert result.status_code == 200, result.json
    for code in (saved['tracking_number'], saved['label_barcode']):
        lookup = client.get(f"/api/shipping/packing-lookup?shop_id={shop_ids['a']}&code={code}", headers=admin_headers)
        assert lookup.status_code == 200 and lookup.json['data']['id'] == new['id']


@pytest.mark.parametrize('dispatched', ['status', 'timestamp', 'scan', 'undone'])
def test_old_archive_cleanup_preserves_dispatched_labels(app, client, admin_headers, shop_ids, dispatched):
    from app.services.after_sale_service import release_archived_tracking_bindings
    sale = create_case(app, client, admin_headers, shop_ids['a'])
    with app.app_context():
        old = AfterSale(shop_id=shop_ids['a'], order_id=sale['order_id'], customer_reference='CF-OLD',
            kind='resend', reason='history', shipping_address={}, deleted_at=datetime.utcnow(),
            tracking_number='4PXHISTORY123', label_barcode='GVHISTORY123',
            status='shipped' if dispatched == 'status' else 'ready',
            shipped_at=datetime.utcnow() if dispatched == 'timestamp' else None)
        db.session.add(old)
        db.session.flush()
        if dispatched in ('scan', 'undone'):
            db.session.add(OutboundEvent(shop_id=old.shop_id, order_id=old.order_id,
                after_sale_id=old.id, order_number=sale['order_number'], action='scan', code=old.label_barcode))
            db.session.flush()
        if dispatched == 'undone':
            db.session.add(OutboundEvent(shop_id=old.shop_id, order_id=old.order_id,
                after_sale_id=old.id, order_number=sale['order_number'], action='undo', code=old.label_barcode))
        db.session.commit()
        old_id = old.id
    with app.test_request_context():
        from flask import g
        g.current_user = {'user_id': 1, 'authority': 'admin'}
        result = release_archived_tracking_bindings(shop_ids['a'])
        assert result['released_ids'] == ([old_id] if dispatched == 'undone' else [])
        again = release_archived_tracking_bindings(shop_ids['a'])
        assert again['released_ids'] == []
    result = bind(client, admin_headers, sale, tracking='4PXHISTORY123', barcode='GVHISTORY123')
    assert result.status_code == (200 if dispatched == 'undone' else 400), result.json


def test_redelete_old_unshipped_archive_releases_legacy_binding(app, client, admin_headers, shop_ids):
    sale = create_case(app, client, admin_headers, shop_ids['a'])
    with app.app_context():
        record = db.session.get(AfterSale, sale['id'])
        record.deleted_at = datetime.utcnow()
        record.tracking_number, record.label_barcode = '4PXOLD123', 'GVOLD123'
        db.session.commit()
    assert client.delete(f"/api/after-sales/{sale['id']}", headers=admin_headers).status_code == 200
    with app.app_context():
        assert db.session.get(AfterSale, sale['id']).tracking_number is None
        assert OutboundEvent.query.filter_by(action='tracking_unbind').count() == 1


@pytest.mark.parametrize('invalid', ['', 'A B', 'A\x00B', 'A' * 121, 'https://tracking.test/a', 1234, True, 'ß'])
def test_invalid_tracking_input_returns_validation_not_500(app, client, admin_headers, shop_ids, invalid):
    sale = create_case(app, client, admin_headers, shop_ids['a'])
    assert bind(client, admin_headers, sale, tracking=invalid).status_code == 400


def test_manual_binding_is_permission_shop_and_deleted_scoped(app, client, admin_headers, sub_a_headers, limited_headers, shop_ids):
    sale = create_case(app, client, admin_headers, shop_ids['b'])
    assert required_permission(f"/api/after-sales/{sale['id']}/tracking") == 'after_sales'
    assert bind(client, sub_a_headers, sale).status_code == 403
    assert bind(client, limited_headers, sale).status_code == 403
    assert client.delete(f"/api/after-sales/{sale['id']}", headers=admin_headers).status_code == 200
    assert bind(client, admin_headers, sale).status_code == 404
    own = create_case(app, client, admin_headers, shop_ids['a'])
    assert bind(client, sub_a_headers, own).status_code == 200


def test_existing_cf_overwrite_can_add_barcode_without_rebinding_original(app, client, admin_headers, shop_ids):
    sale = create_case(app, client, admin_headers, shop_ids['a'])
    with app.app_context():
        record = db.session.get(AfterSale, sale['id'])
        record.tracking_number = '4PX12345678CN'
        order = db.session.get(Order, sale['order_id'])
        order.shipping_label_number = record.tracking_number
        db.session.add(ShippingParcel(shop_id=record.shop_id, order_id=record.order_id,
                                     label_number=record.tracking_number, scanned_at=datetime.utcnow()))
        db.session.commit()
    db.session.expire_all()  # Simulate a fresh GET after the separate-context legacy update.
    current = client.get(f"/api/after-sales/{sale['id']}", headers=admin_headers).json['data']
    result = bind(client, admin_headers, current, barcode='GV26CAA0U078888888')
    assert result.status_code == 200, result.json
    with app.app_context():
        parcel = ShippingParcel.query.filter_by(order_id=sale['order_id']).one()
        assert parcel.label_barcode is None and parcel.scanned_at is not None


def test_replacement_after_shopify_writeback_is_blocked_and_barcode_can_be_added(app, client, admin_headers, shop_ids):
    from app.models import CfTrackingWriteback
    sale = create_case(app, client, admin_headers, shop_ids['a'])
    current = bind(client, admin_headers, sale).json['data']
    with app.app_context():
        db.session.add(CfTrackingWriteback(shop_id=shop_ids['a'], order_id=sale['order_id'], after_sale_id=sale['id'],
            customer_reference=sale['customer_reference'], tracking_number=current['tracking_number'],
            fulfillment_id='remote-fulfillment', previous_tracking_numbers=[], state='confirmed'))
        db.session.commit()
    assert bind(client, admin_headers, current, tracking='OLS999999999', confirm_replace=True).status_code == 400
    assert bind(client, admin_headers, current, barcode='GV26CAA0U078888888').status_code == 200


def test_order_reference_and_malformed_payload_not_accepted_as_tracking(app, client, admin_headers, shop_ids):
    sale = create_case(app, client, admin_headers, shop_ids['a'])
    assert bind(client, admin_headers, sale, tracking=sale['customer_reference']).status_code == 400
    assert bind(client, admin_headers, sale, tracking=sale['order_number']).status_code == 400
    assert client.put(f"/api/after-sales/{sale['id']}/tracking", headers=admin_headers, json=['bad']).status_code == 400
