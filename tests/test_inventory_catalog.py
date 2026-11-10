import json

from app import db
from app.models import Product


def test_manual_picker_includes_unsold_catalog_variants_and_accepts_stock(
    app, client, admin_headers, shop_ids,
):
    db.session.add_all([
        Product(shop_id=shop_ids["a"], shopify_product_id="catalog-full", shopify_variant_id=f"unsold-{i}",
                title="Catalog only case", variant_title=f"Black / iPhone {i}", price=30,
                image_url="https://example.com/black.jpg") for i in range(12, 19)
    ])
    db.session.add(Product(shop_id=shop_ids["b"], shopify_product_id="other-shop", shopify_variant_id="foreign",
                           title="Catalog only case", variant_title="Black / iPhone 19", price=30))
    db.session.commit()
    params = {"shop_id": shop_ids["a"], "search": "Catalog only case", "group_by_product": "true"}
    assert client.get("/api/inventory", headers=admin_headers, query_string=params).get_json()["data"]["products"] == []
    params["include_zero"] = "true"
    response = client.get("/api/inventory", headers=admin_headers, query_string=params)
    assert response.status_code == 200, response.get_json()
    products = response.get_json()["data"]["products"]
    assert len(products) == 1
    assert products[0]["variant_count"] == 7
    assert products[0]["color_count"] == 1
    key = json.dumps(["variant", "unsold-18"])
    response = client.post("/api/inventory/adjustments", headers=admin_headers,
                           json={"shop_id": shop_ids["a"], "group_key": key,
                                 "quantity_delta": 3, "reason": "Catalog only manual receipt"})
    assert response.status_code == 201, response.get_json()
    del params["include_zero"]
    response = client.get("/api/inventory", headers=admin_headers, query_string=params)
    row = response.get_json()["data"]["products"][0]
    assert row["variant_count"] == 1
    assert row["on_hand_quantity"] == 3
    assert row["image_url"] == "https://example.com/black.jpg"


def test_foreign_catalog_variant_cannot_be_stocked(app, client, admin_headers, shop_ids):
    db.session.add(Product(shop_id=shop_ids["b"], shopify_product_id="private", shopify_variant_id="private-variant",
                           title="Private catalog", price=30))
    db.session.commit()
    response = client.post("/api/inventory/adjustments", headers=admin_headers,
                           json={"shop_id": shop_ids["a"], "group_key": json.dumps(["variant", "private-variant"]),
                                 "quantity_delta": 1, "reason": "Wrong shop"})
    assert response.status_code == 404, response.get_json()
