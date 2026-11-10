"""Public count regression: originals and reshipments compete only at atomic scan time."""
import json
from datetime import datetime

import pytest

from app import db
from app.models import (AfterSale, AfterSaleItem, InventoryAdjustment, InventoryOutboundLine,
                        InventoryReservation, Order, OrderItem, Product, PurchaseList, ShippingParcel)
from app.services.inventory_service import _stock_rows
from app.services.inventory_pool_service import build_pool_plan


@pytest.fixture()
def shared(app, shop_ids):
    with app.app_context():
        shop = shop_ids['a']
        orders = []
        for suffix in ('A', 'B'):
            order = Order(shop_id=shop, shopify_order_id='quantity-'+suffix,
                order_number='Q-'+suffix, order_date=datetime.utcnow(), financial_status='paid',
                total_price=10, subtotal_price=10, warehouse_status='pending')
            db.session.add(order)
            db.session.flush()
            db.session.add(OrderItem(shop_id=shop, order_id=order.id,
                shopify_line_item_id='quantity-line-'+suffix, shopify_product_id='quantity-product',
                shopify_variant_id='quantity-17', title='Quantity Case', variant_title='Blue / iPhone 17 Pro', quantity=1))
            db.session.add(ShippingParcel(shop_id=shop, order_id=order.id, label_number='LABEL-Q-'+suffix))
            orders.append(order)
        sale = AfterSale(shop_id=shop, order_id=orders[0].id, customer_reference='CFQ-A',
            kind='resend', reason='quantity test', shipping_address={}, status='ready',
            tracking_number='LABEL-Q-CF', label_barcode='LABEL-Q-CF')
        db.session.add(sale)
        db.session.flush()
        db.session.add(AfterSaleItem(after_sale_id=sale.id, group_key=json.dumps(['variant', 'quantity-17']),
            shopify_product_id='quantity-product', shopify_variant_id='quantity-17', title='Quantity Case',
            variant_title='Blue / iPhone 17 Pro', quantity=1, reserved_quantity=1))
        for suffix in ('17', '18'):
            db.session.add(Product(shop_id=shop, shopify_product_id='quantity-product',
                shopify_variant_id='quantity-'+suffix, title='Quantity Case',
                variant_title=f'Blue / iPhone {suffix} Pro', price=10))
        key = json.dumps(['variant', 'quantity-18'])
        db.session.add(InventoryAdjustment(shop_id=shop, group_key=key, quantity_delta=1, reason='test stock'))
        db.session.commit()
        yield {'shop': shop, 'a': orders[0].id, 'b': orders[1].id, 'cf': sale.id, 'key': key}


def scan(client, headers, shared, label):
    return client.post('/api/shipping/scan', headers=headers,
                       json={'shop_id': shared['shop'], 'shipping_label_number': label})


def test_pool_restores_lines_after_lightweight_list_projection(app, shared):
    from sqlalchemy.orm import noload
    with app.app_context():
        db.session.expire_all()
        projected = Order.query.options(noload(Order.items)).filter(Order.id.in_([shared['a'], shared['b']])).all()
        assert all(order.items == [] for order in projected)
        pool = build_pool_plan(shared['shop'])
        assert sum(row['demand_quantity'] for row in pool['rows'].values()) == 3
        assert {shared['a'], shared['b']} <= pool['ready_order_ids']
        assert all(order.items for order in projected)


def test_original_can_use_cf_stock_and_second_order_cannot_oversell(app, client, admin_headers, shared):
    with app.app_context():
        pool = build_pool_plan(shared['shop'])
        assert {shared['a'], shared['b']} <= pool['ready_order_ids']
        assert shared['cf'] in pool['cf_ready_ids']
        assert len(pool['suggested_ready_order_ids']) + len(pool['suggested_cf_ready_ids']) == 1
        assert sum(row['demand_quantity'] for row in pool['rows'].values()) == 3
        assert sum(row['purchase_needed_quantity'] for row in pool['rows'].values()) == 2
    assert scan(client, admin_headers, shared, 'LABEL-Q-A').status_code == 200
    assert scan(client, admin_headers, shared, 'LABEL-Q-B').status_code == 400
    assert scan(client, admin_headers, shared, 'LABEL-Q-CF').status_code == 400
    with app.app_context():
        assert _stock_rows(shared['shop'], group_keys={shared['key']})[shared['key']]['on_hand_quantity'] == 0
        assert InventoryReservation.query.filter_by(shop_id=shared['shop']).count() == 0
        assert sum(row.quantity_delta for row in InventoryOutboundLine.query.filter_by(order_id=shared['a'])) == -1
    sale = client.get(f"/api/after-sales/{shared['cf']}", headers=admin_headers).get_json()['data']
    assert sale['items'][0]['needed_quantity'] == 1
    assert sale['status'] != 'ready'


def test_cf_undo_restores_actual_compatible_source_not_target(app, client, admin_headers, shared):
    assert scan(client, admin_headers, shared, 'LABEL-Q-CF').status_code == 200
    assert scan(client, admin_headers, shared, 'LABEL-Q-CF').status_code == 400
    response = client.post('/api/shipping/outbound/undo', headers=admin_headers,
                           json={'shop_id': shared['shop'], 'after_sale_id': shared['cf']})
    assert response.status_code == 200, response.get_json()
    with app.app_context():
        rows = _stock_rows(shared['shop'], include_catalog=True)
        assert rows[shared['key']]['on_hand_quantity'] == 1
        assert rows[json.dumps(['variant', 'quantity-17'])]['on_hand_quantity'] == 0
    assert scan(client, admin_headers, shared, 'LABEL-Q-B').status_code == 200


def test_net_procurement_covers_total_demand_without_binding(app, shared):
    with app.app_context():
        db.session.add(PurchaseList(shop_id=shared['shop'], name='Public incoming', status='ordered',
            items=[{'group_key': shared['key'], 'title': 'Quantity Case', 'shopify_product_id': 'quantity-product',
                    'variant_title': 'Blue / iPhone 18 Pro', 'quantity': 1}], allocations=[], order_ids=[]))
        db.session.commit()
        pool = build_pool_plan(shared['shop'])
        assert sum(row['demand_quantity'] for row in pool['rows'].values()) == 3
        assert sum(row['incoming_quantity'] for row in pool['rows'].values()) == 1
        assert sum(row['purchase_needed_quantity'] for row in pool['rows'].values()) == 1
        assert sum(row['on_hand_quantity'] for row in pool['rows'].values()) == 1


def test_partial_packages_deduct_once_and_return_snapshot(app, client, admin_headers, shared):
    with app.app_context():
        db.session.get(Order, shared['a']).expected_parcel_count = 2
        db.session.add(ShippingParcel(shop_id=shared['shop'], order_id=shared['a'], label_number='LABEL-Q-A2'))
        db.session.commit()
    first = scan(client, admin_headers, shared, 'LABEL-Q-A')
    assert first.status_code == 200
    assert first.get_json()['data']['warehouse_status'] == 'pending'
    assert scan(client, admin_headers, shared, 'LABEL-Q-B').status_code == 400
    assert scan(client, admin_headers, shared, 'LABEL-Q-A2').status_code == 200
    with app.app_context():
        assert InventoryOutboundLine.query.filter_by(order_id=shared['a']).count() == 1
    assert client.post('/api/shipping/outbound/undo', headers=admin_headers,
                       json={'shop_id': shared['shop'], 'order_id': shared['a']}).status_code == 200
    with app.app_context():
        assert _stock_rows(shared['shop'], group_keys={shared['key']})[shared['key']]['on_hand_quantity'] == 1


def test_edit_after_first_package_cannot_change_committed_snapshot(app, client, admin_headers, shared):
    with app.app_context():
        db.session.get(Order, shared['a']).expected_parcel_count = 2
        db.session.add(ShippingParcel(shop_id=shared['shop'], order_id=shared['a'], label_number='LABEL-Q-A2'))
        db.session.commit()
    assert scan(client, admin_headers, shared, 'LABEL-Q-A').status_code == 200
    with app.app_context():
        OrderItem.query.filter_by(order_id=shared['a']).one().quantity = 2
        db.session.commit()
    response = scan(client, admin_headers, shared, 'LABEL-Q-A2')
    assert response.status_code == 400
    assert '发生变化' in response.get_json()['message']
    with app.app_context():
        assert InventoryOutboundLine.query.filter_by(order_id=shared['a']).count() == 1
        assert db.session.get(Order, shared['a']).warehouse_status == 'pending'
