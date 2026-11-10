"""Inventory follows accepted receipts, shipped orders and audited corrections."""

from datetime import datetime

import pytest

from app import db
from app.models import Order, OrderItem


@pytest.mark.parametrize("historical_source", ["purchase", "reservation"])
def test_stocktake_never_releases_shipped_stock(app, client, admin_headers, shop_ids, historical_source, monkeypatch):
    import json
    from app.models import InventoryReservation, PurchaseList
    from app.services.inventory_service import _stock_rows

    with app.app_context():
        shop_id = shop_ids["a"]
        live = Order.query.filter_by(shop_id=shop_id).first()
        live.warehouse_status = "pending"
        live.financial_status = "paid"
        live.order_date = datetime(2026, 1, 1)
        shipped = Order(shop_id=shop_id, shopify_order_id="stocktake-shipped",
                        order_number="stocktake-shipped", total_price=1, subtotal_price=1,
                        financial_status="paid", warehouse_status="shipped",
                        order_date=datetime(2026, 2, 1), shipped_at=datetime(2026, 2, 2))
        db.session.add(shipped)
        db.session.flush()
        items = []
        for order in (live, shipped):
            item = OrderItem(shop_id=shop_id, order_id=order.id,
                             shopify_line_item_id=f"stocktake-{order.id}",
                             shopify_product_id="stocktake-p", shopify_variant_id="stocktake-v",
                             title="Case", variant_title="Purple / iPhone 15 Pro Max", quantity=2)
            db.session.add(item)
            items.append(item)
        db.session.flush()
        key = json.dumps(["variant", "stocktake-v"])
        allocations = [{"order_id": live.id, "order_item_id": items[0].id,
                        "group_key": key, "quantity": 2, "received_quantity": 2}]
        if historical_source == "purchase":
            allocations.append({"order_id": shipped.id, "order_item_id": items[1].id,
                                "group_key": key, "quantity": 2, "received_quantity": 2})
        else:
            db.session.add(InventoryReservation(shop_id=shop_id, order_id=shipped.id,
                           order_item_id=items[1].id, group_key=key, quantity=2))
        purchase = PurchaseList(shop_id=shop_id, name="Stocktake regression", status="received",
                                order_ids=[live.id, shipped.id], total_quantity=4,
                                items=[{"group_key": key, "title": "Case", "variant_title": "Purple / iPhone 15 Pro Max",
                                        "shopify_product_id": "stocktake-p", "quantity": 4}],
                                receipt=[{"group_key": key, "received_quantity": 4}], allocations=allocations)
        db.session.add(purchase)
        db.session.commit()
        purchase_id, shipped_id = purchase.id, shipped.id
    from app.services import inventory_service
    release = inventory_service._release_occupancy
    monkeypatch.setattr(inventory_service, "_release_occupancy", lambda *_: [])
    invalid = client.post("/api/inventory/adjustments", headers=admin_headers,
                          json={"shop_id": shop_id, "group_key": key, "quantity_delta": -1})
    assert invalid.status_code == 201  # No ownership release is required for a physical count change.
    with app.app_context():
        assert _stock_rows(shop_id, group_keys={key})[key]["on_hand_quantity"] == 1
    monkeypatch.setattr(inventory_service, "_release_occupancy", release)
    for expected in (0,):
        response = client.post("/api/inventory/adjustments", headers=admin_headers,
                               json={"shop_id": shop_id, "group_key": key, "quantity_delta": -1})
        assert response.status_code == 201, response.get_json()
        with app.app_context():
            row = _stock_rows(shop_id, group_keys={key})[key]
            assert row["on_hand_quantity"] == expected
            assert row["shipped_quantity"] == 2
            assert row["reserved_quantity"] == 0
            assert db.session.get(Order, shipped_id).warehouse_status == "shipped"
            if historical_source == "purchase":
                old = next(r for r in db.session.get(PurchaseList, purchase_id).allocations if r["order_id"] == shipped_id)
                assert old["received_quantity"] == 2
            else:
                assert InventoryReservation.query.filter_by(order_id=shipped_id).one().quantity == 2


def test_inventory_receipt_reservation_shipping_and_adjustment(app, client, admin_headers, shop_ids):
    with app.app_context():
        order = Order.query.filter_by(shop_id=shop_ids["a"]).first()
        order.fulfillment_status = "unfulfilled"
        db.session.add(OrderItem(
            shop_id=shop_ids["a"], order_id=order.id,
            shopify_line_item_id="stock-blue-1", shopify_product_id="stock-p1",
            shopify_variant_id="stock-v1", title="Phone Case",
            variant_title="Blue / iPhone 18 Pro", sku="BLUE-18",
            image_url="https://cdn.shopify.com/blue.jpg", quantity=3,
        ))
        db.session.commit()
        order_id = order.id
        order_number = order.order_number
    created = client.post(
        "/api/purchase-lists", headers=admin_headers,
        json={"shop_id": shop_ids["a"], "order_ids": [order_id]},
    ).get_json()["data"]
    key = created["items"][0]["group_key"]

    before = client.get(f"/api/inventory?shop_id={shop_ids['a']}&include_zero=true", headers=admin_headers).get_json()["data"]
    row = next(item for item in before["items"] if item["group_key"] == key)
    assert row["on_hand_quantity"] == row["available_quantity"] == 0
    assert row["image_url"] == "https://cdn.shopify.com/blue.jpg"

    assert client.post(f"/api/purchase-lists/{created['id']}/confirm", headers=admin_headers).status_code == 200
    ordered = client.get(f"/api/inventory?shop_id={shop_ids['a']}&include_zero=true", headers=admin_headers).get_json()["data"]
    assert next(item for item in ordered["items"] if item["group_key"] == key)["on_hand_quantity"] == 0
    received = client.post(
        f"/api/purchase-lists/{created['id']}/receive", headers=admin_headers,
        json={"items": [{"group_key": key, "received_quantity": 2, "damaged_quantity": 1}]},
    )
    assert received.status_code == 200
    stocked = client.get(f"/api/inventory?shop_id={shop_ids['a']}", headers=admin_headers).get_json()["data"]
    row = next(item for item in stocked["items"] if item["group_key"] == key)
    assert (row["received_quantity"], row["on_hand_quantity"], row["reserved_quantity"], row["available_quantity"]) == (2, 2, 0, 2)
    assert row["occupied_orders"] == [{"order_id": order_id, "order_number": order_number, "quantity": 3}]

    positive = client.post(
        "/api/inventory/adjustments", headers=admin_headers,
        json={"shop_id": shop_ids["a"], "group_key": key, "quantity_delta": 2, "reason": "期初盘点"},
    )
    assert positive.status_code == 201
    # Report the current soft plan, without creating a permanent reservation.
    assert [(item["order_id"], item["quantity"]) for item in positive.get_json()["data"]["allocated_orders"]] == [
        (order_id, 3)
    ]
    assert client.get(
        f"/api/orders/{order_id}", headers=admin_headers
    ).get_json()["data"]["procurement_status"] == "ready_to_ship"
    # 扣到超过在库数量仍然要拦住（当前在库 4）
    too_much = client.post(
        "/api/inventory/adjustments", headers=admin_headers,
        json={"shop_id": shop_ids["a"], "group_key": key, "quantity_delta": -5, "reason": "盘亏"},
    )
    assert too_much.status_code == 400
    assert len(client.get(
        f"/api/inventory/adjustments?shop_id={shop_ids['a']}", headers=admin_headers
    ).get_json()["data"]) == 1

    # 订单已经备齐，没有待备货数量了，手工再分配会被拦住
    assert client.post(
        "/api/inventory/reservations", headers=admin_headers,
        json={"shop_id": shop_ids["a"], "group_key": key, "order_number": order_number, "quantity": 1},
    ).status_code == 400
    occupied = client.get(
        f"/api/inventory?shop_id={shop_ids['a']}&group_by_product=true", headers=admin_headers
    ).get_json()["data"]["products"][0]
    assert occupied["occupied_orders"] == [{"order_id": order_id, "order_number": order_number, "quantity": 3}]
    assert occupied["colors"][0]["occupied_orders"] == occupied["occupied_orders"]
    assert occupied["colors"][0]["variants"][0]["occupied_orders"] == occupied["occupied_orders"]
    assert client.get(f"/api/orders/{order_id}", headers=admin_headers).get_json()["data"]["procurement_status"] == "ready_to_ship"
    assert client.post(
        "/api/inventory/reservations", headers=admin_headers,
        json={"shop_id": shop_ids["a"], "group_key": key, "order_number": order_number, "quantity": 1},
    ).status_code == 400

    with app.app_context():
        order = db.session.get(Order, order_id)
        order.fulfillment_status = "fulfilled"
        db.session.commit()
    still_stocked = client.get(f"/api/inventory?shop_id={shop_ids['a']}", headers=admin_headers).get_json()["data"]
    row = next(item for item in still_stocked["items"] if item["group_key"] == key)
    assert (row["shipped_quantity"], row["reserved_quantity"]) == (0, 0)
    assert client.put(
        f"/api/orders/{order_id}/shipping-label", headers=admin_headers,
        json={"shipping_label_number": "4PX-TEST-001"},
    ).status_code == 200
    assert client.post(
        f"/api/orders/{order_id}/scan-outbound", headers=admin_headers,
        json={"shipping_label_number": "4PX-TEST-001"},
    ).status_code == 200
    shipped = client.get(f"/api/inventory?shop_id={shop_ids['a']}", headers=admin_headers).get_json()["data"]
    row = next(item for item in shipped["items"] if item["group_key"] == key)
    assert (row["shipped_quantity"], row["on_hand_quantity"], row["reserved_quantity"], row["available_quantity"]) == (3, 1, 0, 1)
    assert row["occupied_orders"] == []


def test_inventory_is_shop_and_permission_scoped(app, client, admin_headers, limited_headers, shop_ids):
    assert client.get(f"/api/inventory?shop_id={shop_ids['a']}", headers=limited_headers).status_code == 403
    assert client.get(f"/api/inventory?shop_id={shop_ids['b']}", headers=admin_headers).status_code == 200
    assert client.post(
        "/api/inventory/adjustments", headers=limited_headers,
        json={"shop_id": shop_ids["a"], "group_key": "x", "quantity_delta": 1, "reason": "test"},
    ).status_code == 403
    assert client.post(
        "/api/inventory/reservations", headers=limited_headers,
        json={"shop_id": shop_ids["a"], "group_key": "x", "order_number": "A0", "quantity": 1},
    ).status_code == 403


def test_available_stock_can_partially_prepare_an_order_without_claiming_purchase_started(
    app, client, admin_headers, shop_ids
):
    with app.app_context():
        order = Order.query.filter_by(shop_id=shop_ids["a"]).first()
        order.fulfillment_status = "unfulfilled"
        db.session.add(OrderItem(
            shop_id=shop_ids["a"], order_id=order.id,
            shopify_line_item_id="existing-stock", shopify_variant_id="existing-stock-v1",
            title="Case", variant_title="Green / iPhone 17", quantity=2,
        ))
        db.session.commit()
        order_id = order.id
        order_number = order.order_number
    row = client.get(f"/api/inventory?shop_id={shop_ids['a']}&include_zero=true", headers=admin_headers).get_json()["data"]["items"][0]
    # 期初库存只有 1 件：自动分配给订单后仍差 1 件，不需要手工预留
    adjusted = client.post(
        "/api/inventory/adjustments", headers=admin_headers,
        json={"shop_id": shop_ids["a"], "group_key": row["group_key"], "quantity_delta": 1, "reason": "期初库存"},
    )
    assert adjusted.status_code == 201
    allocated = adjusted.get_json()["data"]["allocated_orders"]
    assert [(item["order_id"], item["quantity"]) for item in allocated] == [(order_id, 1)]
    assert allocated[0]["order_number"] == order_number
    order_data = client.get(f"/api/orders/{order_id}", headers=admin_headers).get_json()["data"]
    assert order_data["procurement_status"] == "partially_stocked"
    assert order_data["purchase_initiated"] is False
    assert order_data["procurement_needed_quantity"] == 1


def test_removed_order_item_releases_received_stock_occupancy(app, client, admin_headers, shop_ids):
    with app.app_context():
        order = Order.query.filter_by(shop_id=shop_ids["a"]).first()
        order.fulfillment_status = "unfulfilled"
        item = OrderItem(
            shop_id=shop_ids["a"], order_id=order.id,
            shopify_line_item_id="edited-away", shopify_variant_id="edited-away-v1",
            title="Case", variant_title="Pink / iPhone 17", quantity=2,
        )
        db.session.add(item)
        db.session.commit()
        order_id = order.id
        item_id = item.id

    created = client.post(
        "/api/purchase-lists", headers=admin_headers,
        json={"shop_id": shop_ids["a"], "order_ids": [order_id]},
    ).get_json()["data"]
    key = created["items"][0]["group_key"]
    assert client.post(f"/api/purchase-lists/{created['id']}/confirm", headers=admin_headers).status_code == 200
    assert client.post(
        f"/api/purchase-lists/{created['id']}/receive", headers=admin_headers,
        json={"items": [{"group_key": key, "received_quantity": 2, "damaged_quantity": 0}]},
    ).status_code == 200

    with app.app_context():
        db.session.get(OrderItem, item_id).quantity = 0
        db.session.commit()
    data = client.get(f"/api/inventory?shop_id={shop_ids['a']}", headers=admin_headers).get_json()["data"]
    row = next(value for value in data["items"] if value["group_key"] == key)
    assert (row["on_hand_quantity"], row["reserved_quantity"], row["available_quantity"]) == (2, 0, 2)


def test_daily_movements_record_receipts_and_outbound(app, client, admin_headers, shop_ids):
    """每天的出入库流水：采购到货计入入库，扫码出库计入出库。"""
    with app.app_context():
        order = Order.query.filter_by(shop_id=shop_ids["a"]).first()
        order.fulfillment_status = "unfulfilled"
        db.session.add(OrderItem(
            shop_id=shop_ids["a"], order_id=order.id,
            shopify_line_item_id="mv-1", shopify_product_id="mv-p1",
            shopify_variant_id="mv-v1", title="Movement Case",
            variant_title="Red / iPhone 18", sku="MV-18", quantity=3,
        ))
        db.session.commit()
        order_id = order.id

    created = client.post(
        "/api/purchase-lists", headers=admin_headers,
        json={"shop_id": shop_ids["a"], "order_ids": [order_id]},
    ).get_json()["data"]
    key = created["items"][0]["group_key"]
    client.post(f"/api/purchase-lists/{created['id']}/confirm", headers=admin_headers)
    received = client.post(
        f"/api/purchase-lists/{created['id']}/receive", headers=admin_headers,
        json={"items": [{"group_key": key, "received_quantity": 2, "damaged_quantity": 1}]},
    )
    assert received.status_code == 200
    # 这批到货已被订单占用，先补一批可用库存，再做盘亏
    client.post(
        "/api/inventory/adjustments", headers=admin_headers,
        json={"shop_id": shop_ids["a"], "group_key": key, "quantity_delta": 2, "reason": "期初盘点"},
    )
    client.post(
        "/api/inventory/adjustments", headers=admin_headers,
        json={"shop_id": shop_ids["a"], "group_key": key, "quantity_delta": -1, "reason": "盘亏"},
    )

    body = client.get(
        f"/api/inventory/movements?shop_id={shop_ids['a']}", headers=admin_headers
    ).get_json()["data"]

    # 入库 = 到货 2 件（货损不计入）+ 期初盘点 2 件；出库 = 盘亏 1 件
    assert body["totals"] == {"in_quantity": 4, "out_quantity": 1}
    assert len(body["days"]) == 1
    assert body["days"][0]["in_quantity"] == 4
    assert body["days"][0]["out_quantity"] == 1

    directions = {row["direction"] for row in body["movements"]}
    assert directions == {"in", "out"}
    receipt = next(
        row for row in body["movements"]
        if row["direction"] == "in" and "采购清单" in row["source"]
    )
    assert receipt["quantity"] == 2
    assert "采购清单" in receipt["source"]
    assert receipt["variant_title"] == "Red / iPhone 18"


def test_movements_reject_reversed_range_and_scope_by_shop(app, client, admin_headers, shop_ids):
    response = client.get(
        f"/api/inventory/movements?shop_id={shop_ids['a']}&start_date=2026-10-05&end_date=2026-10-01",
        headers=admin_headers,
    )
    assert response.status_code == 400

    other = client.get(
        f"/api/inventory/movements?shop_id={shop_ids['b']}", headers=admin_headers
    ).get_json()["data"]
    assert other["totals"] == {"in_quantity": 0, "out_quantity": 0}


def test_loss_can_consume_reserved_stock_and_sends_order_back_to_purchase(app, client, admin_headers, shop_ids):
    """货损扣减可以吃掉被订单占用的库存；受影响的订单要重新回到待采购。"""
    with app.app_context():
        order = Order.query.filter_by(shop_id=shop_ids["a"]).first()
        order.fulfillment_status = "unfulfilled"
        db.session.add(OrderItem(
            shop_id=shop_ids["a"], order_id=order.id,
            shopify_line_item_id="loss-1", shopify_product_id="loss-p1",
            shopify_variant_id="loss-v1", title="Loss Case",
            variant_title="Green / iPhone 18", sku="LOSS-18", quantity=1,
        ))
        db.session.commit()
        order_id = order.id
        order_number = order.order_number

    created = client.post(
        "/api/purchase-lists", headers=admin_headers,
        json={"shop_id": shop_ids["a"], "order_ids": [order_id]},
    ).get_json()["data"]
    key = created["items"][0]["group_key"]
    client.post(f"/api/purchase-lists/{created['id']}/confirm", headers=admin_headers)
    assert client.post(
        f"/api/purchase-lists/{created['id']}/receive", headers=admin_headers,
        json={"items": [{"group_key": key, "received_quantity": 1, "damaged_quantity": 0}]},
    ).status_code == 200

    # 只有 1 件、而且已被订单占用：可用为 0
    stocked = client.get(f"/api/inventory?shop_id={shop_ids['a']}", headers=admin_headers).get_json()["data"]
    row = next(item for item in stocked["items"] if item["group_key"] == key)
    assert (row["on_hand_quantity"], row["reserved_quantity"], row["available_quantity"]) == (1, 0, 1)
    assert client.get(
        f"/api/orders/{order_id}", headers=admin_headers
    ).get_json()["data"]["procurement_status"] == "ready_to_ship"

    # 发现货损，把这唯一一件扣掉
    loss = client.post(
        "/api/inventory/adjustments", headers=admin_headers,
        json={"shop_id": shop_ids["a"], "group_key": key, "quantity_delta": -1, "reason": "货损报废"},
    )
    assert loss.status_code == 201
    assert loss.get_json()["data"]["released_orders"] == []  # Demand changes, not ownership.

    after = client.get(
        f"/api/inventory?shop_id={shop_ids['a']}&include_zero=true", headers=admin_headers
    ).get_json()["data"]
    row = next(item for item in after["items"] if item["group_key"] == key)
    assert (row["on_hand_quantity"], row["reserved_quantity"], row["available_quantity"]) == (0, 0, 0)
    assert row["demand_orders"] == [{"order_id": order_id, "order_number": order_number, "quantity": 1}]

    # 订单失去库存占用 → 不再备齐，回到待采购
    detail = client.get(f"/api/orders/{order_id}", headers=admin_headers).get_json()["data"]
    assert detail["procurement_status"] != "ready_to_ship"
    pending = client.get(
        f"/api/purchase-candidates?shop_id={shop_ids['a']}&view=needs_purchase",
        headers=admin_headers,
    ).get_json()["data"]
    assert order_id in pending["all_order_ids"]
    assert pending["category_counts"]["needs_purchase"] == 1


def test_available_stock_auto_binds_to_waiting_order(app, client, admin_headers, shop_ids):
    """有可用库存时自动绑定到缺货订单，订单不再出现在待采购里（避免重复采购）。"""
    with app.app_context():
        order = Order.query.filter_by(shop_id=shop_ids["a"]).first()
        order.fulfillment_status = "unfulfilled"
        db.session.add(OrderItem(
            shop_id=shop_ids["a"], order_id=order.id,
            shopify_line_item_id="auto-1", shopify_product_id="auto-p1",
            shopify_variant_id="auto-v1", title="Auto Bind Case",
            variant_title="Blue / iPhone 19", sku="AUTO-19", quantity=1,
        ))
        db.session.commit()
        order_id = order.id
        order_number = order.order_number

    def needs_purchase():
        return client.get(
            f"/api/purchase-candidates?shop_id={shop_ids['a']}&view=needs_purchase",
            headers=admin_headers,
        ).get_json()["data"]["all_order_ids"]

    # 一开始没库存 → 该订单需要采购
    assert order_id in needs_purchase()

    row = client.get(
        f"/api/inventory?shop_id={shop_ids['a']}&include_zero=true", headers=admin_headers
    ).get_json()["data"]["items"]
    key = next(item["group_key"] for item in row if item["sku"] == "AUTO-19")

    # 手动补 1 件 → 自动绑定到这张缺货订单
    adjusted = client.post(
        "/api/inventory/adjustments", headers=admin_headers,
        json={"shop_id": shop_ids["a"], "group_key": key, "quantity_delta": 1, "reason": "期初库存"},
    )
    assert adjusted.status_code == 201
    allocated = adjusted.get_json()["data"]["allocated_orders"]
    assert [(item["order_id"], item["quantity"]) for item in allocated] == [(order_id, 1)]
    assert allocated[0]["order_number"] == order_number

    # 订单已备齐，且不再出现在待采购里
    detail = client.get(f"/api/orders/{order_id}", headers=admin_headers).get_json()["data"]
    assert detail["procurement_status"] == "ready_to_ship"
    assert order_id not in needs_purchase()

    # 库存被占用，可用归零
    stocked = client.get(f"/api/inventory?shop_id={shop_ids['a']}", headers=admin_headers).get_json()["data"]
    row = next(item for item in stocked["items"] if item["group_key"] == key)
    assert (row["on_hand_quantity"], row["reserved_quantity"], row["available_quantity"]) == (1, 0, 1)


def _sync_service(app, shop_id):
    """构造一个只用来跑「同步后自动绑定」这一步的服务实例（不碰 Shopify 网络）。"""
    from app.services.shopify_service import ShopifyService

    service = ShopifyService()
    service.app = app
    service.shop_id = shop_id
    return service


def test_sync_binds_new_order_to_existing_free_stock(app, client, admin_headers, shop_ids):
    """新订单同步进来时，仓库里的现货要直接绑上去，不能又进待采购。"""
    shop_id = shop_ids["a"]
    with app.app_context():
        # 存量订单全部当作已出库，这样第一步入库不会被它们占用，库存保持「可用」
        for order in Order.query.filter_by(shop_id=shop_id).all():
            order.warehouse_status = "shipped"
        holder = Order.query.filter_by(shop_id=shop_id).first()
        db.session.add(OrderItem(
            shop_id=shop_id, order_id=holder.id,
            shopify_line_item_id="sync-holder", shopify_product_id="sync-p1",
            shopify_variant_id="sync-v1", title="Sync Case",
            variant_title="Blue / iPhone 19", sku="SYNC-19", quantity=1,
        ))
        db.session.commit()

    items = client.get(
        f"/api/inventory?shop_id={shop_id}&include_zero=true", headers=admin_headers
    ).get_json()["data"]["items"]
    key = next(item["group_key"] for item in items if item["sku"] == "SYNC-19")

    # 先入 2 件，此时没有等货订单 → 全部留作可用库存
    first = client.post(
        "/api/inventory/adjustments", headers=admin_headers,
        json={"shop_id": shop_id, "group_key": key, "quantity_delta": 2, "reason": "期初库存"},
    )
    assert first.status_code == 201
    assert first.get_json()["data"]["allocated_orders"] == []

    # 新订单随后同步进来（同一变体）
    with app.app_context():
        order = Order(
            shop_id=shop_id, shopify_order_id="sync-new-1", order_number="#SYNC-NEW",
            total_price=20, subtotal_price=20, currency="USD",
            financial_status="paid", fulfillment_status="unfulfilled",
            order_date=datetime(2026, 10, 4, 6, 0, 0), created_at=datetime(2026, 10, 4, 6, 0, 0),
        )
        db.session.add(order)
        db.session.flush()
        db.session.add(OrderItem(
            shop_id=shop_id, order_id=order.id,
            shopify_line_item_id="sync-new-line", shopify_product_id="sync-p1",
            shopify_variant_id="sync-v1", title="Sync Case",
            variant_title="Blue / iPhone 19", sku="SYNC-19", quantity=2,
        ))
        db.session.commit()
        new_order_id = order.id

    def needs_purchase():
        return client.get(
            f"/api/purchase-candidates?shop_id={shop_id}&view=needs_purchase",
            headers=admin_headers,
        ).get_json()["data"]["all_order_ids"]

    # Read-only shared planning recognizes stock immediately, without waiting
    # for the sync hook to materialize a permanent inventory reservation.
    assert new_order_id not in needs_purchase()

    # 同步收尾时自动绑定
    _sync_service(app, shop_id)._auto_bind_available_stock(1)

    detail = client.get(f"/api/orders/{new_order_id}", headers=admin_headers).get_json()["data"]
    assert detail["procurement_status"] == "ready_to_ship"
    assert new_order_id not in needs_purchase()

    stocked = client.get(f"/api/inventory?shop_id={shop_id}", headers=admin_headers).get_json()["data"]
    row = next(item for item in stocked["items"] if item["group_key"] == key)
    assert (row["on_hand_quantity"], row["reserved_quantity"], row["available_quantity"]) == (2, 0, 2)


def test_sync_auto_bind_skips_when_no_new_orders(app, client, admin_headers, shop_ids):
    """没有新订单时不去动库存，避免每次同步都做无用功。"""
    shop_id = shop_ids["a"]
    with app.app_context():
        for order in Order.query.filter_by(shop_id=shop_id).all():
            order.warehouse_status = "shipped"
        holder = Order.query.filter_by(shop_id=shop_id).first()
        db.session.add(OrderItem(
            shop_id=shop_id, order_id=holder.id,
            shopify_line_item_id="skip-holder", shopify_product_id="skip-p1",
            shopify_variant_id="skip-v1", title="Skip Case",
            variant_title="Red / iPhone 19", sku="SKIP-19", quantity=1,
        ))
        db.session.commit()

    items = client.get(
        f"/api/inventory?shop_id={shop_id}&include_zero=true", headers=admin_headers
    ).get_json()["data"]["items"]
    key = next(item["group_key"] for item in items if item["sku"] == "SKIP-19")
    client.post(
        "/api/inventory/adjustments", headers=admin_headers,
        json={"shop_id": shop_id, "group_key": key, "quantity_delta": 1, "reason": "期初库存"},
    )

    with app.app_context():
        order = Order(
            shop_id=shop_id, shopify_order_id="skip-new-1", order_number="#SKIP-NEW",
            total_price=20, subtotal_price=20, currency="USD",
            financial_status="paid", fulfillment_status="unfulfilled",
            order_date=datetime(2026, 10, 4, 6, 0, 0), created_at=datetime(2026, 10, 4, 6, 0, 0),
        )
        db.session.add(order)
        db.session.flush()
        db.session.add(OrderItem(
            shop_id=shop_id, order_id=order.id,
            shopify_line_item_id="skip-new-line", shopify_product_id="skip-p1",
            shopify_variant_id="skip-v1", title="Skip Case",
            variant_title="Red / iPhone 19", sku="SKIP-19", quantity=1,
        ))
        db.session.commit()

    _sync_service(app, shop_id)._auto_bind_available_stock(0)

    stocked = client.get(f"/api/inventory?shop_id={shop_id}", headers=admin_headers).get_json()["data"]
    row = next(item for item in stocked["items"] if item["group_key"] == key)
    assert (row["on_hand_quantity"], row["reserved_quantity"], row["available_quantity"]) == (1, 0, 1)


def test_inventory_rows_expose_bundle_configuration(app, shop_ids):
    import json
    from app.models import PurchaseList
    from app.services.inventory_service import _stock_rows

    with app.app_context():
        shop_id = shop_ids["a"]
        plain_key = json.dumps(["variant", "plain-pink-lime"], ensure_ascii=False)
        wrist_key = json.dumps(["variant", "wrist-pink-lime"], ensure_ascii=False)
        base = {"title": "Gradient Magnetic iPhone Case with Matching Stand",
                "shopify_product_id": "gradient-magnetic", "quantity": 1}
        purchase = PurchaseList(
            shop_id=shop_id, name="bundle configuration regression", status="received",
            order_ids=[], total_quantity=2,
            items=[
                {**base, "group_key": plain_key, "variant_title": "iPhone 17 Pro / Pink Lime",
                 "sku": "CE-GMS-17-PRO-PINK-LIME",
                 "image_url": "https://example.com/pink-lime.png"},
                {**base, "group_key": wrist_key, "variant_title": "iPhone 17 Pro / Pink Lime + Wrist Chain",
                 "sku": "CE-GMS-17-PRO-PINK-LIME-WRIST",
                 "image_url": "https://example.com/pink-lime-wrist.png"},
            ],
            receipt=[{"group_key": plain_key, "received_quantity": 1},
                     {"group_key": wrist_key, "received_quantity": 1}],
        )
        db.session.add(purchase)
        db.session.commit()
        rows = _stock_rows(shop_id)
        assert rows[plain_key]["model"] == rows[wrist_key]["model"] == "iPhone 17 Pro"
        assert rows[plain_key]["configuration"] == ""
        assert rows[wrist_key]["configuration"] == "+ Wrist Chain"

        from app.services.inventory_service import _group_stock_products
        product = _group_stock_products([rows[plain_key], rows[wrist_key]])[0]
        # 同颜色、不同配置合并成一个颜色框；配置在型号行上区分
        assert product["color_count"] == 1
        color = product["colors"][0]
        assert color["name"] == "Pink Lime"
        # 普通款与 +Wrist Chain 同型号合并为一行（链子单独采购）
        assert color["variant_count"] == 1
        assert color["image_url"].endswith("pink-lime.png")
        merged = color["variants"][0]
        assert len(merged["group_keys"]) == 2
        assert set(merged["merged_configurations"]) == {"", "+ Wrist Chain"}
        assert merged["on_hand_quantity"] == 2
        assert merged["configuration"] == ""
