"""Completed scans include both procurement and reserved stock, once per variant."""

from datetime import datetime
from json import dumps
from urllib.parse import quote

from app import db
from app.models import InventoryAdjustment, InventoryReservation, Order, OrderItem, PurchaseList, ShippingParcel


def _seed(app, shop_id, *, partial=False):
    with app.app_context():
        order = Order.query.filter_by(shop_id=shop_id).first()
        order.warehouse_status = 'pending' if partial else 'shipped'
        order.expected_parcel_count = 2
        order.shipped_at = None if partial else datetime(2026, 10, 13, 0, 30)
        item = OrderItem(shop_id=shop_id, order_id=order.id, shopify_line_item_id='movement-source',
                         shopify_product_id='movement-product', shopify_variant_id='movement-variant',
                         title='Movement Product', variant_title='Blue / iPhone 17', sku='00123', quantity=3)
        db.session.add(item)
        db.session.flush()
        key = dumps(['variant', 'movement-variant'])
        db.session.add(PurchaseList(shop_id=shop_id, name='采购测试', order_ids=[order.id], items=[],
                                   status='received', total_quantity=2,
                                   allocations=[{'order_id': order.id, 'order_item_id': item.id, 'group_key': key,
                                                 'quantity': 2, 'received_quantity': 2}],
                                   receipt_batches=[{'received_at': '2026-10-12T23:00:00Z',
                                                     'items': [{'group_key': key, 'received_quantity': 2}]}]))
        db.session.add(InventoryReservation(shop_id=shop_id, order_id=order.id, order_item_id=item.id,
                                            group_key=key, quantity=1))
        db.session.add_all([
            ShippingParcel(shop_id=shop_id, order_id=order.id, label_number='MOVEMENT-A',
                           scanned_at=datetime(2026, 10, 12, 15, 0)),
            ShippingParcel(shop_id=shop_id, order_id=order.id, label_number='MOVEMENT-B',
                           scanned_at=None if partial else datetime(2026, 10, 13, 0, 30)),
            InventoryAdjustment(shop_id=shop_id, group_key=key, quantity_delta=-1, reason='盘亏',
                                created_at=datetime(2026, 10, 13, 1)),
            InventoryAdjustment(shop_id=shop_id, group_key=key, quantity_delta=-1,
                                reason='售后 CF8303 扫码出库', created_at=datetime(2026, 10, 13, 2)),
            InventoryAdjustment(shop_id=shop_id, group_key=key, quantity_delta=1,
                                reason=f'通用型号调配至订单 {order.order_number}：source → target',
                                created_at=datetime(2026, 10, 13, 3)),
        ])
        db.session.commit()
        return order.order_number


def _get(client, headers, shop_id, suffix=''):
    return client.get(f'/api/inventory/movements?shop_id={shop_id}&start_date=2026-10-13&end_date=2026-10-13{suffix}',
                      headers=headers)


def test_purchase_and_reservation_merge_at_final_scan(app, client, admin_headers, shop_ids):
    ref = _seed(app, shop_ids['a'])
    result = _get(client, admin_headers, shop_ids['a'], '&movement_type=scan_outbound').get_json()['data']
    original = [row for row in result['movements'] if row['order_number'] == ref]
    assert len(original) == 1
    assert original[0]['quantity'] == 3  # 2 procurement arrivals + 1 manual reservation, not 2 scans x 3
    assert original[0]['date'] == '2026-10-13'
    assert original[0]['at'] == '2026-10-13T00:30:00Z'  # UTC is formatted once on the client
    assert original[0]['sku'] == '00123'
    assert result['totals'] == {'in_quantity': 0, 'out_quantity': 4}  # includes CF scan, not manual loss
    assert result['pagination']['total'] == 2


def test_partial_scan_not_counted_as_stock_out(app, client, admin_headers, shop_ids):
    ref = _seed(app, shop_ids['a'], partial=True)
    result = _get(client, admin_headers, shop_ids['a'], '&movement_type=scan_outbound').get_json()['data']
    assert not any(row['order_number'] == ref for row in result['movements'])
    assert result['totals']['out_quantity'] == 1  # CF only


def test_purchase_only_outbound_is_not_omitted(app, client, admin_headers, shop_ids):
    ref = _seed(app, shop_ids['a'])
    with app.app_context():
        InventoryReservation.query.filter_by(shop_id=shop_ids['a']).delete()
        db.session.commit()
    result = _get(client, admin_headers, shop_ids['a'], '&movement_type=scan_outbound').get_json()['data']
    row = next(row for row in result['movements'] if row['order_number'] == ref)
    assert row['quantity'] == 2


def test_automatic_adjustments_are_not_manual(app, client, admin_headers, shop_ids):
    _seed(app, shop_ids['a'])
    with app.app_context():
        db.session.add(InventoryAdjustment(shop_id=shop_ids['a'], group_key=dumps(['variant', 'movement-variant']),
                                           quantity_delta=1, reason='撤回售后 CF8303 出库',
                                           created_at=datetime(2026, 10, 13, 4)))
        db.session.commit()
    undo = _get(client, admin_headers, shop_ids['a'], '&movement_type=scan_undo').get_json()['data']
    assert undo['totals'] == {'in_quantity': 1, 'out_quantity': 0}
    assert undo['movements'][0]['order_number'] == 'CF8303'
    transfer = _get(client, admin_headers, shop_ids['a'], '&movement_type=stock_transfer').get_json()['data']
    assert transfer['pagination']['total'] == 1


def test_filters_pagination_summaries_and_shop_scope(app, client, admin_headers, shop_ids):
    ref = _seed(app, shop_ids['a'])
    manual = _get(client, admin_headers, shop_ids['a'], '&movement_type=manual_adjustment').get_json()['data']
    assert manual['pagination']['total'] == 1
    assert manual['totals'] == {'in_quantity': 0, 'out_quantity': 1}
    assert manual['movements'][0]['source'] == '手动调整 · 盘亏'
    receipt = _get(client, admin_headers, shop_ids['a'], '&movement_type=purchase_receipt').get_json()['data']
    assert receipt['totals'] == {'in_quantity': 2, 'out_quantity': 0}
    searched = _get(client, admin_headers, shop_ids['a'], f'&search={quote(ref, safe="")}&movement_type=scan_outbound').get_json()['data']
    assert searched['totals']['out_quantity'] == 3
    page = _get(client, admin_headers, shop_ids['a'], '&per_page=1&page=2').get_json()['data']
    assert len(page['movements']) == 1
    assert page['pagination']['total'] == 5
    assert page['totals'] == {'in_quantity': 3, 'out_quantity': 5}
    other = _get(client, admin_headers, shop_ids['b']).get_json()['data']
    assert other['pagination']['total'] == 0
    assert _get(client, admin_headers, shop_ids['a'], '&movement_type=unknown').status_code == 400
    assert _get(client, admin_headers, shop_ids['a'], '&per_page=0').status_code == 400


def test_movements_require_authorized_shop(client, sub_a_headers, shop_ids):
    assert _get(client, sub_a_headers, shop_ids['b'], '&movement_type=scan_outbound').status_code == 403
    assert client.get(f"/api/inventory/movements?shop_id={shop_ids['a']}").status_code == 401
