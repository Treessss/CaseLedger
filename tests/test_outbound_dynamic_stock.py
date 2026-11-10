"""Outbound can borrow matching physical stock without crossing configuration boundaries."""

from json import dumps

import pytest

from app import db
from app.models import InventoryAdjustment, InventoryReservation, Order, OrderItem, OutboundEvent, PurchaseList, ShippingParcel
from app.services.inventory_service import _stock_rows
from app.services.purchase_service import _group_key, attach_procurement_status


def _setup(app, shop_ids, *, target='Pink / iPhone 18 Pro', source='Pink / iPhone 18 Pro', purchase=False):
    with app.app_context():
        a, b = Order.query.filter_by(shop_id=shop_ids['a']).order_by(Order.id).limit(2).all()
        # This receiver explicitly has dispatch priority; without it the
        # whole-order planner may correctly favor the older donor.
        a.fulfillment_priority = 100
        lines = []
        for order, variant, vid in ((a, target, 'target'), (b, source, 'target' if target == source else 'source')):
            item = OrderItem(shop_id=order.shop_id, order_id=order.id, shopify_line_item_id=f'line-{order.id}',
                             shopify_product_id='case', shopify_variant_id=vid, title='Case', variant_title=variant, quantity=1)
            db.session.add(item)
            lines.append(item)
        db.session.flush()
        key = dumps(_group_key(lines[1]), ensure_ascii=False)
        if purchase:
            db.session.add(PurchaseList(shop_id=b.shop_id, name='Receipt', status='partially_received',
                order_ids=[b.id], items=[{'group_key': key, 'title': 'Case', 'variant_title': source}],
                allocations=[{'order_id': b.id, 'order_item_id': lines[1].id, 'group_key': key,
                              'quantity': 1, 'received_quantity': 1}],
                receipt=[{'group_key': key, 'received_quantity': 1}]))
        else:
            db.session.add(InventoryAdjustment(shop_id=b.shop_id, group_key=key, quantity_delta=1, reason='Seed'))
            db.session.add(InventoryReservation(shop_id=b.shop_id, order_id=b.id, order_item_id=lines[1].id, group_key=key, quantity=1))
        db.session.add(ShippingParcel(shop_id=a.shop_id, order_id=a.id, label_number='LABEL-A', label_barcode='LABEL-A', source='manual'))
        db.session.commit()
        return a.id, b.id, key


@pytest.mark.parametrize('purchase', [False, True])
def test_scan_reassigns_reserved_or_received_stock_and_marks_donor_shortage(app, client, admin_headers, shop_ids, purchase):
    a, b, key = _setup(app, shop_ids, purchase=purchase)
    with app.app_context():
        from app.services.shipping_service import order_outbound_blockers
        assert order_outbound_blockers(db.session.get(Order, a)) == []
        # Preflight is read-only; donor still owns the only item.
        assert _stock_rows(shop_ids['a'], group_keys={key})[key]['occupied_orders'] == []
    response = client.post(f'/api/orders/{a}/scan-outbound', headers=admin_headers, json={'shipping_label_number': 'LABEL-A'})
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['data']['inventory_transfers'] == []  # No donor/ownership transfer.
    with app.app_context():
        assert db.session.get(Order, a).warehouse_status == 'shipped'
        status = [{'id': b, 'shop_id': shop_ids['a'], 'warehouse_status': 'pending', 'financial_status': 'paid'}]
        attach_procurement_status(status)
        assert status[0]['procurement_stocked_quantity'] == 0
        assert status[0]['procurement_pending_quantity'] == 0
        stock = _stock_rows(shop_ids['a'], group_keys={key})[key]
        assert stock['on_hand_quantity'] == stock['reserved_quantity'] == 0
        assert OutboundEvent.query.filter_by(order_id=a, action='scan').count() == 1
    assert client.post(f'/api/orders/{a}/scan-outbound', headers=admin_headers, json={'shipping_label_number': 'LABEL-A'}).status_code == 400


@pytest.mark.parametrize('source,target,success', [
    ('Pink / iPhone 17 Pro', 'Pink / iPhone 18 Pro', True),
    ('Pink / iPhone 18 Pro Max', 'Pink / iPhone 17 Pro Max', True),
    ('Pink / iPhone 17 Pro Max', 'Pink / iPhone 18 Pro', False),
    ('Blue / iPhone 17 Pro', 'Pink / iPhone 18 Pro', False),
    ('Pink + Wrist Chain / iPhone 17 Pro', 'Pink / iPhone 18 Pro', False),
    ('Pink / iPhone 16 Pro', 'Pink / iPhone 18 Pro', False),
])
def test_compatible_pairs_keep_product_color_configuration_and_tier(app, client, admin_headers, shop_ids, source, target, success):
    a, b, key = _setup(app, shop_ids, target=target, source=source)
    response = client.post(f'/api/orders/{a}/scan-outbound', headers=admin_headers, json={'shipping_label_number': 'LABEL-A'})
    assert response.status_code == (200 if success else 400), response.get_json()
    with app.app_context():
        stock = _stock_rows(shop_ids['a'], group_keys={key})[key]
        assert stock['on_hand_quantity'] == (0 if success else 1)
        assert db.session.get(Order, b).warehouse_status == 'pending'
        assert InventoryAdjustment.query.count() == 1  # Charge the actual source key; no fake reclassification.


def test_missing_second_item_rolls_back_all_and_partially_scanned_donor_is_protected(app, client, admin_headers, shop_ids):
    a, b, key = _setup(app, shop_ids)
    with app.app_context():
        db.session.add(OrderItem(shop_id=shop_ids['a'], order_id=a, shopify_line_item_id='missing',
                                shopify_product_id='other', shopify_variant_id='other', title='Missing', quantity=1))
        db.session.commit()
    assert client.post(f'/api/orders/{a}/scan-outbound', headers=admin_headers, json={'shipping_label_number': 'LABEL-A'}).status_code == 400
    with app.app_context():
        assert InventoryReservation.query.filter_by(order_id=b).one().quantity == 1
        assert OutboundEvent.query.count() == 0
        OrderItem.query.filter_by(order_id=a, shopify_variant_id='other').delete()
        from datetime import datetime
        db.session.add(ShippingParcel(shop_id=shop_ids['a'], order_id=b, label_number='PARTIAL', source='manual', scanned_at=datetime.utcnow()))
        db.session.commit()
    assert client.post(f'/api/orders/{a}/scan-outbound', headers=admin_headers, json={'shipping_label_number': 'LABEL-A'}).status_code == 400


def test_exact_model_free_stock_is_used_before_compatible_reserved_stock(app, client, admin_headers, shop_ids):
    a, b, source_key = _setup(app, shop_ids, target='Pink / iPhone 18 Pro', source='Pink / iPhone 17 Pro')
    with app.app_context():
        target = OrderItem.query.filter_by(order_id=a).one()
        key = dumps(_group_key(target), ensure_ascii=False)
        db.session.add(InventoryAdjustment(shop_id=shop_ids['a'], group_key=key, quantity_delta=1, reason='Exact'))
        db.session.commit()
    response = client.post(f'/api/orders/{a}/scan-outbound', headers=admin_headers, json={'shipping_label_number': 'LABEL-A'})
    assert response.status_code == 200, response.get_json()
    with app.app_context():
        assert InventoryReservation.query.filter_by(order_id=b).one().quantity == 1
        assert _stock_rows(shop_ids['a'], group_keys={source_key})[source_key]['on_hand_quantity'] == 1
        assert InventoryAdjustment.query.count() == 2


def test_compatible_stock_from_another_product_is_not_used(app, client, admin_headers, shop_ids):
    a, b, _key_value = _setup(app, shop_ids, target='Pink / iPhone 18 Pro', source='Pink / iPhone 17 Pro')
    with app.app_context():
        OrderItem.query.filter_by(order_id=b).one().shopify_product_id = 'different-product'
        db.session.commit()
    response = client.post(f'/api/orders/{a}/scan-outbound', headers=admin_headers, json={'shipping_label_number': 'LABEL-A'})
    assert response.status_code == 400
    with app.app_context():
        assert InventoryReservation.query.filter_by(order_id=b).one().quantity == 1
        assert OutboundEvent.query.count() == 0
