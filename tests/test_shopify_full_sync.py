"""Full-history sync must walk every cursor without a date or 250-order cap."""

from types import SimpleNamespace

import pytest

from app.models import Shop
from app.services import shopify_graphql, shopify_service
from app import tasks


def test_full_sync_processes_pages_past_250(app, monkeypatch):
    cursors = []
    checked = []

    def fetch_page(shop, since_utc, first, after):
        checked.append(shop.id)
        assert since_utc is None
        assert first == 25
        cursors.append(after)
        page = len(cursors)
        return ([{"id": page * 100 + index} for index in range(25)], {
            "hasNextPage": page < 11,
            "endCursor": f"page-{page}",
        })

    monkeypatch.setattr(shopify_service, "require_all_orders_access", lambda shop: None)
    monkeypatch.setattr(shopify_service, "fetch_orders_page", fetch_page)
    monkeypatch.setattr(
        shopify_service.ShopifyService,
        "_normalize_graphql_orders",
        lambda self, shop, nodes: [SimpleNamespace(id=node["id"]) for node in nodes],
    )
    monkeypatch.setattr(
        shopify_service.ShopifyService,
        "sync_orders",
        lambda self, _orders, **_kwargs: {
            "total_fetched": len(_orders), "new_orders": 0,
            "updated_orders": len(_orders), "errors": 0,
        },
    )

    with app.app_context():
        shop = Shop.query.filter_by(shop_url="shop-a.myshopify.com").one()
        service = shopify_service.ShopifyService()
        service.shop_id = shop.id
        stats = service.sync_all_orders()

    assert stats == {"total_fetched": 275, "new_orders": 0, "updated_orders": 275, "errors": 0}
    assert cursors == [None] + [f"page-{page}" for page in range(1, 11)]
    assert len(checked) == 11


def test_full_sync_rejects_cursor_that_does_not_advance(app, monkeypatch):
    monkeypatch.setattr(shopify_service, "require_all_orders_access", lambda shop: None)
    monkeypatch.setattr(
        shopify_service, "fetch_orders_page",
        lambda *args, **kwargs: ([], {"hasNextPage": True, "endCursor": "same"}),
    )

    with app.app_context():
        shop = Shop.query.filter_by(shop_url="shop-a.myshopify.com").one()
        service = shopify_service.ShopifyService()
        service.shop_id = shop.id
        with pytest.raises(shopify_graphql.ShopifyGraphQLError, match="空订单页"):
            service.sync_all_orders()


def test_full_sync_requires_read_all_orders(monkeypatch):
    monkeypatch.setattr(
        shopify_graphql, "_execute",
        lambda *args, **kwargs: {
            "currentAppInstallation": {"accessScopes": [{"handle": "read_orders"}]}
        },
    )

    with pytest.raises(shopify_graphql.ShopifyGraphQLError, match="read_all_orders"):
        shopify_graphql.require_all_orders_access(SimpleNamespace())


def test_scheduled_full_task_uses_unbounded_sync(app, monkeypatch):
    calls = []
    shop = SimpleNamespace(id=1, name="test")

    class Service:
        def sync_all_orders(self):
            calls.append("all")
            return {"total_fetched": 275, "new_orders": 0, "updated_orders": 275, "errors": 0}

    monkeypatch.setattr(tasks, "create_app", lambda: app)
    monkeypatch.setattr(tasks, "_active_shops", lambda: [shop])
    monkeypatch.setattr(tasks, "_shop_service", lambda *_: Service())
    monkeypatch.setattr(tasks, "_run_order_sync", lambda shop, app, runner: runner(Service()))
    monkeypatch.setattr(
        tasks, "_sync_fees_after_orders",
        lambda shops, *, days_back=7: calls.append(days_back) or [],
    )

    result = tasks.sync_shopify_orders_full_task.run()

    assert result["test"]["total_fetched"] == 275
    assert calls == ["all", None]
