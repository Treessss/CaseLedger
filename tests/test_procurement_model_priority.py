"""In-transit stock covers its own model before compatible model demand."""
import json
from datetime import datetime, timedelta

import pytest

from app import db
from app.models import (AfterSale, AfterSaleItem, InventoryAdjustment, Order,
                        OrderItem, Product, PurchaseList)
from app.services.inventory_pool_service import build_pool_plan


def _model_case(shop_id, *, reduced_model=17, tier='Pro Max', newer_kind='order'):
    incoming_model = 18 if reduced_model == 17 else 17
    keys = {model: json.dumps(['variant', f'khaki-{model}']) for model in (17, 18)}
    product_id = 'khaki-model-priority'
    orders = []
    for index, model in enumerate((reduced_model, incoming_model)):
        order = Order(shop_id=shop_id, shopify_order_id=f'khaki-order-{model}',
            order_number=f'MODEL-{model}', order_date=datetime(2026, 1, 1) + timedelta(days=index),
            financial_status='paid', warehouse_status='pending', total_price=10, subtotal_price=10)
        db.session.add(order)
        db.session.flush()
        orders.append(order)
        db.session.add(Product(shop_id=shop_id, shopify_product_id=product_id,
            shopify_variant_id=f'khaki-{model}', title='Silicone Case', price=10,
            variant_title=f'Khaki Case Only / iPhone {model} {tier}'))
        if index == 1 and newer_kind == 'sale':
            order.warehouse_status = 'shipped'
            sale = AfterSale(shop_id=shop_id, order_id=order.id,
                customer_reference=f'CFMODEL-{model}', kind='resend', reason='test',
                shipping_address={}, status='purchasing')
            db.session.add(sale)
            db.session.flush()
            db.session.add(AfterSaleItem(after_sale_id=sale.id, group_key=keys[model],
                shopify_product_id=product_id, shopify_variant_id=f'khaki-{model}',
                title='Silicone Case', variant_title=f'Khaki Case Only / iPhone {model} {tier}',
                quantity=1))
        else:
            db.session.add(OrderItem(shop_id=shop_id, order_id=order.id,
                shopify_line_item_id=f'khaki-line-{model}', shopify_product_id=product_id,
                shopify_variant_id=f'khaki-{model}', title='Silicone Case',
                variant_title=f'Khaki Case Only / iPhone {model} {tier}', quantity=1))
    db.session.add(InventoryAdjustment(shop_id=shop_id, group_key=keys[reduced_model],
        quantity_delta=1, reason='initial count'))
    purchase = PurchaseList(shop_id=shop_id, name='incoming exact model', status='ordered',
        order_ids=[], allocations=[], items=[{'group_key': keys[incoming_model],
            'title': 'Silicone Case', 'shopify_product_id': product_id,
            'shopify_variant_id': f'khaki-{incoming_model}',
            'variant_title': f'Khaki Case Only / iPhone {incoming_model} {tier}', 'quantity': 1}])
    db.session.add(purchase)
    db.session.commit()
    return keys, orders, purchase


@pytest.mark.parametrize('reduced_model', [17, 18])
@pytest.mark.parametrize('tier', ['Pro', 'Pro Max'])
@pytest.mark.parametrize('newer_kind', ['order', 'sale'])
def test_count_reduction_keeps_shortage_on_reduced_model(
        app, client, admin_headers, shop_ids, reduced_model, tier, newer_kind):
    shop_id = shop_ids['a']
    with app.app_context():
        keys, orders, _ = _model_case(shop_id, reduced_model=reduced_model,
                                     tier=tier, newer_kind=newer_kind)
        incoming_model = 18 if reduced_model == 17 else 17
        pool = build_pool_plan(shop_id)
        assert sum(row['purchase_needed_quantity'] for row in pool['rows'].values()) == 0
    response = client.post('/api/inventory/adjustments', headers=admin_headers,
        json={'shop_id': shop_id, 'group_key': keys[reduced_model], 'quantity_delta': -1})
    assert response.status_code == 201, response.get_json()
    with app.app_context():
        pool = build_pool_plan(shop_id)
        assert pool['rows'][keys[reduced_model]]['on_hand_quantity'] == 0
        assert pool['rows'][keys[reduced_model]]['purchase_needed_quantity'] == 1
        assert pool['rows'][keys[incoming_model]]['purchase_needed_quantity'] == 0
        assert pool['rows'][keys[incoming_model]]['incoming_quantity'] == 1
        assert pool['uncovered_by_order'][orders[0].id][keys[reduced_model]] == 1
        if newer_kind == 'order':
            assert pool['pending_by_order'][orders[1].id][keys[incoming_model]] == 1
        else:
            assert not any(pool['cf_uncovered'][AfterSale.query.one().id].values())
        assert not pool['ready_order_ids'] and not pool['cf_ready_ids']


def test_surplus_incoming_still_covers_compatible_model(app, shop_ids):
    with app.app_context():
        shop_id = shop_ids['a']
        keys, orders, purchase = _model_case(shop_id)
        InventoryAdjustment.query.filter_by(shop_id=shop_id).update({'quantity_delta': 0})
        purchase.items = [{**purchase.items[0], 'quantity': 2}]
        db.session.commit()
        pool = build_pool_plan(shop_id)
        assert sum(row['purchase_needed_quantity'] for row in pool['rows'].values()) == 0
        assert pool['pending_by_order'][orders[0].id][keys[17]] == 1
        assert pool['pending_by_order'][orders[1].id][keys[18]] == 1
        assert not pool['ready_order_ids']  # In-transit goods are never physical stock.
