"""Explicit operator-only, backed-up dispatch reconciliation. No external writes.

Not exposed as an API. The caller must supply a verified manifest, a backup and
an administrator context. All mutations use a single transaction; callers may
inspect the report and roll the transaction back for a dry run.
"""

import json
from collections import Counter
from datetime import datetime

from sqlalchemy.orm import noload, selectinload

from app import db
from app.models import (
    AfterSale, AfterSaleItem, InventoryAdjustment, InventoryReservation, Order,
    OrderItem, OutboundEvent, Product, PurchaseList, ShippingParcel, Shop,
)
from app.models.cf_tracking_writeback import CfTrackingWriteback
from app.services.inventory_service import _stock_rows
from app.services.purchase_service import _group_key, attach_procurement_status
from app.utils.errors import ForbiddenError, ValidationError
from app.utils.order_lines import is_tip_line
from app.utils.security import current_user


def reconcile_dispatch(manifest, *, batch_id):
    user = current_user() or {}
    if user.get("authority") != "admin":
        raise ForbiddenError("仓库重置仅允许管理员执行")
    if not batch_id or len(batch_id) > 100:
        raise ValidationError("必须提供唯一操作编号")
    shop_id = int(manifest["shop_id"])
    shop = Shop.query.filter_by(id=shop_id).with_for_update().one()
    if shop.name != manifest["shop_name"]:
        raise ValidationError("店铺名称与已确认范围不一致")
    marker = f"仓库盘点重置 {batch_id}"
    if OutboundEvent.query.filter_by(shop_id=shop_id, reason=marker).first():
        raise ValidationError("此操作已经执行，禁止重复清零和创建售后")
    shortages = set(manifest["shortage_numbers"])
    resend_numbers = list(manifest["resend_numbers"])
    if len(resend_numbers) != len(set(resend_numbers)) or shortages & set(resend_numbers):
        raise ValidationError("缺货或重发订单重复、范围冲突")
    orders = Order.query.options(
        noload(Order.items), noload(Order.parcels), noload(Order.after_sales),
    ).filter_by(shop_id=shop_id).with_for_update().all()
    by_number = {str(order.order_number).strip().removeprefix("#"): order for order in orders}
    if len(by_number) != len(orders) or any(not number.isdigit() for number in by_number):
        raise ValidationError("店铺存在非数字或重复订单号，无法安全应用截止规则")
    missing = (shortages | set(resend_numbers)) - by_number.keys()
    if missing:
        raise ValidationError("服务器缺少订单：" + ",".join(sorted(missing)))
    pending_ids = {order.id for number, order in by_number.items()
                   if int(number) >= int(manifest["cutoff_inclusive"]) or number in shortages}
    if any(by_number[number].id in pending_ids for number in resend_numbers):
        raise ValidationError("重发原订单与未出库范围冲突")
    now = datetime.utcnow()
    old_sales = AfterSale.query.options(
        selectinload(AfterSale.items), selectinload(AfterSale.costs), noload(AfterSale.purchase_lists),
    ).filter_by(shop_id=shop_id).with_for_update().all()
    old_sale_ids = {record.id for record in old_sales}
    purchases = PurchaseList.query.filter_by(shop_id=shop_id).with_for_update().all()
    removed_allocations = 0
    changed_purchases = []
    for purchase in purchases:
        removed = [row for row in purchase.allocations or [] if
                   row.get("order_id") in pending_ids or row.get("after_sale_id") in old_sale_ids]
        dedicated_test_sale = purchase.after_sale_id in old_sale_ids
        if not removed and not dedicated_test_sale:
            continue
        removed_allocations += len(removed)
        changed_purchases.append(purchase.id)
        purchase.allocations = [row for row in purchase.allocations or [] if row not in removed]
        removed_by_key = Counter()
        for row in removed:
            removed_by_key[row["group_key"]] += int(row.get("quantity") or 0)
        new_items = []
        for raw in purchase.items or []:
            item = dict(raw)
            item["quantity"] = max(int(item.get("quantity") or 0) - removed_by_key[item["group_key"]], 0)
            item["order_numbers"] = [number for number in item.get("order_numbers", [])
                                     if str(number).removeprefix("#") not in shortages
                                     and (not str(number).removeprefix("#").isdigit()
                                          or int(str(number).removeprefix("#")) < int(manifest["cutoff_inclusive"]))
                                     and not str(number).startswith("CF")]
            if item["quantity"]:
                new_items.append(item)
        purchase.items = new_items
        purchase.order_ids = [order_id for order_id in purchase.order_ids or [] if order_id not in pending_ids]
        purchase.order_count = len(purchase.order_ids) + len({row["after_sale_id"] for row in purchase.allocations
                                                           if row.get("after_sale_id")})
        purchase.total_quantity = sum(item["quantity"] for item in new_items)
        if dedicated_test_sale:
            purchase.after_sale_id = None
        if not purchase.allocations or dedicated_test_sale:
            purchase.status = "cancelled"
    # History/audit references retain their own identifiers; remove only the
    # foreign-key link to user-confirmed test after-sales before hard deletion.
    if old_sale_ids:
        OutboundEvent.query.filter(OutboundEvent.shop_id == shop_id,
                                  OutboundEvent.after_sale_id.in_(old_sale_ids)).update(
            {OutboundEvent.after_sale_id: None}, synchronize_session="fetch")
        CfTrackingWriteback.query.filter(CfTrackingWriteback.shop_id == shop_id,
                                        CfTrackingWriteback.after_sale_id.in_(old_sale_ids)).update(
            {CfTrackingWriteback.after_sale_id: None}, synchronize_session="fetch")
        db.session.flush()
        for record in old_sales:
            db.session.delete(record)
    reservations_deleted = InventoryReservation.query.filter(
        InventoryReservation.shop_id == shop_id, InventoryReservation.order_id.in_(pending_ids),
    ).delete(synchronize_session="fetch")
    changed_orders = 0
    for order in orders:
        target = "pending" if order.id in pending_ids else "shipped"
        if order.warehouse_status != target or (target == "pending" and order.shipped_at):
            changed_orders += 1
            db.session.add(OutboundEvent(shop_id=shop_id, order_id=order.id,
                order_number=order.order_number, action="reconcile", user_id=user.get("user_id"),
                reason=marker, created_at=now))
        order.warehouse_status = target
        if target == "pending":
            order.shipped_at = None
        # Do not manufacture a physical scan timestamp for imported confirmations.
    cleared_parcels = ShippingParcel.query.filter(
        ShippingParcel.shop_id == shop_id, ShippingParcel.order_id.in_(pending_ids),
        ShippingParcel.scanned_at.isnot(None),
    ).update({ShippingParcel.scanned_at: None, ShippingParcel.scanned_by_user_id: None}, synchronize_session="fetch")
    db.session.flush()
    balances = _stock_rows(shop_id, include_catalog=False)
    adjustments = []
    for key, row in balances.items():
        if row["reserved_quantity"]:
            raise ValidationError("清零前仍存在库存占用，操作已中止")
        if row["on_hand_quantity"]:
            delta = -row["on_hand_quantity"]
            adjustments.append(delta)
            db.session.add(InventoryAdjustment(shop_id=shop_id, group_key=key,
                quantity_delta=delta, reason=marker, created_at=now))
    db.session.flush()
    # Create exactly the original physical lines, not uncertain sheet replacements.
    source_lines = OrderItem.query.filter(OrderItem.order_id.in_(
        [by_number[number].id for number in resend_numbers]), OrderItem.shop_id == shop_id).all()
    lines_by_order = {}
    for item in source_lines:
        if int(item.quantity or 0) > 0 and not is_tip_line(item):
            lines_by_order.setdefault(item.order_id, []).append(item)
    created = []
    for number in resend_numbers:
        order = by_number[number]
        lines = lines_by_order.get(order.id, [])
        if not lines:
            raise ValidationError(f"订单 {number} 没有可补发的实体商品")
        record = AfterSale(shop_id=shop_id, order_id=order.id, customer_reference=f"CF{number}",
            kind="resend", reason="根据发货核对表重建售后，按原订单商品补发",
            note=manifest.get("resend_notes", {}).get(number), status="pending_purchase",
            shipping_address=order.shipping_address or {}, created_at=now, updated_at=now)
        db.session.add(record)
        for item in lines:
            catalog = Product.query.filter_by(shop_id=shop_id, shopify_variant_id=item.shopify_variant_id).first() \
                if item.shopify_variant_id else None
            record.items.append(AfterSaleItem(source_order_item_id=item.id, catalog_order_item_id=item.id,
                shopify_product_id=item.shopify_product_id, shopify_variant_id=item.shopify_variant_id,
                vendor=item.vendor, group_key=json.dumps(_group_key(item), ensure_ascii=False),
                title=item.title or "未命名商品", variant_title=item.variant_title, sku=item.sku,
                image_url=(catalog.image_url if catalog and catalog.image_url else item.image_url),
                quantity=item.quantity, reserved_quantity=0, received_quantity=0, damaged_quantity=0))
        db.session.flush()
        created.append({"id": record.id, "reference": record.customer_reference,
                        "quantity": sum(item.quantity for item in record.items)})
    final_balances = _stock_rows(shop_id, include_catalog=False)
    if any(row["on_hand_quantity"] or row["reserved_quantity"] or row["available_quantity"]
           for row in final_balances.values()):
        raise ValidationError("库存清零核验失败，操作已中止")
    pending = [{"id": order.id, "shop_id": shop_id, "warehouse_status": order.warehouse_status,
                "financial_status": order.financial_status, "cancelled_at": order.cancelled_at}
               for order in orders if order.id in pending_ids]
    attach_procurement_status(pending)
    if any(row["purchase_initiated"] or row["procurement_stocked_quantity"]
           or row["procurement_pending_quantity"] for row in pending):
        raise ValidationError("未采购状态核验失败，操作已中止")
    return {"shop_id": shop_id, "orders_total": len(orders), "pending": len(pending_ids),
            "shipped": len(orders) - len(pending_ids), "shortages": len(shortages),
            "changed_orders": changed_orders, "cleared_parcel_scans": cleared_parcels,
            "deleted_test_after_sales": len(old_sales), "changed_purchase_lists": changed_purchases,
            "removed_allocations": removed_allocations, "released_reservations": reservations_deleted,
            "zeroed_inventory_variants": len(adjustments), "inventory_delta": sum(adjustments),
            "created_after_sales": created, "inventory_zero_verified": True,
            "pending_unprocured_verified": True}
