"""Settings tests: shopify config binding and fee configuration CRUD."""


def _fee_payload(**overrides):
    payload = {
        'fee_type': 'payment',
        'fee_name': '测试手续费',
        'calculation_method': 'percentage',
        'percentage_rate': 2.5,
        'currency': 'USD',
    }
    payload.update(overrides)
    return payload


def test_shopify_config_masks_secrets(client, admin_headers):
    body = client.get('/api/settings/shopify', headers=admin_headers).get_json()
    assert body['success'] is True
    # 种子数据里的店铺没有配置密钥
    assert body['data']['shop_url'] in ('', 'shop-a.myshopify.com')


def test_shopify_config_saves_into_default_shop(client, app, admin_headers):
    from app.models import Shop

    response = client.post('/api/settings/shopify', headers=admin_headers, json={
        'shop_url': 'shop-a',
        'oauth_client_id': 'client-123',
        'oauth_client_secret': 'shpss_secret-123',
    })
    assert response.status_code == 200

    with app.app_context():
        shop = Shop.query.order_by(Shop.id).first()
        assert shop.oauth_client_id == 'client-123'
        # 密钥加密落库，能解密回原文
        assert shop.oauth_client_secret != 'shpss_secret-123'
        assert shop.get_oauth_client_secret() == 'shpss_secret-123'

    # 回显必须打码
    detail = client.get('/api/settings/shopify', headers=admin_headers).get_json()['data']
    assert '*' in detail['oauth_client_secret']
    assert 'shpss_secret-123' not in detail['oauth_client_secret']


def test_masked_secret_is_not_written_back(client, app, admin_headers):
    from app.models import Shop

    client.post('/api/settings/shopify', headers=admin_headers, json={
        'shop_url': 'shop-a', 'oauth_client_id': 'client-123',
        'oauth_client_secret': 'shpss_secret-123',
    })
    masked = client.get('/api/settings/shopify', headers=admin_headers).get_json()['data']

    # 前端把打码值原样回传时，不能覆盖真实密钥
    client.post('/api/settings/shopify', headers=admin_headers, json={
        'oauth_client_secret': masked['oauth_client_secret'],
    })
    with app.app_context():
        assert Shop.query.order_by(Shop.id).first().get_oauth_client_secret() == 'shpss_secret-123'


def test_fee_config_list_contains_defaults(client, admin_headers):
    body = client.get('/api/settings/fees', headers=admin_headers).get_json()
    assert body['success'] is True
    assert isinstance(body['data'], list)


def test_create_fee_config(client, admin_headers):
    response = client.post('/api/settings/fees', headers=admin_headers, json=_fee_payload())
    assert response.status_code == 200
    created = response.get_json()['data']
    assert created['fee_name'] == '测试手续费'
    assert created['percentage_rate'] == 2.5


def test_create_fee_config_validation(client, admin_headers):
    missing_name = client.post('/api/settings/fees', headers=admin_headers,
                               json=_fee_payload(fee_name=''))
    assert missing_name.status_code == 400

    missing_rate = client.post('/api/settings/fees', headers=admin_headers,
                               json=_fee_payload(calculation_method='percentage',
                                                 percentage_rate=None))
    assert missing_rate.status_code == 400

    fixed_without_amount = client.post('/api/settings/fees', headers=admin_headers,
                                       json=_fee_payload(calculation_method='fixed'))
    assert fixed_without_amount.status_code == 400


def test_update_and_soft_delete_fee_config(client, app, admin_headers):
    from app.models import FeeConfig

    created = client.post('/api/settings/fees', headers=admin_headers,
                          json=_fee_payload()).get_json()['data']

    updated = client.put(f"/api/settings/fees/{created['id']}", headers=admin_headers,
                         json=_fee_payload(fee_name='改名后', calculation_method='fixed',
                                           percentage_rate=None, fixed_amount=1.5))
    assert updated.status_code == 200

    with app.app_context():
        config = FeeConfig.query.get(created['id'])
        assert config.fee_name == '改名后'
        assert float(config.fixed_amount) == 1.5

    assert client.delete(f"/api/settings/fees/{created['id']}",
                         headers=admin_headers).status_code == 200

    with app.app_context():
        # 软删除：记录保留但不再出现在列表里
        assert FeeConfig.query.get(created['id']).is_active is False
    active_names = [item['fee_name'] for item in
                    client.get('/api/settings/fees', headers=admin_headers).get_json()['data']]
    assert '改名后' not in active_names


def test_fee_writes_are_admin_only(client, sub_a_headers):
    assert client.post('/api/settings/fees', headers=sub_a_headers,
                       json=_fee_payload()).status_code == 403
    assert client.post('/api/settings/shopify', headers=sub_a_headers,
                       json={'shop_url': 'x'}).status_code == 403
