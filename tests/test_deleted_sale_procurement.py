"""Archiving a case releases draft demand, never actual purchases or stock."""
import copy
from datetime import datetime

import pytest

from app import db
from app.models import AfterSale, Order, PurchaseList, PurchaseListEdit


@pytest.mark.parametrize('status', ['draft', 'ordered', 'partially_received', 'received'])
def test_delete_preserves_other_demand_and_confirmed_history(app, client, admin_headers, shop_ids, status):
    with app.app_context():
        order = Order.query.filter_by(shop_id=shop_ids['a']).first()
        order_number = order.order_number
        order_id = order.id
        sale = AfterSale(shop_id=order.shop_id, order_id=order.id, customer_reference='CFTEST',
                         kind='resend', reason='test', shipping_address={}, status='pending_purchase')
        db.session.add(sale)
        db.session.flush()
        sale_id = sale.id
        allocations = [dict(group_key='same', after_sale_id=sale.id, after_sale_item_id=1, quantity=1),
                       dict(group_key='same', order_id=order.id, order_item_id=1, quantity=2),
                       dict(group_key='manual', stock_only=True, quantity=3)]
        purchase = PurchaseList(shop_id=order.shop_id, name='test', status=status, order_ids=[order.id],
            order_count=2, total_quantity=6, allocations=allocations,
            items=[dict(group_key='same', quantity=3, order_numbers=['CFTEST', order.order_number]),
                   dict(group_key='manual', quantity=3, order_numbers=[])])
        db.session.add(purchase)
        db.session.commit()
        pid = purchase.id
        before = copy.deepcopy(purchase.to_dict(include_items=True))
    result = client.delete(f'/api/after-sales/{sale_id}', headers=admin_headers)
    assert result.status_code == 200, result.json
    with app.app_context():
        purchase = db.session.get(PurchaseList, pid)
        if status == 'draft':
            assert purchase.total_quantity == 5
            assert purchase.order_count == 1
            assert purchase.items[0]['quantity'] == 2
            assert purchase.items[0]['order_numbers'] == [order_number]
            assert all(row.get('after_sale_id') != sale_id for row in purchase.allocations)
            assert PurchaseListEdit.query.filter_by(purchase_id=pid).count() == 1
        else:
            assert purchase.to_dict(include_items=True) == before
            assert PurchaseListEdit.query.filter_by(purchase_id=pid).count() == 0
            from app.services.purchase_service import _serialize_purchase
            assert _serialize_purchase(purchase)['customer_references'] == ['CFTEST（已删除）']
    assert client.delete(f'/api/after-sales/{sale_id}', headers=admin_headers).status_code == 200
    with app.app_context():
        assert PurchaseListEdit.query.filter_by(purchase_id=pid).count() == (1 if status == 'draft' else 0)
        from app.services.purchase_links_service import after_sale_links
        links = after_sale_links(db.session.get(PurchaseList, pid))
        assert links == [{'id': sale_id, 'customer_reference': 'CFTEST', 'order_id': order_id,
                          'order_number': order_number, 'removed': status == 'draft', 'deleted': True}]


def test_repeat_delete_repairs_legacy_sale_only_draft(app, client, admin_headers, shop_ids):
    with app.app_context():
        order = Order.query.filter_by(shop_id=shop_ids['a']).first()
        sale = AfterSale(shop_id=order.shop_id, order_id=order.id, customer_reference='CFOLD',
                         kind='resend', reason='test', shipping_address={}, status='pending_purchase',
                         deleted_at=datetime.utcnow())
        db.session.add(sale)
        db.session.flush()
        sale_id = sale.id
        purchase = PurchaseList(shop_id=order.shop_id, name='legacy', status='draft', order_ids=[],
            after_sale_id=sale.id, order_count=1, total_quantity=1,
            allocations=[dict(group_key='one', after_sale_id=sale.id, quantity=1)],
            items=[dict(group_key='one', quantity=1, order_numbers=['CFOLD'])])
        db.session.add(purchase)
        db.session.commit()
        pid = purchase.id
    result = client.delete(f'/api/after-sales/{sale_id}', headers=admin_headers)
    assert result.status_code == 200, result.json
    with app.app_context():
        purchase = db.session.get(PurchaseList, pid)
        assert purchase.status == 'cancelled'
        assert purchase.items == purchase.allocations == []
        assert purchase.order_count == purchase.total_quantity == 0


def test_corrupt_draft_rolls_back_archive(app, client, admin_headers, shop_ids):
    with app.app_context():
        order = Order.query.filter_by(shop_id=shop_ids['a']).first()
        sale = AfterSale(shop_id=order.shop_id, order_id=order.id, customer_reference='CFMISMATCH',
                         kind='resend', reason='test', shipping_address={}, status='pending_purchase')
        db.session.add(sale)
        db.session.flush()
        sale_id = sale.id
        purchase = PurchaseList(shop_id=order.shop_id, name='bad', status='draft', order_ids=[],
            order_count=1, total_quantity=2, allocations=[dict(group_key='one', after_sale_id=sale.id, quantity=1)],
            items=[dict(group_key='one', quantity=2, order_numbers=['CFMISMATCH'])])
        db.session.add(purchase)
        db.session.commit()
        pid = purchase.id
    result = client.delete(f'/api/after-sales/{sale_id}', headers=admin_headers)
    assert result.status_code == 400
    assert f'#{pid}' in result.json['message']
    with app.app_context():
        assert db.session.get(AfterSale, sale_id).deleted_at is None
        assert db.session.get(PurchaseList, pid).total_quantity == 2
        assert PurchaseListEdit.query.filter_by(purchase_id=pid).count() == 0
