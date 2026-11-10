"""Manual follow-up is independent of carrier/warehouse facts; SQL sorts before pagination."""
from datetime import datetime, timedelta

import pytest

from app import db
from app.models import AfterSale, InventoryAdjustment, Order, TrackingBinding, TrackingRecord, TrackingOutboundAudit
from app.services import logistics_service as service
from tests.test_logistics import tracking_case as _tracking_case, facts

tracking_case = _tracking_case


def update(client, headers, case, value=True, *, key='A', shop=None):
    return client.patch(f'/api/logistics/{case[key]["binding"]}/handling', headers=headers,
        json={'shop_id': shop or case['shop'], 'handled': value})


def get_list(client, headers, case, **args):
    response = client.get('/api/logistics', headers=headers, query_string={'shop_id':case['shop'], 'tab':'overdue', **args})
    assert response.status_code == 200, response.get_json()
    return response.get_json()['data']


def test_manual_handle_filter_actor_idempotence_and_reopen(app, client, admin_headers, tracking_case):
    case = tracking_case
    before = get_list(client, admin_headers, case)
    assert before['handling_counts'] == {'handled':0,'unhandled':4}
    result = update(client, admin_headers, case)
    assert result.status_code == 200, result.get_json()
    value = result.get_json()['data']
    assert value['handled'] and value['handled_at'] and value['handled_by_name'] == 'admin'
    assert update(client, admin_headers, case).get_json()['data']['handled_at'] == value['handled_at']
    assert case['A']['binding'] not in [item['id'] for item in get_list(client, admin_headers, case)['items']]
    done = get_list(client, admin_headers, case, handling='handled')
    assert [item['id'] for item in done['items']] == [case['A']['binding']]
    assert done['items'][0]['handled_by_name'] == 'admin'
    assert done['handling_counts'] == {'handled':1,'unhandled':3}
    assert get_list(client, admin_headers, case, handling='all')['total'] == 4
    with app.app_context():
        record = db.session.get(TrackingBinding, case['A']['binding']).record
        service.save_facts(record, facts(record.number, moved=True))
        service.discover(case['shop'], [case['A']['order']])
        db.session.commit()
        assert record.handled_at and record.handled_by == value['handled_by']
        assert db.session.get(Order, case['A']['order']).warehouse_status == 'shipped'
        assert InventoryAdjustment.query.count() == 1
        assert TrackingOutboundAudit.query.count() == 0
    reopened = update(client, admin_headers, case, False).get_json()['data']
    assert not reopened['handled'] and reopened['handled_at'] is None and reopened['handled_by'] is None
    assert get_list(client, admin_headers, case, handling='handled')['total'] == 0
    assert get_list(client, admin_headers, case)['total'] == 4


def test_handled_delivered_parcel_still_leaves_overdue(app, client, admin_headers, tracking_case):
    assert update(client, admin_headers, tracking_case).status_code == 200
    with app.app_context():
        record = db.session.get(TrackingBinding, tracking_case['A']['binding']).record
        service.save_facts(record, facts(record.number, delivered=True))
        db.session.commit()
        assert record.handled_at and record.delivered_at
    assert get_list(client, admin_headers, tracking_case, handling='handled')['total'] == 0
    assert get_list(client, admin_headers, tracking_case, handling='all')['total'] == 3


@pytest.mark.parametrize('value', ['true', 1, None, [], {}])
def test_handling_requires_boolean(client, admin_headers, tracking_case, value):
    assert update(client, admin_headers, tracking_case, value).status_code == 400


def test_handling_module_shop_scope_and_current_eligibility(app, client, admin_headers, limited_headers, sub_a_headers,
                                                         shop_ids, tracking_case):
    case = tracking_case
    assert update(client, {}, case).status_code == 401
    assert update(client, limited_headers, case).status_code == 403
    assert update(client, sub_a_headers, case, shop=shop_ids['b']).status_code == 403
    assert update(client, admin_headers, case, shop=shop_ids['b']).status_code == 404
    assert update(client, admin_headers, case, key='E').status_code == 400  # Already delivered.
    with app.app_context():
        db.session.get(TrackingBinding, case['A']['binding']).record.first_preinfo_at = datetime.utcnow()-timedelta(days=14)
        db.session.commit()
    # A preinfo parcel stays actionable even after 24h, so refunded orders can be cleared.
    assert update(client, admin_headers, case).status_code == 200
    assert update(client, admin_headers, case, False).status_code == 200
    with app.app_context():
        record = db.session.get(TrackingBinding, case['C']['binding']).record
        record.first_preinfo_at = datetime.utcnow()-timedelta(days=14)
        db.session.commit()
    assert update(client, admin_headers, case, key='C').status_code == 400  # Has movement, not overdue.
    bad = client.get('/api/logistics', headers=admin_headers,
        query_string={'shop_id':case['shop'], 'tab':'overdue', 'handling':'invalid'})
    assert bad.status_code == 400
    assert client.patch(f'/api/logistics/{case["A"]["binding"]}/handling', headers=admin_headers,
                        json=['not an object']).status_code == 400


@pytest.mark.parametrize('tab', ['preinfo','pending','overdue'])
def test_all_tabs_sort_numeric_orders_and_cf_before_pagination(app, client, admin_headers, tracking_case, tab):
    case = tracking_case
    with app.app_context():
        for key, number in [('A','10'),('B','2'),('C','100'),('D','09'),('E','#11')]:
            order = db.session.get(Order, case[key]['order'])
            order.order_number = number
            shipped = tab != 'pending'
            order.warehouse_status = 'shipped' if shipped else 'pending'
            order.shipped_at = order.parcels[0].scanned_at = datetime.utcnow()-timedelta(days=2) if shipped else None
            record = db.session.get(TrackingBinding, case[key]['binding']).record
            record.delivered_at, record.has_movement = None, False
            service.save_facts(record, facts(record.number, moved=tab != 'preinfo'))
        sale = AfterSale(shop_id=case['shop'], order_id=case['A']['order'], customer_reference='CF10',
            kind='same', reason='test', shipping_address={}, status='shipped' if tab != 'pending' else 'ready',
            shipped_at=datetime.utcnow()-timedelta(days=2) if tab != 'pending' else None)
        record = TrackingRecord(shop_id=case['shop'], number='4PXCF10')
        db.session.add_all([sale,record])
        db.session.flush()
        db.session.add(TrackingBinding(shop_id=case['shop'], order_id=sale.order_id, record_id=record.id,
            after_sale_id=sale.id, target_key=f'sale:{sale.id}'))
        service.save_facts(record, facts(record.number, moved=tab != 'preinfo'))
        db.session.commit()
    numbers=[]
    for page in (1,2,3):
        data=get_list(client, admin_headers, case, tab=tab, page=page, per_page=2)
        numbers.extend(item['order_number'] for item in data['items'])
    assert numbers == ['2','09','10','CF10','#11','100']


def test_preinfo_24h_split_and_manual_handling(app, client, admin_headers, tracking_case):
    case = tracking_case

    def preinfo(**args):
        response = client.get('/api/logistics', headers=admin_headers, query_string={
            'shop_id': case['shop'], 'tab': 'preinfo', **args})
        assert response.status_code == 200, response.get_json()
        return response.get_json()['data']

    with app.app_context():
        order = db.session.get(Order, case['A']['order'])
        order.shipped_at = order.parcels[0].scanned_at = datetime.utcnow() - timedelta(hours=2)
        record = db.session.get(TrackingBinding, case['A']['binding']).record
        record.first_preinfo_at = datetime.utcnow() - timedelta(hours=2)
        db.session.commit()

    recent = preinfo(age='within24h')
    assert [item['id'] for item in recent['items']] == [case['A']['binding']]
    assert recent['preinfo_age_counts'] == {'within24h': 1, 'beyond24h': 0}
    assert recent['preinfo_handling_counts'] == {'handled': 0, 'unhandled': 1}
    assert preinfo(age='beyond24h')['items'] == []

    assert update(client, admin_headers, case).status_code == 200
    assert preinfo(age='within24h')['items'] == []
    handled = preinfo(age='within24h', handling='handled')
    assert [item['id'] for item in handled['items']] == [case['A']['binding']]
    assert handled['preinfo_handling_counts'] == {'handled': 1, 'unhandled': 0}
    assert update(client, admin_headers, case, False).status_code == 200

    # Beyond 24 hours stays markable, so refunded/closed shipments can be cleared from the queue.
    with app.app_context():
        order = db.session.get(Order, case['A']['order'])
        order.shipped_at = order.parcels[0].scanned_at = datetime.utcnow() - timedelta(days=2)
        record = db.session.get(TrackingBinding, case['A']['binding']).record
        record.first_preinfo_at = datetime.utcnow() - timedelta(hours=2)
        db.session.commit()
    assert preinfo(age='within24h')['items'] == []
    assert [item['id'] for item in preinfo(age='beyond24h')['items']] == [case['A']['binding']]
    assert update(client, admin_headers, case).status_code == 200
    assert preinfo(age='beyond24h')['items'] == []
    beyond_handled = preinfo(age='beyond24h', handling='handled')
    assert [item['id'] for item in beyond_handled['items']] == [case['A']['binding']]
    assert beyond_handled['handling_counts'] == {'handled': 1, 'unhandled': 0}


def test_order_column_can_sort_descending(app, client, admin_headers, tracking_case):
    case = tracking_case
    with app.app_context():
        for key, number in [('A', '10'), ('B', '2'), ('C', '100'), ('D', '09'), ('E', '#11')]:
            order = db.session.get(Order, case[key]['order'])
            order.order_number = number
            order.warehouse_status = 'shipped'
            order.shipped_at = datetime.utcnow() - timedelta(days=2)
            record = db.session.get(TrackingBinding, case[key]['binding']).record
            record.delivered_at = None
            record.first_preinfo_at = datetime.utcnow() - timedelta(days=16)
        db.session.commit()

    response = client.get('/api/logistics', headers=admin_headers, query_string={
        'shop_id': case['shop'], 'tab': 'overdue', 'order': 'desc', 'per_page': 20})
    assert response.status_code == 200, response.get_json()
    numbers = [item['order_number'] for item in response.get_json()['data']['items']]
    assert numbers == ['100', '#11', '10', '09', '2']
    invalid = client.get('/api/logistics', headers=admin_headers, query_string={
        'shop_id': case['shop'], 'tab': 'overdue', 'order': 'sideways'})
    assert invalid.status_code == 400
