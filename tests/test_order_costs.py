"""Order cost tests, focused on the atomic confirmation / account charging."""
from datetime import date, datetime

import pytest


def _order(shop_id, order_number):
    """order_costs.order_id 是 NOT NULL，测试里先建真实订单。"""
    from app import db
    from app.models import Order

    existing = Order.query.filter_by(order_number=order_number).first()
    if existing:
        return existing.id
    order = Order(
        shop_id=shop_id,
        shopify_order_id=f'OC-{order_number}',
        order_number=order_number,
        total_price=10,
        subtotal_price=10,
        currency='CNY',
        financial_status='paid',
        order_date=date(2026, 2, 1),
        created_at=datetime(2026, 2, 1),
    )
    db.session.add(order)
    db.session.commit()
    return order.id


SHOP_A_ORDERS = [
    '#A100', '#B1', '#B2', '#B3', '#D1', '#S1', '#S2',
    '#L1', '#L2', '#SHOP-A', '#C1', '#C2', '#BAD', '#MISSING-LOOKUP',
]


@pytest.fixture(autouse=True)
def _seed_orders(app, shop_ids):
    """Order costs can only be created for existing orders."""
    with app.app_context():
        for order_number in SHOP_A_ORDERS:
            _order(shop_ids['a'], order_number)
        _order(shop_ids['b'], '#SHOP-B')
    yield


def _account(app, name, balance):
    from app import db
    from app.models import Account

    account = Account(shop_id=1, platform='4px', account_name=name, balance=balance, currency='CNY')
    db.session.add(account)
    db.session.commit()
    return account.id


def _balance(app, account_id):
    from app import db
    from app.models import Account

    return float(db.session.get(Account, account_id).balance)


def _create_cost(client, headers, order_number='#A100', **overrides):
    payload = {
        'order_number': order_number,
        'cost_date': '2026-02-01',
        'shipping_cost': 0,
        'fangguo_cost': 0,
        'other_cost': 0,
    }
    payload.update(overrides)
    return client.post('/api/order-costs', headers=headers, json=payload)


def test_create_and_list_order_cost(client, admin_headers):
    created = _create_cost(client, admin_headers, shipping_cost=25.5)
    assert created.status_code == 201, created.get_data(as_text=True)
    assert created.get_json()['order_cost']['order_number'] == '#A100'

    listing = client.get('/api/order-costs', headers=admin_headers).get_json()
    assert listing['pagination']['total'] == 1
    assert listing['order_costs'][0]['shipping_cost'] == 25.5


def test_create_requires_a_cost_amount(client, admin_headers):
    response = _create_cost(client, admin_headers)
    assert response.status_code == 400
    assert '至少需要填写一种费用' in response.get_json()['message']


def test_duplicate_order_number_rejected(client, admin_headers):
    _create_cost(client, admin_headers, shipping_cost=10)
    duplicate = _create_cost(client, admin_headers, shipping_cost=20)
    assert duplicate.status_code == 400
    assert '已存在费用记录' in duplicate.get_json()['message']


def test_batch_create_returns_batch_id(client, admin_headers):
    response = client.post('/api/order-costs/batch', headers=admin_headers, json={
        'order_numbers': ['#B1', '#B2', '#B3'],
        'cost_date': '2026-02-01',
        'shipping_cost': 5,
    })
    assert response.status_code == 201
    body = response.get_json()
    assert len(body['order_costs']) == 3
    assert body['batch_id']

    batches = client.get('/api/order-cost-batches', headers=admin_headers).get_json()
    assert batches['pagination']['total'] == 1

    detail = client.get(f"/api/order-cost-batches/{body['batch_id']}",
                        headers=admin_headers).get_json()
    assert len(detail['batch']['order_costs']) == 3


def test_update_and_delete_are_blocked_after_confirm(client, app, admin_headers):
    with app.app_context():
        account_id = _account(app, '物流账户', 100)

    cost_id = _create_cost(client, admin_headers, shipping_cost=10,
                           shipping_account_id=account_id).get_json()['order_cost']['id']

    assert client.put(f'/api/order-costs/{cost_id}', headers=admin_headers,
                      json={'shipping_cost': 30}).status_code == 200
    assert client.post(f'/api/order-costs/{cost_id}/confirm',
                       headers=admin_headers).status_code == 200

    blocked_update = client.put(f'/api/order-costs/{cost_id}', headers=admin_headers,
                                json={'shipping_cost': 99})
    assert blocked_update.status_code == 400
    assert '已确认' in blocked_update.get_json()['message']

    blocked_delete = client.delete(f'/api/order-costs/{cost_id}', headers=admin_headers)
    assert blocked_delete.status_code == 400


def test_confirm_charges_account_and_writes_consumption(client, app, admin_headers):
    from app.models import Consumption

    with app.app_context():
        account_id = _account(app, '物流账户', 500)

    cost_id = _create_cost(client, admin_headers, shipping_cost=120,
                           shipping_account_id=account_id).get_json()['order_cost']['id']

    response = client.post(f'/api/order-costs/{cost_id}/confirm', headers=admin_headers)
    assert response.status_code == 200

    with app.app_context():
        assert _balance(app, account_id) == 380.0
        consumption = Consumption.query.filter_by(account_id=account_id).one()
        assert float(consumption.amount) == 120.0
        assert consumption.reference_id == f'order_cost:{cost_id}:shipping'

    # 重复确认必须被拒绝
    assert client.post(f'/api/order-costs/{cost_id}/confirm',
                       headers=admin_headers).status_code == 400
    with app.app_context():
        assert _balance(app, account_id) == 380.0


def test_confirm_is_atomic_when_a_later_account_is_insufficient(client, app, admin_headers):
    """Regression: the old code charged the first account before checking the second."""
    from app.models import Consumption, OrderCost

    with app.app_context():
        shipping_id = _account(app, '物流账户', 1000)
        fangguo_id = _account(app, '方果账户', 10)

    cost_id = _create_cost(
        client, admin_headers,
        shipping_cost=100, shipping_account_id=shipping_id,
        fangguo_cost=500, fangguo_account_id=fangguo_id,
    ).get_json()['order_cost']['id']

    response = client.post(f'/api/order-costs/{cost_id}/confirm', headers=admin_headers)
    assert response.status_code == 400
    assert '余额不足' in response.get_json()['message']

    with app.app_context():
        # 两个账户都不能被扣，状态保持 pending，且不能产生半条流水
        assert _balance(app, shipping_id) == 1000.0
        assert _balance(app, fangguo_id) == 10.0
        assert db_status(cost_id) == 'pending'
        assert Consumption.query.count() == 0


def db_status(cost_id):
    from app import db
    from app.models import OrderCost

    return db.session.get(OrderCost, cost_id).status


def test_batch_confirm_is_all_or_nothing(client, app, admin_headers):
    from app.models import Consumption

    with app.app_context():
        rich_id = _account(app, '充足账户', 1000)
        poor_id = _account(app, '不足账户', 1)

    batch = client.post('/api/order-costs/batch', headers=admin_headers, json={
        'order_numbers': ['#C1', '#C2'],
        'cost_date': '2026-02-01',
        'shipping_cost': 100,
        'shipping_account_id': rich_id,
    }).get_json()
    batch_id = batch['batch_id']

    # 把第二条改到一个余额不足的账户
    second_id = batch['order_costs'][1]['id']
    client.put(f'/api/order-costs/{second_id}', headers=admin_headers,
               json={'shipping_account_id': poor_id})

    response = client.post(f'/api/order-costs/batch/{batch_id}/confirm', headers=admin_headers)
    assert response.status_code == 400
    assert response.get_json()['failed_orders']

    with app.app_context():
        assert _balance(app, rich_id) == 1000.0   # 没有任何扣费
        assert Consumption.query.count() == 0
        assert db_status(batch['order_costs'][0]['id']) == 'pending'
        assert db_status(second_id) == 'pending'


def test_order_cost_summary(client, app, admin_headers):
    with app.app_context():
        account_id = _account(app, '汇总账户', 1000)

    _create_cost(client, admin_headers, order_number='#S1', shipping_cost=10, other_cost=5)
    cost_id = _create_cost(client, admin_headers, order_number='#S2', shipping_cost=20,
                           shipping_account_id=account_id).get_json()['order_cost']['id']
    client.post(f'/api/order-costs/{cost_id}/confirm', headers=admin_headers)

    pending = client.get('/api/order-costs/summary', headers=admin_headers).get_json()['summary']
    assert pending['count'] == 2
    assert pending['total_shipping_cost'] == 30.0
    assert pending['total_other_cost'] == 5.0

    confirmed = client.get('/api/order-costs/summary?status=confirmed',
                           headers=admin_headers).get_json()['summary']
    assert confirmed['count'] == 1
    assert confirmed['total_shipping_cost'] == 20.0


def test_order_costs_are_shop_scoped(client, admin_headers, sub_a_headers, shop_ids):
    mine = _create_cost(client, admin_headers, order_number='#SHOP-A', shipping_cost=1,
                        shop_id=shop_ids['a']).get_json()['order_cost']['id']
    theirs = _create_cost(client, admin_headers, order_number='#SHOP-B', shipping_cost=1,
                          shop_id=shop_ids['b']).get_json()['order_cost']['id']

    listing = client.get('/api/order-costs', headers=sub_a_headers).get_json()
    assert {cost['id'] for cost in listing['order_costs']} == {mine}

    assert client.get(f'/api/order-costs/{theirs}', headers=sub_a_headers).status_code == 403
    assert client.post(f'/api/order-costs/{theirs}/confirm',
                       headers=sub_a_headers).status_code == 403


def test_lookup_by_order_numbers(client, admin_headers):
    _create_cost(client, admin_headers, order_number='#L1', shipping_cost=3)
    _create_cost(client, admin_headers, order_number='#L2', shipping_cost=4)

    body = client.post('/api/order-costs/by-orders', headers=admin_headers,
                       json={'order_numbers': ['#L1', '#L2', '#MISSING']}).get_json()
    assert len(body['order_costs']) == 2


def test_cost_date_is_validated(client, admin_headers):
    response = client.post('/api/order-costs', headers=admin_headers, json={
        'order_number': '#BAD', 'cost_date': '不是日期', 'shipping_cost': 1,
    })
    assert response.status_code == 400


def test_order_cost_detail_shape(client, admin_headers):
    cost_id = _create_cost(client, admin_headers, order_number='#D1', shipping_cost=7
                           ).get_json()['order_cost']['id']

    body = client.get(f'/api/order-costs/{cost_id}', headers=admin_headers).get_json()
    assert body['order_cost']['order_number'] == '#D1'
    assert body['data']['order_number'] == '#D1'
