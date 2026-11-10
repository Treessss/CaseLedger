"""Platform account (credential vault) tests."""


def _create(client, headers, platform='google', username='ops@example.com',
            password='s3cret-pass', **extra):
    payload = {'platform': platform, 'username': username, 'password': password}
    payload.update(extra)
    return client.post('/api/platform-accounts', headers=headers, json=payload)


def test_create_stores_password_encrypted(client, app, admin_headers):
    from app.models import PlatformAccount

    response = _create(client, admin_headers)
    assert response.status_code == 201, response.get_data(as_text=True)

    body = response.get_json()['data']
    assert 'password' not in body

    with app.app_context():
        stored = PlatformAccount.query.filter_by(username='ops@example.com').one()
        # 落库必须是密文，且能用配置的密钥解回来
        assert stored.encrypted_password != 's3cret-pass'
        assert stored.get_password() == 's3cret-pass'


def test_duplicate_username_per_platform_rejected(client, admin_headers):
    assert _create(client, admin_headers).status_code == 201
    duplicate = _create(client, admin_headers)
    assert duplicate.status_code == 400
    assert '已存在相同用户名' in duplicate.get_json()['message']


def test_list_and_search(client, admin_headers):
    _create(client, admin_headers, platform='google', username='a@example.com', notes='广告账号')
    _create(client, admin_headers, platform='paypal', username='b@example.com')

    listing = client.get('/api/platform-accounts?per_page=10', headers=admin_headers).get_json()
    assert listing['pagination']['total'] == 2

    filtered = client.get('/api/platform-accounts?platform=paypal',
                          headers=admin_headers).get_json()
    assert [item['username'] for item in filtered['data']] == ['b@example.com']

    searched = client.get('/api/platform-accounts/search?q=广告',
                          headers=admin_headers).get_json()
    assert [item['username'] for item in searched['data']] == ['a@example.com']


def test_detail_and_password_reveal(client, admin_headers):
    account_id = _create(client, admin_headers).get_json()['data']['id']

    plain = client.get(f'/api/platform-accounts/{account_id}', headers=admin_headers).get_json()
    assert 'password' not in plain['data']

    with_password = client.get(
        f'/api/platform-accounts/{account_id}?include_password=true', headers=admin_headers
    ).get_json()
    assert with_password['data']['password'] == 's3cret-pass'

    revealed = client.get(f'/api/platform-accounts/{account_id}/password',
                          headers=admin_headers).get_json()
    assert revealed['data']['password'] == 's3cret-pass'


def test_update_password_re_encrypts(client, app, admin_headers):
    from app.models import PlatformAccount

    account_id = _create(client, admin_headers).get_json()['data']['id']
    cipher_before = PlatformAccount.query.get(account_id).encrypted_password

    response = client.put(f'/api/platform-accounts/{account_id}', headers=admin_headers,
                          json={'password': 'new-secret-pass', 'notes': 'updated'})
    assert response.status_code == 200

    with app.app_context():
        stored = PlatformAccount.query.get(account_id)
        assert stored.encrypted_password != cipher_before
        assert stored.get_password() == 'new-secret-pass'
        assert stored.notes == 'updated'


def test_update_username_conflict_is_rejected(client, admin_headers):
    _create(client, admin_headers, username='first@example.com')
    second_id = _create(client, admin_headers, username='second@example.com').get_json()['data']['id']

    response = client.put(f'/api/platform-accounts/{second_id}', headers=admin_headers,
                          json={'username': 'first@example.com'})
    assert response.status_code == 400


def test_delete_platform_account(client, admin_headers):
    account_id = _create(client, admin_headers).get_json()['data']['id']
    assert client.delete(f'/api/platform-accounts/{account_id}',
                         headers=admin_headers).status_code == 200
    assert client.get(f'/api/platform-accounts/{account_id}',
                      headers=admin_headers).status_code == 404


def test_platform_list(client, admin_headers):
    _create(client, admin_headers, platform='google')
    body = client.get('/api/platform-accounts/platforms', headers=admin_headers).get_json()
    assert 'google' in body['data']


def test_credentials_are_admin_only(client, sub_a_headers):
    assert client.get('/api/platform-accounts', headers=sub_a_headers).status_code == 403
    assert client.get('/api/platform-accounts/1/password',
                      headers=sub_a_headers).status_code == 403
    assert client.post('/api/platform-accounts', headers=sub_a_headers,
                       json={'platform': 'x', 'username': 'y', 'password': 'z'}).status_code == 403


def test_create_requires_fields(client, admin_headers):
    response = client.post('/api/platform-accounts', headers=admin_headers,
                           json={'platform': 'google'})
    assert response.status_code == 400
    assert '缺少必填字段' in response.get_json()['message']
