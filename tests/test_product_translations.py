from sqlalchemy import event

from app import db
from app.models import Order, OrderItem, Product, ProductTranslation, PurchaseList
from app.services.product_translation_service import display_payload


TITLE = "Dopamine Ring Stand iPhone Case"
VARIANT = "Rose Red / iPhone 18 Pro"


def seed(app, shop_ids):
    with app.app_context():
        orders = [Order.query.filter_by(shop_id=shop_ids[key]).first() for key in ("a", "b")]
        for index, order in enumerate(orders):
            db.session.add(OrderItem(shop_id=order.shop_id, order_id=order.id,
                shopify_line_item_id=f"mapping-{index}", shopify_product_id=f"p-{index}",
                shopify_variant_id=f"v-{index}", title=TITLE, variant_title=VARIANT, quantity=2))
        db.session.add(Product(shop_id=shop_ids['a'], shopify_product_id="p-0", shopify_variant_id="v-0",
                               title=TITLE, variant_title=VARIANT, price=1))
        db.session.commit()
        return orders[0].id, orders[1].id


def save(client, headers, shop_id, title="多巴胺支架手机壳", color="玫红"):
    return client.put('/api/products/translations', headers=headers, json={
        "shop_id": shop_id, "original_title": TITLE, "chinese_name": title,
        "colors": [{"original_color": "Rose Red", "chinese_name": color}],
    })


def test_names_apply_to_details_and_packing_without_overwriting_raw_data(app, client, admin_headers, shop_ids):
    first, other = seed(app, shop_ids)
    assert save(client, admin_headers, shop_ids['a']).status_code == 200
    detail = client.get(f'/api/orders/{first}', headers=admin_headers).get_json()['data']['line_items'][0]
    assert detail['title'] == '多巴胺支架手机壳'
    assert detail['variant_title'] == '玫红 / iPhone 18 Pro'
    assert detail['original_title'] == TITLE
    assert detail['original_variant_title'] == VARIANT
    other_detail = client.get(f'/api/orders/{other}', headers=admin_headers).get_json()['data']['line_items'][0]
    assert other_detail['title'] == TITLE
    with app.app_context():
        order = db.session.get(Order, first)
        number = order.order_number
        assert order.items[0].title == TITLE and order.items[0].variant_title == VARIANT
    packing = client.get('/api/shipping/packing-lookup', headers=admin_headers,
                         query_string={'shop_id': shop_ids['a'], 'code': number}).get_json()['data']
    assert packing['items'][0]['title'] == '多巴胺支架手机壳'
    assert packing['items'][0]['variant_title'] == '玫红 / iPhone 18 Pro'
    assert save(client, admin_headers, shop_ids['a'], '', '').status_code == 200
    cleared = client.get(f'/api/orders/{first}', headers=admin_headers).get_json()['data']['line_items'][0]
    assert cleared['title'] == TITLE and cleared['variant_title'] == VARIANT


def test_catalog_deduplicates_and_searches_chinese(app, client, admin_headers, shop_ids):
    seed(app, shop_ids)
    assert save(client, admin_headers, shop_ids['a']).status_code == 200
    result = client.get('/api/products', headers=admin_headers,
                        query_string={'shop_id': shop_ids['a'], 'search': '玫红'}).get_json()['data']
    assert result['pagination']['total'] == 1
    assert result['items'][0]['variant_count'] == 1
    assert result['items'][0]['original_title'] == TITLE
    assert result['items'][0]['colors'][0]['chinese_name'] == '玫红'
    catalog = client.get('/api/after-sales/catalog', headers=admin_headers,
                         query_string={'shop_id': shop_ids['a'], 'search': '玫红'}).get_json()['data']
    assert catalog['items'][0]['title'] == '多巴胺支架手机壳'
    stock = client.get('/api/inventory', headers=admin_headers, query_string={
        'shop_id': shop_ids['a'], 'search': '玫红', 'include_zero': 'true', 'group_by_product': 'true',
    }).get_json()['data']
    assert stock['products'][0]['title'] == '多巴胺支架手机壳'
    assert stock['products'][0]['colors'][0]['name'] == '玫红'


def test_catalog_image_uses_cached_product_without_historical_orders(app, client, admin_headers, shop_ids):
    with app.app_context():
        product = Product(shop_id=shop_ids['a'], shopify_product_id='cache-p', shopify_variant_id='cache-v',
                          title='Abstract Dreamy Wave Phone Case', variant_title='Blue / iPhone 18',
                          price=10, image_url='https://cdn.shopify.com/product.jpg')
        db.session.add(product)
        db.session.commit()
    response = client.get('/api/products', headers=admin_headers, query_string={'shop_id': shop_ids['a']}).get_json()['data']
    row = next(row for row in response['items'] if row['original_title'] == 'Abstract Dreamy Wave Phone Case')
    assert row['image_url'] == 'https://cdn.shopify.com/product.jpg'
    assert row['colors'][0]['image_url'] == row['image_url']


def test_image_backfill_is_batched_scoped_and_preserves_cached_images(app, shop_ids, monkeypatch):
    from flask import g
    from app.services import product_image_service

    with app.app_context():
        g.current_user = {'authority': 'admin'}
        for index in range(55):
            db.session.add(Product(shop_id=shop_ids['a'], shopify_product_id=f'p{index}',
                                   shopify_variant_id=f'v{index}', title=f'Case {index}', price=10))
        db.session.add(Product(shop_id=shop_ids['b'], shopify_product_id='other', shopify_variant_id='other-v', title='Other', price=10))
        db.session.commit()
        batches = []
        def execute(shop, query, variables):
            batches.append(variables['ids'])
            return {'nodes': [{'id': pid, 'featuredMedia': {'preview': {'image': {'url': f'https://cdn.shopify.com/{pid.rsplit("/", 1)[-1]}.jpg'}}}} for pid in variables['ids']]}
        monkeypatch.setattr(product_image_service, '_execute', execute)
        result = product_image_service.refresh_missing_images(shop_ids['a'])
        assert result['variants_updated'] == 55 and [len(batch) for batch in batches] == [50, 5]
        assert Product.query.filter_by(shop_id=shop_ids['b']).first().image_url is None
        assert product_image_service.refresh_missing_images(shop_ids['a'])['products_checked'] == 0


def test_purchase_snapshot_remains_raw_and_new_items_use_existing_mapping(app, client, admin_headers, shop_ids):
    first, _ = seed(app, shop_ids)
    assert save(client, admin_headers, shop_ids['a']).status_code == 200
    with app.app_context():
        purchase = PurchaseList(shop_id=shop_ids['a'], name='mapping-test', order_ids=[first],
                                items=[{'title': TITLE, 'variant_title': VARIANT, 'group_key': 'fixed-key',
                                        'quantity': 2, 'shopify_product_id': 'p-0'}], allocations=[])
        db.session.add(purchase)
        db.session.commit()
        purchase_id = purchase.id
    result = client.get(f'/api/purchase-lists/{purchase_id}', headers=admin_headers).get_json()['data']
    assert result['products'][0]['title'] == '多巴胺支架手机壳'
    assert result['products'][0]['colors'][0]['name'] == '玫红'
    assert result['items'][0]['group_key'] == 'fixed-key'
    with app.app_context():
        assert db.session.get(PurchaseList, purchase_id).items[0]['title'] == TITLE
        db.session.add(OrderItem(shop_id=shop_ids['a'], order_id=first, shopify_line_item_id='future-item',
                                 title=TITLE, variant_title='Rose Red / iPhone 19', quantity=1))
        db.session.commit()
    future = client.get(f'/api/orders/{first}', headers=admin_headers).get_json()['data']['line_items'][-1]
    assert future['variant_title'] == '玫红 / iPhone 19'


def test_current_variant_image_wins_over_old_order_main_image(app, client, admin_headers, shop_ids):
    first, _ = seed(app, shop_ids)
    with app.app_context():
        Product.query.filter_by(shop_id=shop_ids['a']).first().image_url = 'https://cdn.shopify.com/rose.jpg'
        OrderItem.query.filter_by(order_id=first).first().image_url = 'https://cdn.shopify.com/main.jpg'
        db.session.commit()
    data = client.get('/api/products', headers=admin_headers,
                      query_string={'shop_id': shop_ids['a']}).get_json()['data']
    row = next(row for row in data['items'] if row['original_title'] == TITLE)
    assert row['colors'][0]['image_url'] == 'https://cdn.shopify.com/rose.jpg'


def test_variant_image_refresh_replaces_fallback_and_isolates_shop(app, shop_ids, monkeypatch):
    from flask import g
    from app.services import product_image_service
    seed(app, shop_ids)
    with app.app_context():
        g.current_user = {'authority': 'admin'}
        Product.query.filter_by(shop_id=shop_ids['a']).first().image_url = 'https://cdn.shopify.com/main.jpg'
        db.session.commit()
        monkeypatch.setattr(product_image_service, '_execute', lambda *args: {'nodes': [{
            'id': 'gid://shopify/ProductVariant/v-0', 'image': {'url': 'https://cdn.shopify.com/rose.jpg'},
            'product': {'featuredMedia': {'preview': {'image': {'url': 'https://cdn.shopify.com/main.jpg'}}}},
        }]})
        assert product_image_service.refresh_variant_images(shop_ids['a'], ['p-0'])['variants_updated'] == 1
        assert Product.query.filter_by(shop_id=shop_ids['a']).first().image_url.endswith('/rose.jpg')


def test_module_permission_and_shop_isolation(app, client, admin_headers, sub_a_headers, shop_ids):
    seed(app, shop_ids)
    assert client.get('/api/products', headers=sub_a_headers, query_string={'shop_id': shop_ids['a']}).status_code == 403
    assert save(client, sub_a_headers, shop_ids['a']).status_code == 403
    assert save(client, admin_headers, shop_ids['b']).status_code == 200
    from app.models import User
    with app.app_context():
        user = User.query.filter_by(username='sub_a').one()
        user.set_permissions([*user.permission_keys(), 'products'])
        db.session.commit()
    assert client.get('/api/products', headers=sub_a_headers, query_string={'shop_id': shop_ids['b']}).status_code == 403


def test_invalid_mapping_is_atomic(app, client, admin_headers, shop_ids):
    seed(app, shop_ids)
    result = client.put('/api/products/translations', headers=admin_headers, json={
        'shop_id': shop_ids['a'], 'original_title': TITLE, 'chinese_name': '壳',
        'colors': [{'original_color': 'Wrong Color', 'chinese_name': '错误'}],
    })
    assert result.status_code == 400
    assert save(client, admin_headers, shop_ids['a'], '字' * 256).status_code == 400
    with app.app_context():
        assert ProductTranslation.query.count() == 0


def test_bundle_color_mapping_preserves_configuration_and_image(app, shop_ids):
    with app.app_context():
        db.session.add(ProductTranslation(
            shop_id=shop_ids['a'], original_title=TITLE, original_color='Pink Lime', chinese_name='粉绿',
        ))
        db.session.commit()
        source = {'title': TITLE, 'colors': [{
            'name': 'Pink Lime + Wrist Chain', 'base_color': 'Pink Lime',
            'configuration': '+ Wrist Chain', 'image_url': 'bundle.webp',
            'variants': [{'variant_title': 'iPhone 18 Pro Max / Pink Lime + Wrist Chain',
                          'group_key': 'bundle', 'image_url': 'bundle.webp'}],
        }]}
        displayed = display_payload(source, shop_id=shop_ids['a'])
        color = displayed['colors'][0]
        assert color['name'] == '粉绿 + Wrist Chain'
        assert color['image_url'] == 'bundle.webp'
        assert color['variants'][0]['variant_title'] == 'iPhone 18 Pro Max / 粉绿 + Wrist Chain'
        assert color['variants'][0]['group_key'] == 'bundle'


def test_display_projection_batches_lookup_and_never_translates_notes_or_keys(app, shop_ids):
    with app.app_context():
        db.session.add_all([
            ProductTranslation(shop_id=shop_ids['a'], original_title=TITLE, chinese_name='壳'),
            ProductTranslation(shop_id=shop_ids['a'], original_title=TITLE, original_color='Rose Red', chinese_name='红'),
        ])
        db.session.commit()
        statements = []
        def record(_connection, _cursor, statement, _parameters, _context, _executemany):
            if 'product_translations' in statement:
                statements.append(statement)
        event.listen(db.engine, 'before_cursor_execute', record)
        try:
            data = {'shop_id': shop_ids['a'], 'note': TITLE, 'items': [
                {'title': TITLE, 'variant_title': VARIANT, 'group_key': 'original-key'} for _ in range(1000)]}
            displayed = display_payload(data)
            assert len(statements) == 1
            assert displayed['note'] == TITLE
            assert displayed['items'][0]['group_key'] == 'original-key'
            assert displayed['items'][0]['title'] == '壳'
            assert data['items'][0]['title'] == TITLE
        finally:
            event.remove(db.engine, 'before_cursor_execute', record)
