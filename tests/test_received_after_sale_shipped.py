import json
from datetime import datetime

import pytest
from flask import g

from app import db
from app.models import AfterSale, AfterSaleItem, InventoryAdjustment, Order, OrderItem, PurchaseList
from app.services.after_sale_service import scan_outbound
from app.services.inventory_service import _stock_rows
from app.services.purchase_service import _group_key


def test_receipt_after_shared_stock_scan_preserves_outbound_and_does_not_double_debit(app, client, admin_headers, shop_ids):
    with app.app_context():
        order = Order.query.filter_by(shop_id=shop_ids['a']).first()
        order.warehouse_status = 'shipped'
        item = OrderItem(shop_id=order.shop_id, order_id=order.id, shopify_line_item_id='receipt-after-scan',
            shopify_variant_id='receipt-after-scan', title='Case', variant_title='Purple / iPhone 16 Pro', quantity=1)
        db.session.add(item)
        db.session.flush()
        key = json.dumps(_group_key(item), ensure_ascii=False)
        sale = AfterSale(shop_id=order.shop_id, order_id=order.id, customer_reference='CFRECEIPT',
            kind='resend', reason='test', status='purchasing', shipping_address={},
            purchased_at=datetime.utcnow(), tracking_number='4PX-RECEIPT', label_barcode='4PX-RECEIPT')
        sale.items.append(AfterSaleItem(source_order_item_id=item.id, group_key=key, title=item.title,
            variant_title=item.variant_title, shopify_variant_id=item.shopify_variant_id, quantity=1))
        db.session.add(sale)
        db.session.flush()
        sale_id, sid = sale.id, order.shop_id
        purchase = PurchaseList(shop_id=sid, name='late arrival', status='ordered', order_ids=[],
            after_sale_id=sale.id, order_count=1, total_quantity=3,
            items=[{'group_key': key, 'title': item.title, 'variant_title': item.variant_title,
                    'quantity': 3, 'order_numbers': ['CFRECEIPT']}],
            allocations=[{'after_sale_id': sale.id, 'after_sale_item_id': sale.items[0].id,
                          'group_key': key, 'quantity': 3, 'received_quantity': 0}])
        db.session.add(purchase)
        db.session.add(InventoryAdjustment(shop_id=sid, group_key=key, quantity_delta=1, reason='existing shared stock'))
        db.session.commit()
        pid = purchase.id
        with app.test_request_context():
            g.current_user = {'authority': 'admin'}
            scan_outbound({'shop_id': sid, 'label_barcode': '4PX-RECEIPT'})
        shipped_at = db.session.get(AfterSale, sale_id).shipped_at
        assert _stock_rows(sid, group_keys={key})[key]['on_hand_quantity'] == 0
        adjustments = InventoryAdjustment.query.filter_by(shop_id=sid).count()
    first = {'batch_id': 'after-scan-1', 'finalize': False, 'items': [
        {'group_key': key, 'received_quantity': 1, 'damaged_quantity': 0, 'missing_quantity': 0}]}
    result = client.post(f'/api/purchase-lists/{pid}/receive', headers=admin_headers, json=first)
    assert result.status_code == 200, result.json
    assert result.json['data']['status'] == 'partially_received'
    assert client.post(f'/api/purchase-lists/{pid}/receive', headers=admin_headers, json=first).status_code == 200
    with app.app_context():
        assert _stock_rows(sid, group_keys={key})[key]['on_hand_quantity'] == 1
    final = {'batch_id': 'after-scan-2', 'finalize': True, 'items': [
        {'group_key': key, 'received_quantity': 0, 'damaged_quantity': 1, 'missing_quantity': 1}]}
    result = client.post(f'/api/purchase-lists/{pid}/receive', headers=admin_headers, json=final)
    assert result.status_code == 200, result.json
    with app.app_context():
        sale = db.session.get(AfterSale, sale_id)
        assert sale.status == 'shipped' and sale.shipped_at == shipped_at
        assert sale.tracking_number == sale.label_barcode == '4PX-RECEIPT'
        assert sale.items[0].received_quantity == sale.items[0].damaged_quantity == 1
        assert InventoryAdjustment.query.filter_by(shop_id=sid).count() == adjustments
        assert _stock_rows(sid, group_keys={key})[key]['on_hand_quantity'] == 1
    repeated = client.post(f'/api/purchase-lists/{pid}/receive', headers=admin_headers, json=final)
    assert repeated.status_code == 200


@pytest.mark.parametrize('invalid', ['missing', 'other_shop'])
def test_invalid_sale_identity_still_rejects_receipt_atomically(app, client, admin_headers, shop_ids, invalid):
    with app.app_context():
        order = Order.query.filter_by(shop_id=shop_ids['b']).first()
        sale = AfterSale(shop_id=order.shop_id, order_id=order.id, customer_reference='CFOTHER',
            kind='resend', reason='test', shipping_address={}, status='shipped')
        db.session.add(sale)
        db.session.flush()
        sale_id = sale.id if invalid == 'other_shop' else sale.id + 10000
        purchase = PurchaseList(shop_id=shop_ids['a'], name='invalid identity', status='ordered',
            order_ids=[], total_quantity=1, items=[{'group_key': 'invalid', 'title': 'Case', 'quantity': 1}],
            allocations=[{'after_sale_id': sale_id, 'after_sale_item_id': 1,
                          'group_key': 'invalid', 'quantity': 1, 'received_quantity': 0}])
        db.session.add(purchase)
        db.session.commit()
        pid = purchase.id
    result = client.post(f'/api/purchase-lists/{pid}/receive', headers=admin_headers,
        json={'batch_id': 'invalid-case', 'finalize': True, 'items': [
            {'group_key': 'invalid', 'received_quantity': 1, 'damaged_quantity': 0}]})
    assert result.status_code == 400, result.json
    assert '归属不一致' in result.json['message']
    with app.app_context():
        purchase = db.session.get(PurchaseList, pid)
        assert purchase.status == 'ordered'
        assert purchase.receipt == purchase.receipt_batches == []
        assert purchase.allocations[0]['received_quantity'] == 0
