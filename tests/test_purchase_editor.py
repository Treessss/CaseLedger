"""Manual procurement is catalog-scoped supply, not a rewrite of orders or receipts."""
import pytest

from app import db
from app.models import Order, OrderItem, Product, PurchaseList, PurchaseListEdit
from app.services.inventory_pool_service import build_pool_plan


def _seed(app, shop_id):
    with app.app_context():
        order = Order.query.filter_by(shop_id=shop_id).first()
        order.financial_status = 'paid'
        order.warehouse_status = 'pending'
        item = OrderItem(shop_id=shop_id, order_id=order.id, shopify_line_item_id='editor-line',
                         shopify_product_id=f'edit-product-{shop_id}', shopify_variant_id=f'edit-{shop_id}-blue', title='Edit Case',
                         variant_title='Blue / iPhone 17 Pro', sku='BLUE-17', quantity=3)
        products = [Product(shop_id=shop_id, shopify_product_id=f'edit-product-{shop_id}',
                    shopify_variant_id=f'edit-{shop_id}-{color}', title='Edit Case', price=1,
                    variant_title=f'{color.title()} / iPhone 17 Pro', sku=f'{color}-17') for color in ('blue', 'red')]
        db.session.add_all([item, *products])
        db.session.commit()
        return order.id, [row.id for row in products]


def _create(client, headers, shop_id, order_id):
    response = client.post('/api/purchase-lists', headers=headers,
                            json={'shop_id': shop_id, 'order_ids': [order_id]})
    assert response.status_code == 201, response.get_json()
    return response.get_json()['data']


def _edit(client, headers, detail, quantity, additions=None):
    return client.patch(f"/api/purchase-lists/{detail['id']}/items", headers=headers,
        json={'edit_token': detail['edit_token'], 'items': [
            {'group_key': row['group_key'], 'quantity': quantity} for row in detail['items']],
            'additions': additions or []})


def test_manual_purchase_catalog_stock_and_receipt(app, client, admin_headers, shop_ids):
    order_id, products = _seed(app, shop_ids['a'])
    response = client.post('/api/purchase-lists/manual', headers=admin_headers,
        json={'shop_id': shop_ids['a'], 'name': '额外备货', 'items': [{'product_id': products[1], 'quantity': 5}]})
    assert response.status_code == 201, response.get_json()
    detail = response.get_json()['data']
    assert detail['order_ids'] == detail['orders'] == []
    assert detail['order_count'] == 0 and detail['can_edit_items']
    assert detail['items'][0]['order_numbers'] == []
    assert client.post(f"/api/purchase-lists/{detail['id']}/confirm", headers=admin_headers).status_code == 200
    received = client.post(f"/api/purchase-lists/{detail['id']}/receive", headers=admin_headers,
        json={'items': [{'group_key': detail['items'][0]['group_key'], 'received_quantity': 5, 'damaged_quantity': 0}]})
    assert received.status_code == 200
    assert not received.get_json()['data']['can_edit_items']
    with app.app_context():
        assert build_pool_plan(shop_ids['a'])['rows'][detail['items'][0]['group_key']]['on_hand_quantity'] == 5
        assert db.session.get(Order, order_id).warehouse_status == 'pending'


def test_edit_draft_merges_extra_same_variant_without_fabricating_order_demand(app, client, admin_headers, shop_ids):
    order_id, products = _seed(app, shop_ids['a'])
    detail = _create(client, admin_headers, shop_ids['a'], order_id)
    response = _edit(client, admin_headers, detail, 2, [{'product_id': products[0], 'quantity': 2}])
    assert response.status_code == 200, response.get_json()
    changed = response.get_json()['data']
    assert changed['total_quantity'] == 4
    assert len(changed['items']) == 1
    assert sum(row['quantity'] for row in changed['allocations'] if row.get('stock_only')) == 2
    assert sum(row['quantity'] for row in changed['allocations'] if row.get('order_item_id')) == 2
    assert changed['order_count'] == 1
    assert _edit(client, admin_headers, detail, 2, [{'product_id': products[0], 'quantity': 2}]).status_code == 409
    with app.app_context():
        assert OrderItem.query.filter_by(order_id=order_id).one().quantity == 3
        history = PurchaseListEdit.query.filter_by(purchase_id=detail['id']).one()
        assert history.before['total_quantity'] == 3 and history.after['total_quantity'] == 4
        assert history.user_id is not None
        assert not db.session.get(PurchaseList, detail['id']).receipt
    confirmed = client.post(f"/api/purchase-lists/{detail['id']}/confirm", headers=admin_headers)
    assert confirmed.status_code == 200, confirmed.get_json()
    for endpoint in ('pdf', 'excel'):
        export = client.get(f"/api/purchase-lists/{detail['id']}/{endpoint}", headers=admin_headers)
        assert export.status_code == 200
        assert export.data.startswith(b'%PDF' if endpoint == 'pdf' else b'PK')


def test_ordered_reduction_reopens_only_removed_incoming(app, client, admin_headers, shop_ids):
    order_id, _products = _seed(app, shop_ids['a'])
    detail = _create(client, admin_headers, shop_ids['a'], order_id)
    confirmed = client.post(f"/api/purchase-lists/{detail['id']}/confirm", headers=admin_headers).get_json()['data']
    edited = _edit(client, admin_headers, confirmed, 1)
    assert edited.status_code == 200, edited.get_json()
    with app.app_context():
        row = build_pool_plan(shop_ids['a'])['rows'][detail['items'][0]['group_key']]
        assert row['incoming_quantity'] == 1
        assert row['on_hand_quantity'] == 0
        assert row['purchase_needed_quantity'] == 2
    replenish = _create(client, admin_headers, shop_ids['a'], order_id)
    assert replenish['total_quantity'] == 2


def test_partial_receipt_history_cannot_be_reduced_or_received_twice(app, client, admin_headers, shop_ids):
    order_id, products = _seed(app, shop_ids['a'])
    detail = _create(client, admin_headers, shop_ids['a'], order_id)
    pid, key = detail['id'], detail['items'][0]['group_key']
    assert client.post(f'/api/purchase-lists/{pid}/confirm', headers=admin_headers).status_code == 200
    payload = {'finalize': False, 'batch_id': 'before-edit', 'items': [
        {'group_key': key, 'received_quantity': 1, 'damaged_quantity': 0, 'missing_quantity': 1}]}
    detail = client.post(f'/api/purchase-lists/{pid}/receive', headers=admin_headers, json=payload).get_json()['data']
    assert _edit(client, admin_headers, detail, 1).status_code == 400
    changed = _edit(client, admin_headers, detail, 2, [{'product_id': products[1], 'quantity': 2}])
    assert changed.status_code == 200, changed.get_json()
    data = changed.get_json()['data']
    assert data['receipt_batches'] == detail['receipt_batches']
    assert data['receipt'][0]['received_quantity'] == data['receipt'][0]['confirmed_missing_quantity'] == 1
    assert data['receipt'][0]['pending_quantity'] == 0
    with app.app_context():
        assert build_pool_plan(shop_ids['a'])['rows'][key]['on_hand_quantity'] == 1
        assert PurchaseListEdit.query.filter_by(purchase_id=pid).count() == 1
    assert client.post(f'/api/purchase-lists/{pid}/receive', headers=admin_headers,
        json={'finalize': False, 'batch_id': 'overcount', 'items': [
            {'group_key': key, 'received_quantity': 1, 'damaged_quantity': 0}]}).status_code == 400
    assert client.post(f'/api/purchase-lists/{pid}/receive', headers=admin_headers, json=payload).status_code == 200


@pytest.mark.parametrize('quantity', [-1, True, 1.5, None, 100001])
def test_edit_rejects_invalid_quantity_atomically(app, client, admin_headers, shop_ids, quantity):
    order_id, _products = _seed(app, shop_ids['a'])
    detail = _create(client, admin_headers, shop_ids['a'], order_id)
    assert _edit(client, admin_headers, detail, quantity).status_code == 400
    with app.app_context():
        assert db.session.get(PurchaseList, detail['id']).total_quantity == 3
        assert PurchaseListEdit.query.count() == 0


def test_edit_scope_permissions_and_finalized_protection(app, client, admin_headers, sub_a_headers, limited_headers, shop_ids):
    order_id, products = _seed(app, shop_ids['b'])
    detail = _create(client, admin_headers, shop_ids['b'], order_id)
    assert _edit(client, sub_a_headers, detail, 4).status_code == 403
    assert _edit(client, limited_headers, detail, 4).status_code == 403
    assert client.post('/api/purchase-lists/manual', headers=limited_headers, json={}).status_code == 403
    assert client.get('/api/purchase-lists/catalog/products', headers=limited_headers).status_code == 403
    assert client.get('/api/purchase-lists/catalog/variants', headers=sub_a_headers,
                       query_string={'shop_id': shop_ids['b'], 'shopify_product_id': 'edit-product'}).status_code == 403
    other_id, _ = _seed(app, shop_ids['a'])
    other = _create(client, admin_headers, shop_ids['a'], other_id)
    assert _edit(client, admin_headers, other, 3, [{'product_id': products[0], 'quantity': 1}]).status_code == 400
    assert client.post(f"/api/purchase-lists/{detail['id']}/confirm", headers=admin_headers).status_code == 200
    detail = client.post(f"/api/purchase-lists/{detail['id']}/receive", headers=admin_headers, json={'items': [
        {'group_key': detail['items'][0]['group_key'], 'received_quantity': 3, 'damaged_quantity': 0}]}).get_json()['data']
    assert _edit(client, admin_headers, detail, 4).status_code == 400


def test_zero_removes_only_uncounted_goods_and_noop_does_not_audit(app, client, admin_headers, shop_ids):
    order_id, products = _seed(app, shop_ids['a'])
    detail = _create(client, admin_headers, shop_ids['a'], order_id)
    assert _edit(client, admin_headers, detail, 3).status_code == 200
    with app.app_context():
        assert PurchaseListEdit.query.count() == 0
    assert _edit(client, admin_headers, detail, 0).status_code == 400
    changed = _edit(client, admin_headers, detail, 0, [{'product_id': products[1], 'quantity': 2}]).get_json()['data']
    assert changed['order_ids'] == [] and changed['order_count'] == 0
    assert changed['items'][0]['order_numbers'] == []
    assert client.post(f"/api/purchase-lists/{detail['id']}/confirm", headers=admin_headers).status_code == 200


def test_purchase_catalog_pages_all_variants_and_preserves_configurations(app, client, admin_headers, shop_ids):
    with app.app_context():
        db.session.add_all([Product(shop_id=shop_ids['a'], shopify_product_id='many',
            shopify_variant_id=f'many-{index}', title='Many Variants', price=1, status='draft',
            variant_title=f'Pink / iPhone {index} + wrist chain', sku=f'SKU-{index}') for index in range(205)])
        db.session.commit()
    response = client.get('/api/purchase-lists/catalog/products', headers=admin_headers,
        query_string={'shop_id': shop_ids['a'], 'search': 'Many'})
    assert response.status_code == 200
    assert response.get_json()['data']['products'][0]['variant_count'] == 205
    rows = []
    for page in (1, 2):
        result = client.get('/api/purchase-lists/catalog/variants', headers=admin_headers,
            query_string={'shop_id': shop_ids['a'], 'shopify_product_id': 'many', 'page': page}).get_json()['data']
        rows.extend(result['items'])
    assert len({row['id'] for row in rows}) == 205
    assert all(row['configuration'] == '+ wrist chain' and row['sku'] for row in rows)


def test_cf_reduction_preserves_reference_and_reopens_pending_purchase(app, client, admin_headers, shop_ids):
    order_id, products = _seed(app, shop_ids['a'])
    with app.app_context():
        order = db.session.get(Order, order_id)
        order.warehouse_status = 'shipped'
        source_id = OrderItem.query.filter_by(order_id=order_id).one().id
        db.session.commit()
    sale = client.post('/api/after-sales', headers=admin_headers, json={'order_id': order_id,
        'kind': 'missing', 'reason': '少发', 'items': [{'source_order_item_id': source_id, 'quantity': 2}]}).get_json()['data']
    detail = client.post('/api/purchase-lists', headers=admin_headers, json={'shop_id': shop_ids['a'],
        'order_ids': [], 'after_sale_ids': [sale['id']]}).get_json()['data']
    detail = client.post(f"/api/purchase-lists/{detail['id']}/confirm", headers=admin_headers).get_json()['data']
    response = _edit(client, admin_headers, detail, 1, [{'product_id': products[1], 'quantity': 1}])
    assert response.status_code == 200, response.get_json()
    data = response.get_json()['data']
    assert data['customer_references'] == [sale['customer_reference']]
    assert any(row['order_numbers'] == [sale['customer_reference']] for row in data['items'])
    candidates = client.get('/api/purchase-candidates', headers=admin_headers,
        query_string={'shop_id': shop_ids['a'], 'view': 'after_sale'}).get_json()['data']
    assert candidates['after_sales'][0]['needed_quantity'] == 1
    with app.app_context():
        assert db.session.get(Order, order_id).warehouse_status == 'shipped'


def test_deleting_edited_draft_removes_its_edit_snapshots(app, client, admin_headers, shop_ids):
    order_id, _products = _seed(app, shop_ids['a'])
    detail = _create(client, admin_headers, shop_ids['a'], order_id)
    assert _edit(client, admin_headers, detail, 2).status_code == 200
    assert client.delete(f"/api/purchase-lists/{detail['id']}", headers=admin_headers).status_code == 200
    with app.app_context():
        assert PurchaseListEdit.query.filter_by(purchase_id=detail['id']).count() == 0
