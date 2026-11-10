"""每个店铺独立的 Shopify PII 凭据（client_id / client_secret）。

核心约束：
    - client_secret 必须加密落库，任何接口都不能回显明文；
    - 每个店铺用各自的凭据，互不串；
    - 留空或传回显掩码时，不覆盖已保存的密钥。
"""

import pytest

from app import db
from app.models import Shop
from app.services.shopify_auth import (
    ShopifyAuthError,
    api_root,
    credentials_for,
    get_access_token,
)
from app.services.shopify_graphql import endpoint_for


def _shop_id(client, headers, name):
    body = client.get('/api/shops', headers=headers).get_json()
    return next(item['id'] for item in body['data'] if item['name'] == name)


def test_secret_is_encrypted_at_rest(client, admin_headers, app):
    shop_id = _shop_id(client, admin_headers, 'Shop A')
    response = client.put(
        f'/api/shops/{shop_id}',
        json={'oauth_client_id': 'client-abc', 'oauth_client_secret': 'shpss_supersecret'},
        headers=admin_headers,
    )
    assert response.status_code == 200

    with app.app_context():
        shop = db.session.get(Shop, shop_id)
        # 落库的是密文，不是明文
        assert shop.oauth_client_secret != 'shpss_supersecret'
        assert 'shpss_supersecret' not in shop.oauth_client_secret
        # 但能解密回原文
        assert shop.get_oauth_client_secret() == 'shpss_supersecret'
        assert shop.has_oauth_credentials() is True


def test_api_never_returns_plaintext_secret(client, admin_headers):
    shop_id = _shop_id(client, admin_headers, 'Shop A')
    client.put(
        f'/api/shops/{shop_id}',
        json={'oauth_client_id': 'client-abc', 'oauth_client_secret': 'shpss_supersecret'},
        headers=admin_headers,
    )

    body = client.get('/api/shops', headers=admin_headers).get_json()
    shop = next(item for item in body['data'] if item['id'] == shop_id)

    assert shop['oauth_client_id'] == 'client-abc'
    assert shop['has_oauth_credentials'] is True
    # 回显必须是打码值，且带 * 以便前端识别「未修改」
    assert 'shpss_supersecret' not in shop['oauth_client_secret']
    assert '*' in shop['oauth_client_secret']

    detail = client.get(f'/api/shops/{shop_id}', headers=admin_headers).get_json()
    assert 'shpss_supersecret' not in detail['data']['oauth_client_secret']


def test_masked_secret_on_update_is_ignored(client, admin_headers, app):
    shop_id = _shop_id(client, admin_headers, 'Shop A')
    client.put(
        f'/api/shops/{shop_id}',
        json={'oauth_client_id': 'client-abc', 'oauth_client_secret': 'shpss_supersecret'},
        headers=admin_headers,
    )

    # 前端编辑表单会把打码值带回来，这句不能把真密钥覆盖掉
    client.put(
        f'/api/shops/{shop_id}',
        json={'oauth_client_secret': 'shps************'},
        headers=admin_headers,
    )

    with app.app_context():
        assert db.session.get(Shop, shop_id).get_oauth_client_secret() == 'shpss_supersecret'


def test_each_shop_keeps_its_own_credentials(client, admin_headers, app):
    shop_a = _shop_id(client, admin_headers, 'Shop A')
    shop_b = _shop_id(client, admin_headers, 'Shop B')

    client.put(
        f'/api/shops/{shop_a}',
        json={'oauth_client_id': 'client-a', 'oauth_client_secret': 'secret-a'},
        headers=admin_headers,
    )
    client.put(
        f'/api/shops/{shop_b}',
        json={'oauth_client_id': 'client-b', 'oauth_client_secret': 'secret-b'},
        headers=admin_headers,
    )

    with app.app_context():
        assert db.session.get(Shop, shop_a).oauth_client_id == 'client-a'
        assert db.session.get(Shop, shop_a).get_oauth_client_secret() == 'secret-a'
        assert db.session.get(Shop, shop_b).oauth_client_id == 'client-b'
        assert db.session.get(Shop, shop_b).get_oauth_client_secret() == 'secret-b'


def test_sub_account_cannot_edit_credentials(client, sub_a_headers):
    """凭据属于管理员操作，子账户连店铺都改不了。"""
    body = client.get('/api/shops', headers=sub_a_headers).get_json()
    shop_id = body['data'][0]['id']

    response = client.put(
        f'/api/shops/{shop_id}',
        json={'oauth_client_id': 'client-x', 'oauth_client_secret': 'secret-x'},
        headers=sub_a_headers,
    )
    assert response.status_code == 403


# --------------------------------------------------------------------------
# 授权模块本身：令牌取用与接口地址拼接
# --------------------------------------------------------------------------


def test_endpoint_urls_are_built_from_the_shop_domain(app):
    """回归：endpoint_for / api_root 依赖的模块级工具必须可解析，避免 NameError。"""
    with app.app_context():
        shop = Shop.query.order_by(Shop.id).first()
        assert api_root(shop) == f'https://{shop.shop_url}/admin/api/2026-07'
        assert endpoint_for(shop) == f'https://{shop.shop_url}/admin/api/2026-07/graphql.json'


def test_credentials_for_uses_the_shop_own_app(app):
    with app.app_context():
        shop = Shop.query.order_by(Shop.id).first()
        assert credentials_for(shop) is None

        shop.set_oauth_client_secret('shpss_secret')
        shop.oauth_client_id = 'client-abc'
        assert credentials_for(shop) == ('client-abc', 'shpss_secret')


def test_get_access_token_without_credentials_fails_loudly(app):
    """没绑定凭据时要给出能直接照做的提示，而不是抛底层异常。"""
    with app.app_context():
        shop = Shop.query.order_by(Shop.id).first()
        with pytest.raises(ShopifyAuthError) as excinfo:
            get_access_token(shop)
        assert 'Client ID' in str(excinfo.value)
