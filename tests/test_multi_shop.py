"""Multi-shop isolation and account-permission tests."""


def _order_total(client, headers, params=''):
    body = client.get(f'/api/orders?per_page=50{params}', headers=headers).get_json()
    return body['data']['pagination']['total']


def test_admin_sees_every_shop(client, admin_headers):
    body = client.get('/api/shops', headers=admin_headers).get_json()
    assert [shop['name'] for shop in body['data']] == ['Shop A', 'Shop B']


def test_sub_account_sees_only_assigned_shops(client, sub_a_headers, sub_b_headers):
    body = client.get('/api/shops', headers=sub_a_headers).get_json()
    assert [shop['name'] for shop in body['data']] == ['Shop A']

    body = client.get('/api/shops', headers=sub_b_headers).get_json()
    assert [shop['name'] for shop in body['data']] == ['Shop A', 'Shop B']


def test_orders_are_scoped_by_assigned_shops(client, admin_headers, sub_a_headers, shop_ids):
    assert _order_total(client, admin_headers) == 4
    assert _order_total(client, sub_a_headers) == 3

    scoped = client.get(
        f'/api/orders?per_page=50&shop_id={shop_ids["b"]}', headers=sub_a_headers
    ).get_json()
    assert scoped['data']['pagination']['total'] == 0


def test_sub_account_cannot_read_other_shop_order(client, admin_headers, sub_a_headers, shop_ids):
    """Order #B0 belongs to Shop B and must not be reachable by sub_a."""
    admin_list = client.get(
        f'/api/orders?per_page=50&shop_id={shop_ids["b"]}', headers=admin_headers
    ).get_json()
    other_order_id = admin_list['data']['orders'][0]['id']

    assert client.get(f'/api/orders/{other_order_id}', headers=sub_a_headers).status_code == 403
    assert client.put(
        f'/api/orders/{other_order_id}', headers=sub_a_headers, json={'notes': 'hack'}
    ).status_code == 403


def test_sub_account_cannot_manage_shops_or_accounts(client, sub_a_headers, shop_ids):
    assert client.get('/api/users', headers=sub_a_headers).status_code == 403
    assert client.get('/api/platform-accounts', headers=sub_a_headers).status_code == 403
    assert client.post(
        '/api/shops',
        headers=sub_a_headers,
        json={'name': 'Rogue', 'shop_url': 'rogue'},
    ).status_code == 403


def test_shop_assignment_change_applies_immediately(client, admin_headers, sub_a_headers, shop_ids):
    """An already-issued token must respect the new assignment without re-login."""
    assert _order_total(client, sub_a_headers) == 3

    sub_id = client.get('/api/users', headers=admin_headers).get_json()['data']
    sub_id = next(user['id'] for user in sub_id if user['username'] == 'sub_a')

    updated = client.put(
        f'/api/users/{sub_id}/shops', headers=admin_headers,
        json={'shop_ids': [shop_ids['b']]},
    )
    assert updated.status_code == 200

    assert _order_total(client, sub_a_headers) == 1


def test_disabling_account_invalidates_existing_token(client, admin_headers, sub_a_headers):
    users = client.get('/api/users', headers=admin_headers).get_json()['data']
    sub_id = next(user['id'] for user in users if user['username'] == 'sub_a')

    assert client.put(
        f'/api/users/{sub_id}', headers=admin_headers, json={'is_active': False}
    ).status_code == 200

    assert client.get('/api/orders', headers=sub_a_headers).status_code == 401


def test_admin_can_create_shop_and_assign_multiple_shops(client, admin_headers):
    created = client.post('/api/shops', headers=admin_headers, json={
        'name': 'Shop C',
        'shop_url': 'shop-c',
        'currency': 'EUR',
        # 新增店铺必须带上应用凭据（域名 + Client ID + Client Secret）
        'oauth_client_id': 'client-c',
        'oauth_client_secret': 'shpss_secret-c',
    })
    assert created.status_code == 201
    shop_c = created.get_json()['data']['id']
    assert created.get_json()['data']['shop_url'] == 'shop-c.myshopify.com'

    user = client.post('/api/users', headers=admin_headers, json={
        'username': 'multi_shop_user',
        'password': 'multi-shop-password',
        'shop_ids': [shop_c],
    })
    assert user.status_code == 201
    assert user.get_json()['data']['shop_ids'] == [shop_c]

    token = client.post('/api/login/account', json={
        'username': 'multi_shop_user',
        'password': 'multi-shop-password',
    }).get_json()['token']
    shops = client.get('/api/shops', headers={'Authorization': f'Bearer {token}'}).get_json()
    assert [shop['name'] for shop in shops['data']] == ['Shop C']


def test_shop_with_business_data_cannot_be_deleted(client, admin_headers, shop_ids):
    response = client.delete(f'/api/shops/{shop_ids["a"]}', headers=admin_headers)
    assert response.status_code == 400
    assert response.get_json()['has_data'] is True
