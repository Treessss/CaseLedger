"""Expense API: shop attribution rules and scoping."""


def _default_shop_id():
    """测试库里的 Shop A。费用现在必须归属到店铺，默认挂到第一个店铺。"""
    from app.models import Shop

    return Shop.query.order_by(Shop.id).first().id


def _expense_payload(**overrides):
    payload = {
        'date': '2026-01-15',
        'category': 'facebook_ads',
        'amount': 100,
        'description': 'ads',
        'shop_id': _default_shop_id(),
    }
    payload.update(overrides)
    return payload


def test_single_shop_sub_account_gets_automatic_shop_id(client, app, sub_a_headers, shop_ids):
    # 不传 shop_id，验证后端会自动归属到该子账户唯一的店铺
    payload = _expense_payload()
    payload.pop('shop_id')

    response = client.post('/api/expenses', headers=sub_a_headers, json=payload)
    assert response.status_code == 200

    from app.models import Expense
    with app.app_context():
        expense = Expense.query.filter_by(description='ads').order_by(Expense.id.desc()).first()
        assert expense is not None
        assert expense.shop_id == shop_ids['a']  # Shop A


def test_multi_shop_sub_account_must_specify_shop(client, sub_b_headers, shop_ids):
    payload = _expense_payload()
    payload.pop('shop_id')

    response = client.post('/api/expenses', headers=sub_b_headers, json=payload)
    assert response.status_code == 403
    assert 'shop_id' in response.get_json()['message']

    # 显式指定自己的店铺后可以写入
    created = client.post(
        '/api/expenses',
        headers=sub_b_headers,
        json=_expense_payload(shop_id=shop_ids['b']),
    )
    assert created.status_code == 200


def test_admin_must_pick_a_shop_when_several_exist(client, admin_headers):
    """管理员在多个店铺下也必须显式选店铺，避免产生「无店铺」的孤儿费用。"""
    payload = _expense_payload()
    payload.pop('shop_id')

    response = client.post('/api/expenses', headers=admin_headers, json=payload)
    assert response.status_code == 400
    assert '店铺' in response.get_json()['message']


def test_expense_list_exposes_shop_attribution(client, admin_headers, shop_ids):
    """列表要能看出每条费用属于哪个店铺。"""
    client.post(
        '/api/expenses',
        headers=admin_headers,
        json=_expense_payload(description='归属可见性', shop_id=shop_ids['b']),
    )

    body = client.get('/api/expenses?per_page=50', headers=admin_headers).get_json()
    row = next(item for item in body['data']['expenses'] if item['description'] == '归属可见性')
    assert row['shop_id'] == shop_ids['b']
    assert row['shop_name'] == 'Shop B'


def test_expense_date_filter_uses_business_date(client, admin_headers, shop_ids):
    client.post(
        '/api/expenses', headers=admin_headers,
        json=_expense_payload(description='日期命中', date='2026-01-02', shop_id=shop_ids['a']),
    )
    client.post(
        '/api/expenses', headers=admin_headers,
        json=_expense_payload(description='日期之外', date='2026-01-03', shop_id=shop_ids['a']),
    )
    body = client.get(
        '/api/expenses?start_date=2026-01-02&end_date=2026-01-02',
        headers=admin_headers,
    ).get_json()
    assert {row['description'] for row in body['data']['expenses']} == {'日期命中'}


def test_sub_account_cannot_write_to_another_shop(client, sub_a_headers, shop_ids):
    response = client.post(
        '/api/expenses',
        headers=sub_a_headers,
        json=_expense_payload(shop_id=shop_ids['b']),
    )
    assert response.status_code == 403


def test_expenses_are_isolated_in_list_summary_and_update(client, admin_headers, sub_a_headers, shop_ids):
    created = client.post(
        '/api/expenses', headers=admin_headers,
        json=_expense_payload(description='Shop B only', shop_id=shop_ids['b']),
    ).get_json()['data']['id']

    listing = client.get('/api/expenses?per_page=50', headers=sub_a_headers).get_json()
    assert all(item['id'] != created for item in listing['data']['expenses'])
    assert listing['data']['pagination']['total'] == 1

    summary = client.get('/api/expenses/summary', headers=sub_a_headers).get_json()
    assert summary['data']['total_count'] == 1
    assert client.put(
        f'/api/expenses/{created}', headers=sub_a_headers,
        json={'description': 'changed'},
    ).status_code == 403


def test_expense_cannot_link_another_shops_order(client, admin_headers, shop_ids):
    other_shop_orders = client.get(
        f"/api/orders?shop_id={shop_ids['b']}", headers=admin_headers,
    ).get_json()['data']['orders']
    other_order_id = other_shop_orders[0]['id']

    creation = client.post(
        '/api/expenses', headers=admin_headers,
        json=_expense_payload(shop_id=shop_ids['a'], order_ids=[other_order_id]),
    )
    assert creation.status_code == 400

    own = client.post(
        '/api/expenses', headers=admin_headers,
        json=_expense_payload(shop_id=shop_ids['a']),
    ).get_json()['data']['id']
    update = client.put(
        f'/api/expenses/{own}', headers=admin_headers,
        json={'order_ids': [other_order_id]},
    )
    assert update.status_code == 400


def test_expense_summary_respects_shop_scope(client, admin_headers, sub_a_headers):
    admin_body = client.get('/api/expenses/summary', headers=admin_headers).get_json()
    sub_body = client.get('/api/expenses/summary', headers=sub_a_headers).get_json()
    assert admin_body['success'] is True
    assert sub_body['success'] is True
    assert sub_body['data']['total_count'] <= admin_body['data']['total_count']
    assert sub_body['data']['total_amount'] <= admin_body['data']['total_amount']


def test_expense_list_requires_valid_date(client, admin_headers):
    response = client.get('/api/expenses?start_date=not-a-date', headers=admin_headers)
    assert response.status_code == 400


def test_health_endpoint_is_public(client):
    response = client.get('/health')
    assert response.status_code == 200
    assert response.get_json()['database'] == 'up'


def _make_account(balance=1000, name='Test Account'):
    from app import db
    from app.models import Account

    account = Account(shop_id=_default_shop_id(), platform='facebook', account_name=name, balance=balance, currency='CNY')
    db.session.add(account)
    db.session.commit()
    return account.id


def test_expense_charges_account_and_records_consumption(client, app, admin_headers):
    from app import db
    from app.models import Account, Consumption

    with app.app_context():
        account_id = _make_account(balance=1000)

    response = client.post('/api/expenses', headers=admin_headers, json=_expense_payload(
        amount=200, description='charge test', account_id=account_id,
    ))
    assert response.status_code == 200

    with app.app_context():
        assert float(db.session.get(Account, account_id).balance) == 800.0
        consumption = Consumption.query.filter_by(account_id=account_id).one()
        assert float(consumption.amount) == 200.0
        # 关联使用稳定的 reference_id，而不是靠描述匹配
        assert consumption.reference_id.startswith('expense:')


def test_expense_rejects_insufficient_balance(client, app, admin_headers):
    with app.app_context():
        account_id = _make_account(balance=10)

    response = client.post('/api/expenses', headers=admin_headers, json=_expense_payload(
        amount=500, description='too expensive', account_id=account_id,
    ))
    assert response.status_code == 400
    assert '余额不足' in response.get_json()['message']


def test_deleting_expense_restores_exactly_its_own_amount(client, app, admin_headers):
    """Regression: identical descriptions must not cross-restore balances."""
    from app import db
    from app.models import Account

    with app.app_context():
        account_id = _make_account(balance=1000, name='Duplicate description test')

    first = client.post('/api/expenses', headers=admin_headers, json=_expense_payload(
        amount=100, description='same description', account_id=account_id,
    )).get_json()['data']['id']
    client.post('/api/expenses', headers=admin_headers, json=_expense_payload(
        amount=300, description='same description', account_id=account_id,
    ))

    with app.app_context():
        assert float(db.session.get(Account, account_id).balance) == 600.0

    assert client.delete(f'/api/expenses/{first}', headers=admin_headers).status_code == 200

    with app.app_context():
        # 只恢复被删除那条的 100，而不是同名的 300
        assert float(db.session.get(Account, account_id).balance) == 700.0


def test_update_expense_fields(client, admin_headers):
    created = client.post('/api/expenses', headers=admin_headers, json=_expense_payload(
        description='to update',
    )).get_json()['data']['id']

    response = client.put(f'/api/expenses/{created}', headers=admin_headers, json={
        'amount': 321.5,
        'description': 'updated',
        'vendor': 'ACME',
    })
    assert response.status_code == 200
    body = response.get_json()['data']
    assert body['amount'] == 321.5
    assert body['description'] == 'updated'
    assert body['vendor'] == 'ACME'


def test_delete_expense_requires_shop_access(client, admin_headers, sub_a_headers, shop_ids):
    created = client.post('/api/expenses', headers=admin_headers, json=_expense_payload(
        description='shop b expense', shop_id=shop_ids['b'],
    )).get_json()['data']['id']

    assert client.delete(f'/api/expenses/{created}', headers=sub_a_headers).status_code == 403
    assert client.delete(f'/api/expenses/{created}', headers=admin_headers).status_code == 200


def test_batch_delete_requires_delete_permission(client, admin_headers, sub_a_headers, shop_ids):
    first = client.post('/api/expenses', headers=admin_headers, json=_expense_payload(
        description='a expense', shop_id=shop_ids['a'],
    )).get_json()['data']['id']
    second = client.post('/api/expenses', headers=admin_headers, json=_expense_payload(
        description='b expense', shop_id=shop_ids['b'],
    )).get_json()['data']['id']

    # Module access alone never grants destructive expense access.
    assert client.post('/api/expenses/batch-delete', headers=sub_a_headers, json={
        'expense_ids': [first, second],
    }).status_code == 403

    response = client.post('/api/expenses/batch-delete', headers=admin_headers, json={
        'expense_ids': [first, second],
    })
    assert response.status_code == 200
    assert response.get_json()['deleted_count'] == 2

    remaining = client.get('/api/expenses?per_page=50', headers=admin_headers).get_json()
    remaining_ids = {item['id'] for item in remaining['data']['expenses']}
    assert first not in remaining_ids
    assert second not in remaining_ids


def test_expense_delete_permission_is_separate_and_shop_scoped(app, client, admin_headers, limited_headers, shop_ids):
    from app.models import User, Expense
    from app import db
    first = client.post('/api/expenses', headers=admin_headers, json=_expense_payload(
        shop_id=shop_ids['a'], description='allowed-delete')).get_json()['data']['id']
    second = client.post('/api/expenses', headers=admin_headers, json=_expense_payload(
        shop_id=shop_ids['b'], description='forbidden-delete')).get_json()['data']['id']
    response = client.delete(f'/api/expenses/{first}', headers=limited_headers)
    assert response.status_code == 403
    assert response.get_json()['permission'] == 'expenses_delete'
    with app.app_context():
        user = User.query.filter_by(username='sub_limited').one()
        user.set_permissions([*user.permission_keys(), 'expenses_delete'])
        db.session.commit()
    # A mixed-store batch must never partially delete the accessible row.
    response = client.post('/api/expenses/batch-delete', headers=limited_headers,
                           json={'expense_ids': [first, second]})
    assert response.status_code == 404
    assert client.delete(f'/api/expenses/{second}', headers=limited_headers).status_code == 403
    with app.app_context():
        assert db.session.get(Expense, first) is not None
        assert db.session.get(Expense, second) is not None
    assert client.delete(f'/api/expenses/{first}', headers=limited_headers).status_code == 200
    with app.app_context():
        user = User.query.filter_by(username='sub_limited').one()
        user.set_permissions(['expenses_delete'])
        db.session.commit()
    # Action permission cannot replace module permission.
    assert client.delete(f'/api/expenses/{second}', headers=limited_headers).status_code == 403


def test_invalid_delete_batch_has_no_side_effects(client, admin_headers):
    for ids in ([], '1', [True], [1.5], [-1], ['1'], [None]):
        assert client.post('/api/expenses/batch-delete', headers=admin_headers,
                           json={'expense_ids': ids}).status_code == 400


def test_expense_list_includes_orders_info(client, admin_headers):
    listing = client.get('/api/expenses?per_page=50', headers=admin_headers).get_json()
    assert listing['success'] is True
    for item in listing['data']['expenses']:
        assert 'orders_info' in item
        assert 'account_name' in item


def test_expense_timestamps_are_stored_in_utc(client, app, admin_headers):
    """回归：created_at 必须按 UTC 写入（此前用本地时间，部署到 UTC 服务器会偏 8 小时）。"""
    from datetime import datetime

    from app import db
    from app.models import Expense

    before = datetime.utcnow()
    response = client.post('/api/expenses', headers=admin_headers, json=_expense_payload(
        description='utc check',
    ))
    expense_id = response.get_json()['data']['id']

    with app.app_context():
        expense = db.session.get(Expense, expense_id)
        drift = abs((expense.created_at - before).total_seconds())
        assert drift < 60, f'created_at 与 UTC 当前时间相差 {drift:.0f} 秒，说明用了本地时间'


def test_consumption_date_follows_the_expense_date(client, app, admin_headers):
    """消耗流水日期应等于这笔费用的发生日期，不受服务器时区影响。"""
    from app.models import Consumption

    with app.app_context():
        account_id = _make_account(balance=1000, name='日期一致性账户')

    client.post('/api/expenses', headers=admin_headers, json=_expense_payload(
        date='2026-02-10', amount=50, description='date check', account_id=account_id,
    ))

    with app.app_context():
        consumption = Consumption.query.filter_by(account_id=account_id).one()
        assert consumption.consumption_date.isoformat() == '2026-02-10'


def test_expense_filters_and_sorting_apply_before_pagination(client, app, admin_headers, sub_a_headers, shop_ids):
    """筛选、店铺权限、排序和总数都必须在数据库分页前完成。"""
    from datetime import date

    from app import db
    from app.models import Expense

    with app.app_context():
        rows = [
            (shop_ids['a'], '2026-01-01', 'pending', '4PX Alice', 'Alice', 15),
            (shop_ids['a'], '2026-01-02', 'confirmed', '4PX Bob', 'Bob', 50),
            (shop_ids['a'], '2026-01-03', 'pending', 'Other Alice', 'Alice', 30),
            (shop_ids['b'], '2026-01-02', 'pending', '4PX Alice', 'Alice', 5),
        ]
        for shop_id, day, status, description, submitter, amount in rows:
            db.session.add(Expense(
                shop_id=shop_id,
                record_type='expense',
                category='shipping_cost',
                amount=amount,
                currency='CNY',
                description=description,
                submitter=submitter,
                status=status,
                expense_date=date.fromisoformat(day),
            ))
        db.session.commit()

    query = ('category=shipping_cost&status=pending&description=4PX&submitter=Alice'
             '&start_date=2026-01-01&end_date=2026-01-02&per_page=1')
    first = client.get(f'/api/expenses?{query}&page=1&sort_by=amount&sort_order=asc',
                       headers=admin_headers).get_json()['data']
    second = client.get(f'/api/expenses?{query}&page=2&sort_by=amount&sort_order=asc',
                        headers=admin_headers).get_json()['data']
    assert first['pagination']['total'] == second['pagination']['total'] == 2
    assert [first['expenses'][0]['amount'], second['expenses'][0]['amount']] == [5.0, 15.0]

    scoped = client.get(f'/api/expenses?{query}&page=1', headers=sub_a_headers).get_json()['data']
    assert scoped['pagination']['total'] == 1
    assert scoped['expenses'][0]['shop_id'] == shop_ids['a']

    searched = client.get('/api/expenses?category=shipping_cost&search=Other',
                          headers=admin_headers).get_json()['data']
    assert searched['pagination']['total'] == 1
    assert searched['expenses'][0]['description'] == 'Other Alice'
