"""Packing checks use exact local codes and never change outbound state."""

from app import db
from app.models import AfterSale, AfterSaleItem, Order, OrderItem, ShippingParcel


def test_cf_label_uses_replacement_items_and_independent_outbound(app, client, admin_headers, shop_ids):
    import json
    from datetime import datetime
    from app.models import InventoryAdjustment, Product
    with app.app_context():
        order=Order.query.filter_by(shop_id=shop_ids['a']).first()
        order.warehouse_status='shipped'
        order.shipped_at=datetime(2026,1,1)
        order.shipping_label_number='4PX-REPLACEMENT'
        order.fulfillments=[{'tracking_numbers':['4PX-REPLACEMENT']}]
        db.session.add(OrderItem(shop_id=order.shop_id,order_id=order.id,shopify_line_item_id='old-cf-item',
                                shopify_variant_id='old-cf-v',title='Old Case',variant_title='Old variant',quantity=1))
        parcel=ShippingParcel(shop_id=order.shop_id,order_id=order.id,label_number='4PX-REPLACEMENT',
                              label_barcode='OLD-CODE',scanned_at=datetime(2026,1,1))
        db.session.add(parcel)
        product=Product(shop_id=order.shop_id,shopify_product_id='new-cf-p',shopify_variant_id='new-cf-v',
                        title='Replacement Case',variant_title='Blue / iPhone 15 Pro',price=1)
        db.session.add(product)
        key=json.dumps(['variant','new-cf-v'])
        sale=AfterSale(shop_id=order.shop_id,order_id=order.id,customer_reference='CF-REPLACEMENT',
                       kind='exchange',reason='换品',status='ready',shipping_address={},
                       tracking_number='4PX-REPLACEMENT',label_barcode='GV-REPLACEMENT')
        sale.items.append(AfterSaleItem(group_key=key,shopify_product_id='new-cf-p',shopify_variant_id='new-cf-v',
                                       title='Replacement Case',variant_title='Blue / iPhone 15 Pro',quantity=1,reserved_quantity=1))
        db.session.add_all([sale,InventoryAdjustment(shop_id=order.shop_id,group_key=key,quantity_delta=1,reason='实物入库')])
        db.session.commit()
        original_id,sale_id,parcel_id=order.id,sale.id,parcel.id
    for code in ('4PX-REPLACEMENT','GV-REPLACEMENT'):
        row=_lookup(client,admin_headers,shop_ids['a'],code)
        assert row['type']=='after_sale'
        assert row['reference']=='CF-REPLACEMENT'
        assert row['items'][0]['title']=='Replacement Case'
        assert row['outbound']['ready'] is True
        assert row['outbound']['already_scanned'] is False
    reference=_lookup(client,admin_headers,shop_ids['a'],'CF-REPLACEMENT')
    assert reference['outbound']['code_matches_label'] is False
    scanned=client.post('/api/shipping/scan',headers=admin_headers,
                        json={'shop_id':shop_ids['a'],'shipping_label_number':'4PX-REPLACEMENT'})
    assert scanned.status_code==200,scanned.get_json()
    assert scanned.get_json()['data']['after_sale_id']==sale_id
    with app.app_context():
        assert db.session.get(Order,original_id).shipped_at==datetime(2026,1,1)
        assert db.session.get(ShippingParcel,parcel_id).scanned_at==datetime(2026,1,1)
        assert db.session.get(AfterSale,sale_id).status=='shipped'
    row=_lookup(client,admin_headers,shop_ids['a'],'GV-REPLACEMENT')
    assert row['outbound']['already_scanned'] is True
    assert client.post('/api/shipping/scan',headers=admin_headers,
                       json={'shop_id':shop_ids['a'],'shipping_label_number':'GV-REPLACEMENT'}).status_code==400


def test_known_cf_without_active_contents_never_falls_back_to_original(app, client, admin_headers, shop_ids):
    from app.models.cf_tracking_writeback import CfTrackingWriteback
    with app.app_context():
        order=Order.query.filter_by(shop_id=shop_ids['a']).first()
        order.shipping_label_number='4PX-CF-NO-CONTENTS'
        db.session.add(CfTrackingWriteback(shop_id=order.shop_id,order_id=order.id,
                        customer_reference='CF-MISSING',tracking_number='4PX-CF-NO-CONTENTS',
                        fulfillment_id='fulfill-test',previous_tracking_numbers=[],state='confirmed'))
        db.session.commit()
    lookup=client.get(f"/api/shipping/packing-lookup?shop_id={shop_ids['a']}&code=4PX-CF-NO-CONTENTS",headers=admin_headers)
    assert lookup.status_code==400
    assert '售后商品清单' in lookup.get_json()['message']
    scanned=client.post('/api/shipping/scan',headers=admin_headers,
                        json={'shop_id':shop_ids['a'],'shipping_label_number':'4PX-CF-NO-CONTENTS'})
    assert scanned.status_code==400


def test_cf_code_collision_with_other_order_fails_closed(app, client, admin_headers, shop_ids):
    with app.app_context():
        orders=Order.query.filter_by(shop_id=shop_ids['a']).order_by(Order.id).limit(2).all()
        orders[0].warehouse_status='shipped'
        sale=AfterSale(shop_id=shop_ids['a'],order_id=orders[0].id,customer_reference='CF-COLLISION',
                       kind='exchange',reason='换品',status='ready',shipping_address={},tracking_number='4PX-COLLISION')
        db.session.add_all([sale,ShippingParcel(shop_id=shop_ids['a'],order_id=orders[1].id,label_number='4PX-COLLISION')])
        db.session.commit()
    for endpoint in ('lookup','scan'):
        if endpoint=='lookup':
            response=client.get(f"/api/shipping/packing-lookup?shop_id={shop_ids['a']}&code=4PX-COLLISION",headers=admin_headers)
        else:
            response=client.post('/api/shipping/scan',headers=admin_headers,
                                 json={'shop_id':shop_ids['a'],'shipping_label_number':'4PX-COLLISION'})
        assert response.status_code==400
        assert '其他订单' in response.get_json()['message']


def test_lookup_order_by_number_tracking_and_barcode(app, client, admin_headers, shop_ids):
    with app.app_context():
        order = Order.query.filter_by(shop_id=shop_ids["a"]).order_by(Order.id).first()
        order.warehouse_status = "pending"
        order.note = "礼品订单，请放入贺卡"
        db.session.add_all([
            OrderItem(shop_id=order.shop_id, order_id=order.id, shopify_line_item_id=f"pack-{index}",
                      shopify_variant_id="variant-1", title="Phone Case",
                      variant_title="Rose Red / iPhone 17 Pro", sku="RED-17", quantity=quantity,
                      image_url="https://example.com/red.jpg")
            for index, quantity in enumerate((1, 2))
        ])
        db.session.add(ShippingParcel(shop_id=order.shop_id, order_id=order.id,
                                      label_number="4PX-TEST-1", label_barcode="SCAN-TEST-1"))
        db.session.commit()
        order_id = order.id
        number = order.order_number

    for code in (number.lstrip("#"), "4PX-TEST-1", "SCAN-TEST-1"):
        response = client.get(f"/api/shipping/packing-lookup?shop_id={shop_ids['a']}&code={code}",
                              headers=admin_headers)
        assert response.status_code == 200
        row = response.get_json()["data"]
        assert row["id"] == order_id
        assert row["items"] == [{"title": "Phone Case", "variant_title": "Rose Red / iPhone 17 Pro",
                                 "sku": "RED-17", "image_url": "https://example.com/red.jpg", "quantity": 3}]
        assert row["total_quantity"] == 3
        assert row["status"] == "pending"
        assert row["order_note"] == "礼品订单，请放入贺卡"


def test_lookup_after_sale_and_shop_isolation(app, client, admin_headers, sub_a_headers, shop_ids):
    with app.app_context():
        order = Order.query.filter_by(shop_id=shop_ids["a"]).order_by(Order.id).first()
        order.note = "原订单请勿附价格单"
        sale = AfterSale(shop_id=order.shop_id, order_id=order.id,
                         customer_reference="CF-PACK-1", kind="exchange", reason="换品",
                         note="补发前核对颜色", status="ready", shipping_address={"name": "New recipient"},
                         tracking_number="4PX-CF-PACK", label_barcode="CF-SCAN")
        sale.items.append(AfterSaleItem(group_key="pack-group", title="New Case",
                                        variant_title="Blue / iPhone 18", image_url="https://example.com/blue.jpg",
                                        quantity=2))
        db.session.add(sale)
        db.session.commit()

    response = client.get(f"/api/shipping/packing-lookup?shop_id={shop_ids['a']}&code=CF-SCAN",
                          headers=admin_headers)
    assert response.status_code == 200
    row = response.get_json()["data"]
    assert row["type"] == "after_sale"
    assert row["reference"] == "CF-PACK-1"
    assert row["total_quantity"] == 2
    assert row["shipping_address"] == {"name": "New recipient"}
    assert row["order_note"] == "原订单请勿附价格单"
    assert row["note"] == "补发前核对颜色"
    assert client.get(f"/api/shipping/packing-lookup?shop_id={shop_ids['b']}&code=CF-SCAN",
                      headers=admin_headers).status_code == 404
    assert client.get(f"/api/shipping/packing-lookup?shop_id={shop_ids['b']}&code=CF-SCAN",
                      headers=sub_a_headers).status_code == 403


def test_lookup_rejects_ambiguous_code(app, client, admin_headers, shop_ids):
    with app.app_context():
        order = Order.query.filter_by(shop_id=shop_ids["a"]).order_by(Order.id).first()
        other = Order.query.filter_by(shop_id=shop_ids["a"]).order_by(Order.id.desc()).first()
        db.session.add(ShippingParcel(shop_id=other.shop_id, order_id=other.id,
                                      label_number=order.order_number.lstrip("#")))
        db.session.commit()
        code = order.order_number.lstrip("#")
    response = client.get(f"/api/shipping/packing-lookup?shop_id={shop_ids['a']}&code={code}",
                          headers=admin_headers)
    assert response.status_code == 400
    assert "多张清单" in response.get_json()["message"]


def test_lookup_by_shopify_tracking_number(app, client, admin_headers, shop_ids):
    """运单号存在 orders.fulfillments 里（历史单都是这种），也必须能扫出来。"""
    with app.app_context():
        order = Order.query.filter_by(shop_id=shop_ids["a"]).order_by(Order.id).first()
        order.warehouse_status = "shipped"
        # 模拟 Shopify 同步下来的履约记录：这里没有 ShippingParcel，也没有 shipping_label_number
        order.fulfillments = [
            {
                "id": "6645982069039",
                "status": "SUCCESS",
                "tracking_company": "4PX",
                "tracking_numbers": ["4PX3003191701753CN"],
                "tracking_urls": ["https://track.4px.com/#/result/0/4PX3003191701753CN"],
            }
        ]
        db.session.add(OrderItem(shop_id=order.shop_id, order_id=order.id,
                                 shopify_line_item_id="pack-fulfill", shopify_variant_id="variant-9",
                                 title="Wallet", variant_title="Carbon", sku="WALLET-C",
                                 quantity=2, image_url="https://example.com/wallet.jpg"))
        db.session.commit()
        order_id = order.id

    response = client.get(
        f"/api/shipping/packing-lookup?shop_id={shop_ids['a']}&code=4PX3003191701753CN",
        headers=admin_headers,
    )
    assert response.status_code == 200
    row = response.get_json()["data"]
    assert row["id"] == order_id
    assert row["total_quantity"] == 2
    assert row["items"][0]["sku"] == "WALLET-C"
    # 运单号要回显出来，方便打包台核对
    assert row["labels"][0]["tracking_number"] == "4PX3003191701753CN"


def test_tracking_lookup_does_not_match_a_substring(app, client, admin_headers, shop_ids):
    """粗筛用的是文本包含，必须靠精确比对挡住子串误命中。"""
    with app.app_context():
        order = Order.query.filter_by(shop_id=shop_ids["a"]).order_by(Order.id).first()
        order.fulfillments = [{"tracking_numbers": ["4PX3003191701753CN"]}]
        db.session.commit()

    # 少最后两位，不是同一个单号
    response = client.get(
        f"/api/shipping/packing-lookup?shop_id={shop_ids['a']}&code=4PX30031917017",
        headers=admin_headers,
    )
    assert response.status_code == 404


def _order_with_one_parcel(app, shop_id):
    """一张还没备货的订单 + 一张已登记的面单，返回 (order_id, 订单号)。"""
    with app.app_context():
        order = Order.query.filter_by(shop_id=shop_id).order_by(Order.id).first()
        order.fulfillment_status = "unfulfilled"
        db.session.add(OrderItem(
            shop_id=shop_id, order_id=order.id, shopify_line_item_id="ready-line",
            shopify_variant_id="ready-variant", title="Case", variant_title="Blue",
            quantity=1,
        ))
        db.session.flush()
        db.session.add(ShippingParcel(
            shop_id=shop_id, order_id=order.id,
            label_number="4PX-READY-1", label_barcode="BAR-READY-1",
        ))
        db.session.commit()
        return order.id, order.order_number


def _lookup(client, headers, shop_id, code):
    response = client.get(
        f"/api/shipping/packing-lookup?shop_id={shop_id}&code={code}", headers=headers
    )
    assert response.status_code == 200
    return response.get_json()["data"]


def test_packing_lookup_tells_whether_a_second_scan_can_ship(app, client, admin_headers, shop_ids):
    """打包台要把「核对」和「出库」合成一页，所以查询结果必须带上出库判断。"""
    shop_id = shop_ids["a"]
    order_id, order_number = _order_with_one_parcel(app, shop_id)

    # 商品还没备齐：能看清单，但不能出库
    row = _lookup(client, admin_headers, shop_id, "BAR-READY-1")
    assert row["outbound"]["ready"] is False
    assert row["outbound"]["code_matches_label"] is True
    assert row["outbound"]["blockers"] == ["订单商品尚未备齐，不能扫码出库"]

    # 扫订单号只能看清单，出库必须扫面单条码
    row = _lookup(client, admin_headers, shop_id, order_number.lstrip("#"))
    assert row["outbound"]["code_matches_label"] is False
    assert row["outbound"]["ready"] is False

    # 备货完成
    created = client.post(
        "/api/purchase-lists", headers=admin_headers,
        json={"shop_id": shop_id, "order_ids": [order_id]},
    ).get_json()["data"]
    assert client.post(
        f"/api/purchase-lists/{created['id']}/confirm", headers=admin_headers
    ).status_code == 200
    assert client.post(
        f"/api/purchase-lists/{created['id']}/receive", headers=admin_headers,
        json={"items": [{"group_key": created["items"][0]["group_key"],
                         "received_quantity": 1, "damaged_quantity": 0}]},
    ).status_code == 200

    # 备齐后：再扫一次同一张面单即可出库
    row = _lookup(client, admin_headers, shop_id, "BAR-READY-1")
    assert row["outbound"]["ready"] is True
    assert row["outbound"]["blockers"] == []

    scan = client.post(
        "/api/shipping/scan", headers=admin_headers,
        json={"shop_id": shop_id, "shipping_label_number": "BAR-READY-1"},
    )
    assert scan.status_code == 200
    assert scan.get_json()["data"]["warehouse_status"] == "shipped"

    # 出库之后再查：明确告诉现场已经扫过，别重复出库
    row = _lookup(client, admin_headers, shop_id, "BAR-READY-1")
    assert row["outbound"]["ready"] is False
    assert row["outbound"]["already_scanned"] is True
    assert row["outbound"]["scanned_parcel_count"] == 1
    assert row["outbound"]["blockers"] == ["订单已完成出库，请勿重复扫码"]
