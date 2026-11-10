from json import dumps

from app import db
from app.models import InventoryAdjustment, Product


def _products(shop_id):
    rows = [Product(shop_id=shop_id, shopify_product_id='batch-p', shopify_variant_id=f'batch-v{i}',
                    title='Batch Case', variant_title=variant, price=10, status='active')
            for i, variant in enumerate(('Blue / iPhone 16', 'Blue / iPhone 17', 'Red / iPhone 16'))]
    db.session.add_all(rows)
    db.session.commit()
    return [dumps(['variant', f'batch-v{i}'], ensure_ascii=False) for i in range(3)]


def test_picker_loads_whole_product_without_stock_calculation(app, client, admin_headers, shop_ids, monkeypatch):
    from app.services import inventory_service

    with app.app_context():
        _products(shop_ids['a'])
    monkeypatch.setattr(inventory_service, '_stock_rows', lambda *a, **k: (_ for _ in ()).throw(AssertionError('stock scan')))
    response = client.get(f"/api/inventory/receiving/products?shop_id={shop_ids['a']}&search=Blue",
                          headers=admin_headers)
    assert response.status_code == 200
    products = response.get_json()['data']['products']
    assert products[0]['variant_count'] == 3
    assert len(products[0]['colors']) == 2


def test_batch_atomic_receipt_and_invalid_line_rejection(app, client, admin_headers, shop_ids):
    with app.app_context():
        keys = _products(shop_ids['a'])
    payload = {'shop_id': shop_ids['a'], 'items': [{'group_key': keys[0], 'quantity': 2},
                                                {'group_key': keys[1], 'quantity': 3}]}
    failed = client.post('/api/inventory/receiving', headers=admin_headers,
                         json={**payload, 'items': [payload['items'][0], {'group_key': 'missing', 'quantity': 3}]})
    assert failed.status_code == 404
    with app.app_context():
        assert InventoryAdjustment.query.count() == 0
    response = client.post('/api/inventory/receiving', headers=admin_headers, json=payload)
    assert response.status_code == 201
    assert response.get_json()['data']['quantity'] == 5
    with app.app_context():
        assert InventoryAdjustment.query.count() == 2
        assert sum(row.quantity_delta for row in InventoryAdjustment.query.all()) == 5
    duplicated = client.post('/api/inventory/receiving', headers=admin_headers,
                             json={**payload, 'items': [payload['items'][0], payload['items'][0]]})
    assert duplicated.status_code == 400


def test_receiving_shop_isolation(app, client, admin_headers, sub_a_headers, shop_ids):
    with app.app_context():
        keys = _products(shop_ids['b'])
    response = client.post('/api/inventory/receiving', headers=sub_a_headers,
                           json={'shop_id': shop_ids['b'], 'items': [{'group_key': keys[0], 'quantity': 1}]})
    assert response.status_code == 403
    wrong_shop = client.post('/api/inventory/receiving', headers=admin_headers,
                             json={'shop_id': shop_ids['a'], 'items': [{'group_key': keys[0], 'quantity': 1}]})
    assert wrong_shop.status_code == 404
