"""Shared planning is read-only; hard holds and physical deductions are atomic."""
import json
from datetime import datetime, timedelta

import pytest

from app import db
from app.models import (InventoryAdjustment, InventoryOutboundLine, InventoryReservation,
                        Order, OrderItem, PackingSession, Product, PurchaseList, ShippingParcel)
from app.services.inventory_pool_service import build_pool_plan
from app.services.inventory_service import _stock_rows


@pytest.fixture()
def pool_case(app, shop_ids):
    with app.app_context():
        shop_id = shop_ids["a"]
        a = Order(shop_id=shop_id, shopify_order_id="pool-A", order_number="POOL-A",
                  total_price=1, subtotal_price=1, financial_status="paid", warehouse_status="pending",
                  order_date=datetime(2026, 1, 1), expected_parcel_count=1)
        b = Order(shop_id=shop_id, shopify_order_id="pool-B", order_number="POOL-B",
                  total_price=1, subtotal_price=1, financial_status="paid", warehouse_status="pending",
                  order_date=datetime(2026, 1, 10), expected_parcel_count=1)
        db.session.add_all([a, b])
        db.session.flush()
        items = {}
        for order, variant in ((a,"X"),(a,"Y"),(b,"X")):
            item = OrderItem(shop_id=shop_id,order_id=order.id,shopify_line_item_id=f"pool-{order.id}-{variant}",
                            shopify_product_id="pool-product",shopify_variant_id=f"pool-{variant}",
                            title="Pool Case",variant_title=f"{variant} / iPhone 15 Pro",quantity=1)
            db.session.add(item)
            items[order.order_number,variant] = item
        for variant in ("X","Y"):
            db.session.add(Product(shop_id=shop_id,shopify_product_id="pool-product",shopify_variant_id=f"pool-{variant}",
                                   title="Pool Case",variant_title=f"{variant} / iPhone 15 Pro",price=1))
            db.session.add(InventoryAdjustment(shop_id=shop_id,group_key=json.dumps(["variant",f"pool-{variant}"]),
                                               quantity_delta=1,reason="Test physical stock"))
        db.session.flush()
        for order, variant in ((a,"Y"),(b,"X")):
            db.session.add(InventoryReservation(shop_id=shop_id,order_id=order.id,
                order_item_id=items[order.order_number,variant].id,
                group_key=json.dumps(["variant",f"pool-{variant}"]),quantity=1))
        for order in (a,b):
            db.session.add(ShippingParcel(shop_id=shop_id,order_id=order.id,label_number=f"LABEL-{order.order_number}"))
        db.session.commit()
        case = {"shop_id":shop_id,"a":a.id,"b":b.id,"x":json.dumps(["variant","pool-X"]),
               "y":json.dumps(["variant","pool-Y"]),"a_x":items['POOL-A','X'].id,"a_y":items['POOL-A','Y'].id}
    yield case


def _start(client, headers, case, target):
    return client.post('/api/shipping/packing/start',headers=headers,
                       json={"shop_id":case['shop_id'],"kind":"order","id":case[target]})


def test_existing_purchase_keeps_owner_covered_without_changing_scan_rights(app, pool_case):
    case = pool_case
    with app.app_context():
        InventoryAdjustment.query.filter_by(shop_id=case['shop_id']).delete()
        order = db.session.get(Order, case['a'])
        item = next(i for i in order.items if i.id == case['a_x'])
        purchase = PurchaseList(shop_id=case['shop_id'], name='owned', status='ordered',
            order_ids=[order.id], order_count=1, total_quantity=1,
            items=[{'group_key': case['x'], 'quantity': 1, 'title': item.title,
                    'variant_title': item.variant_title}],
            allocations=[{'order_id': order.id, 'order_item_id': item.id,
                          'group_key': case['x'], 'quantity': 1, 'received_quantity': 0}])
        db.session.add(purchase)
        db.session.get(Order, case['b']).fulfillment_priority = 100
        db.session.commit()
        pool = build_pool_plan(case['shop_id'])
        assert pool['uncovered_by_order'][case['a']][case['x']] == 0
        assert pool['uncovered_by_order'][case['a']][case['y']] == 1
        assert pool['uncovered_by_order'][case['b']][case['x']] == 1
        # Arrival preserves procurement coverage but does NOT make a hard scan hold.
        purchase.status = 'received'
        purchase.receipt = [{'group_key': case['x'], 'received_quantity': 1}]
        purchase.allocations = [{**purchase.allocations[0], 'received_quantity': 1}]
        db.session.commit()
        pool = build_pool_plan(case['shop_id'])
        assert pool['uncovered_by_order'][case['a']][case['x']] == 0
        assert pool['uncovered_by_order'][case['b']][case['x']] == 1
        assert case['b'] in pool['ready_order_ids']
        assert _stock_rows(case['shop_id'])[case['x']]['on_hand_quantity'] == 1


def test_cf_all_selection_cannot_repurchase_incoming_but_missing_reopens(app, client, admin_headers, pool_case):
    from app.models import AfterSale, AfterSaleItem
    case = pool_case
    with app.app_context():
        InventoryAdjustment.query.filter_by(shop_id=case['shop_id']).delete()
        InventoryReservation.query.filter_by(shop_id=case['shop_id']).delete()
        db.session.get(Order, case['b']).warehouse_status = 'shipped'
        sale = AfterSale(shop_id=case['shop_id'], order_id=case['b'], customer_reference='CF-PROMISE',
                         kind='resend', reason='Test', shipping_address={}, status='purchasing')
        sale.items.append(AfterSaleItem(group_key=case['x'], shopify_variant_id='pool-X',
            shopify_product_id='pool-product', title='Pool Case', variant_title='X / iPhone 15 Pro', quantity=1))
        db.session.add(sale)
        db.session.flush()
        purchase = PurchaseList(shop_id=case['shop_id'], name='CF incoming', status='ordered',
            order_ids=[], order_count=1, total_quantity=1,
            items=[{'group_key':case['x'], 'quantity':1, 'title':'Pool Case'}],
            allocations=[{'after_sale_id':sale.id, 'after_sale_item_id':sale.items[0].id,
                          'group_key':case['x'], 'quantity':1, 'received_quantity':0}])
        db.session.add(purchase)
        db.session.commit()
        sale_id, purchase_id = sale.id, purchase.id
        original = json.dumps(purchase.allocations)
    candidates = client.get('/api/purchase-candidates', headers=admin_headers,
        query_string={'shop_id':case['shop_id'], 'view':'needs_purchase'}).get_json()['data']
    assert sale_id not in candidates['all_after_sale_ids']
    assert case['a'] in candidates['all_order_ids']
    repeat = client.post('/api/purchase-lists', headers=admin_headers,
        json={'shop_id':case['shop_id'], 'order_ids':[], 'after_sale_ids':[sale_id]})
    assert repeat.status_code == 400
    with app.app_context():
        assert json.dumps(db.session.get(PurchaseList, purchase_id).allocations) == original
    missing = client.post(f'/api/purchase-lists/{purchase_id}/receive', headers=admin_headers,
        json={'batch_id':'CF-missing', 'finalize':False,
              'items':[{'group_key':case['x'], 'received_quantity':0, 'damaged_quantity':0, 'missing_quantity':1}]})
    assert missing.status_code == 200, missing.get_json()
    candidates = client.get('/api/purchase-candidates', headers=admin_headers,
        query_string={'shop_id':case['shop_id'], 'view':'needs_purchase'}).get_json()['data']
    assert sale_id in candidates['all_after_sale_ids']
    created = client.post('/api/purchase-lists', headers=admin_headers,
        json={'shop_id':case['shop_id'], 'order_ids':[], 'after_sale_ids':[sale_id]})
    assert created.status_code == 201, created.get_json()
    new_id = created.get_json()['data']['id']
    assert client.post(f'/api/purchase-lists/{new_id}/confirm', headers=admin_headers).status_code == 200
    repeated = client.post('/api/purchase-lists', headers=admin_headers,
        json={'shop_id':case['shop_id'], 'order_ids':[], 'after_sale_ids':[sale_id]})
    assert repeated.status_code == 400


def test_old_nearly_complete_order_uses_shared_stock_without_mutating_claims(app, pool_case):
    case=pool_case
    with app.app_context():
        before=[(r.id,r.quantity) for r in InventoryReservation.query.all()]
        pool=build_pool_plan(case['shop_id'])
        assert case['a'] in pool['ready_order_ids']
        assert case['b'] in pool['ready_order_ids']  # Both can use the last unit; first scan wins.
        assert pool['rows'][case['x']]['purchase_needed_quantity']==1
        for row in pool['rows'].values():
            assert row['on_hand_quantity']==sum(row[field] for field in
                ('ready_quantity','waiting_quantity','packing_quantity','idle_quantity'))
        assert before==[(r.id,r.quantity) for r in InventoryReservation.query.all()]


def test_dismissed_batch_remains_shippable_without_reopening_procurement(app, client, admin_headers, pool_case):
    from flask import g
    from app.services.purchase_service import exclude_order_procurement
    from app.services.shipping_queue_service import ready_rows

    case = pool_case
    with app.app_context():
        for order_id in (case['a'], case['b']):
            db.session.get(Order, order_id).fulfillment_status = 'fulfilled'
        db.session.commit()
        g.current_user = {'authority': 'admin'}
        exclude_order_procurement(case['shop_id'], [case['a'], case['b']])
        stamps = {order_id: db.session.get(Order, order_id).procurement_excluded_at
                  for order_id in (case['a'], case['b'])}
        default_pool = build_pool_plan(case['shop_id'])
        assert {case['a'], case['b']} <= default_pool['ready_order_ids']
        assert case['a'] not in default_pool['allocations']
        assert all(row['purchase_needed_quantity'] == 0 for row in default_pool['rows'].values())
        assert {case['a'], case['b']} <= {row['id'] for row in ready_rows(case['shop_id'])}
        assert {case['a'], case['b']} <= {row['id'] for row in ready_rows(case['shop_id'], pool=default_pool)}
        shipping_pool = build_pool_plan(case['shop_id'], respect_procurement_exclusion=False)
        assert case['a'] in shipping_pool['suggested_ready_order_ids']
        assert case['b'] not in shipping_pool['suggested_ready_order_ids']  # One X cannot print twice.
    result = client.get(f"/api/shipping/ready?shop_id={case['shop_id']}", headers=admin_headers)
    assert result.status_code == 200
    assert {case['a'], case['b']} <= {row['id'] for row in result.get_json()['data']['orders']}
    details = client.get(f"/api/orders/{case['a']}", headers=admin_headers).get_json()['data']
    assert details['procurement_status'] == 'ready_to_ship'
    for view in ('needs_purchase', 'replenish', 'unprocured'):
        candidates = client.get(f"/api/purchase-candidates?shop_id={case['shop_id']}&view={view}",
                                headers=admin_headers).get_json()['data']
        assert not {case['a'], case['b']} & set(candidates['all_order_ids'])
    with app.app_context():
        for order_id, stamp in stamps.items():
            order = db.session.get(Order, order_id)
            assert order.procurement_excluded_at == stamp and order.warehouse_status == 'pending'
        assert _stock_rows(case['shop_id'], group_keys={case['x']})[case['x']]['on_hand_quantity'] == 1
        assert InventoryOutboundLine.query.count() == 0


def test_dismissed_shortage_is_still_blocked_from_shipping(app, client, admin_headers, pool_case):
    case = pool_case
    with app.app_context():
        for order_id in (case['a'], case['b']):
            db.session.get(Order, order_id).procurement_excluded_at = datetime.utcnow()
        InventoryAdjustment.query.filter_by(group_key=case['y']).update({'quantity_delta': 0})
        db.session.commit()
        pool = build_pool_plan(case['shop_id'])
        assert case['a'] not in pool['ready_order_ids'] and case['b'] in pool['ready_order_ids']
        assert sum(line['quantity'] for line in pool['physical_allocations'][case['a']]) == 1
    result = client.get(f"/api/shipping/ready?shop_id={case['shop_id']}", headers=admin_headers).get_json()['data']
    ids = {row['id'] for row in result['orders']}
    assert case['a'] not in ids and case['b'] in ids
    blocked = client.post('/api/shipping/scan', headers=admin_headers,
                          json={'shop_id': case['shop_id'], 'shipping_label_number': 'LABEL-POOL-A'})
    assert blocked.status_code == 400


@pytest.mark.parametrize('financial_status,cancelled,status', [
    ('partially_paid', False, 'pending'), ('refunded', False, 'pending'),
    ('paid', True, 'pending'), ('paid', False, 'shipped'),
])
def test_dismissal_does_not_bypass_other_shipping_rules(app, client, admin_headers, pool_case,
                                                      financial_status, cancelled, status):
    case = pool_case
    with app.app_context():
        order = db.session.get(Order, case['b'])
        order.procurement_excluded_at = datetime.utcnow()
        order.financial_status = financial_status
        order.cancelled_at = datetime.utcnow() if cancelled else None
        order.warehouse_status = status
        db.session.commit()
    result = client.get(f"/api/shipping/ready?shop_id={case['shop_id']}", headers=admin_headers).get_json()['data']
    assert case['b'] not in {row['id'] for row in result['orders']}


def test_do_not_break_a_complete_new_order_when_old_order_still_cannot_ship(app, pool_case):
    case=pool_case
    with app.app_context():
        db.session.get(OrderItem,case['a_y']).quantity=2
        db.session.commit()
        pool=build_pool_plan(case['shop_id'])
        assert case['b'] in pool['ready_order_ids']
        assert case['a'] not in pool['ready_order_ids']
        assert pool['rows'][case['x']]['ready_quantity']==1
        assert pool['rows'][case['y']]['waiting_quantity']==1


def test_partially_paid_soft_claim_can_supply_paid_complete_order(app, client, admin_headers, pool_case):
    case = pool_case
    with app.app_context():
        db.session.get(Order, case['b']).financial_status = 'partially_paid'
        db.session.commit()
    start = _start(client, admin_headers, case, 'a')
    assert start.status_code == 200, start.get_json()
    with app.app_context():
        assert db.session.get(Order, case['b']).warehouse_status == 'pending'
        assert _stock_rows(case['shop_id'], group_keys={case['x']})[case['x']]['on_hand_quantity'] == 1


def test_legacy_partial_claim_is_protected_until_reconciliation(app, client, admin_headers, pool_case):
    case = pool_case
    with app.app_context():
        db.session.get(Order, case['b']).warehouse_status = 'legacy_partial_unverified'
        db.session.commit()
        pool = build_pool_plan(case['shop_id'])
        assert case['a'] not in pool['ready_order_ids']
        assert pool['rows'][case['x']]['packing_quantity'] == 0
        assert pool['rows'][case['x']]['on_hand_quantity'] == 0
    assert _start(client, admin_headers, case, 'a').status_code == 400


def test_priority_hold_expiry_and_employee_ownership(app, client, admin_headers, sub_b_headers, pool_case):
    case=pool_case
    assert _start(client,admin_headers,case,'b').status_code==200
    priority={'shop_id':case['shop_id'],'kind':'order','id':case['b'],'priority':100}
    assert client.post('/api/shipping/packing/priority',headers=admin_headers,json=priority).status_code==200
    started=_start(client,admin_headers,case,'b')
    assert started.status_code==200,started.get_json()
    assert _start(client,sub_b_headers,case,'b').status_code==400
    priority['priority']=0
    client.post('/api/shipping/packing/priority',headers=admin_headers,json=priority)
    assert _start(client,admin_headers,case,'a').status_code==200  # Other packing task never holds stock.
    with app.app_context():
        session=db.session.get(PackingSession,started.get_json()['data']['id'])
        session.expires_at=datetime.utcnow()-timedelta(seconds=1)
        db.session.commit()
    assert _start(client,admin_headers,case,'a').status_code==200


def test_scan_is_immutable_and_undo_returns_exact_stock(app, client, admin_headers, pool_case):
    case=pool_case
    start=_start(client,admin_headers,case,'a')
    assert start.status_code==200,start.get_json()
    adjustment=client.post('/api/inventory/adjustments',headers=admin_headers,
        json={'shop_id':case['shop_id'],'group_key':case['x'],'quantity_delta':-1})
    assert adjustment.status_code==201
    blocked=client.post('/api/shipping/scan',headers=admin_headers,
        json={'shop_id':case['shop_id'],'shipping_label_number':'LABEL-POOL-A'})
    assert blocked.status_code==400  # Count changes are rechecked even after packing starts.
    assert client.post('/api/inventory/adjustments',headers=admin_headers,
        json={'shop_id':case['shop_id'],'group_key':case['x'],'quantity_delta':1}).status_code==201
    scanned=client.post('/api/shipping/scan',headers=admin_headers,
        json={'shop_id':case['shop_id'],'shipping_label_number':'LABEL-POOL-A'})
    assert scanned.status_code==200,scanned.get_json()
    assert client.post('/api/shipping/scan',headers=admin_headers,
        json={'shop_id':case['shop_id'],'shipping_label_number':'LABEL-POOL-A'}).status_code==400
    with app.app_context():
        assert sum(line.quantity_delta for line in InventoryOutboundLine.query.filter_by(order_id=case['a']))==-2
        # Simulate a later order edit clearing soft claims: physical deductions stay.
        InventoryReservation.query.filter_by(order_id=case['a']).delete()
        db.session.commit()
        rows=_stock_rows(case['shop_id'],group_keys={case['x'],case['y']})
        assert rows[case['x']]['on_hand_quantity']==rows[case['y']]['on_hand_quantity']==0
    response=client.post('/api/shipping/outbound/undo',headers=admin_headers,
                         json={'shop_id':case['shop_id'],'order_id':case['a']})
    assert response.status_code==200,response.get_json()
    with app.app_context():
        rows=_stock_rows(case['shop_id'],group_keys={case['x'],case['y']})
        assert rows[case['x']]['on_hand_quantity']==rows[case['y']]['on_hand_quantity']==1
        assert sum(line.quantity_delta for line in InventoryOutboundLine.query.filter_by(order_id=case['a']))==0


def test_stockless_incoming_is_not_shippable_and_drafts_do_not_cover_shortage(app, pool_case):
    case=pool_case
    with app.app_context():
        InventoryAdjustment.query.filter_by(shop_id=case['shop_id']).update({'quantity_delta':0})
        p=PurchaseList(shop_id=case['shop_id'],name='incoming',status='draft',order_ids=[],order_count=0,
                       total_quantity=2,items=[{'group_key':case['x'],'title':'Pool Case','variant_title':'X / iPhone 15 Pro',
                       'shopify_product_id':'pool-product','quantity':2}],allocations=[])
        db.session.add(p)
        db.session.commit()
        pool=build_pool_plan(case['shop_id'])
        assert pool['rows'][case['x']]['incoming_quantity']==0
        assert pool['rows'][case['x']]['purchase_needed_quantity']==2
        p.status='ordered'
        db.session.commit()
        pool=build_pool_plan(case['shop_id'])
        assert pool['rows'][case['x']]['incoming_quantity']==2
        assert pool['rows'][case['x']]['purchase_needed_quantity']==0
        assert not pool['ready_order_ids']


def test_partial_scan_hold_never_expires_or_cancels_without_undo(app, client, admin_headers, pool_case):
    case=pool_case
    with app.app_context():
        order=db.session.get(Order,case['a'])
        order.expected_parcel_count=2
        db.session.add(ShippingParcel(shop_id=case['shop_id'],order_id=order.id,label_number='LABEL-POOL-A-2'))
        db.session.commit()
    scan=client.post('/api/shipping/scan',headers=admin_headers,
                     json={'shop_id':case['shop_id'],'shipping_label_number':'LABEL-POOL-A'})
    assert scan.status_code==200,scan.get_json()
    with app.app_context():
        session=PackingSession.query.filter_by(order_id=case['a'],state='completed').one()
        session.expires_at=datetime.utcnow()-timedelta(hours=1)
        db.session.commit()
        pool=build_pool_plan(case['shop_id'])
        assert pool['rows'][case['x']]['packing_quantity']==0
        assert pool['rows'][case['x']]['on_hand_quantity']==0
        assert case['b'] not in pool['ready_order_ids']
        assert InventoryOutboundLine.query.filter_by(order_id=case['a']).count()==2
    assert client.post('/api/shipping/packing/cancel',headers=admin_headers,
        json={'shop_id':case['shop_id'],'kind':'order','id':case['a']}).status_code==400
    second=client.post('/api/shipping/scan',headers=admin_headers,
                       json={'shop_id':case['shop_id'],'shipping_label_number':'LABEL-POOL-A-2'})
    assert second.status_code==200,second.get_json()
    with app.app_context():
        assert InventoryOutboundLine.query.filter_by(order_id=case['a']).count()==2


def test_order_edit_after_packing_blocks_scan_without_deducting(app, client, admin_headers, pool_case):
    case=pool_case
    assert _start(client,admin_headers,case,'a').status_code==200
    with app.app_context():
        db.session.get(OrderItem,case['a_x']).quantity=2
        db.session.commit()
    lookup = client.get('/api/shipping/packing-lookup', headers=admin_headers,
        query_string={'shop_id': case['shop_id'], 'code': 'LABEL-POOL-A'})
    assert lookup.status_code == 200
    assert lookup.get_json()['data']['outbound']['ready'] is False
    response=client.post('/api/shipping/scan',headers=admin_headers,
                         json={'shop_id':case['shop_id'],'shipping_label_number':'LABEL-POOL-A'})
    assert response.status_code==400
    assert '发生变化' in response.get_json()['message']
    with app.app_context():
        assert InventoryOutboundLine.query.count()==0
        assert db.session.get(Order,case['a']).warehouse_status=='pending'


def test_packing_permission_and_scope(app, client, limited_headers, admin_headers, pool_case, shop_ids):
    case=pool_case
    assert _start(client,limited_headers,case,'a').status_code==403
    response=client.post('/api/shipping/packing/start',headers=admin_headers,
                         json={'shop_id':shop_ids['b'],'kind':'order','id':case['a']})
    assert response.status_code==404


def test_inventory_summary_metrics_and_drilldown_balance(app, client, admin_headers, pool_case):
    case=pool_case
    response=client.get(f"/api/inventory?shop_id={case['shop_id']}&group_by_product=true",headers=admin_headers)
    assert response.status_code==200,response.get_json()
    data=response.get_json()['data']
    assert data['summary']['on_hand_quantity']==2
    assert data['summary']['ready_quantity']==2
    assert data['summary']['purchase_needed_quantity']==1
    product=data['products'][0]
    assert sum(color['ready_quantity'] for color in product['colors'])==product['ready_quantity']
    assert sum(item['purchase_needed_quantity'] for color in product['colors'] for item in color['variants'])==1
    assert product['purchase_orders'][0]['order_number']=='POOL-B'


def test_order_variant_edit_invalidates_purchase_draft(app, client, admin_headers, pool_case):
    case = pool_case
    with app.app_context():
        InventoryAdjustment.query.filter_by(shop_id=case['shop_id']).update({'quantity_delta': 0})
        InventoryReservation.query.filter_by(shop_id=case['shop_id']).delete()
        db.session.commit()
    created = client.post('/api/purchase-lists', headers=admin_headers,
        json={'shop_id': case['shop_id'], 'order_ids': [case['a']]})
    assert created.status_code == 201, created.get_json()
    with app.app_context():
        db.session.get(OrderItem, case['a_x']).shopify_variant_id = 'different-current-variant'
        db.session.commit()
    response = client.post(f"/api/purchase-lists/{created.get_json()['data']['id']}/confirm", headers=admin_headers)
    assert response.status_code == 400
    assert '变体' in response.get_json()['message']


def test_immutable_outbound_movement_survives_soft_claim_changes(app, client, admin_headers, pool_case):
    case = pool_case
    scan = client.post('/api/shipping/scan', headers=admin_headers,
        json={'shop_id': case['shop_id'], 'shipping_label_number': 'LABEL-POOL-A'})
    assert scan.status_code == 200, scan.get_json()
    with app.app_context():
        InventoryReservation.query.filter_by(order_id=case['a']).delete()
        db.session.commit()
    movement = client.get('/api/inventory/movements', headers=admin_headers,
        query_string={'shop_id': case['shop_id'], 'movement_type': 'scan_outbound'})
    assert movement.status_code == 200
    data = movement.get_json()['data']
    assert data['totals']['out_quantity'] == 2
    assert all(row['order_number'] == 'POOL-A' for row in data['movements'])


@pytest.mark.parametrize('compatible', [False, True])
def test_cf_uses_public_stock_and_only_deducts_replacement(app, client, admin_headers, pool_case, compatible):
    from app.models import AfterSale, AfterSaleItem
    case = pool_case
    with app.app_context():
        # Original is incomplete, so a whole CF package may use the soft-held
        # stock. Different model identities must still respect compatibility.
        db.session.get(OrderItem, case['a_y']).quantity = 2
        db.session.get(Order, case['b']).warehouse_status = 'shipped'
        InventoryReservation.query.filter_by(order_id=case['b']).delete()
        item = db.session.get(OrderItem, case['a_x'])
        item.variant_title = 'X / iPhone 17 Pro' if compatible else item.variant_title
        product = Product.query.filter_by(shopify_variant_id='pool-X').one()
        product.variant_title = item.variant_title
        key = json.dumps(['variant', 'cf-18']) if compatible else case['x']
        sale = AfterSale(shop_id=case['shop_id'], order_id=case['b'], customer_reference='CF-POOL',
                         kind='exchange', reason='Test replacement', shipping_address={}, tracking_number='CF-LABEL')
        sale.items.append(AfterSaleItem(shopify_product_id='pool-product',shopify_variant_id='cf-18' if compatible else 'pool-X',
            group_key=key,title='Pool Case',variant_title='X / iPhone 18 Pro' if compatible else item.variant_title,quantity=1))
        db.session.add(sale)
        db.session.commit()
        sale_id = sale.id
        pool = build_pool_plan(case['shop_id'])
        assert sale.id in pool['cf_ready_ids']
        assert sum(pool['cf_uncovered'][sale.id].values()) == 0
    created = client.post('/api/purchase-lists', headers=admin_headers,
        json={'shop_id':case['shop_id'],'after_sale_ids':[sale_id],'order_ids':[]})
    assert created.status_code == 400, created.get_json()  # Existing stock means no duplicate procurement.
    started = client.post('/api/shipping/packing/start',headers=admin_headers,
        json={'shop_id':case['shop_id'],'kind':'after_sale','id':sale_id})
    assert started.status_code == 200, started.get_json()
    scanned = client.post('/api/shipping/scan',headers=admin_headers,
        json={'shop_id':case['shop_id'],'shipping_label_number':'CF-LABEL'})
    assert scanned.status_code == 200, scanned.get_json()
    assert client.post('/api/shipping/scan',headers=admin_headers,
        json={'shop_id':case['shop_id'],'shipping_label_number':'CF-LABEL'}).status_code == 400
    with app.app_context():
        assert db.session.get(Order,case['b']).warehouse_status == 'shipped'
        assert db.session.get(AfterSale,sale_id).status == 'shipped'
        assert sum(row['on_hand_quantity'] for row in _stock_rows(case['shop_id'],include_catalog=False).values()) == 1
        assert InventoryOutboundLine.query.count() == 0  # CF uses its independent immutable ledger.
