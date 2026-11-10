"""Report endpoints: exact numbers plus query-count regression checks."""
from datetime import datetime, timedelta


def _today():
    """报表按北京时间归日，测试也使用北京时间当天。"""
    return (datetime.utcnow() + timedelta(hours=8)).date()


def _add_order(shop_id, suffix, day, actual_received, currency='CNY', status='paid'):
    from app import db
    from app.models import Order

    order = Order(
        shop_id=shop_id,
        shopify_order_id=f'R-{suffix}',
        order_number=f'#R{suffix}',
        total_price=actual_received,
        subtotal_price=actual_received,
        actual_received=actual_received,
        currency=currency,
        financial_status=status,
        fulfillment_status='fulfilled',
        order_date=datetime.combine(day, datetime.min.time()),
        created_at=datetime.combine(day, datetime.min.time()),
    )
    db.session.add(order)
    db.session.commit()
    return order.id


def _add_expense(shop_id, day, amount):
    from app import db
    from app.models import Expense

    expense = Expense(
        shop_id=shop_id,
        category='facebook_ads',
        amount=amount,
        currency='CNY',
        description=f'expense {day}',
        expense_date=day,
    )
    db.session.add(expense)
    db.session.commit()
    return expense.id


def test_revenue_trend_matches_expected_numbers(client, app, admin_headers, shop_ids):
    today = _today()
    yesterday = today - timedelta(days=1)

    with app.app_context():
        _add_order(shop_ids['a'], 'a1', today, 100)
        _add_order(shop_ids['a'], 'a2', today, 50)
        _add_order(shop_ids['a'], 'a3', yesterday, 30)
        _add_order(shop_ids['a'], 'a4', today, 999, status='pending')  # 未支付，不计收入
        _add_order(shop_ids['b'], 'b1', today, 777)                    # 其他店铺

    response = client.get(
        f'/api/reports/revenue-trend?start_date={yesterday}&end_date={today}',
        headers=admin_headers,
    )
    assert response.status_code == 200
    data = response.get_json()['data']

    by_date = {row['date']: row for row in data}
    # 种子数据: 店铺A 今天/昨天/前天各 1 单(actual_received 为空 → 0 收入)，店铺B 今天 1 单
    assert by_date[str(yesterday)]['revenue'] == 30.0
    assert by_date[str(yesterday)]['order_count'] == 2  # 种子单 + a3
    # 管理员口径：所有店铺都已支付订单的收入合计 100 + 50 + 777
    assert by_date[str(today)]['revenue'] == 927.0
    assert by_date[str(today)]['order_count'] == 6  # 种子A + 种子B + a1 + a2 + a4 + b1


def test_revenue_trend_is_shop_scoped(client, app, sub_a_headers, shop_ids):
    today = _today()
    with app.app_context():
        _add_order(shop_ids['a'], 'a1', today, 100)
        _add_order(shop_ids['b'], 'b1', today, 777)

    data = client.get(
        f'/api/reports/revenue-trend?start_date={today}&end_date={today}',
        headers=sub_a_headers,
    ).get_json()['data']

    assert data[0]['revenue'] == 100.0
    assert data[0]['order_count'] == 2  # 种子单 + a1（都属店铺A）


def test_partially_refunded_order_keeps_remaining_income(client, app, admin_headers, shop_ids):
    today = _today()
    with app.app_context():
        _add_order(shop_ids['a'], 'partial-refund', today, 60, status='partially_refunded')
        _add_order(shop_ids['a'], 'full-refund', today, 100, status='refunded')

    data = client.get(
        f'/api/reports/revenue-trend?start_date={today}&end_date={today}',
        headers=admin_headers,
    ).get_json()['data']
    assert data[0]['revenue'] == 60.0


def test_revenue_uses_settlement_and_beijing_order_date(client, app, admin_headers, shop_ids):
    from app import db
    from app.models import Order

    with app.app_context():
        order_id = _add_order(shop_ids['a'], 'settlement', datetime(2026, 1, 1).date(), 100)
        order = db.session.get(Order, order_id)
        order.order_date = datetime(2026, 1, 1, 16, 30)  # 北京时间 1 月 2 日 00:30
        order.created_at = datetime(2026, 1, 5)  # 数据同步时间不能决定报表归日
        order.settlement_amount = 80
        order.settlement_currency = 'CNY'
        db.session.commit()

    data = client.get(
        '/api/reports/revenue-trend?start_date=2026-01-02&end_date=2026-01-02',
        headers=admin_headers,
    ).get_json()['data']
    assert data == [{
        'date': '2026-01-02', 'revenue': 80.0, 'order_count': 1,
        'extra_income': 0, 'currency': 'CNY',
    }]


def test_profit_analysis_combines_revenue_and_expenses(client, app, admin_headers, shop_ids):
    today = _today()
    with app.app_context():
        _add_order(shop_ids['a'], 'p1', today, 500)
        _add_expense(shop_ids['a'], today, 120)

    data = client.get(
        f'/api/reports/profit-analysis?start_date={today}&end_date={today}',
        headers=admin_headers,
    ).get_json()['data']

    assert len(data) == 1
    row = data[0]
    assert row['revenue'] == 500.0
    assert row['expenses'] == 170.0  # 120（本用例） + 50（种子数据）
    assert row['profit'] == 330.0
    assert row['margin'] == 66.0


def test_expense_analysis_groups_by_month(client, app, admin_headers, shop_ids):
    today = _today()
    with app.app_context():
        _add_expense(shop_ids['a'], today, 200)
        _add_expense(shop_ids['a'], today, 45.5)

    data = client.get('/api/reports/expense-analysis', headers=admin_headers).get_json()['data']
    current_month = today.strftime('%Y-%m')
    row = next(item for item in data if item['month'] == current_month)

    # 种子数据里还有一笔 50 的费用
    assert row['value'] == 295.5


def test_daily_financial_report_matches_expected_numbers(client, app, admin_headers, shop_ids):
    today = _today()
    with app.app_context():
        _add_order(shop_ids['a'], 'd1', today, 400)
        _add_expense(shop_ids['a'], today, 100)

    response = client.get(
        f'/api/reports/financial?report_type=daily&start_date={today}&end_date={today}',
        headers=admin_headers,
    )
    assert response.status_code == 200
    row = response.get_json()['data']['financial_data'][0]

    assert row['date'] == str(today)
    assert row['income'] == 400.0
    assert row['expense'] == 150.0  # 100（当日） + 50（种子数据）
    assert row['profit'] == 250.0


def test_daily_report_uses_beijing_midnight_for_order_and_business_date_for_expense(
    client, app, admin_headers, shop_ids,
):
    from app import db
    from app.models import Order

    day = datetime(2026, 1, 2).date()
    with app.app_context():
        before_id = _add_order(shop_ids['a'], 'before-bj-midnight', day, 10)
        first_id = _add_order(shop_ids['a'], 'at-bj-midnight', day, 20)
        last_id = _add_order(shop_ids['a'], 'end-bj-day', day, 30)
        after_id = _add_order(shop_ids['a'], 'after-bj-day', day, 40)
        for order_id, timestamp in (
            (before_id, datetime(2026, 1, 1, 15, 59, 59, 999999)),
            (first_id, datetime(2026, 1, 1, 16)),
            (last_id, datetime(2026, 1, 2, 15, 59, 59, 999999)),
            (after_id, datetime(2026, 1, 2, 16)),
        ):
            db.session.get(Order, order_id).order_date = timestamp
        _add_expense(shop_ids['a'], day, 7)
        db.session.commit()

    response = client.get(
        '/api/reports/financial?report_type=daily&start_date=2026-01-02&end_date=2026-01-02',
        headers=admin_headers,
    )
    assert response.status_code == 200
    assert response.get_json()['data']['financial_data'] == [
        {'date': '2026-01-02', 'income': 50.0, 'expense': 7.0, 'profit': 43.0},
    ]


def test_reports_reject_invalid_dates(client, admin_headers):
    response = client.get(
        '/api/reports/revenue-trend?start_date=nope&end_date=2026-01-01',
        headers=admin_headers,
    )
    assert response.status_code == 400


def _count_queries(app):
    """Context manager counting SQL statements issued against the app engine."""
    from sqlalchemy import event
    from app import db

    class _Counter:
        def __enter__(self):
            self.count = 0
            self.engine = db.engine

            def _before(conn, cursor, statement, parameters, context, executemany):
                self.count += 1

            self._listener = _before
            event.listen(self.engine, 'before_cursor_execute', _before)
            return self

        def __exit__(self, *exc_info):
            event.remove(self.engine, 'before_cursor_execute', self._listener)
            return False

    return _Counter()


def test_profit_analysis_does_not_scale_queries_with_days(client, app, admin_headers):
    """Regression guard for the N+1 that used to issue ~4 queries per day."""
    today = _today()
    start = today - timedelta(days=29)

    with app.app_context():
        counter = _count_queries(app)
        with counter:
            response = client.get(
                f'/api/reports/profit-analysis?start_date={start}&end_date={today}',
                headers=admin_headers,
            )

    assert response.status_code == 200
    assert len(response.get_json()['data']) == 30
    # 30 天区间应当只有「鉴权 + 3 次区间查询」量级的语句，而不是 100+
    assert counter.count <= 12, f'查询次数过多: {counter.count}'


def test_daily_financial_report_does_not_scale_queries_with_days(client, app, admin_headers):
    today = _today()
    start = today - timedelta(days=29)

    with app.app_context():
        counter = _count_queries(app)
        with counter:
            response = client.get(
                f'/api/reports/financial?report_type=daily&start_date={start}&end_date={today}',
                headers=admin_headers,
            )

    assert response.status_code == 200
    assert counter.count <= 15, f'查询次数过多: {counter.count}'
