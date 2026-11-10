"""扫码出库必须可以撤回，而且撤回后库存要回到出库前的样子。"""

from app import db
from app.models import ACTION_SCAN, ACTION_UNDO, Order, OrderItem, OutboundEvent, ShippingParcel


def _ready_order_with_parcel(app, client, headers, shop_id, label="GV26-UNDO-1"):
    """造一张「商品已备齐 + 面单已登记」的订单，返回 (order_id, group_key)。"""
    with app.app_context():
        order = Order.query.filter_by(shop_id=shop_id).order_by(Order.id).first()
        order.fulfillment_status = "unfulfilled"
        db.session.add(OrderItem(
            shop_id=shop_id, order_id=order.id, shopify_line_item_id="undo-line",
            shopify_variant_id="undo-variant", title="Case", variant_title="Blue",
            quantity=1,
        ))
        db.session.flush()
        db.session.add(ShippingParcel(
            shop_id=shop_id, order_id=order.id, label_number=label, label_barcode=label,
        ))
        db.session.commit()
        order_id = order.id

    created = client.post("/api/purchase-lists", headers=headers,
                          json={"shop_id": shop_id, "order_ids": [order_id]}).get_json()["data"]
    client.post(f"/api/purchase-lists/{created['id']}/confirm", headers=headers)
    client.post(f"/api/purchase-lists/{created['id']}/receive", headers=headers,
                json={"items": [{"group_key": created["items"][0]["group_key"],
                                 "received_quantity": 1, "damaged_quantity": 0}]})
    return order_id, created["items"][0]["group_key"]


def _row(client, headers, shop_id, group_key):
    # 出库后这个变体在库为 0，默认列表会把它过滤掉，所以要带上 include_zero
    data = client.get(
        f"/api/inventory?shop_id={shop_id}&include_zero=true", headers=headers
    ).get_json()["data"]
    return next(item for item in data["items"] if item["group_key"] == group_key)


def test_outbound_scan_can_be_undone_and_restores_inventory(app, client, admin_headers, shop_ids):
    shop_id = shop_ids["a"]
    order_id, group_key = _ready_order_with_parcel(app, client, admin_headers, shop_id)

    # 出库前：在库 1 / 占用 1 / 可用 0
    before = _row(client, admin_headers, shop_id, group_key)
    assert (before["on_hand_quantity"], before["reserved_quantity"], before["available_quantity"]) == (1, 0, 1)

    scan = client.post("/api/shipping/scan", headers=admin_headers,
                       json={"shop_id": shop_id, "shipping_label_number": "GV26-UNDO-1"})
    assert scan.status_code == 200
    assert scan.get_json()["data"]["warehouse_status"] == "shipped"

    # 出库后：货已经离开仓库
    after = _row(client, admin_headers, shop_id, group_key)
    assert after["on_hand_quantity"] == 0

    undo = client.post("/api/shipping/outbound/undo", headers=admin_headers,
                       json={"shop_id": shop_id, "order_id": order_id, "reason": "扫错单号"})
    assert undo.status_code == 200
    assert undo.get_json()["data"]["warehouse_status"] == "pending"
    assert undo.get_json()["data"]["shipped_at"] is None

    # 撤回后库存回到出库前
    restored = _row(client, admin_headers, shop_id, group_key)
    assert (restored["on_hand_quantity"], restored["reserved_quantity"], restored["available_quantity"]) == (1, 0, 1)

    with app.app_context():
        parcel = ShippingParcel.query.filter_by(order_id=order_id).one()
        assert parcel.scanned_at is None
        assert parcel.scanned_by_user_id is None
        events = OutboundEvent.query.filter_by(order_id=order_id).order_by(OutboundEvent.id).all()
        assert [event.action for event in events] == [ACTION_SCAN, ACTION_UNDO]
        assert events[-1].reason == "扫错单号"


def test_undo_requires_an_existing_outbound(app, client, admin_headers, shop_ids):
    """没出库的单点撤回要有明确提示，不能悄悄改状态。"""
    shop_id = shop_ids["a"]
    order_id, _ = _ready_order_with_parcel(app, client, admin_headers, shop_id, label="GV26-UNDO-2")

    response = client.post("/api/shipping/outbound/undo", headers=admin_headers,
                           json={"shop_id": shop_id, "order_id": order_id})
    assert response.status_code == 400
    assert "不需要撤回" in response.get_json()["message"]

    with app.app_context():
        assert db.session.get(Order, order_id).warehouse_status == "pending"
        assert OutboundEvent.query.filter_by(order_id=order_id).count() == 0


def test_recent_outbound_list_tracks_undo_state(app, client, admin_headers, shop_ids):
    shop_id = shop_ids["a"]
    order_id, _ = _ready_order_with_parcel(app, client, admin_headers, shop_id, label="GV26-UNDO-3")
    client.post("/api/shipping/scan", headers=admin_headers,
                json={"shop_id": shop_id, "shipping_label_number": "GV26-UNDO-3"})

    recent = client.get(f"/api/shipping/outbound/recent?shop_id={shop_id}",
                        headers=admin_headers).get_json()["data"]["events"]
    assert len(recent) == 1
    assert recent[0]["order_id"] == order_id
    assert recent[0]["code"] == "GV26-UNDO-3"
    assert recent[0]["undoable"] is True

    client.post("/api/shipping/outbound/undo", headers=admin_headers,
                json={"shop_id": shop_id, "order_id": order_id})

    recent = client.get(f"/api/shipping/outbound/recent?shop_id={shop_id}",
                        headers=admin_headers).get_json()["data"]["events"]
    assert len(recent) == 1
    # 已经撤回过的记录不再显示「撤回」按钮，避免重复点
    assert recent[0]["undoable"] is False


def test_second_scan_after_undo_ships_again(app, client, admin_headers, shop_ids):
    """撤回之后还能重新扫码出库，流程是闭环的。"""
    shop_id = shop_ids["a"]
    order_id, _ = _ready_order_with_parcel(app, client, admin_headers, shop_id, label="GV26-UNDO-4")
    body = {"shop_id": shop_id, "shipping_label_number": "GV26-UNDO-4"}

    assert client.post("/api/shipping/scan", headers=admin_headers, json=body).status_code == 200
    assert client.post("/api/shipping/outbound/undo", headers=admin_headers,
                       json={"shop_id": shop_id, "order_id": order_id}).status_code == 200
    again = client.post("/api/shipping/scan", headers=admin_headers, json=body)
    assert again.status_code == 200
    assert again.get_json()["data"]["warehouse_status"] == "shipped"


def test_outbound_history_pages_all_targets_and_searches_before_paging(app, client, admin_headers, shop_ids):
    from datetime import datetime
    from app.models import AfterSale

    shop_id = shop_ids['a']
    with app.app_context():
        ids = []
        for index in range(125):
            order = Order(shop_id=shop_id, shopify_order_id=f'history-{index}',
                          order_number=f'H{index:04}', order_date=datetime.utcnow(),
                          total_price=1, subtotal_price=1, warehouse_status='shipped')
            db.session.add(order)
            db.session.flush()
            ids.append(order.id)
            db.session.add(OutboundEvent(shop_id=shop_id, order_id=order.id, action=ACTION_SCAN,
                                        order_number=order.order_number, code=f'GV-H{index:04}'))
        sale = AfterSale(shop_id=shop_id, order_id=ids[0], customer_reference='CF-H0000',
                         kind='resend', status='shipped', reason='test', shipping_address={})
        db.session.add(sale)
        db.session.flush()
        db.session.add(OutboundEvent(shop_id=shop_id, order_id=ids[0], after_sale_id=sale.id,
                                    action=ACTION_SCAN, order_number=sale.customer_reference, code='CF-CODE'))
        other = Order.query.filter_by(shop_id=shop_ids['b']).first()
        db.session.add(OutboundEvent(shop_id=shop_ids['b'], order_id=other.id, action=ACTION_SCAN,
                                    order_number=other.order_number, code='GV-H0000'))
        # Lots of repeated scans must never hide the older targets.
        db.session.add_all([OutboundEvent(shop_id=shop_id, order_id=ids[-1], action=ACTION_SCAN,
                                         order_number='H0124', code='RE-SCANNED') for _ in range(85)])
        db.session.commit()
    url = f'/api/shipping/outbound/recent?shop_id={shop_id}'
    first = client.get(url + '&per_page=50', headers=admin_headers).get_json()['data']
    assert first['pagination'] == {'page': 1, 'per_page': 50, 'total': 126, 'pages': 3}
    assert len(first['events']) == 50
    third = client.get(url + '&page=3&per_page=50', headers=admin_headers).get_json()['data']
    assert len(third['events']) == 26 and third['events'][-1]['order_id'] == ids[0]
    found = client.get(url + '&search=GV-H0000', headers=admin_headers).get_json()['data']
    assert found['pagination']['total'] == 1 and found['events'][0]['order_id'] == ids[0]
    cf = client.get(url + '&search=CF-CODE', headers=admin_headers).get_json()['data']
    assert cf['events'][0]['kind'] == 'after_sale' and cf['events'][0]['undoable']
    stale = client.get(url + '&search=GV-H0124', headers=admin_headers).get_json()['data']
    assert stale['events'] == []  # Superseded scans must not reappear on search.


def test_old_undo_not_hidden_by_500_new_undos_and_rescan_reenables(app, client, admin_headers, shop_ids):
    shop_id = shop_ids['a']
    with app.app_context():
        orders = Order.query.filter_by(shop_id=shop_id).order_by(Order.id).all()
        target, noisy = orders[:2]
        target_id = target.id
        db.session.add(OutboundEvent(shop_id=shop_id, order_id=target.id, action=ACTION_SCAN,
                                    order_number=target.order_number, code='OLD-CODE'))
        db.session.flush()
        db.session.add(OutboundEvent(shop_id=shop_id, order_id=target.id, action=ACTION_UNDO,
                                    order_number=target.order_number))
        db.session.flush()
        db.session.add_all([OutboundEvent(shop_id=shop_id, order_id=noisy.id, action=ACTION_UNDO,
                                         order_number=noisy.order_number) for _ in range(501)])
        db.session.commit()
    url = f'/api/shipping/outbound/recent?shop_id={shop_id}'
    row = client.get(url, headers=admin_headers).get_json()['data']['events'][0]
    assert row['order_id'] == target_id and not row['undoable']
    with app.app_context():
        db.session.add(OutboundEvent(shop_id=shop_id, order_id=target_id, action=ACTION_SCAN,
                                    order_number=row['order_number'], code='NEW-CODE'))
        db.session.commit()
    rows = client.get(url, headers=admin_headers).get_json()['data']['events']
    assert len(rows) == 1 and rows[0]['undoable'] and rows[0]['code'] == 'NEW-CODE'


def test_old_outbound_can_be_found_and_reverted_beyond_first_50(app, client, admin_headers, shop_ids):
    from datetime import datetime
    shop_id = shop_ids['a']
    target_id, _ = _ready_order_with_parcel(app, client, admin_headers, shop_id, label='OLD-UNDOABLE')
    assert client.post('/api/shipping/scan', headers=admin_headers,
                       json={'shop_id': shop_id, 'shipping_label_number': 'OLD-UNDOABLE'}).status_code == 200
    with app.app_context():
        for index in range(60):
            order = Order(shop_id=shop_id, shopify_order_id=f'newer-{index}', order_number=f'NEW{index}',
                          order_date=datetime.utcnow(), total_price=1, subtotal_price=1)
            db.session.add(order)
            db.session.flush()
            db.session.add(OutboundEvent(shop_id=shop_id, order_id=order.id, action=ACTION_SCAN,
                                        order_number=order.order_number))
        db.session.commit()
    result = client.get(f'/api/shipping/outbound/recent?shop_id={shop_id}&search=OLD-UNDOABLE',
                        headers=admin_headers).get_json()['data']
    assert result['events'][0]['undoable'] and result['events'][0]['order_id'] == target_id
    assert client.post('/api/shipping/outbound/undo', headers=admin_headers,
                       json={'shop_id': shop_id, 'order_id': target_id}).status_code == 200


def test_recent_history_clamps_pages_and_shop_access(client, admin_headers, sub_a_headers, shop_ids):
    url = f"/api/shipping/outbound/recent?shop_id={shop_ids['a']}&page=invalid&per_page=1000"
    result = client.get(url, headers=admin_headers).get_json()['data']
    assert result['pagination'] == {'page': 1, 'per_page': 100, 'total': 0, 'pages': 0}
    assert client.get(f"/api/shipping/outbound/recent?shop_id={shop_ids['b']}",
                      headers=sub_a_headers).status_code == 403
