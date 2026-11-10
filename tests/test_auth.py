"""Authentication and token hardening tests."""
import datetime

import jwt

from tests.conftest import TEST_ADMIN_PASSWORD, TEST_ADMIN_USERNAME


def test_login_success_returns_token(client):
    response = client.post('/api/login/account', json={
        'username': TEST_ADMIN_USERNAME,
        'password': TEST_ADMIN_PASSWORD,
        'type': 'account',
    })
    assert response.status_code == 200
    body = response.get_json()
    assert body['status'] == 'ok'
    assert body['currentAuthority'] == 'admin'
    assert body['token']


def test_login_can_be_used_to_access_api(client):
    token = client.post('/api/login/account', json={
        'username': TEST_ADMIN_USERNAME,
        'password': TEST_ADMIN_PASSWORD,
    }).get_json()['token']

    response = client.get('/api/orders', headers={'Authorization': f'Bearer {token}'})
    assert response.status_code == 200


def test_login_wrong_password_returns_401(client):
    response = client.post('/api/login/account', json={
        'username': TEST_ADMIN_USERNAME,
        'password': 'not-the-password',
    })
    assert response.status_code == 401
    assert response.get_json()['currentAuthority'] == 'guest'


def test_login_unknown_user_returns_401(client):
    response = client.post('/api/login/account', json={
        'username': 'nobody',
        'password': 'whatever',
    })
    assert response.status_code == 401


def test_login_missing_fields_returns_400(client):
    response = client.post('/api/login/account', json={'username': 'admin'})
    assert response.status_code == 400


def test_login_locks_out_after_repeated_failures(client):
    for _ in range(5):
        response = client.post('/api/login/account', json={
            'username': TEST_ADMIN_USERNAME,
            'password': 'wrong',
        })
        assert response.status_code == 401

    blocked = client.post('/api/login/account', json={
        'username': TEST_ADMIN_USERNAME,
        'password': TEST_ADMIN_PASSWORD,
    })
    assert blocked.status_code == 429


def test_api_requires_token(client):
    assert client.get('/api/orders').status_code == 401


def test_legacy_backdoor_token_is_rejected(client):
    """The old `?token=123` bypass must stay closed."""
    assert client.get('/api/orders?token=123').status_code == 401


def test_token_in_query_string_is_rejected(client, admin_headers):
    """Tokens must travel in the Authorization header only."""
    token = admin_headers['Authorization'].split(' ', 1)[1]
    assert client.get(f'/api/orders?token={token}').status_code == 401


def test_jwt_signed_with_old_hardcoded_secret_is_rejected(client):
    forged = jwt.encode(
        {
            'username': 'attacker',
            'authority': 'admin',
            'exp': datetime.datetime.utcnow() + datetime.timedelta(hours=1),
        },
        'caseledger-secret-key',
        algorithm='HS256',
    )
    response = client.get('/api/orders', headers={'Authorization': f'Bearer {forged}'})
    assert response.status_code == 401


def test_invalid_token_returns_401(client):
    response = client.get('/api/orders', headers={'Authorization': 'Bearer not.a.jwt'})
    assert response.status_code == 401


def test_current_user_exposes_role_and_shops(client, admin_headers, sub_a_headers):
    admin_body = client.get('/api/currentUser', headers=admin_headers).get_json()['data']
    assert admin_body['access'] == 'admin'
    assert len(admin_body['shops']) == 2

    sub_body = client.get('/api/currentUser', headers=sub_a_headers).get_json()['data']
    assert sub_body['access'] == 'user'
    assert [shop['name'] for shop in sub_body['shops']] == ['Shop A']


def test_token_lifetime_follows_config(client, app):
    """令牌有效期必须等于 JWT_EXPIRES_HOURS 配置（改 .env 即生效）。"""
    token = client.post('/api/login/account', json={
        'username': TEST_ADMIN_USERNAME,
        'password': TEST_ADMIN_PASSWORD,
    }).get_json()['token']

    payload = jwt.decode(token, app.config['JWT_SECRET_KEY'], algorithms=['HS256'])
    configured = app.config['JWT_EXPIRES_HOURS']
    lifetime_seconds = payload['exp'] - payload['iat']  # PyJWT 解出来是 Unix 时间戳（秒）

    assert lifetime_seconds == configured * 3600


def test_expired_token_is_rejected(client, app):
    """过期令牌必须返回 401，让前端跳回登录页。"""
    expired = jwt.encode(
        {
            'username': TEST_ADMIN_USERNAME,
            'authority': 'admin',
            'exp': datetime.datetime.utcnow() - datetime.timedelta(minutes=1),
            'iat': datetime.datetime.utcnow() - datetime.timedelta(hours=13),
        },
        app.config['JWT_SECRET_KEY'],
        algorithm='HS256',
    )
    response = client.get('/api/orders', headers={'Authorization': f'Bearer {expired}'})
    assert response.status_code == 401
    assert response.get_json()['code'] == 'TOKEN_EXPIRED'
