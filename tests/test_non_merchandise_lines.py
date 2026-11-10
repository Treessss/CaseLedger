from datetime import datetime
from types import SimpleNamespace

import pytest

from app import db
from app.models import Order, OrderItem, PurchaseList
from app.services.inventory_service import _stock_rows
from app.services.packing_service import _lines
from app.services.purchase_service import (
    _aggregate_items, _available_quantities, _build_allocations,
    attach_procurement_status, remove_draft_tip_lines,
)
from app.utils.errors import ValidationError
from app.utils.order_lines import is_tip_line


@pytest.mark.parametrize("fields,expected", [
    ({"title": " Tip "}, True),
    ({"title": "TIP", "shopify_variant_id": "123"}, False),
    ({"title": "Tip", "sku": "real-product"}, False),
    ({"title": "Tip", "variant_title": "Blue"}, False),
    ({"title": "Custom handmade case"}, False),
    ({"title": "Paper Bag Cat Cute Animal iPhone Phone Case"}, False),
])
def test_tip_detection_is_conservative(fields, expected):
    assert is_tip_line(fields) is expected
    assert is_tip_line(SimpleNamespace(**fields)) is expected


def test_tip_does_not_affect_procurement_stock_or_packing(app, shop_ids, client, admin_headers):
    order = Order(shop_id=shop_ids["a"], shopify_order_id="tip-test", order_number="12299",
                  total_price=31.4, subtotal_price=31.4, currency="USD", financial_status="paid",
                  order_date=datetime(2026, 10, 9), warehouse_status="pending")
    order.items = [
        OrderItem(shop_id=order.shop_id, shopify_line_item_id="case", title="Case",
                  shopify_product_id="1", shopify_variant_id="2", quantity=1, price=30),
        OrderItem(shop_id=order.shop_id, shopify_line_item_id="tip", title="Tip", quantity=1, price=1.4),
    ]
    db.session.add(order)
    db.session.commit()
    quantity = _available_quantities([order], order.shop_id)
    assert quantity[order.items[1].id] == 0
    assert [row["title"] for row in _aggregate_items([order], quantity)] == ["Case"]
    assert len(_build_allocations([order], quantity)) == 1
    assert [row["title"] for row in _lines(order.items)] == ["Case"]
    assert all(row["title"] != "Tip" for row in _stock_rows(order.shop_id).values())
    rows = [{"id": order.id, "shop_id": order.shop_id, "warehouse_status": "pending"}]
    attach_procurement_status(rows, order_items={order.id: order.items})
    assert rows[0]["procurement_needed_quantity"] == 1
    assert float(order.total_price) == 31.4
    assert order.items[1].quantity == 1
    # Exercise the actual projected order-list query, not only the ORM helper.
    for params in ({"shop_id": order.shop_id, "search": "12299"}, {"per_page": 1000}):
        response = client.get("/api/orders", headers=admin_headers, query_string=params)
        assert response.status_code == 200, response.get_json()
        row = next(row for row in response.get_json()["data"]["orders"] if row["id"] == order.id)
        assert row["procurement_needed_quantity"] == 1
    # Expenses preload this same order list for their association selector.
    expenses = client.get("/api/expenses", headers=admin_headers, query_string={"shop_id": order.shop_id})
    assert expenses.status_code == 200, expenses.get_json()


def test_repair_removes_only_tip_from_draft(app, shop_ids):
    purchase = PurchaseList(shop_id=shop_ids["a"], name="Draft", order_ids=[1], order_count=1,
                            total_quantity=2, items=[
                                {"group_key": "tip", "title": "Tip", "quantity": 1},
                                {"group_key": "case", "title": "Case", "quantity": 1},
                            ], allocations=[{"group_key": "tip"}, {"group_key": "case"}])
    db.session.add(purchase)
    db.session.commit()
    assert remove_draft_tip_lines(purchase.id, purchase.shop_id) == 1
    assert purchase.total_quantity == 1
    assert purchase.allocations == [{"group_key": "case"}]
    assert remove_draft_tip_lines(purchase.id, purchase.shop_id) == 0
    purchase.status = "ordered"
    with pytest.raises(ValidationError):
        remove_draft_tip_lines(purchase.id, purchase.shop_id)
