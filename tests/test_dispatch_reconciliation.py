import json
from datetime import datetime

import pytest
from flask import g

from app import db
from app.models import (
    AfterSale, AfterSaleItem, InventoryAdjustment, InventoryReservation, Order,
    OrderItem, OutboundEvent, PurchaseList, ShippingParcel,
)
from app.services.dispatch_reconciliation_service import reconcile_dispatch
from app.services.inventory_service import _stock_rows, auto_allocate_stock
from app.utils.errors import ForbiddenError, ValidationError


def seed(shop_id, other_shop_id):
    orders = Order.query.filter_by(shop_id=shop_id).order_by(Order.id).all()
    key = json.dumps(["variant", "reset-variant"])
    for number, order in zip((100, 101, 102), orders):
        order.order_number = str(number)
        order.warehouse_status = "shipped" if number != 101 else "pending"
        order.shipped_at = datetime(2026, 10, 1) if number != 101 else None
        order.items.append(OrderItem(shop_id=shop_id, order_id=order.id,
            shopify_line_item_id=f"reset-{number}", shopify_variant_id="reset-variant",
            title="Case", variant_title="Purple / iPhone 17", quantity=1))
    db.session.flush()
    sale = AfterSale(shop_id=shop_id, order_id=orders[1].id, customer_reference="CF101",
                     reason="test", kind="resend", status="pending_purchase", shipping_address={})
    db.session.add(sale)
    db.session.flush()
    sale.items.append(AfterSaleItem(group_key=key, title="Test", quantity=1))
    allocations = [{"order_id": order.id, "order_item_id": order.items[0].id,
                    "group_key": key, "quantity": 1, "received_quantity": 1} for order in orders]
    allocations.append({"after_sale_id": sale.id, "after_sale_item_id": sale.items[0].id,
                        "group_key": key, "quantity": 1, "received_quantity": 0})
    purchase = PurchaseList(shop_id=shop_id, name="Mixed", status="received",
        order_ids=[order.id for order in orders], order_count=3, total_quantity=4,
        items=[{"group_key": key, "title": "Case", "quantity": 4,
                "order_numbers": ["100", "101", "102", "CF101"]}],
        allocations=allocations, receipt=[{"group_key": key, "received_quantity": 3}])
    db.session.add(purchase)
    db.session.add(InventoryAdjustment(shop_id=shop_id, group_key=key, quantity_delta=2, reason="test"))
    db.session.add(InventoryReservation(shop_id=shop_id, order_id=orders[0].id,
        order_item_id=orders[0].items[0].id, group_key=key, quantity=1))
    db.session.add(ShippingParcel(shop_id=shop_id, order_id=orders[0].id,
        label_number="reset-label", source="4px", scanned_at=datetime(2026, 10, 1)))
    db.session.add(OutboundEvent(shop_id=shop_id, order_id=orders[1].id,
        order_number="101", after_sale_id=sale.id, action="scan", reason="old test audit"))
    other = Order.query.filter_by(shop_id=other_shop_id).first()
    other.warehouse_status = "pending"
    db.session.commit()
    return orders, purchase.id, sale.id, other.id


def manifest(shop_id):
    return {"shop_id": shop_id, "shop_name": "Shop A", "cutoff_inclusive": 102,
            "shortage_numbers": ["100"], "resend_numbers": ["101"],
            "resend_notes": {"101": "按原订单创建"}}


def test_reconciliation_scoped_atomic_original_items_and_idempotence(app, shop_ids):
    with app.app_context():
        orders, purchase_id, old_sale_id, other_id = seed(shop_ids['a'], shop_ids['b'])
        g.current_user = {"authority": "admin"}
        prices = [(order.total_price, order.actual_received) for order in orders]
        result = reconcile_dispatch(manifest(shop_ids['a']), batch_id="unit-test")
        assert result['pending'] == 2 and result['shipped'] == 1
        assert result['deleted_test_after_sales'] == 1
        assert result['inventory_zero_verified'] and result['pending_unprocured_verified']
        # SQLite may reuse a deleted integer PK; PostgreSQL sequences do not.
        assert AfterSale.query.filter_by(reason="test").count() == 0
        new = AfterSale.query.one()
        assert new.customer_reference == "CF101" and new.status == "pending_purchase"
        assert new.items[0].variant_title == "Purple / iPhone 17"
        assert new.items[0].quantity == 1 and new.items[0].reserved_quantity == 0
        assert [(order.total_price, order.actual_received) for order in orders] == prices
        assert db.session.get(Order, other_id).warehouse_status == "pending"
        purchase = db.session.get(PurchaseList, purchase_id)
        assert purchase.order_ids == [orders[1].id]
        assert len(purchase.allocations) == 1 and purchase.total_quantity == 1
        assert purchase.receipt[0]['received_quantity'] == 3  # receipt history retained
        assert ShippingParcel.query.one().scanned_at is None
        assert OutboundEvent.query.filter_by(reason="old test audit").one().after_sale_id is None
        db.session.commit()
        auto_allocate_stock(shop_ids['a'])
        assert all(row['on_hand_quantity'] == row['reserved_quantity'] == 0
                   for row in _stock_rows(shop_ids['a']).values())
        with pytest.raises(ValidationError, match="已经执行"):
            reconcile_dispatch(manifest(shop_ids['a']), batch_id="unit-test")


def test_reconciliation_preview_rolls_back(app, shop_ids):
    with app.app_context():
        orders, _, _, _ = seed(shop_ids['a'], shop_ids['b'])
        g.current_user = {"authority": "admin"}
        before = [order.warehouse_status for order in orders]
        reconcile_dispatch(manifest(shop_ids['a']), batch_id="preview")
        db.session.rollback()
        assert [order.warehouse_status for order in orders] == before
        assert AfterSale.query.one().reason == "test"


def test_reconciliation_rejects_wrong_scope_and_missing_orders(app, shop_ids):
    with app.app_context():
        seed(shop_ids['a'], shop_ids['b'])
        data = manifest(shop_ids['a'])
        with pytest.raises(ForbiddenError):
            reconcile_dispatch(data, batch_id="wrong-user")
        g.current_user = {"authority": "admin"}
        data['shop_name'] = 'wrong shop'
        with pytest.raises(ValidationError, match="店铺名称"):
            reconcile_dispatch(data, batch_id="wrong-shop")
        data['shop_name'] = 'Shop A'
        data['shortage_numbers'].append('999')
        with pytest.raises(ValidationError, match="服务器缺少订单"):
            reconcile_dispatch(data, batch_id="missing")
