"""User management tests (sub-account CRUD, shop assignment, passwords)."""


def _create_user(client, headers, username='worker', password='worker-password', **extra):
    payload = {'username': username, 'password': password}
    payload.update(extra)
    return client.post('/api/users', headers=headers, json=payload)


def _login(client, username, password):
    return client.post('/api/login/account', json={'username': username, 'password': password})


def test_create_sub_account(client, admin_headers):
    response = _create_user(client, admin_headers, display_name='小王')
    assert response.status_code == 201
    body = response.get_json()['data']
    assert body['username'] == 'worker'
    assert body['role'] == 'user'
    assert body['is_active'] is True


def test_create_validates_password_length(client, admin_headers):
    response = _create_user(client, admin_headers, password='short')
    assert response.status_code == 400
    assert '至少' in response.get_json()['message']


def test_create_rejects_duplicate_username(client, admin_headers):
    _create_user(client, admin_headers)
    duplicate = _create_user(client, admin_headers)
    assert duplicate.status_code == 400
    assert '已存在' in duplicate.get_json()['message']


def test_create_rejects_unknown_shop(client, admin_headers):
    response = _create_user(client, admin_headers, shop_ids=[999])
    assert response.status_code == 400
    assert '店铺不存在' in response.get_json()['message']


def test_create_with_multiple_shops(client, admin_headers, shop_ids):
    response = _create_user(client, admin_headers, shop_ids=[shop_ids['a'], shop_ids['b']])
    assert response.status_code == 201
    assert sorted(response.get_json()['data']['shop_ids']) == sorted(
        [shop_ids['a'], shop_ids['b']]
    )


def test_list_users_supports_filters(client, admin_headers):
    _create_user(client, admin_headers, username='alice')
    _create_user(client, admin_headers, username='bob')

    all_users = client.get('/api/users', headers=admin_headers).get_json()['data']
    assert {user['username'] for user in all_users} >= {'admin', 'alice', 'bob'}

    filtered = client.get('/api/users?keyword=ali', headers=admin_headers).get_json()['data']
    assert [user['username'] for user in filtered] == ['alice']


def test_update_user_fields_and_disable(client, admin_headers):
    user_id = _create_user(client, admin_headers).get_json()['data']['id']

    updated = client.put(f'/api/users/{user_id}', headers=admin_headers, json={
        'display_name': '新名字', 'email': 'worker@example.com', 'is_active': False,
    })
    assert updated.status_code == 200
    body = updated.get_json()['data']
    assert body['display_name'] == '新名字'
    assert body['email'] == 'worker@example.com'
    assert body['is_active'] is False

    # 停用后不能登录
    assert _login(client, 'worker', 'worker-password').status_code == 401


def test_admin_account_cannot_be_disabled_or_deleted(client, admin_headers):
    users = client.get('/api/users', headers=admin_headers).get_json()['data']
    admin_id = next(user['id'] for user in users if user['role'] == 'admin')

    disabled = client.put(f'/api/users/{admin_id}', headers=admin_headers, json={'is_active': False})
    assert disabled.status_code == 400

    deleted = client.delete(f'/api/users/{admin_id}', headers=admin_headers)
    assert deleted.status_code == 400


def test_delete_sub_account(client, admin_headers):
    user_id = _create_user(client, admin_headers).get_json()['data']['id']
    assert _login(client, 'worker', 'worker-password').status_code == 200

    assert client.delete(f'/api/users/{user_id}', headers=admin_headers).status_code == 200
    assert _login(client, 'worker', 'worker-password').status_code == 401
    assert client.get(f'/api/users/{user_id}', headers=admin_headers).status_code == 404


def test_assign_shops_can_be_cleared(client, admin_headers, shop_ids):
    user_id = _create_user(client, admin_headers,
                           shop_ids=[shop_ids['a']],
                           permissions=['orders']).get_json()['data']['id']

    cleared = client.put(f'/api/users/{user_id}/shops', headers=admin_headers,
                         json={'shop_ids': []})
    assert cleared.status_code == 200
    assert cleared.get_json()['data']['shop_ids'] == []

    token = _login(client, 'worker', 'worker-password').get_json()['token']
    headers = {'Authorization': f'Bearer {token}'}
    assert client.get('/api/shops', headers=headers).get_json()['data'] == []
    assert client.get('/api/orders', headers=headers).get_json()['data']['pagination']['total'] == 0


def test_new_sub_account_has_no_permissions_by_default(client, admin_headers, shop_ids):
    """新建子账户默认无任何功能权限：菜单看不到、接口也调不通。"""
    created = _create_user(client, admin_headers, shop_ids=[shop_ids['a']]).get_json()['data']
    assert created['permissions'] == []

    token = _login(client, 'worker', 'worker-password').get_json()['token']
    headers = {'Authorization': f'Bearer {token}'}
    assert client.get('/api/orders', headers=headers).status_code == 403
    assert client.get('/api/expenses', headers=headers).status_code == 403
    # 但基础能力仍可用，否则连界面都进不去
    assert client.get('/api/currentUser', headers=headers).status_code == 200
    assert client.get('/api/shops', headers=headers).status_code == 200


def test_change_own_password(client, admin_headers):
    _create_user(client, admin_headers)
    token = _login(client, 'worker', 'worker-password').get_json()['token']
    headers = {'Authorization': f'Bearer {token}'}

    wrong = client.post('/api/users/me/password', headers=headers,
                        json={'old_password': 'nope', 'new_password': 'brand-new-pass'})
    assert wrong.status_code == 400

    short = client.post('/api/users/me/password', headers=headers,
                        json={'old_password': 'worker-password', 'new_password': '123'})
    assert short.status_code == 400

    ok = client.post('/api/users/me/password', headers=headers,
                     json={'old_password': 'worker-password', 'new_password': 'brand-new-pass'})
    assert ok.status_code == 200
    assert _login(client, 'worker', 'worker-password').status_code == 401
    assert _login(client, 'worker', 'brand-new-pass').status_code == 200


def test_reset_password_by_admin(client, admin_headers):
    user_id = _create_user(client, admin_headers).get_json()['data']['id']

    response = client.put(f'/api/users/{user_id}', headers=admin_headers,
                          json={'password': 'admin-reset-pass'})
    assert response.status_code == 200
    assert _login(client, 'worker', 'admin-reset-pass').status_code == 200


def test_current_user_shape(client, admin_headers, sub_a_headers):
    admin_body = client.get('/api/currentUser', headers=admin_headers).get_json()['data']
    assert admin_body['access'] == 'admin'
    assert admin_body['shop_ids'] is None          # 管理员不受店铺限制
    assert len(admin_body['shops']) >= 1

    sub_body = client.get('/api/currentUser', headers=sub_a_headers).get_json()['data']
    assert sub_body['access'] == 'user'
    assert sub_body['shop_ids'] == [1]


def test_user_management_is_admin_only(client, sub_a_headers):
    assert client.get('/api/users', headers=sub_a_headers).status_code == 403
    assert client.post('/api/users', headers=sub_a_headers,
                       json={'username': 'x', 'password': 'xxxxxxxx'}).status_code == 403
