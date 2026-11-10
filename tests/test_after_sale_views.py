from datetime import datetime

from app import db
from app.models import AfterSale, Order, PurchaseList


def test_workflow_tabs_are_shop_scoped_paginated_and_independent_of_stock(app, client, admin_headers, shop_ids):
    with app.app_context():
        order = Order.query.filter_by(shop_id=shop_ids['a']).first()
        records = []
        for index, status in enumerate(['ready', 'pending_purchase', 'purchasing', 'ready', 'shipped', 'ready', 'ready']):
            sale = AfterSale(shop_id=order.shop_id, order_id=order.id, customer_reference=f'CF{index}',
                kind='resend', reason='test', shipping_address={}, status=status,
                shipped_at=datetime.utcnow() if index == 5 else None,
                deleted_at=datetime.utcnow() if index == 6 else None)
            db.session.add(sale)
            records.append(sale)
        db.session.flush()
        for index, status in [(1, 'draft'), (2, 'ordered'), (3, 'received')]:
            db.session.add(PurchaseList(shop_id=order.shop_id, name=status, status=status, order_ids=[],
                items=[], allocations=[{'after_sale_id': records[index].id, 'quantity': 1}]))
        other_order = Order.query.filter_by(shop_id=shop_ids['b']).first()
        db.session.add(AfterSale(shop_id=other_order.shop_id, order_id=other_order.id,
            customer_reference='CFOTHER', kind='resend', reason='test', shipping_address={}, status='ready'))
        db.session.commit()
    expected = {'all': {'CF0', 'CF1', 'CF2', 'CF3', 'CF4', 'CF5'},
                'unprocessed': {'CF0', 'CF1', 'CF2', 'CF3'}, 'unprocured': {'CF0', 'CF1'},
                'shipped': {'CF4', 'CF5'}}
    for view, references in expected.items():
        result = client.get(f"/api/after-sales?shop_id={shop_ids['a']}&view={view}", headers=admin_headers)
        assert result.status_code == 200, result.json
        data = result.json['data']
        assert {row['customer_reference'] for row in data['after_sales']} == references
        assert data['counts'] == {key: len(values) for key, values in expected.items()}
    paged = client.get(f"/api/after-sales?shop_id={shop_ids['a']}&view=shipped&per_page=1&page=2",
                       headers=admin_headers).json['data']
    assert paged['pagination']['total'] == 2
    assert len(paged['after_sales']) == 1
    bad = client.get(f"/api/after-sales?shop_id={shop_ids['a']}&view=invalid", headers=admin_headers)
    assert bad.status_code == 400
