from app import db
from app.models import Order, Product


def _catalog(app, shop_ids):
    with app.app_context():
        for index in range(243):
            db.session.add(Product(shop_id=shop_ids['a'], shopify_product_id='complete-product',
                                   shopify_variant_id=f'complete-{index}', title='Gradient Magnetic Case',
                                   variant_title=f'iPhone {index} / Mint Lavender' + (' + Wrist Chain' if index % 2 else ''),
                                   sku=f'COMPLETE-{index}', price=29,
                                   status='inactive' if index > 199 else 'active'))
        db.session.add(Product(shop_id=shop_ids['b'], shopify_product_id='foreign-product',
                               shopify_variant_id='foreign-variant', title='Gradient Magnetic Case',
                               variant_title='iPhone 18 / Mint Lavender', price=29, status='active'))
        db.session.commit()


def test_product_search_returns_full_variant_count_then_all_pages(app, client, admin_headers, shop_ids):
    _catalog(app, shop_ids)
    url = f"/api/after-sales/catalog/products?shop_id={shop_ids['a']}&search=COMPLETE-242"
    result = client.get(url, headers=admin_headers).get_json()['data']
    assert result['pagination']['total'] == 1
    assert result['products'][0]['variant_count'] == 243  # SKU chooses a product, not just that variant
    base = f"/api/after-sales/catalog?shop_id={shop_ids['a']}&shopify_product_id=complete-product&per_page=200"
    first = client.get(base + '&page=1', headers=admin_headers).get_json()['data']
    last = client.get(base + '&page=2', headers=admin_headers).get_json()['data']
    assert len(first['items']) == 200 and len(last['items']) == 43
    assert first['pagination']['has_more'] is True and last['pagination']['has_more'] is False
    items = first['items'] + last['items']
    assert len({row['id'] for row in items}) == 243
    assert sum(row['status'] == 'inactive' for row in items) == 43
    assert any(row['configuration'] for row in items)


def test_catalog_is_shop_scoped_and_inactive_variants_can_be_used(app, client, admin_headers, sub_a_headers, shop_ids):
    _catalog(app, shop_ids)
    assert client.get(f"/api/after-sales/catalog/products?shop_id={shop_ids['b']}", headers=sub_a_headers).status_code == 403
    assert client.get('/api/after-sales/catalog/products?shop_id=1').status_code == 401
    with app.app_context():
        order = Order.query.filter_by(shop_id=shop_ids['a']).first()
        order.warehouse_status = 'shipped'
        product = Product.query.filter_by(shop_id=shop_ids['a'], shopify_variant_id='complete-242').one()
        order_id, product_id = order.id, product.id
        db.session.commit()
    response = client.post('/api/after-sales', headers=admin_headers,
                           json={'order_id': order_id, 'kind': 'exchange', 'reason': '历史商品换品补发',
                                 'items': [{'catalog_product_id': product_id, 'quantity': 1}]})
    assert response.status_code == 201
    assert response.get_json()['data']['items'][0]['shopify_variant_id'] == 'complete-242'
