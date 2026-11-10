"""Packing records are advisory; only physical scanning consumes shared stock."""
from collections import Counter
from datetime import datetime, timedelta

from app import db
from app.models import AfterSale, InventoryOutboundLine, Order, PackingSession, Shop, ShippingParcel
from app.utils.errors import ForbiddenError, NotFoundError, ValidationError
from app.utils.security import can_access_shop, current_user, is_admin

HOLD_DURATION = timedelta(minutes=30)


def _target(payload):
    from app.services.purchase_service import _positive_int
    shop_id = _positive_int(payload.get('shop_id'), 'shop_id')
    if not can_access_shop(shop_id):
        raise ForbiddenError('无权操作该店铺打包任务')
    if db.session.get(Shop, shop_id) is None:
        raise NotFoundError('店铺不存在')
    kind = payload.get('kind', 'order')
    if kind not in ('order', 'after_sale'):
        raise ValidationError('打包对象类型错误')
    target_id = _positive_int(payload.get('id'), 'id')
    model = Order if kind == 'order' else AfterSale
    record = model.query.filter_by(id=target_id, shop_id=shop_id).first()
    if record is None or (kind == 'after_sale' and record.deleted_at):
        raise NotFoundError('订单或售后单不存在')
    return shop_id, kind, record


def serialize_session(session):
    return {'id': session.id, 'state': session.state, 'user_id': session.user_id,
            'expires_at': session.expires_at.isoformat()+'Z', 'lines': session.lines,
            'order_id': session.order_id, 'after_sale_id': session.after_sale_id}


def current_session(shop_id, *, order_id=None, after_sale_id=None):
    if order_id is None and after_sale_id is None:
        return None
    query = PackingSession.query.filter_by(shop_id=shop_id, state='packing')
    query = query.filter_by(order_id=order_id) if order_id is not None else query.filter_by(after_sale_id=after_sale_id)
    partial = db.session.query(ShippingParcel.id).filter(
        ShippingParcel.order_id == PackingSession.order_id,
        ShippingParcel.scanned_at.isnot(None)).exists()
    return query.filter((PackingSession.expires_at > datetime.utcnow()) | partial).first()


def validate_snapshot(record, lines):
    from app.services.outbound_inventory_service import _key
    from app.utils.order_lines import is_tip_line
    expected = Counter()
    prior = Counter()
    for item in record.items:
        if item.quantity > 0 and not is_tip_line(item):
            expected[getattr(item, 'group_key', None) or _key(item)] += item.quantity
    for line in lines:
        prior[line.get('target_key') or line['group_key']] += line['quantity']
    if expected != prior:
        raise ValidationError('开始核对后订单商品发生变化，请取消核对或撤回出库后重新清点')


def validate_committed_snapshot(order):
    session = PackingSession.query.filter_by(shop_id=order.shop_id, order_id=order.id,
        state='completed').order_by(PackingSession.id.desc()).first()
    if session is None:
        raise ValidationError('缺少已扣减装箱快照，请先撤回并核实库存')
    validate_snapshot(order, session.lines)


def begin_packing(payload, *, commit=True, for_outbound=False):
    shop_id, kind, record = _target(payload)
    Shop.query.filter_by(id=shop_id).with_for_update().one()
    db.session.refresh(record, with_for_update=True)
    if kind == 'order':
        if record.warehouse_status != 'pending' or record.cancelled_at or record.financial_status not in ('paid', 'partially_refunded'):
            raise ValidationError('已出库、未付清或已取消的订单不能开始打包')
    elif record.shipped_at or record.status == 'shipped':
        raise ValidationError('售后包裹已出库')
    user_id = (current_user() or {}).get('user_id')
    session = current_session(shop_id, **{f'{kind}_id': record.id})
    if session and not for_outbound and session.user_id != user_id:
        raise ValidationError('该包裹已有员工核对，请勿重复领取；这不会占用库存')
    from app.services.outbound_inventory_service import quantity_plan, apply_outbound_stock
    if session and for_outbound:
        validate_snapshot(record, session.lines)
    plan = quantity_plan(record)
    lines = [{'group_key': step['source_key'], 'target_key': step['target_key'],
              'quantity': step['quantity']} for step in plan]
    if not session:
        session = PackingSession(shop_id=shop_id, order_id=record.id if kind == 'order' else None,
            after_sale_id=record.id if kind == 'after_sale' else None,
            user_id=user_id, lines=lines, expires_at=datetime.utcnow()+HOLD_DURATION)
        db.session.add(session)
    else:
        session.lines = lines  # Revalidate counts at the actual scan, never trust an old preview.
        session.expires_at = datetime.utcnow()+HOLD_DURATION
    db.session.flush()
    if commit:
        db.session.commit()
    return {**serialize_session(session), 'inventory_transfers': apply_outbound_stock(record, plan)}


def cancel_packing(payload):
    shop_id, kind, record = _target(payload)
    Shop.query.filter_by(id=shop_id).with_for_update().one()
    session = current_session(shop_id, **{f'{kind}_id': record.id})
    if session is None:
        raise ValidationError('当前没有有效打包记录')
    if session.user_id != (current_user() or {}).get('user_id') and not is_admin():
        raise ForbiddenError('只能取消自己的打包任务')
    if kind == 'order' and ShippingParcel.query.filter(
        ShippingParcel.order_id == record.id, ShippingParcel.scanned_at.isnot(None)).first():
        raise ValidationError('已有包裹扫码出库，请先按整单撤回')
    session.state, session.ended_at = 'cancelled', datetime.utcnow()
    db.session.commit()
    return serialize_session(session)


def set_priority(payload):
    shop_id, kind, record = _target(payload)
    if kind != 'order':
        raise ValidationError('仅原订单支持采购需求优先级')
    priority = payload.get('priority')
    if isinstance(priority, bool) or not isinstance(priority, int) or priority not in (0, 100):
        raise ValidationError('优先级必须为普通（0）或加急（100）')
    Shop.query.filter_by(id=shop_id).with_for_update().one()
    db.session.refresh(record, with_for_update=True)
    if record.warehouse_status != 'pending':
        raise ValidationError('已出库订单不能修改优先级')
    record.fulfillment_priority = priority
    record.procurement_excluded_at = None
    from app.services.shipping_service import record_outbound_event
    record_outbound_event(record, action='priority', reason='采购需求加急' if priority else '恢复普通优先级')
    db.session.commit()
    return {'id': record.id, 'priority': priority}


def finish_session(shop_id, *, order_id=None, after_sale_id=None, state='completed'):
    if order_id is None and after_sale_id is None:
        return
    query = PackingSession.query.filter_by(shop_id=shop_id, state='packing')
    query = query.filter_by(order_id=order_id) if order_id is not None else query.filter_by(after_sale_id=after_sale_id)
    for session in query.all():
        session.state, session.ended_at = state, datetime.utcnow()


def has_physical_outbound(order):
    lines = InventoryOutboundLine.query.filter_by(shop_id=order.shop_id, order_id=order.id).order_by(
        InventoryOutboundLine.id).all()
    markers = [line for line in lines if line.action in ('carrier_confirm', 'carrier_undo')]
    return (sum(line.quantity_delta for line in lines) < 0
            or bool(markers and markers[-1].action == 'carrier_confirm'))


def record_physical_outbound(order):
    session = current_session(order.shop_id, order_id=order.id)
    if session is None:
        raise ValidationError('请重新核对当前实物库存')
    if has_physical_outbound(order):
        raise ValidationError('订单已有有效出库扣减，禁止重复扣减')
    for line in session.lines:
        db.session.add(InventoryOutboundLine(shop_id=order.shop_id, order_id=order.id,
            group_key=line['group_key'], quantity_delta=-line['quantity'], action='outbound',
            user_id=(current_user() or {}).get('user_id')))
    finish_session(order.shop_id, order_id=order.id)


def return_physical_outbound(order):
    totals = Counter()
    lines = InventoryOutboundLine.query.filter_by(shop_id=order.shop_id, order_id=order.id).order_by(
        InventoryOutboundLine.id).all()
    for line in lines:
        totals[line.group_key] += line.quantity_delta
    for key, delta in totals.items():
        if delta < 0:
            db.session.add(InventoryOutboundLine(shop_id=order.shop_id, order_id=order.id,
                group_key=key, quantity_delta=-delta, action='undo', user_id=(current_user() or {}).get('user_id')))
    markers = [line for line in lines if line.action in ('carrier_confirm', 'carrier_undo')]
    if markers and markers[-1].action == 'carrier_confirm':
        db.session.add(InventoryOutboundLine(shop_id=order.shop_id, order_id=order.id,
            group_key=markers[-1].group_key, quantity_delta=0, action='carrier_undo',
            user_id=(current_user() or {}).get('user_id')))
    finish_session(order.shop_id, order_id=order.id, state='cancelled')
