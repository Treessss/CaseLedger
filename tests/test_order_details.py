"""订单客户信息 / 收货地址 / 商品明细的解析。"""

from decimal import Decimal

from app.utils.shopify_resources import extract_customer_info, extract_line_items


ORDER_PAYLOAD = {
    "id": 7481040326831,
    "email": "buyer@example.com",
    "phone": "+61412345678",
    "customer": {"first_name": "Julie", "last_name": "Tremblay", "email": "buyer@example.com"},
    "shipping_address": {
        "name": "Julie Tremblay",
        "address1": "753 Rue Emond",
        "city": "Portneuf-sur-Mer",
        "province": "Quebec",
        "province_code": "QC",
        "country": "Canada",
        "country_code": "CA",
        "zip": "G0T 1P0",
        "latitude": 48.1,  # 不需要的字段不应留存
    },
    "line_items": [
        {
            "id": 17640983003439,
            "product_id": 16110917222703,
            "variant_id": 58448536961327,
            "title": "Dopamine Contrast Color iPhone Case",
            "variant_title": "Yellow-Blue / iPhone 18 Pro Max",
            "sku": "DC-001",
            "vendor": "Caseease",
            "quantity": 2,
            "price": "22.57",
            "total_discount": "3.00",
        }
    ],
}


class _Resource:
    def __init__(self, attributes):
        self.attributes = attributes


def test_extract_customer_info_from_dict():
    info = extract_customer_info(ORDER_PAYLOAD)
    assert info["email"] == "buyer@example.com"
    assert info["name"] == "Julie Tremblay"
    assert info["phone"] == "+61412345678"
    assert info["shipping_address"]["address1"] == "753 Rue Emond"
    assert info["shipping_address"]["city"] == "Portneuf-sur-Mer"
    assert info["shipping_address"]["country_code"] == "CA"
    assert "latitude" not in info["shipping_address"]


def test_extract_customer_info_when_shopify_masks_pii():
    """Basic 套餐下 Shopify 会屏蔽 PII，只剩省份/国家，不能因此报错。"""
    masked = {
        "id": 1,
        "email": None,
        "phone": None,
        "customer": {"first_name": None, "last_name": None, "email": None},
        "shipping_address": {"province": "New South Wales", "country": "Australia", "country_code": "AU"},
    }
    info = extract_customer_info(masked)
    assert info["email"] is None
    assert info["name"] is None
    assert info["phone"] is None
    assert info["shipping_address"] == {
        "province": "New South Wales",
        "country": "Australia",
        "country_code": "AU",
    }


def test_extract_customer_info_handles_resource_objects():
    resource = _Resource(
        {
            "customer": _Resource({"first_name": "Julie", "last_name": "Tremblay"}),
            "shipping_address": _Resource({"city": "Sydney", "country_code": "AU"}),
        }
    )
    info = extract_customer_info(resource)
    assert info["name"] == "Julie Tremblay"
    assert info["shipping_address"] == {"city": "Sydney", "country_code": "AU"}


def test_extract_customer_info_tolerates_empty_input():
    for payload in (None, {}, "not-an-order"):
        info = extract_customer_info(payload)
        assert info == {"email": None, "name": None, "phone": None, "shipping_address": None}


def test_extract_line_items():
    rows = extract_line_items(ORDER_PAYLOAD)
    assert len(rows) == 1
    row = rows[0]
    assert row["shopify_line_item_id"] == "17640983003439"
    assert row["shopify_product_id"] == "16110917222703"
    assert row["shopify_variant_id"] == "58448536961327"
    assert row["title"] == "Dopamine Contrast Color iPhone Case"
    assert row["variant_title"] == "Yellow-Blue / iPhone 18 Pro Max"
    assert row["quantity"] == 2
    assert row["price"] == Decimal("22.57")
    assert row["total_discount"] == Decimal("3.00")


def test_extract_line_items_skips_entries_without_id():
    payload = {"line_items": [{"title": "bad row"}, {"id": 5, "title": "ok", "quantity": 1, "price": "1.00"}]}
    rows = extract_line_items(payload)
    assert [row["title"] for row in rows] == ["ok"]
    assert extract_line_items({}) == []


def test_extract_line_items_prefers_current_quantity_after_edit():
    payload = {"line_items": [
        {"id": 1, "quantity": 2, "current_quantity": 0, "price": "29.99"},
        {"id": 2, "quantity": 3, "current_quantity": 1, "price": "29.99"},
    ]}
    assert [row["quantity"] for row in extract_line_items(payload)] == [0, 1]
