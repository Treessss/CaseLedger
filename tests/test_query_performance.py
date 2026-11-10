"""List endpoints should not issue one SQL query per returned row."""

from contextlib import contextmanager

from sqlalchemy import event

from app import db
from app.models import AfterSale, Order, PurchaseList


@contextmanager
def _query_counter(engine):
    statements = []

    def record(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", record)


def test_after_sale_list_queries_stay_bounded(app, client, admin_headers, shop_ids):
    with app.app_context():
        order = Order.query.filter_by(shop_id=shop_ids["a"]).first()
        db.session.add_all([
            AfterSale(
                shop_id=shop_ids["a"], order_id=order.id,
                customer_reference=f"CF-PERF-{number}", kind="resend",
                reason="query test", status="pending_purchase", shipping_address={},
            ) for number in range(12)
        ])
        db.session.commit()
        engine = db.engine

    with _query_counter(engine) as small:
        assert client.get("/api/after-sales?per_page=1", headers=admin_headers).status_code == 200
    with _query_counter(engine) as large:
        response = client.get("/api/after-sales?per_page=12", headers=admin_headers)
    assert response.status_code == 200
    assert len(response.get_json()["data"]["after_sales"]) == 12
    assert len(large) <= len(small) + 3


def test_purchase_list_queries_stay_bounded(app, client, admin_headers, shop_ids):
    with app.app_context():
        db.session.add_all([
            PurchaseList(
                shop_id=shop_ids["a"], name=f"perf-{number}", order_ids=[],
                items=[], order_count=0, total_quantity=0, status="draft",
                allocations=[],
            ) for number in range(12)
        ])
        db.session.commit()
        engine = db.engine

    with _query_counter(engine) as small:
        assert client.get("/api/purchase-lists?per_page=1", headers=admin_headers).status_code == 200
    with _query_counter(engine) as large:
        response = client.get("/api/purchase-lists?per_page=12", headers=admin_headers)
    assert response.status_code == 200
    assert len(response.get_json()["data"]["purchase_lists"]) == 12
    assert len(large) <= len(small) + 2


def test_order_stats_uses_aggregates_instead_of_per_metric_queries(app, client, admin_headers):
    with app.app_context():
        engine = db.engine
    with _query_counter(engine) as statements:
        response = client.get("/api/orders/stats?days=30", headers=admin_headers)
    assert response.status_code == 200
    assert response.get_json()["data"]["period"]["orders"] == 4
    assert len(statements) <= 6


def test_reports_do_not_load_order_relationships(app, client, admin_headers):
    with app.app_context():
        engine = db.engine
    with _query_counter(engine) as statements:
        response = client.get('/api/reports/financial-summary', headers=admin_headers)
    assert response.status_code == 200
    for statement in statements:
        assert 'FROM shipping_parcels' not in statement
        assert 'FROM order_items' not in statement
        assert 'FROM after_sales' not in statement


def test_purchase_list_returns_summary_but_detail_keeps_receipts(app, client, admin_headers, shop_ids):
    with app.app_context():
        purchase = PurchaseList(shop_id=shop_ids['a'], name='summary', order_ids=[], items=[],
                                order_count=0, total_quantity=3, status='received', allocations=[],
                                receipt=[{'group_key': 'x', 'received_quantity': 2}],
                                receipt_batches=[{'items': [{'received_quantity': 2}]}])
        db.session.add(purchase)
        db.session.commit()
        purchase_id = purchase.id
    row = client.get('/api/purchase-lists', headers=admin_headers).get_json()['data']['purchase_lists'][0]
    assert row['received_quantity'] == 2
    assert 'receipt' not in row and 'receipt_batches' not in row
    detail = client.get(f'/api/purchase-lists/{purchase_id}', headers=admin_headers).get_json()['data']
    assert detail['receipt'][0]['received_quantity'] == 2
    assert len(detail['receipt_batches']) == 1
