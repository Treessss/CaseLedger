"""Shopify 订单备注、商品图片与物流信息归一化。"""

from app.services import shopify_graphql
from app.utils.shopify_resources import extract_fulfillments


def test_extract_rest_fulfillments():
    rows = extract_fulfillments(
        {
            "fulfillments": [
                {
                    "id": 123,
                    "status": "success",
                    "shipment_status": "in_transit",
                    "tracking_company": "4PX",
                    "tracking_number": "YT123",
                    "tracking_url": "https://track.example/YT123",
                }
            ]
        }
    )
    assert rows[0]["tracking_company"] == "4PX"
    assert rows[0]["tracking_numbers"] == ["YT123"]
    assert rows[0]["tracking_urls"] == ["https://track.example/YT123"]


def test_graphql_details_prefers_variant_image_then_product(monkeypatch):
    payload = {
        "order": {
            "displayFulfillmentStatus": "FULFILLED",
            "note": "请周末发货",
            "email": "buyer@example.com",
            "shippingAddress": {"country": "Canada"},
            "customer": {"displayName": "Buyer"},
            "lineItems": {
                "nodes": [
                    {
                        "id": "gid://shopify/LineItem/11",
                        "variant": {
                            "media": {"nodes": [{"preview": {"image": {"url": "https://img/variant.jpg"}}}]},
                            "product": {
                                "featuredMedia": {"preview": {"image": {"url": "https://img/product.jpg"}}}
                            },
                        },
                    },
                    {
                        "id": "gid://shopify/LineItem/12",
                        "variant": {
                            "media": {"nodes": []},
                            "product": {
                                "featuredMedia": {"preview": {"image": {"url": "https://img/fallback.jpg"}}}
                            },
                        },
                    },
                ]
            },
            "fulfillments": [
                {
                    "id": "gid://shopify/Fulfillment/99",
                    "status": "SUCCESS",
                    "trackingInfo": [{"company": "4PX", "number": "YT123", "url": "https://track/YT123"}],
                }
            ],
        }
    }
    monkeypatch.setattr(shopify_graphql, "_execute", lambda *args, **kwargs: payload)

    details = shopify_graphql.fetch_order_details(object(), "123")
    assert details["note"] == "请周末发货"
    assert details["fulfillment_status"] == "fulfilled"
    assert details["line_items"][0]["image_url"] == "https://img/variant.jpg"
    assert details["line_items"][1]["image_url"] == "https://img/fallback.jpg"
    assert details["fulfillments"][0]["tracking_numbers"] == ["YT123"]


def test_normalize_graphql_fulfillment_status():
    assert shopify_graphql.normalize_fulfillment_status("FULFILLED") == "fulfilled"
    assert shopify_graphql.normalize_fulfillment_status("PARTIALLY_FULFILLED") == "partial"
    assert shopify_graphql.normalize_fulfillment_status("UNFULFILLED") == "unfulfilled"
    assert shopify_graphql.normalize_fulfillment_status("IN_PROGRESS") == "unfulfilled"
    assert shopify_graphql.normalize_fulfillment_status(None) is None
