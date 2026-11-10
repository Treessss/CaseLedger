"""4PX configuration must stay encrypted, isolated, and admin-only."""

from app import db
from app.models import Shop


def _shop_id(client, headers, name):
    shops = client.get("/api/shops", headers=headers).get_json()["data"]
    return next(shop["id"] for shop in shops if shop["name"] == name)


def test_per_shop_fourpx_secrets_are_encrypted_and_not_returned(client, admin_headers, app):
    first = _shop_id(client, admin_headers, "Shop A")
    second = _shop_id(client, admin_headers, "Shop B")
    for shop_id, suffix in ((first, "a"), (second, "b")):
        result = client.put(
            f"/api/shops/{shop_id}",
            json={
                "fourpx_app_name": f"test-{suffix}",
                "fourpx_app_key": f"key-{suffix}",
                "fourpx_app_secret": f"secret-{suffix}",
                "fourpx_resource_code": f"resource-{suffix}",
                "fourpx_environment": "test",
                "oauth_client_id": "test-shopify-client",
                "oauth_client_secret": "test-shopify-secret",
            },
            headers=admin_headers,
        )
        assert result.status_code == 200
        assert result.get_json()["data"]["has_fourpx_credentials"] is True
        assert "fourpx_app_key" not in result.get_json()["data"]
        assert "fourpx_app_secret" not in result.get_json()["data"]

    with app.app_context():
        shop_a = db.session.get(Shop, first)
        shop_b = db.session.get(Shop, second)
        assert shop_a.get_fourpx_credentials() == ("key-a", "secret-a")
        assert shop_b.get_fourpx_credentials() == ("key-b", "secret-b")
        assert "secret-a" not in shop_a.fourpx_app_secret_encrypted
        assert "key-a" not in shop_a.fourpx_app_key_encrypted

    detail = client.get(f"/api/shops/{first}", headers=admin_headers).get_json()["data"]
    assert "key-a" not in str(detail)
    assert "secret-a" not in str(detail)


def test_fourpx_credentials_require_both_fields_and_admin(client, admin_headers, sub_a_headers):
    shop_id = _shop_id(client, admin_headers, "Shop A")
    partial = client.put(
        f"/api/shops/{shop_id}",
        json={"fourpx_app_key": "key-only"},
        headers=admin_headers,
    )
    assert partial.status_code == 400
    denied = client.put(
        f"/api/shops/{shop_id}",
        json={"fourpx_app_key": "key", "fourpx_app_secret": "secret"},
        headers=sub_a_headers,
    )
    assert denied.status_code == 403


def test_blank_values_preserve_existing_credentials(client, admin_headers, app):
    shop_id = _shop_id(client, admin_headers, "Shop A")
    client.put(
        f"/api/shops/{shop_id}",
        json={"oauth_client_id": "test-shopify-client", "oauth_client_secret": "test-shopify-secret"},
        headers=admin_headers,
    )
    client.put(
        f"/api/shops/{shop_id}",
        json={"fourpx_app_key": "key", "fourpx_app_secret": "secret"},
        headers=admin_headers,
    )
    result = client.put(
        f"/api/shops/{shop_id}",
        json={"fourpx_app_name": "renamed", "fourpx_app_key": "", "fourpx_app_secret": ""},
        headers=admin_headers,
    )
    assert result.status_code == 200
    with app.app_context():
        assert db.session.get(Shop, shop_id).get_fourpx_credentials() == ("key", "secret")
