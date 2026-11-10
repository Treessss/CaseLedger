"""功能权限：子账户只能访问被授予的模块（后端强制，不只是隐藏菜单）。"""
from app.utils.permissions import ADMIN_ONLY_KEYS, GRANTABLE_KEYS


def test_current_user_exposes_permissions(client, admin_headers, limited_headers):
    admin = client.get('/api/currentUser', headers=admin_headers).get_json()['data']
    assert set(admin['permissions']) == set(GRANTABLE_KEYS) | set(ADMIN_ONLY_KEYS)

    limited = client.get('/api/currentUser', headers=limited_headers).get_json()['data']
    assert sorted(limited['permissions']) == ['expenses', 'orders']


def test_every_delete_endpoint_has_module_permission(app):
    import re
    from app import _requires_admin
    from app.utils.permissions import required_permission
    for rule in app.url_map.iter_rules():
        # /api/ai/* 是 API Key 鉴权的外部接口，不走网页模块权限
        if rule.rule.startswith('/api/ai/'):
            continue
        if 'DELETE' in rule.methods and rule.rule.startswith('/api/'):
            path = re.sub(r'<[^>]+>', '1', rule.rule)
            assert required_permission(path) or _requires_admin('DELETE', path), rule.rule


def test_sensitive_deletions_still_require_admin(client, limited_headers):
    for path in ('/api/accounts/1', '/api/order-costs/1', '/api/shops/1',
                 '/api/platform-accounts/1', '/api/users/1', '/api/settings/fees/1'):
        assert client.delete(path, headers=limited_headers).status_code == 403


def test_order_permission_alone_cannot_remove_parcels(client, limited_headers):
    response = client.delete('/api/orders/1/parcels/1', headers=limited_headers)
    assert response.status_code == 403
    assert response.get_json()['permission'] == 'fulfillment'


def test_granted_modules_are_accessible(client, limited_headers):
    assert client.get('/api/orders?per_page=1', headers=limited_headers).status_code == 200
    assert client.get('/api/expenses?per_page=1', headers=limited_headers).status_code == 200
    # 店铺列表属于基础能力（用于店铺切换），始终可用
    assert client.get('/api/shops', headers=limited_headers).status_code == 200
    assert client.get('/api/currentUser', headers=limited_headers).status_code == 200


def test_ungranted_modules_return_403(client, limited_headers):
    blocked = [
        '/api/accounts',
        '/api/accounts/summary',
        '/api/reports/financial-summary',
        '/api/reports/revenue-trend',
        '/api/platform-accounts',
        '/api/settings/fees',
        '/api/users',
        '/api/permissions',
    ]
    for path in blocked:
        response = client.get(path, headers=limited_headers)
        assert response.status_code == 403, f'{path} 应为 403，实际 {response.status_code}'
        assert response.get_json()['code'] in ('FORBIDDEN', 'FORBIDDEN_FEATURE')


def test_order_costs_follow_the_orders_permission(client, limited_headers):
    """订单费用属于「订单」模块，有订单权限即可访问。"""
    assert client.get('/api/order-costs', headers=limited_headers).status_code == 200


def test_orders_permission_no_longer_grants_fulfillment(client, limited_headers):
    """回归：履约作业（采购/发货/装箱）和售后已从「订单管理」拆出去。

    拆分前，勾了「订单管理」的账号能直接调发货、采购、售后接口；现在必须单独授予。
    """
    # 订单本身仍然可读
    assert client.get('/api/orders?per_page=1', headers=limited_headers).status_code == 200

    blocked = [
        '/api/shipping/ready',
        '/api/purchase-lists',
        '/api/purchase-candidates',
        '/api/after-sales',
    ]
    for path in blocked:
        response = client.get(path, headers=limited_headers)
        assert response.status_code == 403, f'{path} 应为 403，实际 {response.status_code}'
        assert response.get_json()['code'] in ('FORBIDDEN', 'FORBIDDEN_FEATURE')


def test_fulfillment_permission_unlocks_shipping_only(
    client, admin_headers, limited_headers
):
    """授予「履约作业」后可以调发货/采购，但拿不到订单查询和售后。"""
    users = client.get('/api/users', headers=admin_headers).get_json()['data']
    user_id = next(u['id'] for u in users if u['username'] == 'sub_limited')

    client.put(
        f'/api/users/{user_id}/permissions',
        headers=admin_headers,
        json={'permissions': ['fulfillment']},
    )
    # 发货接口放行
    assert client.get('/api/shipping/ready', headers=limited_headers).status_code in (200, 400)
    # 订单、售后仍然被挡
    assert client.get('/api/orders?per_page=1', headers=limited_headers).status_code == 403
    assert client.get('/api/after-sales', headers=limited_headers).status_code == 403


def test_fulfillment_and_after_sales_are_grantable():
    """两条新权限必须能被授予子账户（不能是 admin_only）。"""
    assert 'fulfillment' in GRANTABLE_KEYS
    assert 'after_sales' in GRANTABLE_KEYS
    assert 'fulfillment' not in ADMIN_ONLY_KEYS
    assert 'after_sales' not in ADMIN_ONLY_KEYS


def test_write_endpoints_respect_permissions(client, limited_headers):
    """写操作同样受功能权限约束，不能靠直接调接口绕过。"""
    assert client.post('/api/accounts', headers=limited_headers,
                       json={'platform': 'facebook', 'account_name': 'x'}).status_code == 403
    assert client.put('/api/order-costs/1', headers=limited_headers,
                      json={'shipping_cost': 1}).status_code == 403


def test_admin_can_grant_and_revoke_permissions(client, admin_headers, limited_headers):
    users = client.get('/api/users', headers=admin_headers).get_json()['data']
    user_id = next(u['id'] for u in users if u['username'] == 'sub_limited')

    # 授予「财务报表」后立刻可访问，无需重新登录
    granted = client.put(f'/api/users/{user_id}/permissions', headers=admin_headers,
                         json={'permissions': ['orders', 'expenses', 'reports']})
    assert granted.status_code == 200
    assert sorted(granted.get_json()['data']['permissions']) == ['expenses', 'orders', 'reports']
    assert client.get('/api/reports/financial-summary',
                      headers=limited_headers).status_code == 200

    # 收回后立刻 403
    revoked = client.put(f'/api/users/{user_id}/permissions', headers=admin_headers,
                         json={'permissions': ['orders']})
    assert revoked.status_code == 200
    assert client.get('/api/expenses?per_page=1', headers=limited_headers).status_code == 403
    assert client.get('/api/orders?per_page=1', headers=limited_headers).status_code == 200


def test_admin_only_permissions_cannot_be_granted(client, admin_headers):
    users = client.get('/api/users', headers=admin_headers).get_json()['data']
    user_id = next(u['id'] for u in users if u['username'] == 'sub_a')

    for key in ('platform_accounts', 'shops', 'sub_accounts', 'settings'):
        response = client.put(f'/api/users/{user_id}/permissions', headers=admin_headers,
                              json={'permissions': [key]})
        assert response.status_code == 400, f'{key} 不应可授予'
        assert '不可授予' in response.get_json()['message']


def test_permissions_can_be_set_on_create(client, admin_headers):
    created = client.post('/api/users', headers=admin_headers, json={
        'username': 'zhangsan',
        'password': 'zhangsan-password',
        'shop_ids': [1],
        'permissions': ['orders', 'expenses'],
    })
    assert created.status_code == 201
    assert sorted(created.get_json()['data']['permissions']) == ['expenses', 'orders']

    token = client.post('/api/login/account', json={
        'username': 'zhangsan', 'password': 'zhangsan-password',
    }).get_json()['token']
    headers = {'Authorization': f'Bearer {token}'}
    assert client.get('/api/orders?per_page=1', headers=headers).status_code == 200
    assert client.get('/api/accounts', headers=headers).status_code == 403


def test_permission_catalog_is_admin_only(client, admin_headers, limited_headers):
    catalog = client.get('/api/permissions', headers=admin_headers)
    assert catalog.status_code == 200
    keys = [item['key'] for item in catalog.get_json()['data']['permissions']]
    assert 'orders' in keys and 'settings' in keys

    assert client.get('/api/permissions', headers=limited_headers).status_code == 403


def test_shop_scoping_still_applies_with_permission(client, limited_headers, shop_ids):
    """功能权限与店铺权限是叠加关系，不是替代关系。"""
    body = client.get('/api/orders?per_page=50', headers=limited_headers).get_json()
    assert body['data']['pagination']['total'] == 3          # 只有 Shop A
    other = client.get(f'/api/orders?per_page=50&shop_id={shop_ids["b"]}',
                       headers=limited_headers).get_json()
    assert other['data']['pagination']['total'] == 0


def test_permission_catalog_describes_scope_and_dependencies(client, admin_headers):
    rows = client.get('/api/permissions', headers=admin_headers).get_json()['data']['permissions']
    by_key = {row['key']: row for row in rows}
    assert by_key['fulfillment']['group'] == '仓储作业'
    assert '4PX 预申报' in by_key['fulfillment']['description']
    assert by_key['dashboard']['requires'] == ['orders', 'reports']
    assert by_key['tracking_force_outbound']['requires'] == ['fulfillment']
    assert by_key['settings']['admin_only'] is True
