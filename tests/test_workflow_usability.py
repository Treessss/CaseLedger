from app import db
from app.models import Product


def test_exchange_catalog_paginates_all_variants_and_uses_catalog_images(app, client, admin_headers, shop_ids):
    with app.app_context():
        db.session.add_all([
            Product(shop_id=shop_ids['a'], shopify_product_id='complete-catalog',
                    shopify_variant_id=f'complete-{index}', title='Complete Replacement Case',
                    variant_title=f'Blue / Model {index:03d}', price=29, status='active',
                    image_url='https://cdn.shopify.com/variant.jpg')
            for index in range(205)
        ])
        db.session.add(Product(shop_id=shop_ids['b'], shopify_product_id='other-complete',
                               shopify_variant_id='other-complete', title='Complete Replacement Case',
                               variant_title='Other shop', price=29, status='active'))
        db.session.commit()
    pages = [client.get('/api/after-sales/catalog', headers=admin_headers,
                        query_string={'shop_id': shop_ids['a'], 'search': 'Complete Replacement', 'page': page})
             .get_json()['data'] for page in (1, 2)]
    assert len(pages[0]['items']) == 200
    assert len(pages[1]['items']) == 5
    assert pages[0]['pagination']['has_more'] is True
    assert pages[1]['pagination']['has_more'] is False
    assert len({row['id'] for page in pages for row in page['items']}) == 205
    assert all(row['image_url'] for page in pages for row in page['items'])


def test_inventory_adjustment_reason_is_optional(app, client, admin_headers, shop_ids):
    with app.app_context():
        db.session.add(Product(shop_id=shop_ids['a'], shopify_product_id='optional-reason',
                               shopify_variant_id='optional-reason', title='Optional Reason Case',
                               variant_title='Blue / iPhone 18', price=29, status='active'))
        db.session.commit()
    catalog = client.get('/api/inventory', headers=admin_headers,
                         query_string={'shop_id': shop_ids['a'], 'include_zero': 'true'}).get_json()['data']
    key = next(row['group_key'] for row in catalog['items'] if row['title'] == 'Optional Reason Case')
    for reason in (None, '', '   '):
        payload = {'shop_id': shop_ids['a'], 'group_key': key, 'quantity_delta': 1}
        if reason is not None:
            payload['reason'] = reason
        response = client.post('/api/inventory/adjustments', headers=admin_headers, json=payload)
        assert response.status_code == 201
    assert client.post('/api/inventory/adjustments', headers=admin_headers,
                       json={**payload, 'reason': 'x' * 256}).status_code == 400
