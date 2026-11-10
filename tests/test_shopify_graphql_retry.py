"""Transient Shopify transport failures should not abort a scheduled sync."""

from unittest.mock import Mock

import pytest
import requests

from app.services import shopify_graphql


def test_graphql_retries_transient_ssl_error(monkeypatch):
    calls = [requests.exceptions.SSLError("EOF"), Mock(status_code=200)]
    sleeps = []

    def post(*args, **kwargs):
        result = calls.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(shopify_graphql.requests, "post", post)
    monkeypatch.setattr(shopify_graphql.time, "sleep", sleeps.append)

    response = shopify_graphql._post_graphql(Mock(), "token", "query", {}, 1)

    assert response.status_code == 200
    assert sleeps == [1]


def test_graphql_stops_after_bounded_retries(monkeypatch):
    attempts = []

    def fail(*args, **kwargs):
        attempts.append(1)
        raise requests.exceptions.SSLError("EOF")

    monkeypatch.setattr(shopify_graphql.requests, "post", fail)
    monkeypatch.setattr(shopify_graphql.time, "sleep", lambda _: None)

    with pytest.raises(shopify_graphql.ShopifyGraphQLError, match="连接 Shopify 失败"):
        shopify_graphql._post_graphql(Mock(), "token", "query", {}, 1)
    assert len(attempts) == shopify_graphql.TRANSIENT_RETRIES


def test_graphql_retries_throttle_response(monkeypatch):
    calls = [Mock(status_code=429), Mock(status_code=200)]
    monkeypatch.setattr(shopify_graphql.requests, "post", lambda *args, **kwargs: calls.pop(0))
    monkeypatch.setattr(shopify_graphql.time, "sleep", lambda _: None)

    response = shopify_graphql._post_graphql(Mock(), "token", "query", {}, 1)

    assert response.status_code == 200


def test_graphql_retries_http_200_cost_throttle(monkeypatch):
    throttled = Mock(status_code=200)
    throttled.json.return_value = {
        "errors": [{"message": "Throttled", "extensions": {"code": "THROTTLED"}}],
        "extensions": {"cost": {"requestedQueryCost": 200,
                                "throttleStatus": {"currentlyAvailable": 50, "restoreRate": 100}}},
    }
    success = Mock(status_code=200)
    success.json.return_value = {"data": {"nodes": []}}
    responses = [throttled, success]
    sleeps = []
    monkeypatch.setattr(shopify_graphql, "_post_graphql", lambda *_: responses.pop(0))
    monkeypatch.setattr(shopify_graphql, "_token_for", lambda *_: "token")
    monkeypatch.setattr(shopify_graphql.time, "sleep", sleeps.append)
    assert shopify_graphql._execute(Mock(), "query", {}) == {"nodes": []}
    assert sleeps == [1.5]


def test_batch_query_uses_immutable_ids_and_smaller_first_page(monkeypatch):
    calls = []

    def execute(shop, query, variables):
        calls.append((query, variables))
        return {"nodes": [{"id": "gid://shopify/Order/123"}]}

    monkeypatch.setattr(shopify_graphql, "_execute", execute)
    assert shopify_graphql.fetch_orders_sync_by_ids(Mock(), ["123", "456"])[0]["id"].endswith("123")
    assert calls[0][1] == {"ids": ["gid://shopify/Order/123", "gid://shopify/Order/456"]}
    assert "lineItems(first: 10)" in calls[0][0]
    assert "orders(first:" not in calls[0][0]
