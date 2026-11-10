"""After-sales may reship shipped orders without altering original fulfillment."""

from datetime import datetime, timedelta
from io import BytesIO

from flask import g
from pypdf import PdfReader, PdfWriter

from app import db
from app.models import AfterSale, Order, OrderItem, Product, Shop
from app.services import after_sale_service
from app.services.shipping_batch_pdf_service import build_ready_labels_pdf


class FakeFourPx:
    rows = []

    def __init__(self, app_key, app_secret, *, environment):
        assert app_key == "test-key" and app_secret == "test-secret"
        assert environment == "test"

    def get_predeclared_created_between(self, start_ms, end_ms):
        return self.rows


def _item(shop_id, order_id, suffix):
    item = OrderItem(shop_id=shop_id, order_id=order_id, shopify_line_item_id=f"after-{suffix}",
                     shopify_variant_id=f"after-v-{suffix}", title="Case",
                     variant_title=f"Rose Red / iPhone {suffix}", quantity=2)
    db.session.add(item)
    db.session.commit()
    return item.id


def test_create_uses_targeted_stock_and_preserves_manual_stock_occupancy(app, client, admin_headers, shop_ids):
    import json
    from sqlalchemy import event
    from app.models import InventoryAdjustment
    from app.services.purchase_service import _group_key
    statements = []
    with app.app_context():
        order = Order.query.filter_by(shop_id=shop_ids['a']).first()
        order.warehouse_status = 'shipped'
        db.session.commit()
        order_id = order.id
        item_id = _item(shop_ids['a'], order_id, '17')
        item = db.session.get(OrderItem, item_id)
        key = json.dumps(_group_key(item), ensure_ascii=False)
        db.session.add(InventoryAdjustment(shop_id=shop_ids['a'], group_key=key,
                                            quantity_delta=3, reason='manual stock'))
        db.session.add(InventoryAdjustment(shop_id=shop_ids['b'], group_key=key,
                                            quantity_delta=100, reason='other shop'))
        db.session.commit()
        def capture(_conn, _cursor, statement, _parameters, _context, _many):
            if 'FROM order_items JOIN orders' in statement:
                statements.append(statement)
        event.listen(db.engine, 'before_cursor_execute', capture)
        try:
            for quantity in (2, 2):
                result = client.post('/api/after-sales', headers=admin_headers, json={
                    'order_id': order_id, 'kind': 'resend', 'reason': 'test',
                    'items': [{'source_order_item_id': item_id, 'quantity': quantity}],
                })
                assert result.status_code == 201
                data = result.get_json()['data']
                assert data['items'][0]['reserved_quantity'] == 0
                assert data['items'][0]['available_quantity'] == 2
                assert data['purchase_lists'] == []
            assert statements and all('order_items.id IN' in sql for sql in statements)
        finally:
            event.remove(db.engine, 'before_cursor_execute', capture)


def test_after_sale_partial_receipt_cost_profit_and_scan(app, client, admin_headers, shop_ids, monkeypatch):
    with app.app_context():
        order = Order.query.filter_by(shop_id=shop_ids["a"]).first()
        order.warehouse_status = "shipped"
        db.session.commit()
        order_id = order.id
        item_id = _item(shop_ids["a"], order_id, "18")
        original_profit = order.calculate_gross_profit_cny()
    response = client.post("/api/after-sales", headers=admin_headers,
                           json={"order_id": order_id, "kind": "missing", "reason": "少发一件",
                                 "items": [{"source_order_item_id": item_id, "quantity": 2}]})
    assert response.status_code == 201
    created = response.get_json()["data"]
    assert created["customer_reference"].startswith("CF")
    assert created["status"] == "pending_purchase"
    after_id = created["id"]
    assert created["purchase_lists"] == []
    candidates = client.get(f"/api/purchase-candidates?shop_id={shop_ids['a']}&view=after_sale",
                            headers=admin_headers).get_json()["data"]
    assert candidates["category_counts"]["after_sale"] == 1
    assert candidates["after_sales"][0]["customer_reference"] == created["customer_reference"]
    generated = client.post("/api/purchase-lists/after-sale", headers=admin_headers,
                            json={"after_sale_id": after_id})
    assert generated.status_code == 201
    purchase_id = generated.get_json()["data"]["id"]
    assert client.post("/api/purchase-lists/after-sale", headers=admin_headers,
                       json={"after_sale_id": after_id}).status_code == 400
    assert client.get(f"/api/purchase-candidates?shop_id={shop_ids['a']}&view=after_sale",
                      headers=admin_headers).get_json()["data"]["category_counts"]["after_sale"] == 0
    assert client.delete(f"/api/purchase-lists/{purchase_id}", headers=admin_headers).status_code == 200
    assert client.get(f"/api/purchase-candidates?shop_id={shop_ids['a']}&view=after_sale",
                      headers=admin_headers).get_json()["data"]["category_counts"]["after_sale"] == 1
    purchase_id = client.post("/api/purchase-lists/after-sale", headers=admin_headers,
                              json={"after_sale_id": after_id}).get_json()["data"]["id"]
    purchase = client.get(f"/api/purchase-lists/{purchase_id}", headers=admin_headers).get_json()["data"]
    assert purchase["after_sale_id"] == after_id
    assert purchase["status"] == "draft"
    group_key = purchase["items"][0]["group_key"]
    assert client.post(f"/api/purchase-lists/{purchase_id}/confirm", headers=admin_headers).status_code == 200
    assert client.delete(f"/api/purchase-lists/{purchase_id}", headers=admin_headers).status_code == 400
    assert client.post(f"/api/purchase-lists/{purchase_id}/receive", headers=admin_headers,
                       json={"batch_id": "first", "finalize": False,
                             "items": [{"group_key": group_key, "received_quantity": 1,
                                        "damaged_quantity": 0}]}).status_code == 200
    first = client.get(f"/api/after-sales/{after_id}", headers=admin_headers).get_json()["data"]
    assert first["status"] == "purchasing"
    assert first["items"][0]["needed_quantity"] == 1
    assert client.post(f"/api/purchase-lists/{purchase_id}/receive", headers=admin_headers,
                       json={"batch_id": "second", "finalize": True,
                             "items": [{"group_key": group_key, "received_quantity": 0,
                                        "damaged_quantity": 1}]}).status_code == 200
    damaged = client.get(f"/api/after-sales/{after_id}", headers=admin_headers).get_json()["data"]
    assert damaged["status"] == "purchasing"
    assert len(damaged["purchase_lists"]) == 1
    replenish_candidates = client.get(f"/api/purchase-candidates?shop_id={shop_ids['a']}&view=after_sale",
                                      headers=admin_headers).get_json()["data"]
    assert replenish_candidates["category_counts"]["after_sale"] == 1
    replenish = client.post("/api/purchase-lists/after-sale", headers=admin_headers,
                            json={"after_sale_id": after_id})
    assert replenish.status_code == 201
    replenish_id = replenish.get_json()["data"]["id"]
    assert client.post(f"/api/purchase-lists/{replenish_id}/confirm", headers=admin_headers).status_code == 200
    assert client.post(f"/api/purchase-lists/{replenish_id}/receive", headers=admin_headers,
                       json={"items": [{"group_key": group_key, "received_quantity": 1,
                                        "damaged_quantity": 0}]}).status_code == 200
    second = client.get(f"/api/after-sales/{after_id}", headers=admin_headers).get_json()["data"]
    assert second["status"] == "ready"
    ready_queue = client.get(f"/api/shipping/ready?shop_id={shop_ids['a']}", headers=admin_headers)
    assert ready_queue.status_code == 200
    assert any(row["id"] == after_id for row in ready_queue.get_json()["data"]["after_sales"])
    assert ready_queue.get_json()["data"]["summary"]["after_sale_ready"] == 1
    today = (datetime.utcnow() + timedelta(hours=8)).date().isoformat()
    summary_url = f"/api/reports/financial-summary?start_date={today}&end_date={today}&shop_id={shop_ids['a']}"
    before_expenses = client.get(summary_url, headers=admin_headers).get_json()["data"]["total_expenses"]
    cost = client.post(f"/api/after-sales/{after_id}/costs", headers=admin_headers,
                       json={"cost_type": "shipping", "amount_cny": 12.50, "note": "补发运费"})
    assert cost.status_code == 201
    assert cost.get_json()["data"]["total_cost_cny"] == 12.5
    after_expenses = client.get(summary_url, headers=admin_headers).get_json()["data"]["total_expenses"]
    assert round(after_expenses - before_expenses, 2) == 12.5
    with app.app_context():
        shop = db.session.get(Shop, shop_ids["a"])
        shop.set_fourpx_credentials("test-key", "test-secret")
        db.session.commit()
        FakeFourPx.rows = [{"consignment_info": {"ref_no": created["customer_reference"],
                                                 "4px_tracking_no": "4PX-AFTER-1", "consignment_status": "P"}}]
        with app.test_request_context():
            g.current_user = {"authority": "admin"}
            result = after_sale_service.preview_fourpx({"shop_id": shop_ids["a"], "date": "2026-10-02"},
                                                        client_factory=FakeFourPx)
            assert result["rows"][0]["status"] == "ready_to_bind"
            bound = after_sale_service.bind_fourpx(after_id, {"date": "2026-10-02",
                                                        "tracking_number": "4PX-AFTER-1"}, client_factory=FakeFourPx)
            assert bound["tracking_number"] == "4PX-AFTER-1"
        order = db.session.get(Order, order_id)
        assert order.calculate_gross_profit_cny() == original_profit - 12.5
        assert order.shipping_label_number is None
        assert order.warehouse_status == "shipped"

        writer = PdfWriter()
        writer.add_blank_page(width=288, height=432)
        pdf = BytesIO()
        writer.write(pdf)
        content, filename = build_ready_labels_pdf(
            shop_ids["a"], download_pdf=lambda _url: pdf.getvalue(),
            label_fetch=lambda _order_id, _parcel_id: {
                "pdf_url": "https://label.4px.com/example.pdf", "label_barcode": "PRINTED-CF-1",
            },
        )
        assert filename.endswith(".pdf")
        assert len(PdfReader(BytesIO(content)).pages) >= 1
        assert db.session.get(AfterSale, after_id).label_barcode == "PRINTED-CF-1"

    # CF reference only queries contents. Either bound logistics identifier can
    # identify the physical reshipment; a second identifier cannot deduct twice.
    assert client.post("/api/shipping/scan", headers=admin_headers,
                       json={"shop_id": shop_ids["a"], "shipping_label_number": created["customer_reference"]}).status_code == 400
    outbound = client.post("/api/shipping/scan", headers=admin_headers,
                           json={"shop_id": shop_ids["a"], "shipping_label_number": "4PX-AFTER-1"})
    assert outbound.status_code == 200
    assert outbound.get_json()["data"]["warehouse_status"] == "shipped"
    assert client.post("/api/shipping/scan", headers=admin_headers,
                       json={"shop_id": shop_ids["a"], "shipping_label_number": "PRINTED-CF-1"}).status_code == 400
    assert client.post(f"/api/after-sales/{after_id}/purchase", headers=admin_headers).status_code == 404
    assert client.post(f"/api/after-sales/{after_id}/receive", headers=admin_headers).status_code == 404
    assert client.post("/api/after-sales/scan", headers=admin_headers).status_code == 404


def test_after_sale_uses_unique_cf_reference_and_shop_isolation(app, client, admin_headers, sub_a_headers, shop_ids):
    with app.app_context():
        order = Order.query.filter_by(shop_id=shop_ids["a"]).first()
        order.warehouse_status = "legacy_unverified"
        db.session.commit()
        order_id = order.id
        item_id = _item(shop_ids["a"], order_id, "17")
    payload = {"order_id": order_id, "kind": "resend", "reason": "客户要求补发", "note": "先核对收件地址",
               "items": [{"source_order_item_id": item_id, "quantity": 1}]}
    first = client.post("/api/after-sales", headers=admin_headers, json=payload).get_json()["data"]
    assert first["note"] == "先核对收件地址"
    updated = client.put(f"/api/after-sales/{first['id']}/note", headers=admin_headers,
                         json={"note": "客户已确认地址"})
    assert updated.status_code == 200
    assert updated.get_json()["data"]["note"] == "客户已确认地址"
    second = client.post("/api/after-sales", headers=admin_headers, json=payload).get_json()["data"]
    assert second["customer_reference"] == f"{first['customer_reference']}-2"
    assert client.get(f"/api/after-sales?shop_id={shop_ids['b']}", headers=admin_headers).get_json()["data"]["after_sales"] == []
    assert client.get(f"/api/after-sales?shop_id={shop_ids['b']}", headers=sub_a_headers).status_code == 403


def test_deleted_reference_is_reused_without_losing_archived_case(app, client, admin_headers, shop_ids):
    with app.app_context():
        order = Order.query.filter_by(shop_id=shop_ids['a']).first()
        order.warehouse_status = 'shipped'
        db.session.commit()
        order_id = order.id
        item_id = _item(shop_ids['a'], order_id, 'recreate')
    payload = {'order_id': order_id, 'kind': 'resend', 'reason': 'recreate',
               'items': [{'source_order_item_id': item_id, 'quantity': 1}]}
    old = client.post('/api/after-sales', headers=admin_headers, json=payload).json['data']
    assert client.delete(f"/api/after-sales/{old['id']}", headers=admin_headers).status_code == 200
    new_response = client.post('/api/after-sales', headers=admin_headers, json=payload)
    assert new_response.status_code == 201
    new = new_response.json['data']
    assert new['id'] != old['id']
    assert new['customer_reference'] == old['customer_reference']
    with app.app_context():
        archived = db.session.get(AfterSale, old['id'])
        assert archived.deleted_at is not None
        assert archived.customer_reference == old['customer_reference']
        assert len(archived.items) == 1


def test_reused_reference_preview_prefers_active_case_not_archived_tracking(app, client, admin_headers, shop_ids):
    with app.app_context():
        order = Order.query.filter_by(shop_id=shop_ids['a']).first()
        order.warehouse_status = 'shipped'
        db.session.commit()
        order_id = order.id
        item_id = _item(shop_ids['a'], order_id, 'preview-recreate')
        shop = db.session.get(Shop, shop_ids['a'])
        shop.set_fourpx_credentials('test-key', 'test-secret')
        shop.fourpx_environment = 'test'
        db.session.commit()
    payload = {'order_id': order_id, 'kind': 'resend', 'reason': 'test',
               'items': [{'source_order_item_id': item_id, 'quantity': 1}]}
    old = client.post('/api/after-sales', headers=admin_headers, json=payload).json['data']
    with app.app_context():
        db.session.get(AfterSale, old['id']).tracking_number = 'ARCHIVED-LABEL'
        db.session.commit()
    client.delete(f"/api/after-sales/{old['id']}", headers=admin_headers)
    new = client.post('/api/after-sales', headers=admin_headers, json=payload).json['data']
    FakeFourPx.rows = [{'consignment_info': {'ref_no': new['customer_reference'], '4px_tracking_no': 'NEW-LABEL', 'consignment_status': 'P'}}]
    with app.test_request_context():
        g.current_user = {'authority': 'admin'}
        preview = after_sale_service.preview_fourpx({'shop_id': shop_ids['a'], 'date': '2026-10-15'}, client_factory=FakeFourPx)
    assert preview['rows'][0]['after_sale_id'] == new['id']
    assert preview['rows'][0]['status'] == 'ready_to_bind'
    FakeFourPx.rows[0]['consignment_info']['4px_tracking_no'] = 'ARCHIVED-LABEL'
    with app.test_request_context():
        g.current_user = {'authority': 'admin'}
        preview = after_sale_service.preview_fourpx({'shop_id': shop_ids['a'], 'date': '2026-10-15'}, client_factory=FakeFourPx)
    # Deleting an unused case releases its label for both manual and 4PX binding.
    assert preview['rows'][0]['status'] == 'ready_to_bind'
    with app.test_request_context():
        g.current_user = {'authority': 'admin'}
        rebound = after_sale_service.bind_fourpx(new['id'], {
            'date': '2026-10-15', 'tracking_number': 'ARCHIVED-LABEL',
        }, client_factory=FakeFourPx)
    assert rebound['tracking_number'] == 'ARCHIVED-LABEL'


def test_stock_covered_after_sale_reports_no_procurement(app, client, admin_headers, shop_ids):
    from app.models import InventoryAdjustment
    with app.app_context():
        order = Order.query.filter_by(shop_id=shop_ids['a']).first()
        order.warehouse_status = 'shipped'
        db.session.commit()
        order_id = order.id
        item_id = _item(shop_ids['a'], order_id, 'stock-only')
        key = after_sale_service.json.dumps(after_sale_service._group_key(db.session.get(OrderItem, item_id)), ensure_ascii=False)
        db.session.add(InventoryAdjustment(shop_id=shop_ids['a'], group_key=key, quantity_delta=1, reason='stock'))
        db.session.commit()
    response = client.post('/api/after-sales', headers=admin_headers, json={
        'order_id': order_id, 'kind': 'resend', 'reason': 'existing stock',
        'items': [{'source_order_item_id': item_id, 'quantity': 1}]})
    data = response.json['data']
    assert data['stock_status'] == 'ready'
    assert data['procurement_status'] == 'not_started'
    assert data['stock_missing_quantity'] == 0
    assert data['purchase_lists'] == []


def test_procurement_progress_is_independent_of_shared_stock():
    from app.services.after_sale_service import procurement_progress
    for statuses, expected in [([], 'not_started'), (['draft'], 'draft'), (['ordered'], 'ordered'),
                               (['received'], 'received'), (['partially_received'], 'partially_received'),
                               (['received', 'ordered'], 'ordered'), (['cancelled'], 'not_started')]:
        assert procurement_progress([{'status': status} for status in statuses]) == expected


def test_after_sale_and_normal_order_share_one_purchase_list(app, client, admin_headers, shop_ids):
    with app.app_context():
        original, pending = Order.query.filter_by(shop_id=shop_ids["a"]).order_by(Order.id).limit(2).all()
        original.warehouse_status = "shipped"
        pending.warehouse_status = "pending"
        source = OrderItem(shop_id=shop_ids["a"], order_id=original.id,
                           shopify_line_item_id="mixed-source", shopify_variant_id="mixed-variant",
                           title="Mixed Case", variant_title="Rose Red / iPhone 18", quantity=1)
        regular = OrderItem(shop_id=shop_ids["a"], order_id=pending.id,
                            shopify_line_item_id="mixed-regular", shopify_variant_id="mixed-variant",
                            title="Mixed Case", variant_title="Rose Red / iPhone 18", quantity=1)
        db.session.add_all([source, regular])
        db.session.commit()
        original_id, pending_id, source_id = original.id, pending.id, source.id
    sale = client.post("/api/after-sales", headers=admin_headers,
                       json={"order_id": original_id, "kind": "resend", "reason": "补发",
                             "items": [{"source_order_item_id": source_id, "quantity": 1}]}).get_json()["data"]
    second_sale = client.post("/api/after-sales", headers=admin_headers,
                              json={"order_id": original_id, "kind": "resend", "reason": "再次补发",
                                    "items": [{"source_order_item_id": source_id, "quantity": 1}]}).get_json()["data"]
    candidates = client.get(f"/api/purchase-candidates?shop_id={shop_ids['a']}&view=needs_purchase",
                            headers=admin_headers).get_json()["data"]
    assert sale["id"] in candidates["all_after_sale_ids"]
    assert second_sale["id"] in candidates["all_after_sale_ids"]
    assert pending_id in candidates["all_order_ids"]
    assert candidates["category_counts"]["needs_purchase"] == 3
    created = client.post("/api/purchase-lists", headers=admin_headers,
                          json={"shop_id": shop_ids["a"], "order_ids": [pending_id],
                                "after_sale_ids": [sale["id"], second_sale["id"]]})
    assert created.status_code == 201
    purchase = created.get_json()["data"]
    assert purchase["status"] == "draft"
    assert purchase["order_count"] == 3
    assert purchase["total_quantity"] == 3
    assert len(purchase["items"]) == 1
    assert purchase["after_sale_ids"] == [sale["id"], second_sale["id"]]
    assert sale["customer_reference"] in purchase["customer_references"]
    assert len(purchase["allocations"]) == 3
    assert client.post("/api/purchase-lists", headers=admin_headers,
                       json={"shop_id": shop_ids["a"], "order_ids": [],
                             "after_sale_ids": [sale["id"]]}).status_code == 400
    linked = client.get(f"/api/after-sales/{sale['id']}", headers=admin_headers).get_json()["data"]
    assert linked["purchase_lists"][0]["id"] == purchase["id"]
    assert client.get(f"/api/after-sales/{second_sale['id']}", headers=admin_headers).get_json()["data"]["purchase_lists"][0]["id"] == purchase["id"]
    assert client.post(f"/api/purchase-lists/{purchase['id']}/confirm", headers=admin_headers).status_code == 200
    key = purchase["items"][0]["group_key"]
    first = client.post(f"/api/purchase-lists/{purchase['id']}/receive", headers=admin_headers,
                        json={"finalize": False, "batch_id": "mixed-first",
                              "items": [{"group_key": key, "received_quantity": 1, "damaged_quantity": 0}]})
    assert first.status_code == 200
    second = client.post(f"/api/purchase-lists/{purchase['id']}/receive", headers=admin_headers,
                         json={"finalize": True, "batch_id": "mixed-second",
                               "items": [{"group_key": key, "received_quantity": 2, "damaged_quantity": 0}]})
    assert second.status_code == 200
    assert client.get(f"/api/after-sales/{sale['id']}", headers=admin_headers).get_json()["data"]["status"] == "ready"
    assert client.get(f"/api/after-sales/{second_sale['id']}", headers=admin_headers).get_json()["data"]["status"] == "ready"


def test_exchange_can_choose_shop_catalog_variant_without_original_line(app, client, admin_headers, shop_ids):
    with app.app_context():
        order = Order.query.filter_by(shop_id=shop_ids["a"]).first()
        order.warehouse_status = "shipped"
        replacement = Product(shop_id=shop_ids["a"], shopify_product_id="product-exchange",
                              shopify_variant_id="variant-exchange", title="New Case",
                              variant_title="Rose Red / iPhone 18", sku="NEW-RED-18",
                              price=29, vendor="Catalog Vendor", status="active")
        other_shop = Product(shop_id=shop_ids["b"], shopify_product_id="other-product",
                             shopify_variant_id="other-variant", title="Other Shop Case",
                             variant_title="Blue", price=29, status="active")
        db.session.add_all([replacement, other_shop])
        db.session.commit()
        order_id, product_id, other_id = order.id, replacement.id, other_shop.id
    search = client.get(f"/api/after-sales/catalog?shop_id={shop_ids['a']}&search=Rose%20Red",
                        headers=admin_headers)
    assert search.status_code == 200
    assert any(row["id"] == product_id for row in search.get_json()["data"]["items"])
    assert all(row["id"] != other_id for row in search.get_json()["data"]["items"])
    rejected = client.post("/api/after-sales", headers=admin_headers,
                           json={"order_id": order_id, "kind": "exchange", "reason": "换色",
                                 "items": [{"catalog_product_id": other_id, "quantity": 1}]})
    assert rejected.status_code == 400
    created = client.post("/api/after-sales", headers=admin_headers,
                          json={"order_id": order_id, "kind": "exchange", "reason": "换色",
                                "items": [{"catalog_product_id": product_id, "quantity": 2}]})
    assert created.status_code == 201
    sale = created.get_json()["data"]
    assert sale["items"][0]["source_order_item_id"] is None
    assert sale["items"][0]["catalog_product_id"] == product_id
    assert sale["items"][0]["shopify_variant_id"] == "variant-exchange"
    purchase = client.post("/api/purchase-lists", headers=admin_headers,
                           json={"shop_id": shop_ids["a"], "order_ids": [],
                                 "after_sale_ids": [sale["id"]]})
    assert purchase.status_code == 201, purchase.get_json()
    item = purchase.get_json()["data"]["items"][0]
    assert item["shopify_product_id"] == "product-exchange"
    assert item["shopify_variant_id"] == "variant-exchange"
    assert item["quantity"] == 2


def test_lookup_uses_order_number_not_internal_id_and_detects_cross_shop_ambiguity(
    app, client, admin_headers, shop_ids
):
    with app.app_context():
        order = Order.query.filter_by(shop_id=shop_ids["a"]).first()
        other = Order.query.filter_by(shop_id=shop_ids["b"]).first()
        original_number = order.order_number
        order.order_number = "#8690"
        other.order_number = "#8690"
        db.session.commit()
        assert order.id != 8690
    ambiguous = client.get("/api/after-sales/original-order?order_number=8690", headers=admin_headers)
    assert ambiguous.status_code == 400
    scoped = client.get(f"/api/after-sales/original-order?order_number=%238690&shop_id={shop_ids['a']}",
                        headers=admin_headers)
    assert scoped.status_code == 200
    assert scoped.get_json()["data"]["id"] == order.id
    assert scoped.get_json()["data"]["order_number"] == "#8690"
    assert client.get(f"/api/after-sales/original-order?order_number={original_number.lstrip('#')}&shop_id={shop_ids['a']}",
                      headers=admin_headers).status_code == 404


def test_after_sale_list_search_matches_reference_order_tracking_and_customer(app, client, admin_headers, shop_ids):
    with app.app_context():
        order = Order.query.filter_by(shop_id=shop_ids["a"]).first()
        order.warehouse_status = "shipped"
        order.customer_name = "Search Customer"
        order.customer_email = "search-customer@example.com"
        db.session.commit()
        order_id, order_number = order.id, order.order_number
        item_id = _item(shop_ids["a"], order_id, "search")
    created = client.post("/api/after-sales", headers=admin_headers, json={
        "order_id": order_id, "kind": "resend", "reason": "search test",
        "items": [{"source_order_item_id": item_id, "quantity": 1}],
    })
    assert created.status_code == 201, created.get_json()
    sale = created.get_json()["data"]
    reference, sale_id = sale["customer_reference"], sale["id"]
    with app.app_context():
        record = db.session.get(AfterSale, sale_id)
        record.tracking_number = "4PXSEARCH123CN"
        db.session.commit()

    def search(term):
        response = client.get("/api/after-sales", headers=admin_headers, query_string={
            "shop_id": shop_ids["a"], "search": term})
        assert response.status_code == 200, response.get_json()
        return response.get_json()["data"]

    for term in (reference, order_number, f"#{order_number}", "4PXSEARCH123CN", "search-customer@example.com"):
        result = search(term)
        assert [row["id"] for row in result["after_sales"]] == [sale_id], (term, result["after_sales"])
        assert result["pagination"]["total"] == 1

    empty = search("no-such-after-sale")
    assert empty["after_sales"] == [] and empty["pagination"]["total"] == 0
