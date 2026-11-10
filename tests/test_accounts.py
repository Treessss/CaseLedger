"""Account / recharge / consumption tests, including the balance invariant."""
from decimal import Decimal


def _create_account(client, headers, name='主账户', balance=0, platform='facebook'):
    response = client.post('/api/accounts', headers=headers, json={
        'shop_id': 1,
        'platform': platform,
        'account_name': name,
        'balance': balance,
        'currency': 'CNY',
    })
    assert response.status_code == 201, response.get_data(as_text=True)
    return response.get_json()['account']['id']


def _balance(app, account_id):
    from app import db
    from app.models import Account

    return db.session.get(Account, account_id).balance


def test_create_and_list_accounts(client, admin_headers):
    _create_account(client, admin_headers, 'A账户', balance=100)

    body = client.get('/api/accounts', headers=admin_headers).get_json()
    assert body['success'] is True
    assert body['pagination']['total'] == 1
    assert body['data'][0]['account_name'] == 'A账户'
    assert body['data'][0]['balance'] == 100.0


def test_clear_expense_account_restores_balance_and_can_reassign(app, client, admin_headers):
    from app import db
    from app.models import Expense, Consumption
    with app.app_context():
        first = _create_account(client, admin_headers, '原扣费账户', balance=1000)
        second = _create_account(client, admin_headers, '新扣费账户', balance=500)
        expense_id = client.post('/api/expenses', headers=admin_headers, json={
            'shop_id': 1, 'date': '2026-09-24', 'category': 'product_cost',
            'description': '材料', 'amount': 100, 'account_id': first,
        }).get_json()['data']['id']
        assert _balance(app, first) == 900
        result = client.put(f'/api/expenses/{expense_id}', headers=admin_headers,
                            json={'account_id': None, 'description': '修改材料说明'})
        assert result.status_code == 200
        assert result.get_json()['data']['account_id'] is None
        assert _balance(app, first) == 1000
        assert Consumption.query.filter_by(reference_id=f'expense:{expense_id}').count() == 0
        # Repeat save cannot refund twice.
        assert client.put(f'/api/expenses/{expense_id}', headers=admin_headers,
                          json={'account_id': None}).status_code == 200
        assert _balance(app, first) == 1000
        assert client.put(f'/api/expenses/{expense_id}', headers=admin_headers,
                          json={'account_id': second, 'amount': 120}).status_code == 200
        assert _balance(app, second) == 380
        assert db.session.get(Expense, expense_id).account_id == second


def test_clear_income_account_reverses_credit(app, client, admin_headers):
    from app.models import Recharge
    with app.app_context():
        account = _create_account(client, admin_headers, '收入账户', balance=500)
        expense_id = client.post('/api/expenses', headers=admin_headers, json={
            'shop_id': 1, 'date': '2026-09-24', 'category': 'other_income', 'record_type': 'income',
            'description': '收入', 'amount': 100, 'account_id': account,
        }).get_json()['data']['id']
        assert _balance(app, account) == 600
        assert client.put(f'/api/expenses/{expense_id}', headers=admin_headers,
                          json={'account_id': ''}).status_code == 200
        assert _balance(app, account) == 500
        assert Recharge.query.filter_by(transaction_id=f'income:{expense_id}').count() == 0


def test_account_list_creation_date_filter_uses_beijing_day(app, client, admin_headers):
    from datetime import datetime

    from app import db
    from app.models import Account

    before = _create_account(client, admin_headers, '前一天账户')
    inside = _create_account(client, admin_headers, '当天账户')
    with app.app_context():
        db.session.get(Account, before).created_at = datetime(2026, 1, 1, 15, 59, 59)
        db.session.get(Account, inside).created_at = datetime(2026, 1, 1, 16)
        db.session.commit()

    body = client.get(
        '/api/accounts?start_date=2026-01-02&end_date=2026-01-02',
        headers=admin_headers,
    ).get_json()
    assert [item['account_name'] for item in body['data']] == ['当天账户']


def test_accounts_are_shop_scoped(client, admin_headers, sub_a_headers):
    first = _create_account(client, admin_headers, '共享名称', balance=100)
    second = client.post('/api/accounts', headers=admin_headers, json={
        'shop_id': 2, 'platform': 'facebook', 'account_name': '共享名称',
        'balance': 50, 'currency': 'CNY',
    })
    assert second.status_code == 201
    second_id = second.get_json()['account']['id']

    scoped = client.get('/api/accounts?shop_id=1', headers=admin_headers).get_json()
    assert [item['id'] for item in scoped['data']] == [first]
    assert client.get(f'/api/accounts/{second_id}', headers=sub_a_headers).status_code == 404
    assert client.post(
        f'/api/accounts/{second_id}/recharges', headers=sub_a_headers, json={'amount': 10}
    ).status_code == 404


def test_duplicate_account_name_is_rejected(client, admin_headers):
    _create_account(client, admin_headers, '重复账户')

    response = client.post('/api/accounts', headers=admin_headers, json={
        'shop_id': 1,
        'platform': 'facebook',
        'account_name': '重复账户',
    })
    assert response.status_code == 400
    assert '同名账户' in response.get_json()['message']


def test_account_detail_includes_totals(client, admin_headers):
    account_id = _create_account(client, admin_headers, '统计账户', balance=500)
    client.post(f'/api/accounts/{account_id}/recharges', headers=admin_headers,
                json={'amount': 200})
    client.post(f'/api/accounts/{account_id}/consumptions', headers=admin_headers,
                json={'amount': 50, 'consumption_date': '2026-01-10'})

    body = client.get(f'/api/accounts/{account_id}', headers=admin_headers).get_json()
    account = body['account']
    assert account['total_recharge'] == 200.0
    assert account['total_consumption'] == 50.0
    assert account['balance'] == 650.0


def test_recharge_increases_balance(client, app, admin_headers):
    with app.app_context():
        account_id = _create_account(client, admin_headers, '充值账户')

        response = client.post(f'/api/accounts/{account_id}/recharges',
                               headers=admin_headers, json={'amount': 300})
        assert response.status_code == 201
        assert float(_balance(app, account_id)) == 300.0


def test_pending_recharge_only_applies_after_confirm(client, app, admin_headers):
    with app.app_context():
        account_id = _create_account(client, admin_headers, '待确认充值')

        created = client.post(f'/api/accounts/{account_id}/recharges', headers=admin_headers,
                              json={'amount': 500, 'status': 'pending'}).get_json()
        recharge_id = created['recharge']['id']
        assert float(_balance(app, account_id)) == 0.0

        confirmed = client.post(f'/api/recharges/{recharge_id}/confirm', headers=admin_headers)
        assert confirmed.status_code == 200
        assert float(_balance(app, account_id)) == 500.0

        # 重复确认必须被拒绝，否则余额会被重复增加
        assert client.post(f'/api/recharges/{recharge_id}/confirm',
                           headers=admin_headers).status_code == 400
        assert float(_balance(app, account_id)) == 500.0


def test_consumption_deducts_balance(client, app, admin_headers):
    with app.app_context():
        account_id = _create_account(client, admin_headers, '消耗账户', balance=100)

        response = client.post(f'/api/accounts/{account_id}/consumptions', headers=admin_headers,
                               json={'amount': 30, 'consumption_date': '2026-01-10'})
        assert response.status_code == 201
        assert float(_balance(app, account_id)) == 70.0


def test_consumption_rejected_when_balance_is_insufficient(client, app, admin_headers):
    with app.app_context():
        account_id = _create_account(client, admin_headers, '余额不足账户', balance=10)

        response = client.post(f'/api/accounts/{account_id}/consumptions', headers=admin_headers,
                               json={'amount': 999, 'consumption_date': '2026-01-10'})
        assert response.status_code == 400
        assert float(_balance(app, account_id)) == 10.0


def test_balance_invariant_across_recharge_consumption_and_expense(client, app, admin_headers):
    """余额必须始终等于：期初 + 已完成充值 - 全部消耗。"""
    from app import db
    from app.models import Consumption, Recharge

    with app.app_context():
        account_id = _create_account(client, admin_headers, '资金闭环账户', balance=1000)

        # 1. 已完成充值
        client.post(f'/api/accounts/{account_id}/recharges', headers=admin_headers,
                    json={'amount': 500})
        # 2. 待确认充值（不应影响余额）
        pending = client.post(f'/api/accounts/{account_id}/recharges', headers=admin_headers,
                              json={'amount': 800, 'status': 'pending'}).get_json()
        client.post(f"/api/recharges/{pending['recharge']['id']}/confirm", headers=admin_headers)
        # 3. 直接消耗
        client.post(f'/api/accounts/{account_id}/consumptions', headers=admin_headers,
                    json={'amount': 200, 'consumption_date': '2026-01-10'})
        # 4. 通过费用扣款，然后删除该费用（余额应回滚）
        # 费用必须归属到店铺（默认取第一个店铺）
        from app.models import Shop
        with app.app_context():
            shop_id = Shop.query.order_by(Shop.id).first().id
        expense_id = client.post('/api/expenses', headers=admin_headers, json={
            'date': '2026-01-11', 'category': 'facebook_ads', 'amount': 300,
            'description': 'account linked expense', 'account_id': account_id,
            'shop_id': shop_id,
        }).get_json()['data']['id']
        assert float(_balance(app, account_id)) == 1000 + 500 + 800 - 200 - 300

        assert client.delete(f'/api/expenses/{expense_id}', headers=admin_headers).status_code == 200

        balance = float(_balance(app, account_id))
        completed_recharge = float(db.session.query(db.func.sum(Recharge.amount))
                                   .filter_by(account_id=account_id, status='completed').scalar() or 0)
        total_consumption = float(db.session.query(db.func.sum(Consumption.amount))
                                  .filter_by(account_id=account_id).scalar() or 0)

        assert balance == 1000 + completed_recharge - total_consumption
        assert balance == 2100.0


def test_delete_account_with_records_requires_force(client, admin_headers):
    account_id = _create_account(client, admin_headers, '待删除账户', balance=50)
    client.post(f'/api/accounts/{account_id}/consumptions', headers=admin_headers,
                json={'amount': 10, 'consumption_date': '2026-01-10'})

    blocked = client.delete(f'/api/accounts/{account_id}', headers=admin_headers)
    assert blocked.status_code == 400
    assert blocked.get_json()['has_records'] is True

    assert client.delete(f'/api/accounts/{account_id}?force=true',
                         headers=admin_headers).status_code == 200
    assert client.get(f'/api/accounts/{account_id}', headers=admin_headers).status_code == 404


def test_recharges_and_consumptions_are_paginated(client, admin_headers):
    account_id = _create_account(client, admin_headers, '分页账户', balance=1000)
    for _ in range(3):
        client.post(f'/api/accounts/{account_id}/recharges', headers=admin_headers,
                    json={'amount': 10})
        client.post(f'/api/accounts/{account_id}/consumptions', headers=admin_headers,
                    json={'amount': 5, 'consumption_date': '2026-01-10'})

    recharges = client.get(f'/api/accounts/{account_id}/recharges?per_page=2',
                           headers=admin_headers).get_json()
    assert len(recharges['recharges']) == 2
    assert recharges['pagination']['total'] == 3

    consumptions = client.get(f'/api/accounts/{account_id}/consumptions?per_page=2',
                              headers=admin_headers).get_json()
    assert len(consumptions['consumptions']) == 2
    assert consumptions['pagination']['total'] == 3


def test_account_summary(client, admin_headers):
    _create_account(client, admin_headers, '汇总账户A', balance=100, platform='facebook')
    _create_account(client, admin_headers, '汇总账户B', balance=250, platform='4px')

    body = client.get('/api/accounts/summary', headers=admin_headers).get_json()
    assert body['success'] is True
    assert body['data']['total_accounts'] == 2
    assert body['data']['total_balance'] == 350.0
    assert set(body['data']['platform_summary']) == {'facebook', '4px'}


def test_account_summary_recharge_uses_beijing_day_and_consumption_uses_business_date(
    client, app, admin_headers,
):
    from datetime import datetime

    from app import db
    from app.models import Recharge

    account_id = _create_account(client, admin_headers, '日期边界账户', balance=100)
    for amount, timestamp in (
        (10, datetime(2026, 1, 1, 15, 59, 59)),
        (20, datetime(2026, 1, 1, 16)),
        (30, datetime(2026, 1, 2, 15, 59, 59, 999999)),
        (40, datetime(2026, 1, 2, 16)),
    ):
        response = client.post(
            f'/api/accounts/{account_id}/recharges', headers=admin_headers,
            json={'amount': amount},
        )
        assert response.status_code == 201
        with app.app_context():
            db.session.get(Recharge, response.get_json()['recharge']['id']).recharge_date = timestamp
            db.session.commit()
    client.post(
        f'/api/accounts/{account_id}/consumptions', headers=admin_headers,
        json={'amount': 7, 'consumption_date': '2026-01-02'},
    )

    body = client.get(
        '/api/accounts/summary?start_date=2026-01-02&end_date=2026-01-02',
        headers=admin_headers,
    ).get_json()['data']
    assert body['month_recharge'] == 50.0
    assert body['month_consumption'] == 7.0


def test_lookup_endpoints(client, admin_headers):
    assert client.get('/api/accounts/platforms', headers=admin_headers).get_json()['platforms']
    assert client.get('/api/recharges/methods', headers=admin_headers).get_json()['methods']
    assert client.get('/api/consumptions/types', headers=admin_headers).get_json()['types']


def test_stored_balance_uses_decimal(client, app, admin_headers):
    """金额不能因为浮点运算出现 0.30000000000000004 这类尾差。"""
    with app.app_context():
        account_id = _create_account(client, admin_headers, '精度账户')
        for _ in range(3):
            client.post(f'/api/accounts/{account_id}/recharges', headers=admin_headers,
                        json={'amount': 0.1})

        assert _balance(app, account_id) == Decimal('0.30')
