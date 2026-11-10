"""Order API behaviour (pagination, scoping, updates)."""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import event

from app import db
from app.models import AfterSale, AfterSaleCost, Expense, Order, OrderCost, Payment
from app.services import order_service


def test_list_orders_returns_pagination_envelope(client, admin_headers):
    body = client.get('/api/orders?per_page=2&page=1', headers=admin_headers).get_json()
    assert body['success'] is True
    assert isinstance(body['data']['orders'], list)
    assert body['data']['pagination']['total'] == 4
    assert body['data']['pagination']['per_page'] == 2
    assert len(body['data']['orders']) == 2


def test_list_orders_second_page(client, admin_headers):
    first = client.get('/api/orders?per_page=2&page=1', headers=admin_headers).get_json()
    second = client.get('/api/orders?per_page=2&page=2', headers=admin_headers).get_json()
    first_ids = {order['id'] for order in first['data']['orders']}
    second_ids = {order['id'] for order in second['data']['orders']}
    assert first_ids.isdisjoint(second_ids)
    assert len(second_ids) == 2


def test_list_orders_loads_payments_once_per_page(app, client, admin_headers):
    payment_queries = []

    def count_payments(_connection, _cursor, statement, _parameters, _context, _executemany):
        if 'from payments' in statement.lower():
            payment_queries.append(statement)

    with app.app_context():
        event.listen(db.engine, 'before_cursor_execute', count_payments)
        try:
            response = client.get('/api/orders?per_page=4', headers=admin_headers)
        finally:
            event.remove(db.engine, 'before_cursor_execute', count_payments)

    assert response.status_code == 200
    assert len(payment_queries) == 1


def test_list_projection_matches_detail_financials(app, client, admin_headers):
    with app.app_context():
        order = Order.query.filter_by(order_number='#A0').one()
        order.currency = 'CNY'
        order.total_price = Decimal('100')
        order.actual_received = Decimal('95')
        order.payment_fee = Decimal('5')
        order.product_cost = Decimal('20')
        order.settlement_amount = Decimal('80')
        order.settlement_currency = 'CNY'
        order.fulfillments = [{'tracking_numbers': ['TRACK-1']}]
        db.session.add(OrderCost(
            order_id=order.id, shop_id=order.shop_id, order_number=order.order_number,
            shipping_cost=Decimal('3'), fangguo_cost=Decimal('2'), cost_date=date(2026, 1, 1),
        ))
        product_expense = Expense(
            shop_id=order.shop_id, category='product_cost', amount=Decimal('10'),
            expense_date=date(2026, 1, 1),
        )
        product_expense.orders.append(order)
        db.session.add(product_expense)
        after_sale = AfterSale(
            shop_id=order.shop_id, order_id=order.id, customer_reference='CF-A0',
            kind='original', reason='test', shipping_address={},
        )
        db.session.add(after_sale)
        db.session.flush()
        db.session.add(AfterSaleCost(
            after_sale_id=after_sale.id, shop_id=order.shop_id,
            cost_type='other', amount_cny=Decimal('4'),
        ))
        db.session.add(Payment(
            shop_id=order.shop_id, order_id=order.id, payment_method='paypal',
            amount=Decimal('100'), currency='CNY', net_amount=Decimal('95'),
            fee_source='gateway', status='success', payment_date=datetime.utcnow(),
        ))
        db.session.commit()
        order_id = order.id

    listing = client.get('/api/orders?search=%23A0', headers=admin_headers).get_json()['data']['orders'][0]
    detail = client.get(f'/api/orders/{order_id}', headers=admin_headers).get_json()['data']
    for field in (
        'gross_profit_cny', 'allocated_product_cost', 'allocated_shipping_cost',
        'settlement_amount_cny', 'settlement_exchange_rate', 'actual_received_source',
        'fee_source', 'financial_status', 'warehouse_status',
    ):
        assert listing[field] == detail.get(field, listing[field]), field
    assert listing['shopify_tracking_numbers'] == ['TRACK-1']
    assert 'shipping_address' not in listing
    assert 'line_items' not in listing
    assert 'after_sales' not in listing


def test_list_orders_filters_payment_method(app, client, admin_headers):
    with app.app_context():
        order = Order.query.filter_by(order_number='#A0').one()
        order.payment_method = 'paypal'
        db.session.commit()

    body = client.get('/api/orders?payment_method=paypal', headers=admin_headers).get_json()
    assert body['data']['pagination']['total'] == 1
    assert [order['order_number'] for order in body['data']['orders']] == ['#A0']


def test_order_detail_includes_payments_list(client, admin_headers):
    listing = client.get('/api/orders?per_page=1', headers=admin_headers).get_json()
    order_id = listing['data']['orders'][0]['id']

    body = client.get(f'/api/orders/{order_id}', headers=admin_headers).get_json()
    assert body['success'] is True
    assert body['data']['id'] == order_id
    assert isinstance(body['data']['payments'], list)
    assert isinstance(body['data']['exchange_rate_refresh_currencies'], list)


def test_order_detail_returns_404_for_missing_id(client, admin_headers):
    assert client.get('/api/orders/999999', headers=admin_headers).status_code == 404


def test_order_neighbors_follow_newest_first_order(client, admin_headers):
    listing = client.get(
        '/api/orders?search=%23A1&per_page=10', headers=admin_headers
    ).get_json()
    order_id = listing['data']['orders'][0]['id']

    body = client.get(
        f'/api/orders/{order_id}/neighbors', headers=admin_headers
    ).get_json()

    assert body['success'] is True
    assert body['data']['previous']['order_number'] == '#A0'
    assert body['data']['next']['order_number'] == '#A2'


def test_refresh_order_details_updates_fulfillment_status(
    app, client, admin_headers, monkeypatch
):
    from app.services.shopify_service import ShopifyService

    monkeypatch.setattr(ShopifyService, 'init_shop', lambda self, shop, app: self)
    monkeypatch.setattr(
        ShopifyService, 'sync_order_by_id',
        lambda self, order: {'total_fetched': 1, 'updated_orders': 1, 'errors': 0},
    )
    listing = client.get('/api/orders?per_page=1', headers=admin_headers).get_json()
    order_id = listing['data']['orders'][0]['id']
    with app.app_context():
        order = db.session.get(Order, order_id)
        order.fulfillment_status = 'unfulfilled'
        db.session.commit()

    monkeypatch.setattr(
        order_service,
        'fetch_order_details',
        lambda *args, **kwargs: {
            'email': None,
            'name': None,
            'phone': None,
            'shipping_address': None,
            'note': None,
            'fulfillment_status': 'fulfilled',
            'line_items': [],
            'fulfillments': [{'id': '99', 'status': 'SUCCESS'}],
        },
    )

    response = client.post(
        f'/api/orders/{order_id}/refresh-contact', headers=admin_headers
    )

    assert response.status_code == 200
    assert response.get_json()['data']['fulfillment_status'] == 'fulfilled'
    assert response.get_json()['data']['fulfillments'][0]['status'] == 'SUCCESS'


def test_update_order_updates_whitelisted_fields(client, admin_headers):
    listing = client.get('/api/orders?per_page=1', headers=admin_headers).get_json()
    order_id = listing['data']['orders'][0]['id']

    response = client.put(f'/api/orders/{order_id}', headers=admin_headers, json={
        'notes': 'reviewed',
        'shipping_cost': 12.5,
        # not whitelisted, must be ignored
        'total_price': 1,
    })
    assert response.status_code == 200

    body = client.get(f'/api/orders/{order_id}', headers=admin_headers).get_json()
    assert body['data']['shipping_cost'] == 12.5
    assert body['data']['total_price'] != 1


def test_recent_orders_respect_shop_scope(client, admin_headers, sub_a_headers):
    admin_body = client.get('/api/orders/recent?limit=10', headers=admin_headers).get_json()
    assert len(admin_body['data']) == 4

    sub_body = client.get('/api/orders/recent?limit=10', headers=sub_a_headers).get_json()
    assert len(sub_body['data']) == 3
    assert all(order['shop_id'] != 2 for order in sub_body['data'])


def test_order_stats_respect_shop_scope(client, admin_headers, sub_a_headers):
    admin_stats = client.get('/api/orders/stats?days=30', headers=admin_headers).get_json()
    assert admin_stats['success'] is True
    assert admin_stats['data']['period']['orders'] == 4

    sub_stats = client.get('/api/orders/stats?days=30', headers=sub_a_headers).get_json()
    assert sub_stats['data']['period']['orders'] == 3
    assert sub_stats['data']['period']['revenue'] < admin_stats['data']['period']['revenue']


def test_orders_filter_by_financial_status(client, admin_headers):
    paid = client.get('/api/orders?financial_status=paid', headers=admin_headers).get_json()
    assert paid['data']['pagination']['total'] == 4

    refunded = client.get(
        '/api/orders?financial_status=refunded', headers=admin_headers
    ).get_json()
    assert refunded['data']['pagination']['total'] == 0


def test_order_date_filter_uses_order_time_not_sync_time(app, client, admin_headers):
    with app.app_context():
        order = Order.query.filter_by(order_number='#A0').one()
        order.order_date = datetime(2026, 1, 1, 17, 30)  # Jan 2, 01:30 Beijing
        order.created_at = datetime(2026, 9, 1, 0, 0)
        db.session.commit()

    response = client.get(
        '/api/orders?start_date=2026-01-01%2016:00:00&end_date=2026-01-02%2015:59:59',
        headers=admin_headers,
    )
    order_numbers = [item['order_number'] for item in response.get_json()['data']['orders']]
    assert order_numbers == ['#A0']


def test_order_calendar_filter_uses_half_open_beijing_day(app, client, admin_headers):
    with app.app_context():
        orders = Order.query.filter(Order.order_number.in_(['#A0', '#A1', '#A2'])).order_by(Order.order_number).all()
        for order, timestamp in zip(orders, [
            datetime(2026, 1, 1, 15, 59, 59, 999999),
            datetime(2026, 1, 1, 16),
            datetime(2026, 1, 2, 15, 59, 59, 999999),
        ]):
            order.order_date = timestamp
        db.session.commit()

    body = client.get(
        '/api/orders?start_date=2026-01-02&end_date=2026-01-02',
        headers=admin_headers,
    ).get_json()
    assert {order['order_number'] for order in body['data']['orders']} == {'#A1', '#A2'}


def test_workload_summary_matches_shipping_queue_scope(app, client, admin_headers, shop_ids):
    """今日作业看板：待发货口径与发货中心一致，待采购 = 待发货 - 可出库。"""
    body = client.get('/api/orders/workload', headers=admin_headers).get_json()
    data = body['data']

    assert set(data) == {'pending_ship', 'ready_to_ship', 'awaiting_purchase', 'shipped_today'}

    # 夹具里 3 张 A 店订单都是已付款 + 未出库
    assert data['pending_ship'] >= 3
    assert data['ready_to_ship'] <= data['pending_ship']
    assert data['awaiting_purchase'] == data['pending_ship'] - data['ready_to_ship']


def test_workload_summary_is_shop_scoped(app, client, admin_headers, sub_a_headers, shop_ids):
    """子账户只看得到自己被分配店铺的作业量。"""
    admin_total = client.get('/api/orders/workload', headers=admin_headers).get_json()['data']['pending_ship']
    sub_total = client.get('/api/orders/workload', headers=sub_a_headers).get_json()['data']['pending_ship']
    assert sub_total <= admin_total
