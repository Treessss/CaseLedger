"""Pytest fixtures: isolated app, database and authenticated clients.

Environment variables are set BEFORE importing the app so tests never touch the
real database in instance/.
"""

import os
import sys
import tempfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

TEST_ADMIN_USERNAME = "admin"
TEST_ADMIN_PASSWORD = "test-admin-password"

# 与线上迁移口径一致：旧账号（迁移前勾了「订单管理」）补上拆出来的两条新权限
FULL_GRANTABLE_PERMISSIONS = [
    "dashboard",
    "orders",
    "fulfillment",
    "inventory",
    "after_sales",
    "accounts",
    "expenses",
    "reports",
]

# A valid Fernet key is required to construct the app config.
TEST_ENCRYPTION_KEY = "IhQ_9Ca0M0dVQ2d5Tc1oT1eBqYb5mS8X2n6uKk2nWQ0="

_TMP_DIR = tempfile.mkdtemp(prefix="caseledger-tests-")
TEST_DB_PATH = os.path.join(_TMP_DIR, "test.db")

os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB_PATH}"
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["JWT_SECRET_KEY"] = "test-jwt-secret-key"
os.environ["ENCRYPTION_KEY"] = TEST_ENCRYPTION_KEY
os.environ["ADMIN_USERNAME"] = TEST_ADMIN_USERNAME
os.environ["ADMIN_PASSWORD"] = TEST_ADMIN_PASSWORD
os.environ["EXPOSE_ERROR_DETAILS"] = "False"
os.environ["LOGIN_MAX_ATTEMPTS"] = "5"
os.environ["LOGIN_LOCKOUT_SECONDS"] = "900"

from app import create_app, db  # noqa: E402
from app.models import Expense, Order, Shop, User  # noqa: E402


@pytest.fixture()
def app():
    application = create_app()
    application.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)

    with application.app_context():
        db.drop_all()

        db.create_all()
        _seed(application)
        yield application
        db.session.remove()
        db.drop_all()


@pytest.fixture(autouse=True)
def _reset_login_throttle():
    """The login rate limiter keeps in-memory state; reset it between tests."""
    from app.services import auth_service

    auth_service.reset_throttle_state()
    yield
    auth_service.reset_throttle_state()


@pytest.fixture(autouse=True)
def _isolate_business_exchange_rates(request, monkeypatch):
    """Business workflow tests must not depend on a live FX provider.

    Provider/fallback tests keep the real service. This fixture only supplies
    the singleton used by unrelated order serialization; per-test FX mocks
    can still override it. Nothing is persisted or changed in production.
    """
    modules = {
        "test_gateway_fees", "test_inventory", "test_orders", "test_product_translations",
        "test_purchases", "test_shipping", "test_shipping_parcels", "test_shopify_graphql_sync",
        "test_after_sales", "test_after_sale_archive_and_cf", "test_packing_lookup",
        "test_inventory_pool", "test_outbound_scan_failures",
        "test_purchase_editor",
    }
    if request.module.__name__.split(".")[-1] not in modules:
        return
    import time
    from decimal import Decimal
    from app.services.exchange_rate_service import exchange_rate_service

    quotes = {"CNY": Decimal("1"), "USD": Decimal("6.72"), "HKD": Decimal("0.856"),
              "EUR": Decimal("7.4"), "GBP": Decimal("8.6"), "CAD": Decimal("4.9"),
              "AUD": Decimal("4.5"), "JPY": Decimal("0.045")}
    monkeypatch.setattr(exchange_rate_service, "cache", {
        f"{source}_{target}": {"rate": source_rate / target_rate,
                               "timestamp": time.time(), "is_fallback": False, "source": "test-fixture"}
        for source, source_rate in quotes.items() for target, target_rate in quotes.items()
    })


@pytest.fixture(autouse=True)
def _isolate_legacy_purchase_network(request, monkeypatch):
    """Existing workflow tests exercise local procurement, not live Shopify.

    Dedicated test_purchase_order_refresh tests keep the real refresh service and
    mock only the external GraphQL boundary, including incomplete/failed reads.
    """
    local_workflow_modules = {
        "test_fourpx_label", "test_after_sales", "test_product_translations",
        "test_shipping_parcels", "test_packing_lookup", "test_permissions",
        "test_outbound_undo", "test_shipping_batch_pdf", "test_purchase_pdf",
        "test_after_sale_archive_and_cf", "test_purchases", "test_inventory",
        "test_query_performance",
        "test_inventory_pool", "test_outbound_scan_failures",
        "test_purchase_editor",
    }
    if request.module.__name__.split(".")[-1] in local_workflow_modules:
        from app.services import purchase_order_refresh_service

        monkeypatch.setattr(purchase_order_refresh_service, "fetch_purchase_orders", lambda *_: [])


def _seed(application):
    """Two shops, one admin and two sub-accounts with different shop access."""
    shop_a = Shop(name="Shop A", shop_url="shop-a.myshopify.com", currency="USD")
    shop_b = Shop(name="Shop B", shop_url="shop-b.myshopify.com", currency="USD")
    db.session.add_all([shop_a, shop_b])
    db.session.flush()

    sub_a = User(username="sub_a", display_name="Sub A", role=User.ROLE_USER)
    sub_a.set_password("sub-a-password")
    sub_a.shops = [shop_a]
    # 与线上回填口径一致：老账号默认拥有全部可授予权限
    sub_a.set_permissions(FULL_GRANTABLE_PERMISSIONS)

    sub_b = User(username="sub_b", display_name="Sub B", role=User.ROLE_USER)
    sub_b.set_password("sub-b-password")
    sub_b.shops = [shop_a, shop_b]
    sub_b.set_permissions(FULL_GRANTABLE_PERMISSIONS)

    # 只被授予「订单 + 费用」的子账户，用于验证功能权限收窄
    sub_limited = User(username="sub_limited", display_name="受限子账户", role=User.ROLE_USER)
    sub_limited.set_password("sub-limited-password")
    sub_limited.shops = [shop_a]
    sub_limited.set_permissions(["orders", "expenses"])

    db.session.add_all([sub_a, sub_b, sub_limited])

    from datetime import datetime, timedelta

    now = datetime.utcnow()
    for index in range(3):
        db.session.add(
            Order(
                shop_id=shop_a.id,
                shopify_order_id=f"A-{index}",
                order_number=f"#A{index}",
                total_price=100 + index,
                subtotal_price=100 + index,
                currency="USD",
                financial_status="paid",
                fulfillment_status="fulfilled",
                order_date=now - timedelta(days=index),
                created_at=now - timedelta(days=index),
            )
        )
    db.session.add(
        Order(
            shop_id=shop_b.id,
            shopify_order_id="B-0",
            order_number="#B0",
            total_price=999,
            subtotal_price=999,
            currency="USD",
            financial_status="paid",
            fulfillment_status="fulfilled",
            order_date=now,
            created_at=now,
        )
    )
    db.session.add(
        Expense(
            shop_id=shop_a.id,
            category="facebook_ads",
            amount=50,
            currency="CNY",
            description="Shop A ads",
            expense_date=(now + timedelta(hours=8)).date(),
        )
    )
    db.session.commit()


@pytest.fixture()
def client(app):
    return app.test_client()


def _login(client, username, password):
    response = client.post(
        "/api/login/account",
        json={
            "username": username,
            "password": password,
            "type": "account",
        },
    )
    if response.status_code != 200:
        raise AssertionError(f"login failed for {username}: {response.get_data(as_text=True)}")
    return response.get_json()["token"]


def auth_header(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def admin_headers(client):
    return auth_header(_login(client, TEST_ADMIN_USERNAME, TEST_ADMIN_PASSWORD))


@pytest.fixture()
def sub_a_headers(client):
    return auth_header(_login(client, "sub_a", "sub-a-password"))


@pytest.fixture()
def sub_b_headers(client):
    return auth_header(_login(client, "sub_b", "sub-b-password"))


@pytest.fixture()
def limited_headers(client):
    """登录只有「订单 + 费用」权限的子账户。"""
    return auth_header(_login(client, "sub_limited", "sub-limited-password"))


@pytest.fixture()
def shop_ids(app):
    with app.app_context():
        shops = Shop.query.order_by(Shop.id).all()
        return {"a": shops[0].id, "b": shops[1].id}
