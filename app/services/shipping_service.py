"""Local parcel-label and physical outbound workflow.

Shopify fulfillment is deliberately not read here: creating a label or marking
Shopify fulfilled must never consume warehouse stock.
"""

from datetime import datetime

from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError

from app import db
from app.models import ACTION_SCAN, ACTION_UNDO, Order, OutboundEvent, ShippingParcel, Shop
from app.utils.errors import ForbiddenError, NotFoundError, ValidationError
from app.utils.security import can_access_shop, current_user


def _order_for_update(order_id):
    order = Order.query.filter_by(id=order_id).populate_existing().with_for_update().one_or_none()
    if order is None:
        raise NotFoundError("订单不存在")
    if not can_access_shop(order.shop_id):
        raise ForbiddenError("无权操作该订单")
    if order.shop_id is None:
        raise ValidationError("订单尚未绑定店铺，不能操作物流标签")
    return order


def normalize_label_code(value):
    """Physical labels are case-insensitive; store and compare them uppercase."""
    return str(value or "").strip().upper()


def _label_number(payload):
    number = normalize_label_code(payload.get("shipping_label_number"))
    if not number or len(number) > 120 or any(char.isspace() for char in number):
        raise ValidationError("请填写不含空格、最长 120 字符的物流标签单号")
    return number


def _check_label_unique(order, number, exclude_parcel_id=None):
    duplicate = ShippingParcel.query.filter(
        ShippingParcel.shop_id == order.shop_id,
        or_(func.upper(ShippingParcel.label_number) == number,
            func.upper(ShippingParcel.label_barcode) == number),
        ShippingParcel.id != exclude_parcel_id,
    ).first()
    if duplicate:
        raise ValidationError("该物流标签单号已绑定本店铺的其他包裹")


def _commit_labels():
    try:
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        raise ValidationError("该物流标签单号已绑定本店铺的其他包裹") from exc


def parcel_matches_code(parcel, code):
    """这一枪扫到的编号是不是这张面单的。

    客户手里的面单形态不统一：多数只有一个一维码（就是运单号），
    4PX 也有「运单号 + 另一个条码」两串编号的情况。两种都认，
    否则只存了运单号的 4PX 面单会永远扫不出库。

    运单号和条码在同一个店铺内各自唯一（见 ShippingParcel 的唯一约束），
    所以按哪一串命中都是同一张面单，不存在串单风险。
    """
    if not code:
        return False
    value = normalize_label_code(code)
    return value in {normalize_label_code(parcel.label_barcode),
                     normalize_label_code(parcel.label_number)}


def find_parcel_by_code(shop_id, code):
    """按扫到的编号找包裹：先按实体条码，再按运单号；大小写不敏感。"""
    value = normalize_label_code(code)
    parcel = ShippingParcel.query.filter(
        ShippingParcel.shop_id == shop_id,
        func.upper(ShippingParcel.label_barcode) == value,
    ).first()
    if parcel is not None:
        return parcel
    return ShippingParcel.query.filter(
        ShippingParcel.shop_id == shop_id,
        func.upper(ShippingParcel.label_number) == value,
    ).first()


def order_outbound_blockers(order, parcels=None, *, check_stock=True):
    """列出阻止这个订单扫码出库的原因，空列表表示现在就能出库。

    只做订单层面的检查（状态 / 付款 / 标签是否齐全 / 商品是否备齐），
    不检查「这一枪扫的是不是本单的条码」——那是调用方的事。
    """
    parcels = list(
        parcels
        if parcels is not None
        else ShippingParcel.query.filter_by(order_id=order.id).order_by(ShippingParcel.id).all()
    )
    blockers = []
    if order.warehouse_status in ("legacy_unverified", "legacy_partial_unverified"):
        blockers.append("历史订单出库状态待核实，不能重复扫码出库")
    elif order.warehouse_status == "shipped":
        blockers.append("订单已完成出库，请勿重复扫码")
    elif order.warehouse_status != "pending":
        blockers.append("订单当前状态不能扫码出库")
    if len(parcels) != (order.expected_parcel_count or 1):
        blockers.append("物流标签尚未齐全，不能扫码出库")
    if order.cancelled_at or order.financial_status not in ("paid", "partially_refunded"):
        blockers.append("未付款或已退款的订单不能出库")

    # 状态/付款/标签这些硬条件已经拦住了，就不用再去算商品是否备齐：
    # 一是没意义（比如已经出库的订单，扣完库存当然"不备齐"），
    # 二是 attach_procurement_status 不便宜。
    if blockers or not check_stock:
        return blockers

    from app.services.outbound_inventory_service import quantity_plan
    from app.services.packing_session_service import has_physical_outbound, validate_committed_snapshot
    try:
        if has_physical_outbound(order):
            validate_committed_snapshot(order)
        else:
            quantity_plan(order)
    except ValidationError:
        blockers.append("订单商品尚未备齐，不能扫码出库")
    return blockers


def _summary(order):
    parcels = ShippingParcel.query.filter_by(order_id=order.id).order_by(ShippingParcel.id).all()
    return {
        "order_id": order.id,
        "warehouse_status": order.warehouse_status,
        "shipping_label_number": order.shipping_label_number,
        "shipping_label_source": order.shipping_label_source,
        "expected_parcel_count": order.expected_parcel_count,
        "scanned_parcel_count": sum(parcel.scanned_at is not None for parcel in parcels),
        "parcels": [parcel.to_dict() for parcel in parcels],
        "shipped_at": order.shipped_at.isoformat() if order.shipped_at else None,
    }


def _current_user_id():
    return (current_user() or {}).get("user_id")


def record_outbound_event(
    order, *, action, code=None, parcel=None, after_sale_id=None, reason=None, user_id=None
):
    """记一条出库/撤回流水。调用方负责 commit。"""
    db.session.add(OutboundEvent(
        shop_id=order.shop_id,
        order_id=order.id,
        parcel_id=parcel.id if parcel is not None else None,
        after_sale_id=after_sale_id,
        order_number=order.order_number,
        action=action,
        code=(code or "")[:120] or None,
        user_id=user_id if user_id is not None else _current_user_id(),
        reason=(reason or "")[:255] or None,
    ))


def set_expected_parcel_count(order_id, payload):
    order = _order_for_update(order_id)
    raw = payload.get("expected_parcel_count")
    if isinstance(raw, bool) or not isinstance(raw, int) or not 1 <= raw <= 10:
        raise ValidationError("包裹数必须为 1–10 的整数")
    if order.warehouse_status != "pending":
        raise ValidationError("已出库或状态待核实的订单不能修改包裹数")
    parcels = ShippingParcel.query.filter_by(order_id=order.id).all()
    if any(parcel.scanned_at for parcel in parcels):
        raise ValidationError("已有包裹扫码，不能修改应有包裹数")
    if raw < len(parcels):
        raise ValidationError("应有包裹数不能少于已登记标签数")
    order.expected_parcel_count = raw
    db.session.commit()
    return _summary(order)


def add_shipping_parcel(order_id, payload, source="manual"):
    order = _order_for_update(order_id)
    number = _label_number(payload)
    if order.warehouse_status != "pending":
        raise ValidationError("已出库或状态待核实的订单不能添加包裹")
    if ShippingParcel.query.filter_by(order_id=order.id).count() >= order.expected_parcel_count:
        raise ValidationError("已登记标签数达到应有包裹数，请先核对包裹数")
    _check_label_unique(order, number)
    parcel = ShippingParcel(shop_id=order.shop_id, order_id=order.id, label_number=number,
                            label_barcode=number if source == "manual" else None, source=source)
    db.session.add(parcel)
    if not order.shipping_label_number:
        order.shipping_label_number = number
        order.shipping_label_source = source
    _commit_labels()
    return _summary(order)


def save_shipping_label(order_id, payload, source="manual"):
    """Keep the legacy single-label endpoint for one-parcel integrations."""
    order = _order_for_update(order_id)
    number = _label_number(payload)
    if order.expected_parcel_count != 1:
        raise ValidationError("多包裹订单请逐个登记物流标签")
    parcels = ShippingParcel.query.filter_by(order_id=order.id).order_by(ShippingParcel.id).all()
    if not parcels:
        return add_shipping_parcel(order_id, payload, source)
    parcel = parcels[0]
    if normalize_label_code(parcel.label_number) == number:
        return _summary(order)
    if order.warehouse_status != "pending" or parcel.scanned_at:
        raise ValidationError("已扫码的物流标签不能更换")
    _check_label_unique(order, number, parcel.id)
    parcel.label_number = number
    parcel.label_barcode = number if source == "manual" else None
    parcel.source = source
    order.shipping_label_number = number
    order.shipping_label_source = source
    _commit_labels()
    return _summary(order)


def delete_shipping_parcel(order_id, parcel_id):
    order = _order_for_update(order_id)
    if order.warehouse_status != "pending":
        raise ValidationError("已出库或状态待核实的订单不能删除物流标签")
    parcel = ShippingParcel.query.filter_by(id=parcel_id, order_id=order.id).first()
    if parcel is None:
        raise NotFoundError("包裹不存在")
    if parcel.scanned_at:
        raise ValidationError("已扫码包裹不能删除物流标签")
    db.session.delete(parcel)
    db.session.flush()
    first = ShippingParcel.query.filter_by(order_id=order.id).order_by(ShippingParcel.id).first()
    order.shipping_label_number = first.label_number if first else None
    order.shipping_label_source = first.source if first else None
    db.session.commit()
    return _summary(order)


def scan_outbound(order_id, payload):
    """Scan one parcel; finish the order only after every expected label was scanned."""
    existing = db.session.get(Order, order_id)
    if existing is None:
        raise NotFoundError("订单不存在")
    if not can_access_shop(existing.shop_id):
        raise ForbiddenError("无权操作该订单")
    Shop.query.filter_by(id=existing.shop_id).with_for_update().one()
    order = _order_for_update(order_id)
    scanned = _label_number(payload)
    parcels = ShippingParcel.query.filter_by(order_id=order.id).order_by(ShippingParcel.id).all()
    parcel = next((item for item in parcels if parcel_matches_code(item, scanned)), None)
    if parcel is None:
        raise ValidationError("扫描单号与实体面单条码不一致；4PX 面单请先获取并核对条码")
    if parcel.scanned_at:
        raise ValidationError("该包裹已扫码，禁止重复出库")
    blockers = order_outbound_blockers(order, parcels, check_stock=False)
    if blockers:
        raise ValidationError(blockers[0])

    from app.services.packing_session_service import begin_packing, record_physical_outbound, has_physical_outbound, validate_committed_snapshot
    transfers = []
    if not has_physical_outbound(order):
        if any(item.scanned_at for item in parcels):
            raise ValidationError('历史部分出库缺少实物扣减流水，请先撤回并核对库存，再重新扫码')
        packing = begin_packing({"shop_id": order.shop_id, "kind": "order", "id": order.id},
                                commit=False, for_outbound=True)
        transfers = packing.get('inventory_transfers', [])
        # A multi-parcel order commits its full packing quantity at the first scan.
        # Subsequent parcel scans only finish the order; they never deduct again.
        record_physical_outbound(order)
    else:
        validate_committed_snapshot(order)

    parcel.scanned_at = datetime.utcnow()
    parcel.scanned_by_user_id = _current_user_id()
    if all(item.scanned_at for item in parcels):
        order.warehouse_status = "shipped"
        order.shipped_at = parcel.scanned_at
    reason = None
    if transfers:
        reason = '扫码动态调配：' + '；'.join(
            f"{entry['from_order_number'] or '可用库存'} {entry['quantity']} 件"
            + ('（通用型号）' if entry['source_group_key'] != entry['target_group_key'] else '')
            for entry in transfers
        )
    record_outbound_event(order, action=ACTION_SCAN, code=scanned, parcel=parcel, reason=reason)
    db.session.commit()
    result = _summary(order)
    result['inventory_transfers'] = transfers
    return result


def undo_outbound(order_id, payload=None):
    """撤回一次出库：把订单退回「待出库」，清掉本次的扫码记录。

    库存不是单独记的账，而是从「订单是否已出库」算出来的（见 inventory_service），
    所以把状态退回去，在库/可用数量会自动回到出库前的样子，不需要另行冲销。

    整单一起退：多包裹订单如果只扫了一半，也会把这半数的扫码记录一起清掉，
    避免留下一半已扫一半没扫的中间态让人看不懂。
    """
    payload = payload or {}
    existing = db.session.get(Order, order_id)
    if existing is None:
        raise NotFoundError("订单不存在")
    if not can_access_shop(existing.shop_id):
        raise ForbiddenError("无权操作该订单")
    Shop.query.filter_by(id=existing.shop_id).with_for_update().one()
    order = _order_for_update(order_id)
    if order.warehouse_status not in ("pending", "shipped"):
        raise ValidationError("历史订单出库状态待核实，不能撤回")

    parcels = ShippingParcel.query.filter_by(order_id=order.id).order_by(ShippingParcel.id).all()
    scanned = [parcel for parcel in parcels if parcel.scanned_at]
    if not scanned:
        raise ValidationError("这单还没有扫码出库记录，不需要撤回")

    last_parcel = scanned[-1]
    code = last_parcel.label_barcode or last_parcel.label_number
    reason = str(payload.get("reason") or "").strip()
    if len(reason) > 255:
        raise ValidationError("撤回原因最长 255 字")

    for parcel in scanned:
        parcel.scanned_at = None
        parcel.scanned_by_user_id = None
    order.warehouse_status = "pending"
    order.shipped_at = None
    from app.services.packing_session_service import return_physical_outbound
    return_physical_outbound(order)
    record_outbound_event(
        order, action=ACTION_UNDO, code=code, parcel=last_parcel, reason=reason
    )
    db.session.commit()
    return _summary(order)
