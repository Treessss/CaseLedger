from decimal import Decimal

from app import db
from app.models import Order, OrderItem, Product, ProductNaming, ProductTranslation, Shop
from app.services.product_naming_service import record_catalog_names, reconcile_names
from app.services.product_translation_service import display_payload
from app.services.shopify_service import ShopifyService
from app.services.shopify_webhook_service import process_webhook
from app.utils.shopify_resources import WebhookResource

OLD = 'Magnetic Silicone iPhone Case with Stand & Camera Control'
NEW = 'Silicone iPhone Case with Stand & Camera Control'


def seed(app, shop_ids):
    with app.app_context():
        order = Order.query.filter_by(shop_id=shop_ids['a']).first()
        db.session.add(Product(shop_id=order.shop_id, shopify_product_id='123', shopify_variant_id='456',
            title=OLD, variant_title='Khaki / iPhone 17 Pro Max', price=1, image_url='old.jpg'))
        db.session.add(OrderItem(shop_id=order.shop_id, order_id=order.id, shopify_line_item_id='rename-old',
            shopify_product_id='123', shopify_variant_id='456', title=OLD,
            variant_title='Khaki / iPhone 17 Pro Max', quantity=1))
        db.session.add_all([ProductTranslation(shop_id=order.shop_id, original_title=OLD, chinese_name='纯色支架'),
            ProductTranslation(shop_id=order.shop_id, original_title=OLD, original_color='Khaki', chinese_name='卡其色+支架')])
        db.session.commit()


def test_webhook_rename_keeps_mapping_and_history_and_clear_survives_more_renames(app, client, admin_headers, shop_ids):
    seed(app, shop_ids)
    with app.app_context():
        process_webhook(db.session.get(Shop, shop_ids['a']), 'products/update', {'id':123, 'title':NEW,
            'variants':[{'id':456, 'title':'Khaki / iPhone 17 Pro Max', 'price':'1'}]})
        db.session.commit()
        assert OrderItem.query.filter_by(shopify_line_item_id='rename-old').one().title == OLD
        assert ProductTranslation.query.count() == 2
    response = client.get('/api/products', headers=admin_headers, query_string={'shop_id':shop_ids['a']}).json['data']
    assert response['pagination']['total'] == 1
    row = response['items'][0]
    assert row['original_title'] == NEW and row['chinese_name'] == '纯色支架'
    assert row['colors'][0]['chinese_name'] == '卡其色+支架'
    assert row['shopify_product_id'] == '123'
    for title in (OLD, NEW, 'An entirely new snapshot title'):
        with app.app_context():
            shown = display_payload({'shop_id':shop_ids['a'], 'shopify_product_id':'123', 'title':title,
                'variant_title':'Khaki / iPhone 17 Pro Max', 'group_key':'unchanged-key'})
            assert shown['title']=='纯色支架' and shown['variant_title']=='卡其色+支架 / iPhone 17 Pro Max'
            assert shown['group_key']=='unchanged-key'
    cleared = client.put('/api/products/translations', headers=admin_headers, json={
        'shop_id':shop_ids['a'], 'shopify_product_id':'123', 'original_title':OLD, 'chinese_name':'',
        'colors':[{'original_color':'Khaki', 'chinese_name':''}]})
    assert cleared.status_code == 200, cleared.json
    with app.app_context():
        record_catalog_names(shop_ids['a'], {'123':'Renamed again'})
        db.session.commit()
        naming = ProductNaming.query.one()
        assert naming.chinese_name == '' and naming.color_names == {}
        assert {OLD, NEW, 'Renamed again'} <= set(naming.aliases)
        assert display_payload({'title':OLD, 'variant_title':'Khaki / iPhone 17 Pro Max'}, shop_ids['a'])['title']==OLD


def test_old_order_sync_cannot_undo_current_catalog_title(app, shop_ids):
    seed(app, shop_ids)
    with app.app_context():
        product = Product.query.one()
        product.title = NEW
        db.session.commit()
        service = ShopifyService()
        service.shop_id = shop_ids['a']
        service.app = app
        service._sync_product(WebhookResource({'product_id':123,'variant_id':456,'title':OLD,
            'variant_title':product.variant_title,'sku':'test','price':str(Decimal('1')),'vendor':'vendor'}))
        db.session.commit()
        assert Product.query.one().title == NEW


def test_same_title_different_product_and_shop_remain_distinct(app, client, admin_headers, shop_ids):
    seed(app, shop_ids)
    with app.app_context():
        reconcile_names(shop_ids['a'], {'123':NEW})
        db.session.add(Product(shop_id=shop_ids['a'], shopify_product_id='789', shopify_variant_id='790',
            title=NEW, variant_title='Khaki / iPhone 17 Pro Max', price=1))
        db.session.flush()
        record_catalog_names(shop_ids['a'], {'789':NEW})
        db.session.add(Product(shop_id=shop_ids['b'], shopify_product_id='other', shopify_variant_id='other-v',
            title=NEW, variant_title='Khaki / iPhone 17 Pro Max', price=1))
        record_catalog_names(shop_ids['b'], {'other':NEW})
        db.session.commit()
    response=client.get('/api/products', headers=admin_headers, query_string={'shop_id':shop_ids['a']}).json['data']
    assert response['pagination']['total']==2
    mapped={row['shopify_product_id']:row['chinese_name'] for row in response['items']}
    assert mapped=={'123':'纯色支架','789':''}
    other=client.get('/api/products',headers=admin_headers,query_string={'shop_id':shop_ids['b']}).json['data']
    assert other['items'][0]['chinese_name']==''


def test_names_survive_repeated_catalog_sync_even_without_old_order_snapshots(app, shop_ids):
    seed(app, shop_ids)
    with app.app_context():
        record_catalog_names(shop_ids['a'], {'123':NEW})
        Product.query.one().title=NEW
        OrderItem.query.filter_by(shopify_line_item_id='rename-old').delete()
        db.session.commit()
        for title in ('A second name', 'A third name', 'A third name'):
            record_catalog_names(shop_ids['a'], {'123':title})
            db.session.commit()
            naming=ProductNaming.query.one()
            assert naming.chinese_name=='纯色支架'
            assert naming.color_names=={'Khaki':'卡其色+支架'}
            assert OLD in naming.aliases and NEW in naming.aliases
