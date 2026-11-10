"""Carrier/warehouse mismatch monitoring, durable refresh and safe reconciliation."""
import json
from datetime import datetime, timedelta

import pytest

from app import db
from app.models import (AfterSale, AfterSaleItem, InventoryAdjustment, InventoryOutboundLine, Order,
                        OrderItem, ShippingParcel, Shop, TrackingBinding, TrackingOutboundAudit,
                        TrackingRecord, TrackingRefreshJob)
from app.services import logistics_service as service
from app.services.inventory_service import _stock_rows
from app.services.inventory_pool_service import build_pool_plan
from app.services.mail_tracking_service import normalize_tracking


def facts(number, *, days=16, moved=False, delivered=False, repeat=False):
    first = datetime.utcnow() - timedelta(days=days)
    events = [{'occurDatetime': first.isoformat(), 'timeZone': 'UTC+00:00',
               'businessLinkCode': 'FPX_L_RPIF', 'trackingContent': 'Parcel information received'}]
    if repeat:
        events.append(dict(events[0]))
    if moved or delivered:
        events.append({'occurDatetime': (datetime.utcnow()-timedelta(hours=1)).isoformat(),
            'timeZone': 'UTC+00:00', 'businessLinkCode': 'FPX_S_OK' if delivered else 'FPX_O_RR',
            'trackingContent': 'Delivered' if delivered else 'Shipment arrived at facility'})
    return normalize_tracking({'deliveryOrderNo': number, 'trackingList': events}, number, include_events=True)


@pytest.fixture()
def tracking_case(app, shop_ids, monkeypatch):
    with app.app_context():
        shop = shop_ids['a']
        records = {}
        for suffix, status in [('A', 'shipped'), ('B', 'pending'), ('C', 'shipped'), ('D', 'pending'), ('E', 'pending')]:
            number = '4PXTEST' + suffix + 'CN'
            order = Order(shop_id=shop, shopify_order_id='track-'+suffix, order_number='TRACK-'+suffix,
                warehouse_status=status, financial_status='paid', total_price=1, subtotal_price=1,
                order_date=datetime.utcnow(), shipped_at=datetime.utcnow()-timedelta(days=2) if status == 'shipped' else None,
                shipping_label_number=number, shipping_label_source='4px', expected_parcel_count=1)
            db.session.add(order)
            db.session.flush()
            db.session.add(OrderItem(shop_id=shop, order_id=order.id, shopify_line_item_id='track-line-'+suffix,
                shopify_product_id='track-product', shopify_variant_id='track-'+suffix,
                title='Track case', variant_title='Khaki / iPhone 17 Pro Max', quantity=2))
            parcel = ShippingParcel(shop_id=shop, order_id=order.id, label_number=number, source='4px',
                scanned_at=order.shipped_at)
            db.session.add(parcel)
            records[suffix] = {'order': order.id, 'number': number, 'key': json.dumps(['variant', 'track-'+suffix])}
        db.session.commit()
        service.discover(shop)
        for suffix, record in records.items():
            binding = TrackingBinding.query.filter_by(order_id=record['order']).one()
            record['binding'] = binding.id
            value = facts(record['number'], moved=suffix in ('B', 'C'), delivered=suffix == 'E', repeat=suffix == 'D')
            service.save_facts(binding.record, value)
        db.session.add(InventoryAdjustment(shop_id=shop, group_key=records['B']['key'], quantity_delta=1, reason='test'))
        db.session.commit()
        monkeypatch.setattr(Shop, 'get_fourpx_credentials', lambda *_: ('test-key', 'test-secret'))
        monkeypatch.setattr(service, 'dispatch', lambda *_a, **_k: None)
        monkeypatch.setattr(service, 'fetch_facts', lambda _shop, numbers: {
            number: facts(number, moved=True, delivered=number == records['E']['number']) for number in numbers})
        yield {'shop': shop, **records}


def listing(client, headers, case, tab):
    response = client.get('/api/logistics', query_string={'shop_id': case['shop'], 'tab': tab}, headers=headers)
    assert response.status_code == 200, response.get_json()
    return response.get_json()['data']


def reconcile(client, headers, case, *keys):
    return client.post('/api/logistics/batch-outbound', headers=headers,
        json={'shop_id': case['shop'], 'ids': [case[key]['binding'] for key in keys], 'confirmed': True})


def test_three_filters_are_independent_and_follow_current_warehouse_state(app, client, admin_headers, tracking_case):
    case = tracking_case
    assert {row['order_number'] for row in listing(client, admin_headers, case, 'preinfo')['items']} == {'TRACK-A'}
    assert {row['order_number'] for row in listing(client, admin_headers, case, 'pending')['items']} == {'TRACK-B', 'TRACK-E'}
    assert {row['order_number'] for row in listing(client, admin_headers, case, 'overdue')['items']} == {
        'TRACK-A', 'TRACK-B', 'TRACK-C', 'TRACK-D'}
    with app.app_context():
        record = db.session.get(TrackingBinding, case['A']['binding']).record
        service.save_facts(record, facts(record.number, moved=True))
        order = db.session.get(Order, case['A']['order'])
        order.warehouse_status, order.shipped_at = 'pending', None
        order.parcels[0].scanned_at = None
        db.session.commit()
    assert not listing(client, admin_headers, case, 'preinfo')['items']
    assert 'TRACK-A' in {row['order_number'] for row in listing(client, admin_headers, case, 'pending')['items']}


def test_new_dispatch_is_observation_and_exact_fifteen_day_threshold(app, client, admin_headers, tracking_case):
    case = tracking_case
    with app.app_context():
        order = db.session.get(Order, case['A']['order'])
        order.shipped_at = order.parcels[0].scanned_at = datetime.utcnow() - timedelta(hours=2)
        record = db.session.get(TrackingBinding, case['A']['binding']).record
        record.first_preinfo_at = datetime.utcnow() - timedelta(days=14, hours=23)
        db.session.commit()
    assert listing(client, admin_headers, case, 'preinfo')['items'][0]['observing']
    assert 'TRACK-A' not in {row['order_number'] for row in listing(client, admin_headers, case, 'overdue')['items']}


def test_cached_collection_delivery_leaves_overdue_without_changing_stock_or_warehouse(
        app, client, admin_headers, tracking_case):
    case = tracking_case
    with app.app_context():
        record = db.session.get(TrackingBinding, case['C']['binding']).record
        checked_at, first = record.checked_at, record.first_preinfo_at
        original_status = db.session.get(Order, case['C']['order']).warehouse_status
        stock_before = [(row.id, row.quantity_delta) for row in InventoryAdjustment.query.all()]
        at = datetime.utcnow() - timedelta(days=2)
        signed = {'happenedAt': at.isoformat() + '+00:00', 'carrier_code': 'FPX_S_OKCC',
                  'message': 'Parcel delivered - collected from pick up location', 'status': 'FPX_S_OKCC'}
        # The latest event need not be the signature; inspect saved history too.
        record.events = [record.latest, signed]
        assert service.repair_saved_delivery(record)
        assert record.delivered_at == at
        assert record.checked_at == checked_at and record.first_preinfo_at == first
        assert record.next_check_at is None
        assert record.events[1]['status'] == 'DELIVERED'
        assert service.repair_saved_delivery(record)  # Idempotent, no duplicate business operations.
        db.session.commit()
        assert db.session.get(Order, case['C']['order']).warehouse_status == original_status
        assert [(row.id, row.quantity_delta) for row in InventoryAdjustment.query.all()] == stock_before
        assert TrackingOutboundAudit.query.count() == InventoryOutboundLine.query.count() == 0
    assert 'TRACK-C' not in {row['order_number'] for row in listing(client, admin_headers, case, 'overdue')['items']}


@pytest.mark.parametrize(('code', 'message', 'time'), [
    ('FPX_R_RDOW', 'Delivered, To Original Sender', '2026-09-02T07:00:00+00:00'),
    ('FPX_D_SASPS', 'Parcel available for collection', '2026-09-02T07:00:00+00:00'),
    ('FPX_S_OKCC', 'Parcel delivered - collected from pick up location', 'invalid'),
    ('FPX_S_OKCC', 'Delivered - left as authorised', '2999-01-01T00:00:00+00:00'),
])
def test_cached_delivery_repair_requires_real_customer_signature_and_reliable_time(
        app, tracking_case, code, message, time):
    with app.app_context():
        record = db.session.get(TrackingBinding, tracking_case['C']['binding']).record
        record.latest = {'happenedAt': time, 'carrier_code': code, 'message': message}
        record.events = [record.latest]
        assert not service.repair_saved_delivery(record)
        assert record.delivered_at is None


def test_new_collection_delivery_disappears_from_overdue_and_manual_refresh_remains_possible(
        app, client, admin_headers, tracking_case):
    with app.app_context():
        record = db.session.get(TrackingBinding, tracking_case['B']['binding']).record
        value = facts(record.number, delivered=True)
        for event in value['events']:
            if event['status'] == 'DELIVERED':
                event.update(carrier_code='FPX_S_OKCC', status='FPX_S_OKCC',
                             message='Delivered - left as authorised')
        raw = {'deliveryOrderNo': record.number, 'trackingList': [
            {'occurDatetime': event['happenedAt'], 'businessLinkCode': event['carrier_code'],
             'trackingContent': event['message']} for event in value['events']]}
        service.save_facts(record, normalize_tracking(raw, record.number, include_events=True))
        assert record.delivered_at and record.next_check_at is None
        record.requested_at = datetime.utcnow()
        assert service.repair_saved_delivery(record)
        assert record.next_check_at == record.requested_at
        db.session.commit()
    assert 'TRACK-B' not in {row['order_number'] for row in listing(
        client, admin_headers, tracking_case, 'overdue')['items']}


def test_one_off_cached_repair_is_shop_scoped_dry_run_and_idempotent(app, tracking_case, shop_ids):
    from scripts.repair_tracking_deliveries import repair
    with app.app_context():
        record = db.session.get(TrackingBinding, tracking_case['C']['binding']).record
        signed = {'happenedAt': (datetime.utcnow()-timedelta(days=1)).isoformat()+'+00:00',
                  'carrier_code': 'FPX_S_OKCC', 'message': 'Delivered - left as authorised'}
        record.latest = signed
        record.events = [signed]
        record_id = record.id
        db.session.commit()
        assert repair(shop_ids['b'], apply=True)['corrected'] == 0
        preview = repair(tracking_case['shop'])
        assert preview['corrected'] == 1
        assert preview['overdue_before'] - preview['overdue_after'] == 1
        assert db.session.get(TrackingRecord, record_id).delivered_at is None
        result = repair(tracking_case['shop'], apply=True)
        assert result['corrected'] == 1
        assert db.session.get(TrackingRecord, record_id).delivered_at is not None
        assert repair(tracking_case['shop'], apply=True)['corrected'] == 0


def test_force_deducts_available_only_and_undo_never_returns_missing_goods(app, client, admin_headers, tracking_case):
    case = tracking_case
    result = reconcile(client, admin_headers, case, 'B').get_json()['data']['results'][0]
    assert result['state'] == 'succeeded'
    assert result['stock_result']['deducted_quantity'] == result['stock_result']['unaccounted_quantity'] == 1
    assert reconcile(client, admin_headers, case, 'B').get_json()['data']['results'][0]['state'] == 'skipped'
    with app.app_context():
        assert _stock_rows(case['shop'], group_keys={case['B']['key']})[case['B']['key']]['on_hand_quantity'] == 0
        assert TrackingOutboundAudit.query.count() == 1
        assert sum(line.quantity_delta for line in InventoryOutboundLine.query.all()) == -1
    undo = client.post('/api/shipping/outbound/undo', headers=admin_headers,
        json={'shop_id': case['shop'], 'order_id': case['B']['order']})
    assert undo.status_code == 200, undo.get_json()
    with app.app_context():
        assert _stock_rows(case['shop'], group_keys={case['B']['key']})[case['B']['key']]['on_hand_quantity'] == 1


def test_preinfo_or_unverified_latest_cannot_force_outbound(app, client, admin_headers, tracking_case, monkeypatch):
    case = tracking_case
    for value in (facts(case['B']['number']), {'error': 'provider unavailable'}):
        monkeypatch.setattr(service, 'fetch_facts', lambda _s, nums: {num: value for num in nums})
        result = reconcile(client, admin_headers, case, 'B').get_json()['data']['results'][0]
        assert result['state'] == 'failed'
    with app.app_context():
        assert db.session.get(Order, case['B']['order']).warehouse_status == 'pending'
        assert TrackingOutboundAudit.query.count() == InventoryOutboundLine.query.count() == 0


def test_zero_stock_multi_parcel_reconciliation_then_normal_scan_and_undo(app, client, admin_headers, tracking_case):
    case = tracking_case
    with app.app_context():
        InventoryAdjustment.query.filter_by(shop_id=case['shop']).update({'quantity_delta': 0})
        order = db.session.get(Order, case['B']['order'])
        order.expected_parcel_count = 2
        db.session.add(ShippingParcel(shop_id=case['shop'], order_id=order.id, label_number='4PXSECOND', source='4px'))
        db.session.commit()
    assert reconcile(client, admin_headers, case, 'B').get_json()['data']['results'][0]['state'] == 'succeeded'
    with app.app_context():
        assert db.session.get(Order, case['B']['order']).warehouse_status == 'pending'
        pool = build_pool_plan(case['shop'])
        assert not any(pool['uncovered_by_order'][case['B']['order']].values())
    scan = client.post('/api/shipping/scan', headers=admin_headers,
        json={'shop_id': case['shop'], 'shipping_label_number': '4PXSECOND'})
    assert scan.status_code == 200, scan.get_json()
    assert client.post('/api/shipping/outbound/undo', headers=admin_headers,
        json={'shop_id': case['shop'], 'order_id': case['B']['order']}).status_code == 200
    with app.app_context():
        assert _stock_rows(case['shop'], group_keys={case['B']['key']})[case['B']['key']]['on_hand_quantity'] == 0


def test_cf_mapping_takes_precedence_and_undo_returns_actual_source(app, client, admin_headers, tracking_case):
    case = tracking_case
    with app.app_context():
        sale = AfterSale(shop_id=case['shop'], order_id=case['A']['order'], customer_reference='CFTRACK-A',
            kind='resend', reason='test', status='purchasing', shipping_address={}, tracking_number=case['A']['number'])
        db.session.add(sale)
        db.session.flush()
        sale_id = sale.id
        db.session.add(AfterSaleItem(after_sale_id=sale.id, group_key=case['B']['key'],
            shopify_product_id='track-product', shopify_variant_id='track-B', title='Track case',
            variant_title='Khaki / iPhone 17 Pro Max', quantity=2))
        service.discover(case['shop'])
        db.session.commit()
        binding = TrackingBinding.query.filter_by(after_sale_id=sale_id).one()
        service.save_facts(binding.record, facts(case['A']['number'], moved=True))
        db.session.commit()
        case['CF'] = {'binding': binding.id}
    rows = listing(client, admin_headers, case, 'pending')['items']
    assert 'CFTRACK-A' in {row['order_number'] for row in rows}
    assert 'TRACK-A' not in {row['order_number'] for row in rows}
    result = reconcile(client, admin_headers, case, 'CF').get_json()['data']['results'][0]
    assert result['state'] == 'succeeded'
    response = client.post('/api/shipping/outbound/undo', headers=admin_headers,
        json={'shop_id': case['shop'], 'after_sale_id': sale_id})
    assert response.status_code == 200, response.get_json()
    with app.app_context():
        assert db.session.get(Order, case['A']['order']).warehouse_status == 'shipped'
        assert _stock_rows(case['shop'], group_keys={case['B']['key']})[case['B']['key']]['on_hand_quantity'] == 1


def test_refresh_is_background_idempotent_and_query_failure_retains_last_good_state(
        app, client, admin_headers, tracking_case, monkeypatch):
    case = tracking_case
    sent = []
    with app.app_context():
        for record in TrackingRecord.query.all():
            record.checked_at = datetime.utcnow()-timedelta(hours=8)
            record.next_check_at = datetime.utcnow()-timedelta(seconds=1)
        db.session.commit()
    monkeypatch.setattr(service, 'dispatch', lambda job_id, **_k: sent.append(job_id))
    response = client.post('/api/logistics/refresh', headers=admin_headers, json={'shop_id': case['shop'], 'scope': 'all'})
    assert response.status_code == 200
    job_id = response.get_json()['data']['id']
    assert sent == [job_id]
    assert client.post('/api/logistics/refresh', headers=admin_headers,
        json={'shop_id': case['shop'], 'scope': 'all'}).get_json()['data']['id'] == job_id
    with app.app_context():
        monkeypatch.setattr(service, 'fetch_facts', lambda _s, nums: {num: {'error': 'temporary failure'} for num in nums})
        service.process_batch(job_id)
        job = db.session.get(TrackingRefreshJob, job_id)
        assert job.status == 'completed' and job.failed == job.done > 0
        before = job.done
        service.process_batch(job_id)
        assert job.done == before
        assert db.session.get(TrackingBinding, case['A']['binding']).record.only_preinfo


def test_permissions_scope_and_confirmation(client, admin_headers, limited_headers, sub_b_headers, sub_a_headers, shop_ids, tracking_case):
    case = tracking_case
    assert client.get('/api/logistics?shop_id=1').status_code == 401
    assert client.get('/api/logistics?shop_id=1', headers=limited_headers).status_code == 403
    assert reconcile(client, limited_headers, case, 'B').status_code == 403
    assert client.get(f'/api/logistics?shop_id={shop_ids["b"]}', headers=sub_a_headers).status_code == 403
    assert reconcile(client, sub_b_headers, case, 'B').status_code == 403
    assert client.post('/api/logistics/batch-outbound', headers=admin_headers,
        json={'shop_id': case['shop'], 'ids': [case['B']['binding']]}).status_code == 400


def test_lists_are_local_read_only_and_paginated(app, client, admin_headers, tracking_case, monkeypatch):
    from sqlalchemy import event
    monkeypatch.setattr(service, 'fetch_facts', lambda *_: pytest.fail('List must not query carrier'))
    with app.app_context():
        statements = []
        def count(_conn, _cursor, statement, *_):
            statements.append(statement)
        event.listen(db.engine, 'before_cursor_execute', count)
        try:
            response = client.get('/api/logistics', headers=admin_headers,
                query_string={'shop_id': tracking_case['shop'], 'tab': 'pending', 'per_page': 1, 'page': 2})
        finally:
            event.remove(db.engine, 'before_cursor_execute', count)
        assert response.status_code == 200
        assert response.get_json()['data']['total'] == 2
        assert len(response.get_json()['data']['items']) == 1
        assert len(statements) < 20
        assert not any(s.lstrip().upper().startswith(('UPDATE ', 'INSERT ', 'DELETE ')) for s in statements)


def test_duplicate_preinfo_is_not_transport_and_search_is_exact(client, admin_headers, tracking_case):
    value = facts('4PXREPEAT', repeat=True)
    assert not value['only_preinfo'] and not value['has_movement'] and value['event_count'] == 2
    for tab in ('pending', 'overdue'):
        response = client.get('/api/logistics', headers=admin_headers,
            query_string={'shop_id': tracking_case['shop'], 'tab': tab, 'search': 'TRACK'})
        assert response.get_json()['data']['total'] == 0


def test_stale_or_ambiguous_binding_cannot_reconcile(app, client, admin_headers, tracking_case):
    case = tracking_case
    with app.app_context():
        db.session.add(TrackingBinding(shop_id=case['shop'], target_key='order:duplicate',
            record_id=db.session.get(TrackingBinding, case['B']['binding']).record_id,
            order_id=case['C']['order'], active=True))
        db.session.commit()
    result = reconcile(client, admin_headers, case, 'B').get_json()['data']['results'][0]
    assert result['state'] == 'failed' and '多个订单' in result['message']
    with app.app_context():
        assert InventoryOutboundLine.query.count() == 0


def test_deleted_cf_is_retired_and_original_binding_returns(app, tracking_case):
    case = tracking_case
    with app.app_context():
        sale = AfterSale(shop_id=case['shop'], order_id=case['A']['order'], customer_reference='CFRETIRE',
            kind='resend', reason='test', status='purchasing', shipping_address={}, tracking_number=case['A']['number'])
        db.session.add(sale)
        db.session.flush()
        service.discover(case['shop'])
        db.session.commit()
        assert not db.session.get(TrackingBinding, case['A']['binding']).active
        sale.deleted_at = datetime.utcnow()
        service.discover(case['shop'])
        db.session.commit()
        assert db.session.get(TrackingBinding, case['A']['binding']).active
        assert not TrackingBinding.query.filter_by(after_sale_id=sale.id).one().active


def test_audit_history_has_no_fifty_or_hundred_record_cutoff(app, client, admin_headers, tracking_case):
    case = tracking_case
    with app.app_context():
        db.session.add_all([TrackingOutboundAudit(shop_id=case['shop'], binding_id=case['B']['binding'],
            order_id=case['B']['order'], evidence={'number': case['B']['number']}, stock_result={}) for _ in range(105)])
        db.session.commit()
    response = client.get('/api/logistics/outbound-audits', headers=admin_headers,
        query_string={'shop_id': case['shop'], 'page': 6, 'per_page': 20})
    result = response.get_json()['data']
    assert result['total'] == 105 and len(result['items']) == 5


def test_job_lease_prevents_duplicate_processing_and_recovers_abandonment(app, tracking_case, monkeypatch):
    case = tracking_case
    with app.app_context():
        job = TrackingRefreshJob(id='locked', shop_id=case['shop'], scope='all', status='running',
            record_ids=[db.session.get(TrackingBinding, case['B']['binding']).record_id],
            lease_until=datetime.utcnow()+timedelta(minutes=1))
        db.session.add(job)
        db.session.commit()
        monkeypatch.setattr(service, 'fetch_facts', lambda *_: pytest.fail('Active lease must not query carrier'))
        service.process_batch(job.id)
        assert job.done == 0
        job.lease_until = datetime.utcnow()-timedelta(minutes=1)
        job.updated_at = datetime.utcnow()-timedelta(minutes=6)
        db.session.commit()
        sent = []
        monkeypatch.setattr(service, 'dispatch', lambda job_id: sent.append(job_id))
        service.recover_jobs()
        assert sent == ['locked']


def test_first_preinfo_date_survives_shorter_carrier_history(app, tracking_case):
    with app.app_context():
        record = db.session.get(TrackingBinding, tracking_case['B']['binding']).record
        first = record.first_preinfo_at
        value = facts(record.number, days=2, moved=True)
        service.save_facts(record, value)
        assert record.first_preinfo_at == first
        service.save_facts(record, {**value, 'first_preinfo_at': None})
        assert record.first_preinfo_at == first


def test_bulk_force_argument_cannot_bypass_the_per_number_clock(app, tracking_case):
    with app.app_context():
        result = service.create_job(tracking_case['shop'], force=True)
        assert result['total'] == 0 and result['status'] == 'completed'
        assert TrackingRefreshJob.query.count() == 0


def test_pending_movement_is_auto_reconciled_without_manual_click(app, client, admin_headers, tracking_case):
    from app.services.logistics_outbound_service import auto_reconcile
    case = tracking_case
    with app.app_context():
        summary = auto_reconcile(shop_id=case['shop'])
        assert summary['succeeded'] == 2, summary
        assert db.session.get(Order, case['B']['order']).warehouse_status == 'shipped'
        assert db.session.get(Order, case['E']['order']).warehouse_status == 'shipped'
        assert [row['order_number'] for row in listing(client, admin_headers, case, 'pending')['items']] == []
        audits = TrackingOutboundAudit.query.count()
        assert audits == 2
        second = auto_reconcile(shop_id=case['shop'])
        assert second['succeeded'] == 0
        assert TrackingOutboundAudit.query.count() == audits


def test_refunded_order_with_real_movement_still_auto_outbounds(app, tracking_case):
    """退款只代表不发货；4PX 已有实际运输轨迹说明货已出仓，库存必须扣减。"""
    from app.services.logistics_outbound_service import auto_reconcile
    case = tracking_case
    with app.app_context():
        order = db.session.get(Order, case['B']['order'])
        order.financial_status = 'refunded'
        db.session.commit()
        summary = auto_reconcile(shop_id=case['shop'])
        assert summary['succeeded'] >= 1
        assert db.session.get(Order, case['B']['order']).warehouse_status == 'shipped'


def test_unpaid_order_with_movement_is_not_auto_outbound(app, tracking_case):
    from app.services.logistics_outbound_service import auto_reconcile
    case = tracking_case
    with app.app_context():
        order = db.session.get(Order, case['E']['order'])
        order.financial_status = 'pending'
        db.session.commit()
        summary = auto_reconcile(shop_id=case['shop'])
        assert summary['failed'] == 1
        assert db.session.get(Order, case['E']['order']).warehouse_status == 'pending'


def test_refunded_zero_quantity_order_restores_items_then_outbounds(app, tracking_case, monkeypatch):
    """退款后本地商品数量被同步成 0，补记前应从 Shopify 恢复原始数量再扣库存。"""
    from app.services.logistics_outbound_service import auto_reconcile
    case = tracking_case
    with app.app_context():
        order = db.session.get(Order, case['B']['order'])
        order.financial_status = 'refunded'
        for item in order.items:
            item.quantity = 0
        db.session.commit()
        restored = []

        def fake_restore(target):
            for item in target.items:
                item.quantity = 1
            db.session.flush()
            restored.append(target.id)
            return 1

        monkeypatch.setattr('app.services.order_service.restore_refunded_item_quantities', fake_restore)
        summary = auto_reconcile(shop_id=case['shop'])
        assert summary['succeeded'] >= 1
        assert restored
        assert db.session.get(Order, case['B']['order']).warehouse_status == 'shipped'


def test_legacy_unverified_order_with_movement_marks_outbound_without_stock_deduction(app, tracking_case):
    """历史迁移订单：4PX 有真实轨迹时自动核实出库，但不追溯扣当前库存。"""
    from app.models import InventoryOutboundLine
    from app.services.logistics_outbound_service import auto_reconcile
    case = tracking_case
    with app.app_context():
        order = db.session.get(Order, case['B']['order'])
        order.warehouse_status = 'legacy_unverified'
        before_lines = InventoryOutboundLine.query.filter_by(order_id=order.id).count()
        db.session.commit()
        summary = auto_reconcile(shop_id=case['shop'])
        assert summary['succeeded'] >= 1, summary
        refreshed = db.session.get(Order, case['B']['order'])
        assert refreshed.warehouse_status == 'shipped'
        assert refreshed.shipped_at is not None
        assert InventoryOutboundLine.query.filter_by(order_id=order.id).count() == before_lines
        audit = TrackingOutboundAudit.query.filter_by(order_id=order.id).order_by(
            TrackingOutboundAudit.id.desc()).first()
        assert audit.evidence.get('historical') is True
        assert audit.evidence.get('stock_deducted') is False
