import json

from flask import g

from app import db
from app.models import InventoryAdjustment, InventoryReservation, Order, OrderItem, PurchaseList
from app.services.inventory_service import _stock_rows, auto_allocate_stock, zero_inventory


def test_zero_inventory_releases_live_claims_preserves_shipped_history_and_is_idempotent(app, shop_ids):
    with app.app_context():
        g.current_user = {"authority": "admin"}
        orders = Order.query.filter_by(shop_id=shop_ids['a']).order_by(Order.id).all()
        key = json.dumps(['variant', 'zero-test'])
        for index, order in enumerate(orders[:2]):
            order.warehouse_status = 'pending' if index == 0 else 'shipped'
            order.items.append(OrderItem(shop_id=shop_ids['a'], shopify_line_item_id=f'zero-{index}',
                                        shopify_variant_id='zero-test', title='Case', quantity=1))
        db.session.flush()
        purchase = PurchaseList(shop_id=shop_ids['a'], name='Receipt', order_ids=[o.id for o in orders[:2]],
            status='received', total_quantity=2, items=[{'group_key': key, 'title': 'Case', 'quantity': 2}],
            receipt=[{'group_key': key, 'received_quantity': 2}], allocations=[
                {'order_id': o.id, 'order_item_id': o.items[0].id, 'group_key': key,
                 'quantity': 1, 'received_quantity': 1} for o in orders[:2]])
        db.session.add(purchase)
        db.session.add(InventoryAdjustment(shop_id=shop_ids['b'], group_key=key, quantity_delta=10, reason='other shop'))
        db.session.commit()
        zero_inventory([shop_ids['a']], reason='zero stock test')
        assert purchase.allocations[0]['received_quantity'] == 0
        assert purchase.allocations[1]['received_quantity'] == 1
        assert purchase.receipt[0]['received_quantity'] == 2
        db.session.commit()
        auto_allocate_stock(shop_ids['a'])
        row = _stock_rows(shop_ids['a'], include_catalog=False)[key]
        assert row['on_hand_quantity'] == row['reserved_quantity'] == row['available_quantity'] == 0
        count = InventoryAdjustment.query.count()
        zero_inventory([shop_ids['a']], reason='zero stock test again')
        assert InventoryAdjustment.query.count() == count
        assert InventoryAdjustment.query.filter_by(shop_id=shop_ids['b']).one().quantity_delta == 10
        assert InventoryReservation.query.count() == 0
