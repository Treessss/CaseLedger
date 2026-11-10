"""Saved procurement lists aggregate selected order variants without crossing shops."""

from datetime import datetime

from app import db
from app.models import Order, OrderItem, PurchaseList
from app.services.purchase_service import _group_colors, _group_products


def test_candidates_build_one_fresh_shop_plan(app, client, admin_headers, shop_ids, monkeypatch):
    from app.services import inventory_pool_service
    _seed_items(app, shop_ids['a'])
    original = inventory_pool_service.build_pool_plan
    calls = []
    def counted(shop_id, **kwargs):
        calls.append(shop_id)
        return original(shop_id, **kwargs)
    monkeypatch.setattr(inventory_pool_service, 'build_pool_plan', counted)
    for view in ('needs_purchase', 'after_sale'):
        calls.clear()
        response = client.get(f"/api/purchase-candidates?shop_id={shop_ids['a']}&view={view}",
                              headers=admin_headers)
        assert response.status_code == 200
        assert calls == [shop_ids['a']]


def test_stock_only_purchase_can_confirm_and_receive_without_orders(app, client, admin_headers, shop_ids):
    from flask import g
    from app.models import Product
    from app.services.purchase_service import create_stock_purchase_list
    with app.app_context():
        product = Product(shop_id=shop_ids["a"], shopify_product_id="independent-product",
                          shopify_variant_id="independent-variant", title="Ring Case", price=1,
                          variant_title="Purple / iPhone 15 Pro", image_url="https://example.com/purple.png")
        db.session.add(product)
        db.session.commit()
        g.current_user={"authority":"admin"}
        created=create_stock_purchase_list(shop_ids["a"],[{"product_id":product.id,"quantity":3}],"独立采购")
        assert created["order_ids"] == created["orders"] == []
        assert created["order_count"] == 0
        assert created["items"][0]["order_numbers"] == []
        assert created["total_quantity"] == 3
    confirm=client.post(f"/api/purchase-lists/{created['id']}/confirm",headers=admin_headers)
    assert confirm.status_code == 200,confirm.get_json()
    received=client.post(f"/api/purchase-lists/{created['id']}/receive",headers=admin_headers,
                         json={"items":[{"group_key":created["items"][0]["group_key"],
                                         "received_quantity":3,"damaged_quantity":0}]})
    assert received.status_code == 200,received.get_json()
    stock=client.get(f"/api/inventory?shop_id={shop_ids['a']}",headers=admin_headers).get_json()["data"]["items"]
    row=next(row for row in stock if row["group_key"]==created["items"][0]["group_key"])
    assert row["on_hand_quantity"] == 3
    with app.app_context():
        purchase=db.session.get(PurchaseList,created['id'])
        assert purchase.order_ids == []
        assert all(a.get("stock_only") and not a.get("order_id") for a in purchase.allocations)


def test_procurement_dismissal_preserves_warehouse_and_allows_explicit_purchase(app, client, admin_headers, shop_ids):
    from flask import g
    from app.services.purchase_service import exclude_order_procurement
    order_ids = _seed_items(app, shop_ids["a"])
    with app.app_context():
        order = db.session.get(Order, order_ids[0])
        order.financial_status = "paid"
        order.fulfillment_status = "fulfilled"
        order.warehouse_status = "pending"
        db.session.commit()
        g.current_user = {"authority": "admin"}
        exclude_order_procurement(shop_ids["a"], [order.id])
        assert order.warehouse_status == "pending"
        assert order.procurement_excluded_at is not None
        # A normal Shopify status update never owns the internal opt-out.
        order.fulfillment_status = "unfulfilled"
        db.session.commit()
    for view in ("needs_purchase", "replenish", "unprocured"):
        data = client.get(f"/api/purchase-candidates?shop_id={shop_ids['a']}&view={view}",
                          headers=admin_headers).get_json()["data"]
        assert order_ids[0] not in data["all_order_ids"]
        assert all(row["id"] != order_ids[0] for row in data["orders"])
    response = client.post("/api/purchase-lists", headers=admin_headers,
                           json={"shop_id": shop_ids["a"], "order_ids": [order_ids[0]]})
    assert response.status_code == 201
    with app.app_context():
        assert db.session.get(Order, order_ids[0]).procurement_excluded_at is None


def test_stocktake_reopens_only_related_dismissed_shortages(app, client, admin_headers, shop_ids):
    import json
    from flask import g
    from app.models import InventoryReservation, InventoryAdjustment
    from app.services.purchase_service import exclude_order_procurement
    order_ids = _seed_items(app, shop_ids["a"])
    key = json.dumps(["variant", "blue"])
    with app.app_context():
        for order_id in (order_ids[0], order_ids[2]):
            order = db.session.get(Order, order_id)
            order.fulfillment_status = "fulfilled"
            order.warehouse_status = "pending"
        item = OrderItem.query.filter_by(order_id=order_ids[0]).one()
        db.session.add(InventoryAdjustment(shop_id=shop_ids["a"],group_key=key,quantity_delta=1,reason="期初"))
        db.session.add(InventoryReservation(shop_id=shop_ids["a"],group_key=key,quantity=1,
                       order_id=order_ids[0],order_item_id=item.id))
        db.session.commit()
        g.current_user={"authority":"admin"}
        exclude_order_procurement(shop_ids["a"],[order_ids[0],order_ids[2]])
    result=client.post("/api/inventory/adjustments",headers=admin_headers,
                       json={"shop_id":shop_ids["a"],"group_key":key,"quantity_delta":-1})
    assert result.status_code == 201, result.get_json()
    with app.app_context():
        assert db.session.get(Order,order_ids[0]).procurement_excluded_at is None
        assert db.session.get(Order,order_ids[2]).procurement_excluded_at is not None
        assert db.session.get(Order,order_ids[0]).warehouse_status == "pending"
    candidates=client.get(f"/api/purchase-candidates?shop_id={shop_ids['a']}&view=needs_purchase",
                          headers=admin_headers).get_json()["data"]
    assert order_ids[0] in candidates["all_order_ids"]
    assert order_ids[2] not in candidates["all_order_ids"]


def _order_procurement(client, headers, order_id):
    response = client.get(f"/api/orders/{order_id}", headers=headers)
    assert response.status_code == 200
    return response.get_json()["data"]


def test_purchase_confirm_shortage_repurchase_and_ready_to_ship(app, client, admin_headers, shop_ids):
    order_ids = _seed_items(app, shop_ids["a"])
    with app.app_context():
        for order in Order.query.filter(Order.id.in_(order_ids)):
            order.fulfillment_status = "unfulfilled"
        db.session.commit()
    created = client.post(
        "/api/purchase-lists", headers=admin_headers,
        json={"shop_id": shop_ids["a"], "order_ids": order_ids},
    ).get_json()["data"]
    purchase_id = created["id"]
    assert created["status"] == "draft"
    assert _order_procurement(client, admin_headers, order_ids[0])["procurement_status"] == "not_started"

    confirm = client.post(f"/api/purchase-lists/{purchase_id}/confirm", headers=admin_headers)
    assert confirm.status_code == 200
    assert confirm.get_json()["data"]["status"] == "ordered"
    assert _order_procurement(client, admin_headers, order_ids[0])["procurement_status"] == "purchasing"
    purchasing = client.get(
        f"/api/purchase-candidates?shop_id={shop_ids['a']}&view=purchasing",
        headers=admin_headers,
    ).get_json()["data"]
    assert purchasing["category_counts"]["purchasing"] == 3
    assert purchasing["pagination"]["total"] == 3
    assert purchasing["all_order_ids"] == []
    purchased = client.get(
        f"/api/purchase-candidates?shop_id={shop_ids['a']}&view=purchased",
        headers=admin_headers,
    ).get_json()["data"]
    assert purchased["category_counts"]["purchased"] == 3
    assert purchased["pagination"]["total"] == 3
    assert client.post(f"/api/purchase-lists/{purchase_id}/confirm", headers=admin_headers).status_code == 400
    assert client.post(
        "/api/purchase-lists", headers=admin_headers,
        json={"shop_id": shop_ids["a"], "order_ids": order_ids},
    ).status_code == 400

    receipt = [{
        "group_key": item["group_key"],
        "received_quantity": 1 if item["shopify_variant_id"] == "blue" else 3,
        "damaged_quantity": 1 if item["shopify_variant_id"] == "blue" else 0,
    } for item in created["items"]]
    received = client.post(
        f"/api/purchase-lists/{purchase_id}/receive", headers=admin_headers,
        json={"items": receipt},
    )
    assert received.status_code == 200
    data = received.get_json()["data"]
    assert data["status"] == "received"
    assert data["allocations"]
    assert sum(row["received_quantity"] for row in data["allocations"]) == 4
    assert sum(item["missing_quantity"] for item in data["receipt"]) == 1
    with app.app_context():
        db.session.expire_all()
        stored = db.session.get(PurchaseList, purchase_id)
        assert stored.status == "received"
        assert stored.allocations == data["allocations"]
        assert order_ids[0] in [row["order_id"] for row in stored.allocations]
    assert _order_procurement(client, admin_headers, order_ids[2])["procurement_status"] == "ready_to_ship"
    # Shared stock completes the one-unit order instead of leaving both
    # originals partially supplied by their historical purchase allocations.
    assert _order_procurement(client, admin_headers, order_ids[0])["procurement_status"] == "ready_to_ship"
    assert _order_procurement(client, admin_headers, order_ids[1])["procurement_status"] == "shortage"
    replenish = client.get(
        f"/api/purchase-candidates?shop_id={shop_ids['a']}&view=replenish",
        headers=admin_headers,
    ).get_json()["data"]
    # Procurement provenance stays on the partially supplied owner; physical
    # readiness above is still shared. The aggregate shortage remains 2 units.
    assert replenish["category_counts"]["replenish"] == 2
    assert replenish["category_counts"]["stocked"] == 1
    assert replenish["category_counts"]["purchased"] == 3
    assert set(replenish["all_order_ids"]) == {order_ids[0], order_ids[1]}
    assert client.post(
        f"/api/purchase-lists/{purchase_id}/receive", headers=admin_headers,
        json={"items": receipt},
    ).status_code == 400

    retry = client.post(
        "/api/purchase-lists", headers=admin_headers,
        json={"shop_id": shop_ids["a"], "order_ids": order_ids},
    )
    assert retry.status_code == 201
    retry_data = retry.get_json()["data"]
    assert retry_data["total_quantity"] == 2
    assert client.post(f"/api/purchase-lists/{retry_data['id']}/confirm", headers=admin_headers).status_code == 200
    second_receipt = [{
        "group_key": item["group_key"], "received_quantity": item["quantity"], "damaged_quantity": 0,
    } for item in retry_data["items"]]
    assert client.post(
        f"/api/purchase-lists/{retry_data['id']}/receive", headers=admin_headers,
        json={"items": second_receipt},
    ).status_code == 200
    assert all(_order_procurement(client, admin_headers, order_id)["procurement_status"] == "ready_to_ship"
               for order_id in order_ids)
    with app.app_context():
        order = db.session.get(Order, order_ids[0])
        order.financial_status = "pending"
        db.session.commit()
    assert _order_procurement(client, admin_headers, order_ids[0])["procurement_status"] != "ready_to_ship"


def test_purchase_receipt_rejects_overcount_and_preserves_ordered_state(app, client, admin_headers, shop_ids):
    order_ids = _seed_items(app, shop_ids["a"])
    purchase = client.post(
        "/api/purchase-lists", headers=admin_headers,
        json={"shop_id": shop_ids["a"], "order_ids": order_ids},
    ).get_json()["data"]
    purchase_id = purchase["id"]
    assert client.post(f"/api/purchase-lists/{purchase_id}/confirm", headers=admin_headers).status_code == 200
    invalid = [{
        "group_key": item["group_key"], "received_quantity": item["quantity"], "damaged_quantity": 1,
    } for item in purchase["items"]]
    assert client.post(
        f"/api/purchase-lists/{purchase_id}/receive", headers=admin_headers,
        json={"items": invalid},
    ).status_code == 400
    saved = client.get(f"/api/purchase-lists/{purchase_id}", headers=admin_headers).get_json()["data"]
    assert saved["status"] == "ordered"
    assert saved["receipt"] == []


def test_partially_paid_order_can_be_purchased_but_not_shipped(app, client, admin_headers, shop_ids):
    order_id = _seed_items(app, shop_ids["a"])[0]
    with app.app_context():
        db.session.get(Order, order_id).financial_status = "partially_paid"
        # Isolate payment eligibility from the separate whole-order priority
        # policy; competing paid orders have dedicated pool allocation tests.
        for other in Order.query.filter(Order.shop_id == shop_ids['a'], Order.id != order_id).all():
            other.cancelled_at = datetime.utcnow()
        db.session.commit()
    candidates = client.get(
        f"/api/purchase-candidates?shop_id={shop_ids['a']}", headers=admin_headers,
    ).get_json()["data"]
    assert order_id in candidates["all_order_ids"]
    created = client.post(
        "/api/purchase-lists", headers=admin_headers,
        json={"shop_id": shop_ids["a"], "order_ids": [order_id]},
    )
    assert created.status_code == 201
    purchase = created.get_json()["data"]
    assert client.post(f"/api/purchase-lists/{purchase['id']}/confirm", headers=admin_headers).status_code == 200
    receipt = [{"group_key": item["group_key"], "received_quantity": item["quantity"], "damaged_quantity": 0}
               for item in purchase["items"]]
    assert client.post(f"/api/purchase-lists/{purchase['id']}/receive", headers=admin_headers,
                       json={"items": receipt}).status_code == 200
    assert _order_procurement(client, admin_headers, order_id)["procurement_status"] == "stocked"


def test_cancelled_paid_order_is_not_purchase_eligible(app, client, admin_headers, shop_ids):
    order_id = _seed_items(app, shop_ids["a"])[0]
    with app.app_context():
        db.session.get(Order, order_id).cancelled_at = datetime.utcnow()
        db.session.commit()
    candidates = client.get(
        f"/api/purchase-candidates?shop_id={shop_ids['a']}", headers=admin_headers,
    ).get_json()["data"]
    assert order_id not in candidates["all_order_ids"]
    assert client.post(
        "/api/purchase-lists", headers=admin_headers,
        json={"shop_id": shop_ids["a"], "order_ids": [order_id]},
    ).status_code == 400


def test_purchase_can_receive_multiple_batches_without_double_count(app, client, admin_headers, shop_ids):
    order_ids = _seed_items(app, shop_ids["a"])
    created = client.post(
        "/api/purchase-lists", headers=admin_headers,
        json={"shop_id": shop_ids["a"], "order_ids": order_ids},
    ).get_json()["data"]
    purchase_id = created["id"]
    assert client.post(f"/api/purchase-lists/{purchase_id}/confirm", headers=admin_headers).status_code == 200
    blue = next(item for item in created["items"] if item["shopify_variant_id"] == "blue")
    first = client.post(
        f"/api/purchase-lists/{purchase_id}/receive", headers=admin_headers,
        json={"finalize": False, "batch_id": "batch-one", "items": [{"group_key": blue["group_key"],
              "received_quantity": 1, "damaged_quantity": 0}]},
    )
    assert first.status_code == 200
    data = first.get_json()["data"]
    assert data["status"] == "partially_received"
    assert len(data["receipt_batches"]) == 1
    assert sum(row["received_quantity"] for row in data["receipt"]) == 1
    replay = client.post(
        f"/api/purchase-lists/{purchase_id}/receive", headers=admin_headers,
        json={"finalize": False, "batch_id": "batch-one", "items": [{"group_key": blue["group_key"],
              "received_quantity": 1, "damaged_quantity": 0}]},
    )
    assert replay.status_code == 200
    assert len(replay.get_json()["data"]["receipt_batches"]) == 1
    assert _order_procurement(client, admin_headers, order_ids[0])["procurement_stocked_quantity"] == 1
    assert _order_procurement(client, admin_headers, order_ids[1])["procurement_stocked_quantity"] == 1
    assert _order_procurement(client, admin_headers, order_ids[1])["procurement_stocked_quantity"] == 1
    duplicate = client.post(
        f"/api/purchase-lists/{purchase_id}/receive", headers=admin_headers,
        json={"finalize": False, "batch_id": "batch-two", "items": [{"group_key": blue["group_key"],
              "received_quantity": 3, "damaged_quantity": 0}]},
    )
    assert duplicate.status_code == 400
    second = client.post(
        f"/api/purchase-lists/{purchase_id}/receive", headers=admin_headers,
        json={"finalize": True, "items": [{"group_key": blue["group_key"],
              "received_quantity": 1, "damaged_quantity": 1}]},
    )
    assert second.status_code == 200
    data = second.get_json()["data"]
    assert data["status"] == "received"
    assert len(data["receipt_batches"]) == 2
    assert sum(row["received_quantity"] for row in data["receipt"]) == 2
    assert sum(row["damaged_quantity"] for row in data["receipt"]) == 1
    assert sum(row["received_quantity"] for row in data["allocations"]) == 2
    assert client.post(f"/api/purchase-lists/{purchase_id}/receive", headers=admin_headers,
                       json={"items": [{"group_key": blue["group_key"],
                                        "received_quantity": 1, "damaged_quantity": 0}]}).status_code == 400


def test_purchase_mutations_are_shop_scoped(app, client, admin_headers, sub_a_headers, shop_ids):
    order_ids = _seed_items(app, shop_ids["b"])
    purchase = client.post(
        "/api/purchase-lists", headers=admin_headers,
        json={"shop_id": shop_ids["b"], "order_ids": order_ids},
    ).get_json()["data"]
    purchase_id = purchase["id"]
    assert client.post(f"/api/purchase-lists/{purchase_id}/confirm", headers=sub_a_headers).status_code == 403
    assert client.post(
        f"/api/purchase-lists/{purchase_id}/receive", headers=sub_a_headers,
        json={"items": []},
    ).status_code == 403
    assert client.delete(f"/api/purchase-lists/{purchase_id}", headers=sub_a_headers).status_code == 403
    assert client.get(f"/api/purchase-lists/{purchase_id}", headers=admin_headers).get_json()["data"]["status"] == "draft"


def test_legacy_draft_can_be_confirmed_from_its_saved_snapshot(app, client, admin_headers, shop_ids):
    order_ids = _seed_items(app, shop_ids["a"])
    purchase = client.post(
        "/api/purchase-lists", headers=admin_headers,
        json={"shop_id": shop_ids["a"], "order_ids": order_ids},
    ).get_json()["data"]
    with app.app_context():
        saved = db.session.get(PurchaseList, purchase["id"])
        saved.allocations = []
        db.session.commit()
    response = client.post(f"/api/purchase-lists/{purchase['id']}/confirm", headers=admin_headers)
    assert response.status_code == 200
    assert sum(row["quantity"] for row in response.get_json()["data"]["allocations"]) == purchase["total_quantity"]


def _seed_items(app, shop_id):
    with app.app_context():
        orders = Order.query.filter_by(shop_id=shop_id).order_by(Order.order_number).all()
        for index, order in enumerate(orders):
            order.fulfillment_status = "unfulfilled"
            db.session.add(
                OrderItem(
                    shop_id=shop_id,
                    order_id=order.id,
                    shopify_line_item_id=f"line-{index}",
                    shopify_product_id="product-1",
                    shopify_variant_id="blue" if index < 2 else "red",
                    title="Phone Case",
                    variant_title="Blue" if index < 2 else "Red",
                    sku="CASE-BLUE" if index < 2 else "CASE-RED",
                    image_url="https://example.com/blue.jpg" if index == 0 else "https://example.com/red.jpg",
                    quantity=index + 1,
                )
            )
        db.session.commit()
        return [order.id for order in orders]


def test_explicit_shortage_reopens_demand_while_other_goods_await_delivery(app, client, admin_headers, shop_ids):
    from app.services.inventory_pool_service import build_pool_plan
    order_ids = _seed_items(app, shop_ids['a'])
    created = client.post('/api/purchase-lists', headers=admin_headers,
                          json={'shop_id': shop_ids['a'], 'order_ids': order_ids}).get_json()['data']
    purchase_id = created['id']
    assert client.post(f'/api/purchase-lists/{purchase_id}/confirm', headers=admin_headers).status_code == 200
    blue = next(item for item in created['items'] if item['shopify_variant_id'] == 'blue')
    red = next(item for item in created['items'] if item['shopify_variant_id'] == 'red')
    payload = {'finalize': False, 'batch_id': 'explicit-shortage', 'items': [
        {'group_key': blue['group_key'], 'received_quantity': 1, 'damaged_quantity': 0, 'missing_quantity': 1}]}
    response = client.post(f'/api/purchase-lists/{purchase_id}/receive', headers=admin_headers, json=payload)
    assert response.status_code == 200, response.get_json()
    receipt = next(row for row in response.get_json()['data']['receipt'] if row['group_key'] == blue['group_key'])
    assert receipt['received_quantity'] == receipt['confirmed_missing_quantity'] == receipt['pending_quantity'] == 1
    assert response.get_json()['data']['status'] == 'partially_received'
    with app.app_context():
        pool = build_pool_plan(shop_ids['a'])
        assert pool['rows'][blue['group_key']]['on_hand_quantity'] == 1
        assert pool['rows'][blue['group_key']]['incoming_quantity'] == 1
        assert pool['rows'][blue['group_key']]['purchase_needed_quantity'] == 1
        assert pool['rows'][red['group_key']]['incoming_quantity'] == 3
        assert pool['rows'][red['group_key']]['purchase_needed_quantity'] == 0
    # The same batch cannot count twice or change the confirmed shortage.
    assert client.post(f'/api/purchase-lists/{purchase_id}/receive', headers=admin_headers, json=payload).status_code == 200
    payload['items'][0]['missing_quantity'] = 0
    assert client.post(f'/api/purchase-lists/{purchase_id}/receive', headers=admin_headers, json=payload).status_code == 400
    # A new purchase includes exactly the released shortage, not the delivered or pending goods.
    replacement = client.post('/api/purchase-lists', headers=admin_headers,
                              json={'shop_id': shop_ids['a'], 'order_ids': order_ids})
    assert replacement.status_code == 201, replacement.get_json()
    assert replacement.get_json()['data']['total_quantity'] == 1
    replacement_id = replacement.get_json()['data']['id']
    assert client.post(f'/api/purchase-lists/{replacement_id}/confirm', headers=admin_headers).status_code == 200
    # Only the one still-pending blue unit may arrive on the original list.
    second = {'finalize': False, 'batch_id': 'second', 'items': [
        {'group_key': blue['group_key'], 'received_quantity': 2, 'damaged_quantity': 0}]}
    assert client.post(f'/api/purchase-lists/{purchase_id}/receive', headers=admin_headers, json=second).status_code == 400
    second['items'][0]['received_quantity'] = 1
    assert client.post(f'/api/purchase-lists/{purchase_id}/receive', headers=admin_headers, json=second).status_code == 200
    # Finalizing a different row also marks omitted pending variants missing.
    final = client.post(f'/api/purchase-lists/{purchase_id}/receive', headers=admin_headers,
                        json={'finalize': True, 'batch_id': 'last', 'items': [
                            {'group_key': blue['group_key'], 'received_quantity': 0, 'damaged_quantity': 0}]})
    assert final.status_code == 200, final.get_json()
    assert sum(row['confirmed_missing_quantity'] for row in final.get_json()['data']['receipt']) == 4
    assert sum(row['confirmed_missing_quantity'] for row in final.get_json()['data']['allocations']) == 4
    assert all(row['pending_quantity'] == 0 for row in final.get_json()['data']['receipt'])


def test_all_missing_batch_creates_no_stock_and_rejects_invalid_counts(app, client, admin_headers, shop_ids):
    from app.services.inventory_pool_service import build_pool_plan
    ids = _seed_items(app, shop_ids['a'])
    purchase = client.post('/api/purchase-lists', headers=admin_headers,
                           json={'shop_id': shop_ids['a'], 'order_ids': ids}).get_json()['data']
    pid = purchase['id']
    assert client.post(f'/api/purchase-lists/{pid}/confirm', headers=admin_headers).status_code == 200
    item = purchase['items'][0]
    payload = {'finalize': False, 'batch_id': 'missing-only', 'items': [
        {'group_key': item['group_key'], 'received_quantity': 0, 'damaged_quantity': 0, 'missing_quantity': 0}]}
    for invalid in (-1, True, 1.5, None, item['quantity'] + 1):
        payload['items'][0]['missing_quantity'] = invalid
        assert client.post(f'/api/purchase-lists/{pid}/receive', headers=admin_headers, json=payload).status_code == 400
        with app.app_context():
            assert db.session.get(PurchaseList, pid).receipt == []
    payload['items'][0]['missing_quantity'] = item['quantity']
    assert client.post(f'/api/purchase-lists/{pid}/receive', headers=admin_headers, json=payload).status_code == 200
    with app.app_context():
        row = build_pool_plan(shop_ids['a'])['rows'][item['group_key']]
        assert row['on_hand_quantity'] == row['incoming_quantity'] == 0
        assert row['purchase_needed_quantity'] == item['quantity']


def test_mixed_after_sale_shortage_returns_to_shared_pending_queue(app, client, admin_headers, shop_ids):
    order_ids = _seed_items(app, shop_ids['a'])
    with app.app_context():
        source = OrderItem.query.filter_by(order_id=order_ids[2]).one()
        source_id = source.id
        db.session.get(Order, order_ids[2]).warehouse_status = 'shipped'
        db.session.commit()
    sale = client.post('/api/after-sales', headers=admin_headers,
                       json={'order_id': order_ids[2], 'kind': 'missing', 'reason': '漏发',
                             'items': [{'source_order_item_id': source_id, 'quantity': 1}]}).get_json()['data']
    created = client.post('/api/purchase-lists', headers=admin_headers,
                          json={'shop_id': shop_ids['a'], 'order_ids': order_ids[:2],
                                'after_sale_ids': [sale['id']]}).get_json()['data']
    pid = created['id']
    assert client.post(f'/api/purchase-lists/{pid}/confirm', headers=admin_headers).status_code == 200
    red = next(item for item in created['items'] if item['shopify_variant_id'] == 'red')
    response = client.post(f'/api/purchase-lists/{pid}/receive', headers=admin_headers,
                           json={'finalize': False, 'batch_id': 'cf-missing', 'items': [
                               {'group_key': red['group_key'], 'received_quantity': 0,
                                'damaged_quantity': 0, 'missing_quantity': 1}]})
    assert response.status_code == 200, response.get_json()
    candidates = client.get(f"/api/purchase-candidates?shop_id={shop_ids['a']}&view=needs_purchase",
                            headers=admin_headers).get_json()['data']
    assert sale['id'] in candidates['all_after_sale_ids']
    supplement = client.post('/api/purchase-lists', headers=admin_headers,
                             json={'shop_id': shop_ids['a'], 'after_sale_ids': [sale['id']], 'order_ids': []})
    assert supplement.status_code == 201, supplement.get_json()
    assert supplement.get_json()['data']['total_quantity'] == 1
    with app.app_context():
        assert db.session.get(Order, order_ids[2]).warehouse_status == 'shipped'


def test_create_purchase_list_groups_variants_and_saves_snapshot(
    app, client, admin_headers, shop_ids
):
    order_ids = _seed_items(app, shop_ids["a"])
    response = client.post(
        "/api/purchase-lists",
        headers=admin_headers,
        json={"shop_id": shop_ids["a"], "name": "今日采购", "order_ids": order_ids},
    )
    assert response.status_code == 201
    data = response.get_json()["data"]
    assert data["order_count"] == 3
    assert data["total_quantity"] == 6
    assert len(data["items"]) == 2
    assert data["product_count"] == 1
    assert len(data["products"]) == 1
    product = data["products"][0]
    assert product["title"] == "Phone Case"
    assert product["quantity"] == 6
    assert product["variant_count"] == 2
    assert {variant["variant_title"] for variant in product["variants"]} == {"Blue", "Red"}
    assert product["color_count"] == 2
    assert [(color["name"], color["quantity"]) for color in product["colors"]] == [
        ("Blue", 3),
        ("Red", 3),
    ]
    blue = next(item for item in data["items"] if item["shopify_variant_id"] == "blue")
    assert blue["quantity"] == 3
    assert blue["image_url"] == "https://example.com/blue.jpg"
    assert blue["order_numbers"] == ["#A0", "#A1"]

    with app.app_context():
        order = db.session.get(Order, order_ids[0])
        order.items[0].quantity = 99
        db.session.commit()
    saved = client.get(f"/api/purchase-lists/{data['id']}", headers=admin_headers).get_json()["data"]
    assert saved["total_quantity"] == 6
    assert saved["items"] == data["items"]
    assert saved["products"] == data["products"]


def test_purchase_list_keeps_different_products_separate(app, client, admin_headers, shop_ids):
    order_ids = _seed_items(app, shop_ids["a"])
    with app.app_context():
        db.session.add(
            OrderItem(
                shop_id=shop_ids["a"],
                order_id=order_ids[0],
                shopify_line_item_id="different-product",
                shopify_product_id="product-2",
                shopify_variant_id="other-blue",
                title="Tablet Case",
                variant_title="Blue",
                image_url="https://example.com/tablet.jpg",
                quantity=4,
            )
        )
        db.session.commit()
    response = client.post(
        "/api/purchase-lists",
        headers=admin_headers,
        json={"shop_id": shop_ids["a"], "order_ids": order_ids},
    )
    assert response.status_code == 201
    data = response.get_json()["data"]
    assert data["product_count"] == 2
    assert data["total_quantity"] == 10
    assert [(product["title"], product["quantity"]) for product in data["products"]] == [
        ("Phone Case", 6),
        ("Tablet Case", 4),
    ]


def test_purchase_list_groups_color_before_device_variant(app, client, admin_headers, shop_ids):
    order_ids = _seed_items(app, shop_ids["a"])
    with app.app_context():
        db.session.add_all(
            [
                OrderItem(
                    shop_id=shop_ids["a"],
                    order_id=order_ids[0],
                    shopify_line_item_id="purple-17",
                    shopify_product_id="product-2",
                    shopify_variant_id="purple-17",
                    title="Gradient Case",
                    variant_title="Purple / iPhone 17 Pro",
                    quantity=2,
                ),
                OrderItem(
                    shop_id=shop_ids["a"],
                    order_id=order_ids[1],
                    shopify_line_item_id="purple-18",
                    shopify_product_id="product-2",
                    shopify_variant_id="purple-18",
                    title="Gradient Case",
                    variant_title="iPhone 18 Pro / Purple + Wrist Chain",
                    quantity=3,
                ),
                OrderItem(
                    shop_id=shop_ids["a"],
                    order_id=order_ids[2],
                    shopify_line_item_id="no-color",
                    shopify_product_id="product-2",
                    shopify_variant_id="no-color",
                    title="Gradient Case",
                    variant_title="iPhone 17 / Glossy Shockproof",
                    quantity=1,
                ),
            ]
        )
        db.session.commit()
    response = client.post(
        "/api/purchase-lists",
        headers=admin_headers,
        json={"shop_id": shop_ids["a"], "order_ids": order_ids},
    )
    assert response.status_code == 201
    product = next(p for p in response.get_json()["data"]["products"] if p["title"] == "Gradient Case")
    assert product["quantity"] == 6
    assert product["color_count"] == 3
    assert [(color["name"], color["quantity"], color["variant_count"]) for color in product["colors"]] == [
        ("Purple", 2, 1),
        ("Purple + Wrist Chain", 3, 1),
        ("未标注颜色", 1, 1),
    ]
    assert [item["variant_title"] for item in product["colors"][0]["variants"]] == [
        "Purple / iPhone 17 Pro",
    ]
    assert [item["model_title"] for item in product["colors"][0]["variants"]] == [
        "iPhone 17 Pro",
    ]
    assert product["colors"][1]["variants"][0]["model_title"] == "iPhone 18 Pro"
    assert product["colors"][2]["variants"][0]["model_title"] == "iPhone 17 · SKU: 未设置"
    saved = client.get(
        f"/api/purchase-lists/{response.get_json()['data']['id']}", headers=admin_headers
    ).get_json()["data"]
    assert saved["products"] == response.get_json()["data"]["products"]


def test_uncolored_products_and_groups_are_last_with_stable_order():
    products = _group_products([
        {'shopify_product_id': 'unknown', 'title': 'Unknown', 'variant_title': 'iPhone 17', 'quantity': 2},
        {'shopify_product_id': 'blue', 'title': 'Blue', 'variant_title': 'Blue / iPhone 17', 'quantity': 1},
        {'shopify_product_id': 'bundle', 'title': 'Unknown bundle', 'variant_title': 'iPhone 18 Pro / Wrist Chain', 'quantity': 3},
        {'shopify_product_id': 'red', 'title': 'Red', 'variant_title': 'Red / iPhone 17', 'quantity': 1},
        {'shopify_product_id': 'blue', 'title': 'Blue', 'variant_title': 'iPhone 18', 'quantity': 1},
    ])
    assert [p['title'] for p in products] == ['Red', 'Unknown', 'Blue', 'Unknown bundle']
    mixed = next(p for p in products if p['title'] == 'Blue')
    assert [c['name'] for c in mixed['colors']] == ['Blue', '未标注颜色']
    assert sum(p['quantity'] for p in products) == 8


def test_uncolored_purchase_variants_show_sku_without_changing_identity():
    colors = _group_colors({"variants": [
        {"group_key": "14", "variant_title": "iPhone 14 Pro", "sku": " CAT-14P ",
         "quantity": 2, "order_numbers": []},
        {"group_key": "17", "variant_title": "iPhone 17 Pro", "sku": "",
         "quantity": 1, "order_numbers": []},
    ]})
    assert colors[0]["name"] == "未标注颜色"
    assert colors[0]["quantity"] == 3
    assert [(v["group_key"], v["model_title"]) for v in colors[0]["variants"]] == [
        ("17", "iPhone 17 Pro · SKU: 未设置"), ("14", "iPhone 14 Pro · SKU: CAT-14P"),
    ]


def test_purchase_bundle_keeps_own_configuration_image_and_quantity():
    product = {"variants": [
        {"group_key": "plain", "variant_title": "iPhone 18 Pro Max / Pink Lime",
         "quantity": 2, "image_url": "plain.webp", "order_numbers": ["12979", "12907"]},
        {"group_key": "bundle", "variant_title": "iPhone 18 Pro Max / Pink Lime + Wrist Chain",
         "quantity": 2, "image_url": "bundle.webp", "order_numbers": ["13047", "13028"]},
        {"group_key": "17", "variant_title": "iPhone 17 Pro / Pink Lime",
         "quantity": 1, "image_url": "plain.webp", "order_numbers": ["13052"]},
    ]}
    colors = _group_colors(product)
    assert [(c["name"], c["quantity"], c["image_url"]) for c in colors] == [
        ("Pink Lime", 3, "plain.webp"), ("Pink Lime + Wrist Chain", 2, "bundle.webp"),
    ]
    assert [v["group_key"] for v in colors[0]["variants"]] == ["plain", "17"]
    assert colors[1]["variants"][0]["model_title"] == "iPhone 18 Pro Max"
    assert colors[1]["variants"][0]["order_numbers"] == ["13047", "13028"]


def test_phone_models_sort_newest_and_higher_tier_first_within_color():
    titles = [
        "Purple / iPhone 17",
        "Purple / iPhone 17 Pro",
        "Purple / iPhone 18 Pro",
        "Purple / iPhone 17 Pro Max",
        "Purple / iPhone 18 Pro Max",
        "Purple / Universal",
    ]
    product = {
        "variants": [
            {"variant_title": title, "quantity": 1, "image_url": "", "order_numbers": []}
            for title in titles
        ]
    }
    colors = _group_colors(product)
    assert len(colors) == 1
    assert [item["variant_title"] for item in colors[0]["variants"]] == [
        "Purple / iPhone 18 Pro Max",
        "Purple / iPhone 18 Pro",
        "Purple / iPhone 17 Pro Max",
        "Purple / iPhone 17 Pro",
        "Purple / iPhone 17",
        "Purple / Universal",
    ]
    assert [item["model_title"] for item in colors[0]["variants"]] == [
        "iPhone 18 Pro Max",
        "iPhone 18 Pro",
        "iPhone 17 Pro Max",
        "iPhone 17 Pro",
        "iPhone 17",
        "Universal",
    ]


def test_purchase_list_rejects_cross_shop_orders(app, client, admin_headers, shop_ids):
    a_ids = _seed_items(app, shop_ids["a"])
    with app.app_context():
        b_id = Order.query.filter_by(shop_id=shop_ids["b"]).first().id
    response = client.post(
        "/api/purchase-lists",
        headers=admin_headers,
        json={"shop_id": shop_ids["a"], "order_ids": [a_ids[0], b_id]},
    )
    assert response.status_code == 403
    with app.app_context():
        assert PurchaseList.query.count() == 0


def test_purchase_list_is_shop_scoped(app, client, admin_headers, sub_a_headers, shop_ids):
    order_ids = _seed_items(app, shop_ids["a"])
    created = client.post(
        "/api/purchase-lists",
        headers=admin_headers,
        json={"shop_id": shop_ids["a"], "order_ids": [order_ids[0]]},
    ).get_json()["data"]
    assert client.get("/api/purchase-lists", headers=sub_a_headers).get_json()["data"]["pagination"]["total"] == 1
    assert client.get(f"/api/purchase-lists/{created['id']}", headers=sub_a_headers).status_code == 200
    assert client.get(
        f"/api/purchase-lists?shop_id={shop_ids['b']}", headers=sub_a_headers
    ).get_json()["data"]["pagination"]["total"] == 0
    assert client.post(
        "/api/purchase-lists",
        headers=sub_a_headers,
        json={"shop_id": shop_ids["b"], "order_ids": [order_ids[0]]},
    ).status_code == 403


def test_purchase_list_requires_items(app, client, admin_headers, shop_ids):
    with app.app_context():
        order_id = Order.query.filter_by(shop_id=shop_ids["b"]).first().id
    response = client.post(
        "/api/purchase-lists",
        headers=admin_headers,
        json={"shop_id": shop_ids["b"], "order_ids": [order_id]},
    )
    assert response.status_code == 400


def test_candidates_exclude_shipped_unpaid_and_already_procured_orders(
    app, client, admin_headers, shop_ids,
):
    order_ids = _seed_items(app, shop_ids["a"])
    with app.app_context():
        db.session.get(Order, order_ids[1]).warehouse_status = "shipped"
        db.session.get(Order, order_ids[2]).financial_status = "refunded"
        db.session.commit()
    url = f"/api/purchase-candidates?shop_id={shop_ids['a']}&per_page=1"
    first = client.get(url, headers=admin_headers).get_json()["data"]
    assert first["all_order_ids"] == [order_ids[0]]
    assert first["pagination"]["total"] == 1
    assert first["orders"][0]["procurement_needed_quantity"] == 1

    assert client.post(
        "/api/purchase-lists", headers=admin_headers,
        json={"shop_id": shop_ids["a"], "order_ids": [order_ids[1]]},
    ).status_code == 400
    assert client.post(
        "/api/purchase-lists", headers=admin_headers,
        json={"shop_id": shop_ids["a"], "order_ids": [order_ids[2]]},
    ).status_code == 400
    created = client.post(
        "/api/purchase-lists", headers=admin_headers,
        json={"shop_id": shop_ids["a"], "order_ids": [order_ids[0]]},
    ).get_json()["data"]
    assert client.post(f"/api/purchase-lists/{created['id']}/confirm", headers=admin_headers).status_code == 200
    assert client.get(url, headers=admin_headers).get_json()["data"]["all_order_ids"] == []


def test_all_candidate_ids_include_other_pages_and_only_current_shop(
    app, client, admin_headers, sub_a_headers, shop_ids,
):
    order_ids = _seed_items(app, shop_ids["a"])
    _seed_items(app, shop_ids["b"])
    result = client.get(
        f"/api/purchase-candidates?shop_id={shop_ids['a']}&per_page=1",
        headers=admin_headers,
    ).get_json()["data"]
    assert result["pagination"]["total"] == 3
    assert len(result["orders"]) == 1
    assert set(result["all_order_ids"]) == set(order_ids)
    assert client.get(
        f"/api/purchase-candidates?shop_id={shop_ids['b']}",
        headers=sub_a_headers,
    ).status_code == 403


def test_confirm_keeps_order_shipped_after_draft_for_extra_stock(app, client, admin_headers, shop_ids):
    order_id = _seed_items(app, shop_ids["a"])[0]
    created = client.post(
        "/api/purchase-lists", headers=admin_headers,
        json={"shop_id": shop_ids["a"], "order_ids": [order_id]},
    ).get_json()["data"]
    with app.app_context():
        db.session.get(Order, order_id).warehouse_status = "shipped"
        db.session.commit()
    response = client.post(
        f"/api/purchase-lists/{created['id']}/confirm", headers=admin_headers,
    )
    assert response.status_code == 200, response.get_json()
    data = response.get_json()["data"]
    assert data["status"] == "ordered"
    assert data["order_ids"] == [order_id]
    with app.app_context():
        assert db.session.get(PurchaseList, created["id"]).status == "ordered"


def test_delete_draft_and_cancel_ordered_release_pending_but_keep_received(
    app, client, admin_headers, sub_a_headers, shop_ids,
):
    order_id = _seed_items(app, shop_ids["a"])[0]
    payload = {"shop_id": shop_ids["a"], "order_ids": [order_id]}
    draft = client.post("/api/purchase-lists", headers=admin_headers, json=payload).get_json()["data"]
    assert client.delete(f"/api/purchase-lists/{draft['id']}", headers=sub_a_headers).status_code == 200
    assert client.get(f"/api/purchase-lists/{draft['id']}", headers=admin_headers).status_code == 404

    ordered = client.post("/api/purchase-lists", headers=admin_headers, json=payload).get_json()["data"]
    assert client.post(f"/api/purchase-lists/{ordered['id']}/confirm", headers=admin_headers).status_code == 200
    assert client.delete(f"/api/purchase-lists/{ordered['id']}", headers=admin_headers).status_code == 200
    with app.app_context():
        assert db.session.get(PurchaseList, ordered["id"]).status == "cancelled"
    candidates = client.get(
        f"/api/purchase-candidates?shop_id={shop_ids['a']}", headers=admin_headers,
    ).get_json()["data"]
    assert order_id in candidates["all_order_ids"]
    listed = client.get("/api/purchase-lists", headers=admin_headers).get_json()["data"]
    assert listed["pagination"]["total"] == 0

    received = client.post("/api/purchase-lists", headers=admin_headers, json=payload).get_json()["data"]
    assert client.post(f"/api/purchase-lists/{received['id']}/confirm", headers=admin_headers).status_code == 200
    receipt = [
        {"group_key": item["group_key"], "received_quantity": item["quantity"], "damaged_quantity": 0}
        for item in received["items"]
    ]
    assert client.post(
        f"/api/purchase-lists/{received['id']}/receive", headers=admin_headers,
        json={"items": receipt},
    ).status_code == 200
    assert client.delete(f"/api/purchase-lists/{received['id']}", headers=admin_headers).status_code == 400


def test_order_detail_can_look_up_its_purchase_lists(app, client, admin_headers, shop_ids):
    """订单详情要能反查到它进了哪份采购清单（交叉跳转用）。"""
    order_ids = _seed_items(app, shop_ids["a"])
    created = client.post(
        "/api/purchase-lists", headers=admin_headers,
        json={"shop_id": shop_ids["a"], "order_ids": order_ids},
    ).get_json()["data"]

    body = client.get(
        f"/api/orders/{order_ids[0]}/purchase-lists", headers=admin_headers
    ).get_json()
    assert [row["id"] for row in body["data"]] == [created["id"]]
    assert body["data"][0]["name"] == created["name"]

    # 没进过清单的订单返回空数组，而不是报错
    with app.app_context():
        other = Order.query.filter_by(shop_id=shop_ids["b"]).first()
        other_id = other.id
    empty = client.get(
        f"/api/orders/{other_id}/purchase-lists", headers=admin_headers
    ).get_json()
    assert empty["data"] == []


def test_purchase_detail_lists_linked_orders(app, client, admin_headers, shop_ids):
    """采购详情要带回关联订单（id + 订单号），供跳转到订单详情。"""
    order_ids = _seed_items(app, shop_ids["a"])
    created = client.post(
        "/api/purchase-lists", headers=admin_headers,
        json={"shop_id": shop_ids["a"], "order_ids": order_ids},
    ).get_json()["data"]

    detail = client.get(
        f"/api/purchase-lists/{created['id']}", headers=admin_headers
    ).get_json()["data"]

    linked = detail["orders"]
    assert sorted(item["id"] for item in linked) == sorted(order_ids)
    assert all(item["order_number"] for item in linked)
    assert set(detail["order_ids"]) == set(order_ids)


def test_purchase_list_summary_exposes_received_damaged_shortage_and_pending(app, client, admin_headers, shop_ids):
    order_ids = _seed_items(app, shop_ids["a"])
    created = client.post(
        "/api/purchase-lists", headers=admin_headers,
        json={"shop_id": shop_ids["a"], "order_ids": order_ids},
    ).get_json()["data"]
    purchase_id = created["id"]
    assert client.post(f"/api/purchase-lists/{purchase_id}/confirm", headers=admin_headers).status_code == 200
    blue = next(item for item in created["items"] if item["shopify_variant_id"] == "blue")
    red = next(item for item in created["items"] if item["shopify_variant_id"] == "red")
    received = client.post(
        f"/api/purchase-lists/{purchase_id}/receive", headers=admin_headers,
        json={"items": [
            {"group_key": blue["group_key"], "received_quantity": 1, "damaged_quantity": 1,
             "missing_quantity": 1},
            {"group_key": red["group_key"], "received_quantity": 2, "damaged_quantity": 0,
             "missing_quantity": 1},
        ]},
    )
    assert received.status_code == 200, received.get_json()
    detail = received.get_json()["data"]
    assert detail["status"] == "received"
    assert detail["received_quantity"] == 3
    assert detail["damaged_quantity"] == 1
    assert detail["shortage_quantity"] == 2
    assert detail["pending_quantity"] == 0

    listed = client.get(f"/api/purchase-lists?shop_id={shop_ids['a']}", headers=admin_headers).get_json()["data"]
    row = next(item for item in listed["purchase_lists"] if item["id"] == purchase_id)
    assert row["received_quantity"] == 3
    assert row["damaged_quantity"] == 1
    assert row["shortage_quantity"] == 2
    assert row["pending_quantity"] == 0


def test_replenishment_generates_one_draft_for_confirmed_shortages(app, client, admin_headers, shop_ids):
    order_ids = _seed_items(app, shop_ids["a"])
    created = client.post(
        "/api/purchase-lists", headers=admin_headers,
        json={"shop_id": shop_ids["a"], "order_ids": order_ids},
    ).get_json()["data"]
    purchase_id = created["id"]
    assert client.post(f"/api/purchase-lists/{purchase_id}/confirm", headers=admin_headers).status_code == 200
    items = []
    for item in created["items"]:
        good = item["quantity"] - 1
        items.append({"group_key": item["group_key"], "received_quantity": good,
                      "damaged_quantity": 0, "missing_quantity": 1})
    received = client.post(f"/api/purchase-lists/{purchase_id}/receive", headers=admin_headers,
                           json={"items": items})
    assert received.status_code == 200, received.get_json()
    assert received.get_json()["data"]["shortage_quantity"] == len(created["items"])

    first = client.post(f"/api/purchase-lists/{purchase_id}/replenish", headers=admin_headers)
    assert first.status_code == 201, first.get_json()
    replenishment = first.get_json()["data"]
    assert replenishment["status"] == "draft"
    assert replenishment["total_quantity"] == len(created["items"])
    assert all(row.get("source_purchase_id") == purchase_id for row in replenishment["allocations"])
    assert replenishment["name"].startswith("补采")
    detail = client.get(f"/api/purchase-lists/{purchase_id}", headers=admin_headers).get_json()["data"]
    assert detail["replenishment_ids"] == [replenishment["id"]]

    second = client.post(f"/api/purchase-lists/{purchase_id}/replenish", headers=admin_headers)
    assert second.status_code == 201
    assert second.get_json()["data"]["id"] == replenishment["id"]
    assert len([row for row in client.get(
        f"/api/purchase-lists?shop_id={shop_ids['a']}", headers=admin_headers,
    ).get_json()["data"]["purchase_lists"] if row["name"].startswith("补采")]) == 1

    clean = client.post(
        "/api/purchase-lists", headers=admin_headers,
        json={"shop_id": shop_ids["a"], "order_ids": order_ids},
    ).get_json()["data"]
    assert client.post(f"/api/purchase-lists/{clean['id']}/confirm", headers=admin_headers).status_code == 200
    full = [{"group_key": item["group_key"], "received_quantity": item["quantity"],
             "damaged_quantity": 0, "missing_quantity": 0} for item in clean["items"]]
    assert client.post(f"/api/purchase-lists/{clean['id']}/receive", headers=admin_headers,
                       json={"items": full}).status_code == 200
    assert client.post(f"/api/purchase-lists/{clean['id']}/replenish", headers=admin_headers).status_code == 400


def test_confirm_keeps_shipped_and_cancelled_orders_in_the_snapshot(app, client, admin_headers, shop_ids):
    order_ids = _seed_items(app, shop_ids["a"])
    created = client.post(
        "/api/purchase-lists", headers=admin_headers,
        json={"shop_id": shop_ids["a"], "order_ids": order_ids},
    ).get_json()["data"]
    with app.app_context():
        shipped = db.session.get(Order, order_ids[0])
        shipped.warehouse_status = "shipped"
        shipped.shipped_at = datetime.utcnow()
        cancelled = db.session.get(Order, order_ids[1])
        cancelled.cancelled_at = datetime.utcnow()
        db.session.commit()

    response = client.post(f"/api/purchase-lists/{created['id']}/confirm", headers=admin_headers)
    assert response.status_code == 200, response.get_json()
    data = response.get_json()["data"]
    assert data["status"] == "ordered"
    assert data["order_count"] == 3
    assert data["total_quantity"] == 6
    assert data["order_ids"] == order_ids
    assert "skipped_orders" not in data


def test_confirm_keeps_quantities_covered_by_another_purchase_list(app, client, admin_headers, shop_ids):
    order_ids = _seed_items(app, shop_ids["a"])
    stale = client.post(
        "/api/purchase-lists", headers=admin_headers,
        json={"shop_id": shop_ids["a"], "order_ids": order_ids},
    ).get_json()["data"]
    fresh = client.post(
        "/api/purchase-lists", headers=admin_headers,
        json={"shop_id": shop_ids["a"], "order_ids": [order_ids[0]]},
    ).get_json()["data"]
    assert client.post(f"/api/purchase-lists/{fresh['id']}/confirm", headers=admin_headers).status_code == 200

    response = client.post(f"/api/purchase-lists/{stale['id']}/confirm", headers=admin_headers)
    assert response.status_code == 200, response.get_json()
    data = response.get_json()["data"]
    assert data["status"] == "ordered"
    assert data["order_count"] == 3
    assert data["total_quantity"] == 6
    assert data["order_ids"] == order_ids
    assert "trimmed_orders" not in data


def test_confirm_keeps_shipped_after_sale_for_extra_stock(app, client, admin_headers, shop_ids):
    order_ids = _seed_items(app, shop_ids["a"])
    with app.app_context():
        source = OrderItem.query.filter_by(order_id=order_ids[2]).one()
        source_id = source.id
        db.session.get(Order, order_ids[2]).warehouse_status = "shipped"
        db.session.commit()
    sale = client.post("/api/after-sales", headers=admin_headers, json={
        "order_id": order_ids[2], "kind": "missing", "reason": "漏发",
        "items": [{"source_order_item_id": source_id, "quantity": 1}],
    }).get_json()["data"]
    created = client.post("/api/purchase-lists", headers=admin_headers, json={
        "shop_id": shop_ids["a"], "order_ids": order_ids[:2], "after_sale_ids": [sale["id"]],
    }).get_json()["data"]
    with app.app_context():
        from app.models import AfterSale
        record = db.session.get(AfterSale, sale["id"])
        record.status = "shipped"
        record.shipped_at = datetime.utcnow()
        db.session.commit()
    response = client.post(f"/api/purchase-lists/{created['id']}/confirm", headers=admin_headers)
    assert response.status_code == 200, response.get_json()
    data = response.get_json()["data"]
    assert data["status"] == "ordered"
    assert any(row.get("after_sale_id") == sale["id"] for row in data["allocations"])
    with app.app_context():
        from app.models import AfterSale
        record = db.session.get(AfterSale, sale["id"])
        assert record.status == "shipped"


def test_confirm_keeps_after_sale_quantity_when_demand_already_covered(app, client, admin_headers, shop_ids):
    """售后需求已被现货满足时，清单仍按原数量发起，多采购的到货后计入库存。"""
    from app.models import InventoryAdjustment
    order_ids = _seed_items(app, shop_ids["a"])
    with app.app_context():
        source = OrderItem.query.filter_by(order_id=order_ids[2]).one()
        source_id = source.id
        db.session.get(Order, order_ids[2]).warehouse_status = "shipped"
        db.session.commit()
    sale = client.post("/api/after-sales", headers=admin_headers, json={
        "order_id": order_ids[2], "kind": "missing", "reason": "漏发",
        "items": [{"source_order_item_id": source_id, "quantity": 1}],
    }).get_json()["data"]
    created = client.post("/api/purchase-lists", headers=admin_headers, json={
        "shop_id": shop_ids["a"], "order_ids": order_ids[:2], "after_sale_ids": [sale["id"]],
    }).get_json()["data"]
    allocation = next(row for row in created["allocations"] if row.get("after_sale_id"))
    with app.app_context():
        db.session.add(InventoryAdjustment(shop_id=shop_ids["a"], group_key=allocation["group_key"],
                                           quantity_delta=1, reason="售后现货到货"))
        db.session.commit()
    response = client.post(f"/api/purchase-lists/{created['id']}/confirm", headers=admin_headers)
    assert response.status_code == 200, response.get_json()
    data = response.get_json()["data"]
    assert data["status"] == "ordered"
    assert any(row.get("after_sale_id") for row in data["allocations"])
    assert data["total_quantity"] == created["total_quantity"]
