"""Extra income records (record_type='income') and their effect on reports."""
from datetime import datetime


def _today():
    """报表按 UTC 存储口径比对，测试也用 UTC 当天。"""
    return datetime.utcnow().date()


def _default_shop_id():
    """收入记录同样必须归属到店铺。"""
    from app.models import Shop

    return Shop.query.order_by(Shop.id).first().id


def _income_payload(**overrides):
    payload = {
        'date': '2026-03-10',
        'record_type': 'income',
        'category': 'platform_subsidy',
        'amount': 500,
        'description': '平台补贴',
        'shop_id': _default_shop_id(),
    }
    payload.update(overrides)
    return payload


def _create_income(client, headers, **overrides):
    return client.post('/api/expenses', headers=headers, json=_income_payload(**overrides))


def _account(app, balance=0, name='收入账户'):
    from app import db
    from app.models import Account

    account = Account(shop_id=1, platform='facebook', account_name=name, balance=balance, currency='CNY')
    db.session.add(account)
    db.session.commit()
    return account.id


def test_create_income_record(client, admin_headers):
    response = _create_income(client, admin_headers)
    assert response.status_code == 200, response.get_data(as_text=True)
    body = response.get_json()['data']
    assert body['record_type'] == 'income'
    assert body['amount'] == 500.0


def test_income_and_expense_are_separately_filterable(client, admin_headers):
    _create_income(client, admin_headers)
    client.post('/api/expenses', headers=admin_headers, json={
        'date': '2026-03-10', 'category': 'facebook_ads', 'amount': 100,
        'description': '广告费',
    })

    all_records = client.get('/api/expenses?per_page=50', headers=admin_headers).get_json()
    assert all_records['data']['pagination']['total'] >= 2

    incomes = client.get('/api/expenses?record_type=income&per_page=50',
                         headers=admin_headers).get_json()
    assert all(item['record_type'] == 'income' for item in incomes['data']['expenses'])
    assert any(item['description'] == '平台补贴' for item in incomes['data']['expenses'])

    expenses = client.get('/api/expenses?record_type=expense&per_page=50',
                          headers=admin_headers).get_json()
    assert all(item['record_type'] == 'expense' for item in expenses['data']['expenses'])


def test_summary_separates_income_from_expense(client, admin_headers):
    before = client.get('/api/expenses/summary', headers=admin_headers).get_json()['data']
    _create_income(client, admin_headers, amount=300)

    after = client.get('/api/expenses/summary', headers=admin_headers).get_json()['data']
    # 收入不能计入费用合计
    assert after['total_amount'] == before['total_amount']
    assert after['total_count'] == before['total_count']
    # 但必须体现在收入侧
    assert after['total_income'] == before['total_income'] + 300
    assert after['income_count'] == before['income_count'] + 1
    assert after['net_amount'] == round(after['total_income'] - after['total_amount'], 2)


def test_income_credits_linked_account(client, app, admin_headers):
    from app import db
    from app.models import Account, Recharge

    with app.app_context():
        account_id = _account(app, balance=100)

    response = _create_income(client, admin_headers, amount=250, account_id=account_id)
    income_id = response.get_json()['data']['id']

    with app.app_context():
        assert float(db.session.get(Account, account_id).balance) == 350.0
        recharge = Recharge.query.filter_by(transaction_id=f'income:{income_id}').one()
        assert float(recharge.amount) == 250.0
        assert recharge.status == 'completed'


def test_deleting_income_reverses_account_credit(client, app, admin_headers):
    from app import db
    from app.models import Account, Recharge

    with app.app_context():
        account_id = _account(app, balance=100)

    income_id = _create_income(client, admin_headers, amount=250,
                               account_id=account_id).get_json()['data']['id']
    assert client.delete(f'/api/expenses/{income_id}', headers=admin_headers).status_code == 200

    with app.app_context():
        assert float(db.session.get(Account, account_id).balance) == 100.0
        assert Recharge.query.filter_by(transaction_id=f'income:{income_id}').first() is None


def test_income_requires_positive_amount(client, admin_headers):
    assert _create_income(client, admin_headers, amount=0).status_code == 400
    assert _create_income(client, admin_headers, amount=-10).status_code == 400


def test_invalid_record_type_is_rejected(client, admin_headers):
    response = _create_income(client, admin_headers, record_type='refund')
    assert response.status_code == 400
    assert 'expense' in response.get_json()['message']


def test_income_categories_endpoint(client, admin_headers):
    body = client.get('/api/expenses/income-categories', headers=admin_headers).get_json()
    keys = {item['key'] for item in body['data']}
    assert 'platform_subsidy' in keys
    assert 'facebook_ads' not in keys      # 收入类别不应混入支出类别

    expense_categories = client.get('/api/expenses/categories',
                                    headers=admin_headers).get_json()
    assert {item['key'] for item in expense_categories['data']} == {
        'facebook_ads', 'product_cost', 'shipping_cost', 'other'
    }


def test_income_is_included_in_reports(client, app, admin_headers):
    from app import db
    from app.models import Order

    today = _today()
    with app.app_context():
        order = Order(
            shop_id=1, shopify_order_id='INC-1', order_number='#INC1',
            total_price=100, subtotal_price=100, actual_received=100, currency='CNY',
            financial_status='paid', order_date=today, created_at=today,
        )
        db.session.add(order)
        db.session.commit()

    _create_income(client, admin_headers, date=str(today), amount=50)

    trend = client.get(
        f'/api/reports/revenue-trend?start_date={today}&end_date={today}',
        headers=admin_headers,
    ).get_json()['data']
    row = next(item for item in trend if item['date'] == str(today))
    assert row['revenue'] == 150.0          # 100 订单 + 50 额外收入
    assert row['extra_income'] == 50.0

    profit = client.get(
        f'/api/reports/profit-analysis?start_date={today}&end_date={today}',
        headers=admin_headers,
    ).get_json()['data'][0]
    assert profit['revenue'] == 150.0

    daily = client.get(
        f'/api/reports/financial?report_type=daily&start_date={today}&end_date={today}',
        headers=admin_headers,
    ).get_json()['data']['financial_data'][0]
    assert daily['income'] == 150.0

    summary = client.get(
        f'/api/reports/financial-summary?start_date={today}&end_date={today}',
        headers=admin_headers,
    ).get_json()['data']
    assert summary['total_revenue'] == 150.0
    assert summary['extra_income'] == 50.0


def test_income_counts_as_expense_never(client, admin_headers):
    """收入绝不能出现在费用统计里（含按类别的月度费用分析）。"""
    _create_income(client, admin_headers, amount=999)

    monthly = client.get('/api/reports/expense-analysis',
                         headers=admin_headers).get_json()['data']
    total = sum(item['value'] for item in monthly)
    assert total < 999


def test_income_respects_shop_scope(client, admin_headers, sub_a_headers, shop_ids):
    _create_income(client, admin_headers, amount=100, shop_id=shop_ids['a'],
                   description='A店收入')
    _create_income(client, admin_headers, amount=200, shop_id=shop_ids['b'],
                   description='B店收入')

    sub_view = client.get('/api/expenses?record_type=income&per_page=50',
                          headers=sub_a_headers).get_json()['data']['expenses']
    assert [item['description'] for item in sub_view] == ['A店收入']

    sub_summary = client.get('/api/expenses/summary', headers=sub_a_headers).get_json()['data']
    assert sub_summary['total_income'] == 100.0
