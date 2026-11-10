"""Scan rejections are durable, categorized and never commit failed stock changes."""
from datetime import datetime

from app import db
from app.models import AfterSale, Order, OrderItem, OutboundScanFailure, ShippingParcel, InventoryAdjustment
from app.services import outbound_scan_failure_service as failures
from app.utils.errors import ValidationError
from tests.test_outbound_undo import _ready_order_with_parcel


def _scan(client, headers, shop_id, code):
    return client.post('/api/shipping/scan', headers=headers,
                       json={'shop_id': shop_id, 'shipping_label_number': code})


def _records(client, headers, shop_id, **params):
    return client.get('/api/shipping/outbound/failures', headers=headers,
                      query_string={'shop_id': shop_id, **params})


def test_unknown_labels_persist_and_merge_daily_with_pagination(client, admin_headers, shop_ids):
    for _ in range(2):
        response = _scan(client, admin_headers, shop_ids['a'], 'GV-UNKNOWN')
        assert response.status_code == 400
        assert response.json['scan_category'] == 'not_found'
        assert response.json['scan_failure_recorded'] is True
    page = _records(client, admin_headers, shop_ids['a']).json['data']
    assert len(page['records']) == 1
    assert page['records'][0]['attempts'] == 2
    assert page['records'][0]['order_id'] is None
    assert page['records'][0]['operator']
    assert page['summary']['not_found'] == {'records': 1, 'attempts': 2}
    for index in range(22):
        _scan(client, admin_headers, shop_ids['a'], f'GV-MISSING-{index}')
    assert _records(client, admin_headers, shop_ids['a']).json['data']['pagination']['total'] == 23
    second = _records(client, admin_headers, shop_ids['a'], page=2).json['data']
    assert len(second['records']) == 3
    assert _records(client, admin_headers, shop_ids['a'], category='stock_shortage').json['data']['records'] == []
    assert _records(client, admin_headers, shop_ids['a'], search='GV-UNKNOWN').json['data']['pagination']['total'] == 1


def test_duplicate_original_and_after_sale_scans_are_not_recorded(app, client, admin_headers, shop_ids):
    order_id, _key = _ready_order_with_parcel(app, client, admin_headers, shop_ids['a'])
    assert _scan(client, admin_headers, shop_ids['a'], 'GV26-UNDO-1').status_code == 200
    response = _scan(client, admin_headers, shop_ids['a'], 'GV26-UNDO-1')
    assert response.status_code == 400
    assert response.json['scan_category'] == 'duplicate'
    assert response.json['scan_failure_recorded'] is False
    with app.app_context():
        sale = AfterSale(shop_id=shop_ids['a'], order_id=order_id, customer_reference='CF-TEST',
                         kind='resend', reason='test', status='shipped', shipped_at=datetime.utcnow(),
                         shipping_address={}, tracking_number='CF-LABEL', label_barcode='GV-CF-LABEL')
        db.session.add(sale)
        db.session.commit()
    cf_response = _scan(client, admin_headers, shop_ids['a'], 'GV-CF-LABEL')
    assert cf_response.status_code == 400
    assert cf_response.json['scan_category'] == 'duplicate'
    assert _records(client, admin_headers, shop_ids['a']).json['data']['records'] == []


def test_stock_status_and_label_rejections_include_order_context(app, client, admin_headers, shop_ids):
    with app.app_context():
        order = Order.query.filter_by(shop_id=shop_ids['a']).first()
        order.warehouse_status = 'pending'
        db.session.add(OrderItem(shop_id=order.shop_id, order_id=order.id, shopify_line_item_id='missing-line',
                                 shopify_variant_id='missing-variant', title='Case', variant_title='Blue / iPhone 16', quantity=1))
        db.session.add(ShippingParcel(shop_id=order.shop_id, order_id=order.id, label_number='GV-BLOCKED', label_barcode='GV-BLOCKED'))
        order_id, order_number = order.id, order.order_number
        db.session.commit()
    assert _scan(client, admin_headers, shop_ids['a'], 'GV-BLOCKED').json['scan_category'] == 'stock_shortage'
    with app.app_context():
        order = db.session.get(Order, order_id)
        assert order.warehouse_status == 'pending'
        assert order.parcels[0].scanned_at is None
        order.financial_status = 'refunded'
        db.session.commit()
    assert _scan(client, admin_headers, shop_ids['a'], 'GV-BLOCKED').json['scan_category'] == 'order_status'
    with app.app_context():
        order = db.session.get(Order, order_id)
        order.financial_status = 'paid'
        order.expected_parcel_count = 2
        db.session.commit()
    assert _scan(client, admin_headers, shop_ids['a'], 'GV-BLOCKED').json['scan_category'] == 'label_error'
    records = _records(client, admin_headers, shop_ids['a']).json['data']['records']
    assert len(records) == 3
    assert all(record['order_id'] == order_id and record['order_number'] == order_number for record in records)


def test_beijing_midnight_separates_daily_records(app, client, admin_headers, shop_ids, monkeypatch):
    class Clock(datetime):
        moment = datetime(2026, 10, 14, 15, 59, 59)

        @classmethod
        def utcnow(cls):
            return cls.moment

    monkeypatch.setattr(failures, 'datetime', Clock)
    _scan(client, admin_headers, shop_ids['a'], 'GV-DAY')
    Clock.moment = datetime(2026, 10, 14, 16, 0, 0)
    _scan(client, admin_headers, shop_ids['a'], 'GV-DAY')
    assert _records(client, admin_headers, shop_ids['a'], date='2026-10-14').json['data']['records'][0]['attempts'] == 1
    assert _records(client, admin_headers, shop_ids['a'], date='2026-10-15').json['data']['records'][0]['attempts'] == 1
    assert _records(client, admin_headers, shop_ids['a']).json['data']['date'] == '2026-10-15'


def test_failure_logging_rolls_back_partially_mutated_outbound(app, client, admin_headers, shop_ids, monkeypatch):
    from app.services import shipping_queue_service
    order_id, group_key = _ready_order_with_parcel(app, client, admin_headers, shop_ids['a'])

    def failed_outbound(*_args):
        order = db.session.get(Order, order_id)
        order.warehouse_status = 'shipped'
        db.session.add(InventoryAdjustment(shop_id=order.shop_id, group_key=group_key, quantity_delta=-1, reason='must rollback'))
        db.session.flush()
        raise ValidationError('实际库存不足')

    monkeypatch.setattr(shipping_queue_service, 'scan_outbound', failed_outbound)
    assert _scan(client, admin_headers, shop_ids['a'], 'GV26-UNDO-1').status_code == 400
    with app.app_context():
        assert db.session.get(Order, order_id).warehouse_status == 'pending'
        assert InventoryAdjustment.query.filter_by(reason='must rollback').count() == 0
        assert OutboundScanFailure.query.count() == 1


def test_shop_and_module_isolation_and_invalid_filters(client, admin_headers, sub_a_headers, limited_headers, shop_ids):
    _scan(client, admin_headers, shop_ids['b'], 'GV-SHOP-B')
    assert _records(client, sub_a_headers, shop_ids['b']).status_code == 403
    assert _records(client, limited_headers, shop_ids['a']).status_code == 403
    assert _scan(client, sub_a_headers, shop_ids['b'], 'GV-SHOP-B').status_code == 403
    assert _records(client, admin_headers, shop_ids['a']).json['data']['records'] == []
    for query in ({'date': 'bad'}, {'category': 'duplicate'}, {'page': 'invalid'}):
        assert _records(client, admin_headers, shop_ids['a'], **query).status_code == 400
    assert failures.classify(ValidationError('历史订单出库状态待核实，不能重复扫码出库')) == 'order_status'


def test_employee_deletion_preserves_failed_scan_history(app, client, admin_headers, sub_a_headers, shop_ids):
    from app.models import User
    _scan(client, sub_a_headers, shop_ids['a'], 'GV-EMPLOYEE')
    with app.app_context():
        user_id = User.query.filter_by(username='sub_a').one().id
    assert client.delete(f'/api/users/{user_id}', headers=admin_headers).status_code == 200
    record = _records(client, admin_headers, shop_ids['a']).json['data']['records'][0]
    assert record['scan_code'] == 'GV-EMPLOYEE'
    assert record['user_id'] is None


def test_shop_deletion_only_cleans_its_own_scan_logs(app, client, admin_headers, shop_ids):
    from app.models import Shop
    with app.app_context():
        shop = Shop(name='Temporary', shop_url='temp.myshopify.com', currency='USD')
        db.session.add(shop)
        db.session.commit()
        shop_id = shop.id
    _scan(client, admin_headers, shop_id, 'GV-TEMP')
    _scan(client, admin_headers, shop_ids['a'], 'GV-KEEP')
    assert client.delete(f'/api/shops/{shop_id}', headers=admin_headers).status_code == 400
    assert client.delete(f'/api/shops/{shop_id}?force=true', headers=admin_headers).status_code == 200
    with app.app_context():
        assert OutboundScanFailure.query.filter_by(shop_id=shop_id).count() == 0
        assert OutboundScanFailure.query.filter_by(shop_id=shop_ids['a']).count() == 1
