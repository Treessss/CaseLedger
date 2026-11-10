"""Explicit carrier-confirmed reconciliation; never invent incoming goods or negative stock."""
from collections import Counter
from datetime import datetime

from sqlalchemy.orm import selectinload

from app import db
from app.models import (AfterSale, InventoryAdjustment, InventoryOutboundLine, Order,
                        PackingSession, Shop, ShippingParcel, TrackingBinding, TrackingOutboundAudit)
from app.services import logistics_service as tracking
from app.services.outbound_inventory_service import _key, quantity_plan
from app.services.packing_session_service import (finish_session, has_physical_outbound,
                                                   validate_committed_snapshot)
from app.services.shipping_service import record_outbound_event
from app.utils.errors import ApiError, ValidationError
from app.utils.order_lines import is_tip_line
from app.utils.security import current_user


def _deduct(record, *, after_sale=False):
    """Return actual deductions and missing units separately; caller holds shop lock."""
    items = [item for item in record.items if item.quantity > 0 and not is_tip_line(item)]
    if not items:
        raise ValidationError('订单没有可出库商品，请人工核对')
    plan = quantity_plan(record, allow_partial=True)
    covered = Counter()
    lines = []
    user_id = (current_user() or {}).get('user_id')
    for step in plan:
        covered[step['target_key']] += step['quantity']
        lines.append({'group_key': step['source_key'], 'target_key': step['target_key'], 'quantity': step['quantity']})
        if after_sale:
            db.session.add(InventoryAdjustment(shop_id=record.shop_id, group_key=step['source_key'],
                quantity_delta=-step['quantity'], reason=f'售后 {record.customer_reference} 扫码出库'))
        else:
            db.session.add(InventoryOutboundLine(shop_id=record.shop_id, order_id=record.id,
                group_key=step['source_key'], quantity_delta=-step['quantity'], action='outbound', user_id=user_id))
    shortages = []
    for item in items:
        key = getattr(item, 'group_key', None) or _key(item)
        take = min(item.quantity, covered[key])
        covered[key] -= take
        if item.quantity > take:
            shortages.append({'group_key': key, 'title': item.title, 'variant_title': item.variant_title,
                              'sku': item.sku, 'quantity': item.quantity - take})
    now = datetime.utcnow()
    finish_session(record.shop_id, **({'after_sale_id': record.id} if after_sale else {'order_id': record.id}), state='cancelled')
    snapshot = list(lines)
    if not after_sale:
        # Snapshot validates the full order; unaccounted rows are NOT stock deductions.
        snapshot += [{**line, 'target_key': line['group_key'], 'unaccounted': True} for line in shortages]
        db.session.add(InventoryOutboundLine(shop_id=record.shop_id, order_id=record.id,
            group_key=getattr(items[0], 'group_key', None) or _key(items[0]),
            quantity_delta=0, action='carrier_confirm', user_id=user_id))
    db.session.add(PackingSession(shop_id=record.shop_id, user_id=user_id, state='completed',
        order_id=None if after_sale else record.id, after_sale_id=record.id if after_sale else None,
        lines=snapshot, expires_at=now, ended_at=now))
    return {'deducted': lines, 'shortages': shortages,
            'deducted_quantity': sum(line['quantity'] for line in lines),
            'unaccounted_quantity': sum(line['quantity'] for line in shortages)}


def _deduct_with_refund_restore(order):
    """退款同步会把商品数量写成 0；补记出库前先尝试从 Shopify 恢复原始数量。"""
    if any(item.quantity > 0 and not is_tip_line(item) for item in order.items):
        return _deduct(order)
    from app.services.order_service import restore_refunded_item_quantities
    if not restore_refunded_item_quantities(order):
        raise ValidationError('订单没有可出库商品，请人工核对')
    return _deduct(order)


def _apply(binding_id, shop_id, evidence):
    Shop.query.filter_by(id=shop_id).with_for_update().one()
    binding = TrackingBinding.query.filter_by(id=binding_id, shop_id=shop_id, active=True).populate_existing().with_for_update().one_or_none()
    if binding is None:
        raise ValidationError('单号绑定已变化，请重新核查')
    record = binding.record
    active_bindings = TrackingBinding.query.filter_by(
        shop_id=shop_id, record_id=record.id, active=True).all()
    combined_shipment = len(active_bindings) > 1
    if combined_shipment and any(row.after_sale_id for row in active_bindings):
        # 售后和原订单共用单号时内容可能不同，仍按原规则人工处理。
        raise ValidationError('物流号同时对应订单和售后，禁止自动补记')
    order = Order.query.options(selectinload(Order.items), selectinload(Order.parcels)).filter_by(
        id=binding.order_id, shop_id=shop_id).populate_existing().with_for_update().one()
    now, user_id = datetime.utcnow(), (current_user() or {}).get('user_id')
    parcel = None
    if binding.after_sale_id:
        target = AfterSale.query.options(selectinload(AfterSale.items)).filter_by(
            id=binding.after_sale_id, shop_id=shop_id).populate_existing().with_for_update().one()
        if target.deleted_at or target.tracking_number != record.number:
            raise ValidationError('售后物流号已变更或售后已删除')
        if target.shipped_at or target.status == 'shipped':
            return {'id': binding.id, 'state': 'skipped', 'message': '售后已出库，无重复扣减'}
        stock = _deduct(target, after_sale=True)
        for item in target.items:
            item.reserved_quantity = 0
        target.status, target.shipped_at = 'shipped', now
    else:
        if order.warehouse_status == 'shipped':
            return {'id': binding.id, 'state': 'skipped', 'message': '订单已出库，无重复扣减'}
        if order.warehouse_status in ('legacy_unverified', 'legacy_partial_unverified'):
            # 历史迁移订单：4PX 实际轨迹已证明货已出仓，只核实状态，不追溯扣库存
            # （这些订单发生在库存功能上线之前，现在扣减会把当前库存扣错）。
            if any(p.scanned_at for p in order.parcels):
                return {'id': binding.id, 'state': 'skipped', 'message': '历史订单已有部分实物流水，请人工核实'}
            parcels = order.parcels
            parcel = next((p for p in parcels if p.label_number == record.number), None)
            if parcel is None:
                if (parcels or order.expected_parcel_count != 1
                        or tracking._order_numbers(order) != {record.number}):
                    raise ValidationError('历史订单单号已变更或多包裹面单未登记齐全，请先核对绑定')
                parcel = ShippingParcel(shop_id=shop_id, order_id=order.id,
                                        label_number=record.number, source='4px')
                db.session.add(parcel)
                db.session.flush()
                binding.parcel_id = parcel.id
                parcels = [parcel]
            if len(parcels) != order.expected_parcel_count:
                raise ValidationError('历史订单多包裹面单未登记齐全，请先核对')
            now_legacy = datetime.utcnow()
            parcel.scanned_at, parcel.scanned_by_user_id = now_legacy, user_id
            order.warehouse_status, order.shipped_at = 'shipped', now_legacy
            stock = {'deducted': [], 'shortages': [], 'deducted_quantity': 0,
                     'unaccounted_quantity': 0, 'historical_no_deduction': True}
            record_outbound_event(order, action='scan', code=record.number, parcel=parcel,
                after_sale_id=None,
                reason='历史迁移订单按4PX实际运输轨迹核实出库；不追溯扣库存')
            db.session.add(TrackingOutboundAudit(shop_id=shop_id, binding_id=binding.id,
                order_id=order.id, after_sale_id=None, user_id=user_id, evidence={
                    'number': record.number, 'latest': evidence['latest'],
                    'event_count': evidence['event_count'],
                    'checked_at': evidence['tracking_checked_at'],
                    'source': evidence['source'], 'historical': True, 'stock_deducted': False},
                stock_result=stock))
            db.session.commit()
            return {'id': binding.id, 'state': 'succeeded', 'order_number': order.order_number,
                    'after_sale_id': None, 'stock_result': stock, 'historical': True}
        if order.warehouse_status != 'pending':
            raise ValidationError('历史出库状态待核实，请人工处理')
        # 退款/取消只影响「要不要发货」，不影响「已经发出去的实物」。4PX 有实际运输轨迹
        # 说明货已经出仓，必须补记出库并扣库存，否则库存会长期偏高；未付款订单仍禁止。
        if order.financial_status in ('pending', 'unpaid', 'authorized'):
            raise ValidationError('订单未付款，不能补记出库，请人工处理')
        if AfterSale.query.filter(AfterSale.shop_id == shop_id, AfterSale.deleted_at.is_(None),
                                  AfterSale.tracking_number == record.number).first():
            raise ValidationError('单号已归属售后，不能补记原订单')
        parcels = order.parcels
        parcel = next((p for p in parcels if p.label_number == record.number), None)
        shared_parcel = None
        if combined_shipment:
            # 同一客户两单合并发货：一个运单号/包裹对应多个订单。
            # 包裹只登记在其中一个订单上，其他订单不再重复建包裹，只补出库+扣库存。
            shared_parcel = ShippingParcel.query.filter(
                ShippingParcel.shop_id == shop_id,
                ShippingParcel.label_number == record.number,
                ShippingParcel.order_id != order.id,
            ).first()
        if parcel is None:
            if shared_parcel is not None:
                pass
            else:
                if (parcels or order.expected_parcel_count != 1
                        or tracking._order_numbers(order) != {record.number}):
                    raise ValidationError('单号已变更或多包裹面单未登记齐全，请先核对绑定')
                parcel = ShippingParcel(shop_id=shop_id, order_id=order.id, label_number=record.number, source='4px')
                db.session.add(parcel)
                db.session.flush()
                binding.parcel_id = parcel.id
                parcels = [parcel]
        if shared_parcel is None and parcel is not None and len(parcels) != order.expected_parcel_count:
            raise ValidationError('多包裹面单未登记齐全，请先核对')
        if shared_parcel is None and parcel is not None and parcel.scanned_at:
            return {'id': binding.id, 'state': 'skipped', 'message': '此包裹已出库，无重复扣减'}
        if has_physical_outbound(order):
            validate_committed_snapshot(order)
            stock = {'deducted': [], 'shortages': [], 'deducted_quantity': 0,
                     'unaccounted_quantity': 0, 'prior_deduction_preserved': True}
        else:
            if any(p.scanned_at for p in parcels):
                raise ValidationError('历史部分出库缺少实物流水，请人工核实，不能重复扣减')
            stock = _deduct_with_refund_restore(order)
        if shared_parcel is None and parcel is not None:
            parcel.scanned_at, parcel.scanned_by_user_id = now, user_id
            if all(p.scanned_at for p in parcels):
                order.warehouse_status, order.shipped_at = 'shipped', now
        else:
            order.warehouse_status, order.shipped_at = 'shipped', now
    reason = f"4PX实际运输轨迹强制补记；账实差异{stock['unaccounted_quantity']}件，未虚增库存"
    if shared_parcel is not None:
        reason = "合并发货（同一运单号对应多个订单）；" + reason
    record_outbound_event(order, action='scan', code=record.number,
        parcel=None if shared_parcel is not None else parcel,
        after_sale_id=binding.after_sale_id, reason=reason)
    db.session.add(TrackingOutboundAudit(shop_id=shop_id, binding_id=binding.id, order_id=order.id,
        after_sale_id=binding.after_sale_id, user_id=user_id, evidence={
            'number': record.number, 'latest': evidence['latest'], 'event_count': evidence['event_count'],
            'checked_at': evidence['tracking_checked_at'], 'source': evidence['source']}, stock_result=stock))
    db.session.commit()
    return {'id': binding.id, 'state': 'succeeded', 'order_number': order.order_number,
            'after_sale_id': binding.after_sale_id, 'stock_result': stock}


def auto_reconcile(limit_per_shop=50, shop_id=None):
    """Apply cached movement evidence for pending orders without a manual click.

    This never calls 4PX and never trusts a client snapshot. Only records already
    classified as ``has_movement`` and still linked to a pending order/after-sale
    are eligible; every deduction still runs through ``_apply`` under the shop lock.
    """
    from flask import current_app
    from app.models import Shop as ShopModel
    from app.services import logistics_service as tracking_service

    if not current_app.config.get("TRACKING_AUTO_OUTBOUND", True):
        return {"shops": 0, "succeeded": 0, "skipped": 0, "failed": 0}
    summary = {"shops": 0, "succeeded": 0, "skipped": 0, "failed": 0}
    if shop_id is not None:
        shops = ShopModel.query.filter(ShopModel.id == shop_id).all()
    else:
        shops = ShopModel.query.filter(
            ShopModel.is_active.is_(True),
            ShopModel.fourpx_app_key_encrypted.isnot(None),
            ShopModel.fourpx_app_secret_encrypted.isnot(None),
        ).order_by(ShopModel.id).all()
    for shop in shops:
        rows = tracking_service._query(shop.id).filter(
            tracking_service._tab_filter("pending", datetime.utcnow())
        ).order_by(TrackingBinding.id).limit(limit_per_shop).all()
        if not rows:
            continue
        summary["shops"] += 1
        for binding, record, _order, _sale, _parcel in rows:
            evidence = {
                "has_movement": True,
                "latest": record.latest,
                "event_count": record.event_count,
                "tracking_checked_at": record.checked_at.isoformat() if record.checked_at else None,
                "source": "auto_reconcile",
            }
            try:
                result = _apply(binding.id, shop.id, evidence)
                state = result.get("state")
                summary[state if state in ("succeeded", "skipped", "failed") else "failed"] += 1
            except Exception:  # noqa: BLE001 - one bad binding must not stop the shop
                db.session.rollback()
                summary["failed"] += 1
    return summary


def batch_outbound(payload):
    shop_id = tracking._shop_id(payload.get('shop_id'))
    ids = payload.get('ids')
    if (payload.get('confirmed') is not True or not isinstance(ids, list) or not 1 <= len(ids) <= 20
            or any(isinstance(value, bool) or not isinstance(value, int) for value in ids)):
        raise ValidationError('请确认强制补记出库，并选择1–20条记录')
    ids = list(dict.fromkeys(ids))
    rows = TrackingBinding.query.filter(TrackingBinding.shop_id == shop_id,
        TrackingBinding.active.is_(True), TrackingBinding.id.in_(ids)).all()
    if len(rows) != len(ids):
        raise ValidationError('记录不属于当前店铺或绑定已失效，请刷新列表')
    shop = db.session.get(Shop, shop_id)
    numbers = list(dict.fromkeys(row.record.number for row in rows))
    facts = tracking.fetch_facts(shop, numbers)  # Fresh signed evidence, never trust client snapshots.
    targets = [(row.id, row.record.number) for row in rows]
    for row in rows:
        tracking.save_facts(row.record, facts[row.record.number])
    db.session.commit()
    results = []
    for binding_id, number in targets:
        evidence = facts[number]
        if evidence.get('error') or not evidence.get('has_movement'):
            results.append({'id': binding_id, 'state': 'failed', 'message': '本次未核实到实际运输轨迹，禁止补记出库'})
            continue
        try:
            results.append(_apply(binding_id, shop_id, evidence))
        except Exception as error:  # noqa: BLE001 - one failed order must not roll back prior successes
            db.session.rollback()
            results.append({'id': binding_id, 'state': 'failed',
                'message': error.message if isinstance(error, ApiError) else '补记失败，请刷新核对；未重复扣减库存'})
    return {'results': results}
