from datetime import datetime

from app import db
from app.models import ACTION_SCAN, AfterSale, AfterSaleItem, Order, OrderItem, OutboundEvent
from app.services.shipping_queue_service import today_outbound_summary


def test_today_goods_beijing_boundaries_and_shop_isolation(app, shop_ids):
    now = datetime(2026, 10, 12, 1)
    with app.app_context():
        order = Order.query.filter_by(shop_id=shop_ids['a']).first()
        order.warehouse_status = 'shipped'
        order.shipped_at = datetime(2026, 10, 11, 16)
        db.session.add_all([
            OrderItem(order_id=order.id, shopify_line_item_id='today', title='Case', quantity=4),
            OrderItem(order_id=order.id, shopify_line_item_id='tip', title='Tip', quantity=1),
            OutboundEvent(shop_id=shop_ids['a'], order_id=order.id, order_number=order.order_number, action=ACTION_SCAN),
        ])
        sale = AfterSale(shop_id=shop_ids['a'], order_id=order.id, customer_reference='CFtoday',
                         kind='original', reason='test', shipping_address={}, status='shipped', shipped_at=now)
        db.session.add(sale)
        db.session.flush()
        db.session.add_all([
            AfterSaleItem(after_sale_id=sale.id, group_key='today', title='Case', quantity=3),
            OutboundEvent(shop_id=shop_ids['a'], order_id=order.id, after_sale_id=sale.id,
                          order_number='CFtoday', action=ACTION_SCAN),
        ])
        db.session.commit()
        assert today_outbound_summary(shop_ids['a'], now)['quantity'] == 7
        assert today_outbound_summary(shop_ids['a'], now)['total_orders'] == 2
        assert today_outbound_summary(shop_ids['a'], now)['order_count'] == 1
        assert today_outbound_summary(shop_ids['a'], now)['after_sale_count'] == 1
        assert today_outbound_summary(shop_ids['b'], now)['quantity'] == 0
        order.shipped_at = datetime(2026, 10, 11, 15, 59, 59)
        db.session.flush()
        assert today_outbound_summary(shop_ids['a'], now)['quantity'] == 3
        order.shipped_at = datetime(2026, 10, 12, 16)
        db.session.flush()
        assert today_outbound_summary(shop_ids['a'], now)['quantity'] == 3
        order.shipped_at = now
        order.warehouse_status = 'pending'
        sale.status = 'ready'
        sale.shipped_at = None
        db.session.flush()
        assert today_outbound_summary(shop_ids['a'], now)['quantity'] == 0
        assert today_outbound_summary(shop_ids['a'], now)['total_orders'] == 0
