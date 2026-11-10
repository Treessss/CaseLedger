"""Shop-scoped carrier observations; fast indexed lists, durable bounded jobs."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import re
from uuid import uuid4

from sqlalchemy import and_, case, func, or_, update
from sqlalchemy.orm import Load, load_only

from app import db
from app.models import (AfterSale, Order, ShippingParcel, Shop, TrackingBinding,
                        TrackingRecord, TrackingRefreshJob, TrackingOutboundAudit,
                        TrackingChange, TrackingDiscoveryState, User)
from app.models.shop import ShopCredentialError
from app.services.fourpx_client import FourPxClient, FourPxError
from app.services.mail_tracking_service import _event_time, _is_fourpx, is_delivered_event, normalize_tracking
from app.services.shipping_queue_service import _shop_id
from app.utils.errors import NotFoundError, ValidationError
from app.utils.security import current_user

TABS = ('preinfo', 'pending', 'overdue')
SCOPES = ('all', 'due', 'history', 'single', *TABS)


def stamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).replace(tzinfo=None) if value else None


def _number(value):
    value = str(value or '').lstrip('：: \t\r\n')
    return value if re.fullmatch(r'[A-Za-z0-9-]{1,120}', value) else None


def _order_numbers(order):
    result = set()
    for item in order.fulfillments or []:
        if str(item.get('status') or '').upper() == 'CANCELLED':
            continue
        numbers = item.get('tracking_numbers') or [item.get('tracking_number')]
        urls = item.get('tracking_urls') or []
        for index, raw in enumerate(numbers):
            number = _number(raw)
            if number and _is_fourpx({'number': number, 'company': item.get('tracking_company'),
                    'url': urls[index] if index < len(urls) else item.get('tracking_url')}):
                result.add(number)
    number = _number(order.shipping_label_number)
    if number and (_is_fourpx({'number': number}) or order.shipping_label_source == '4px'):
        result.add(number)
    return result


def discover(shop_id, order_ids=None, *, history=True, _expanded=False):
    """Reconcile only changed/paged orders. None is reserved for explicit legacy one-off callers."""
    if order_ids is not None and not order_ids:
        return
    order_query = db.session.query(Order.id, Order.fulfillments, Order.shipping_label_number,
                                  Order.shipping_label_source).filter_by(shop_id=shop_id)
    sale_query = db.session.query(AfterSale.id, AfterSale.order_id, AfterSale.tracking_number).filter(
        AfterSale.shop_id == shop_id, AfterSale.deleted_at.is_(None), AfterSale.tracking_number.isnot(None))
    parcel_query = db.session.query(ShippingParcel.id, ShippingParcel.order_id,
                                   ShippingParcel.label_number, ShippingParcel.source).filter_by(shop_id=shop_id)
    binding_query = TrackingBinding.query.filter_by(shop_id=shop_id)
    if order_ids is not None:
        order_query = order_query.filter(Order.id.in_(order_ids))
        sale_query = sale_query.filter(AfterSale.order_id.in_(order_ids))
        parcel_query = parcel_query.filter(ShippingParcel.order_id.in_(order_ids))
        binding_query = binding_query.filter(TrackingBinding.order_id.in_(order_ids))
    orders, sales, parcels = order_query.all(), sale_query.all(), parcel_query.all()
    numbers = set()
    numbers.update(value for (value,) in binding_query.join(TrackingRecord).with_entities(TrackingRecord.number).all())
    for order in orders:
        numbers.update(_order_numbers(order))
    numbers.update(number for row in [*sales, *parcels] if (number := _number(
        getattr(row, 'tracking_number', None) or getattr(row, 'label_number', None))))
    if order_ids is not None and not _expanded and numbers:
        related = {value for (value,) in db.session.query(TrackingBinding.order_id).join(TrackingRecord).filter(
            TrackingBinding.shop_id == shop_id, TrackingRecord.number.in_(numbers)).all()}
        if related.difference(order_ids):
            return discover(shop_id, list(set(order_ids) | related), history=history, _expanded=True)
    # A CF can override an original even when only that original changed.
    global_sale_numbers = {number for (number,) in db.session.query(AfterSale.tracking_number).filter(
        AfterSale.shop_id == shop_id, AfterSale.deleted_at.is_(None), AfterSale.tracking_number.in_(numbers)).all()}
    sale_numbers = {}
    wanted = {}
    for sale in sales:
        number = _number(sale.tracking_number)
        if number and _is_fourpx({'number': number, 'company': '4PX'}):
            sale_numbers.setdefault(number, []).append(sale)
            wanted[(f'sale:{sale.id}', number)] = (sale.order_id, sale.id, None)
    for order in orders:
        for number in _order_numbers(order):
            if number not in global_sale_numbers:
                wanted[(f'order:{order.id}', number)] = (order.id, None, None)
    for parcel in parcels:
        number = _number(parcel.label_number)
        if number and number not in global_sale_numbers and (
                parcel.source == '4px' or _is_fourpx({'number': number})):
            wanted[(f'order:{parcel.order_id}', number)] = (parcel.order_id, None, parcel.id)
    wanted_numbers = {number for _, number in wanted}
    records = {row.number: row for row in TrackingRecord.query.options(load_only(
        TrackingRecord.id, TrackingRecord.number, TrackingRecord.requested_at, TrackingRecord.next_check_at,
        TrackingRecord.checked_at, TrackingRecord.attempted_at, TrackingRecord.error_count, TrackingRecord.error,
        TrackingRecord.only_preinfo, TrackingRecord.has_movement, TrackingRecord.delivered_at)).filter(
            TrackingRecord.shop_id == shop_id, TrackingRecord.number.in_(wanted_numbers)).all()}
    for _, number in wanted:
        if number not in records:
            records[number] = TrackingRecord(shop_id=shop_id, number=number)
            db.session.add(records[number])
    db.session.flush()
    bindings = {(row.target_key, row.record_id): row
                for row in binding_query.all()}
    active = set()
    for (target, number), (order_id, sale_id, parcel_id) in wanted.items():
        identity = target, records[number].id
        row = bindings.get(identity)
        if row is None:
            row = TrackingBinding(shop_id=shop_id, target_key=target, record_id=identity[1],
                                  order_id=order_id, after_sale_id=sale_id)
            db.session.add(row)
        if not history and records[number].attempted_at is None:
            records[number].requested_at = datetime.utcnow()
        row.active, row.parcel_id = True, parcel_id
        active.add(identity)
    for identity, row in bindings.items():
        if identity not in active:
            row.active = False
    db.session.flush()
    reschedule(records.values())


def _active_record():
    return db.session.query(TrackingBinding.id).filter(
        TrackingBinding.record_id == TrackingRecord.id, TrackingBinding.active.is_(True)).exists()


def reschedule(records):
    """Recalculate from warehouse state, not Shopify fulfillment; no carrier calls."""
    records = list(records)
    if not records:
        return
    states = {}
    for binding, _track, order, sale, parcel in _query(records[0].shop_id).filter(
            TrackingRecord.id.in_([record.id for record in records])).options(
                load_only(TrackingRecord.id)).all():
        outbound = bool((sale.status == 'shipped' or sale.shipped_at) if sale else (
            order.warehouse_status == 'shipped' or (parcel and parcel.scanned_at)))
        at = sale.shipped_at if sale else ((parcel.scanned_at if parcel else None) or order.shipped_at)
        states.setdefault(binding.record_id, []).append((outbound, at))
    now = datetime.utcnow()
    for record in records:
        if record.delivered_at and not record.requested_at:
            record.next_check_at = None
        elif record.requested_at:
            record.next_check_at = record.requested_at
        elif record.error:
            record.next_check_at = (record.attempted_at or now) + timedelta(
                minutes=min(30 * 2**min(max(record.error_count-1, 0), 4), 360))
        elif record.checked_at is None:
            record.next_check_at = record.next_check_at or now
        else:
            targets = states.get(record.id, [])
            not_outbound = any(not shipped for shipped, _at in targets)
            interval = 6 if record.has_movement else 1 if not_outbound else 2
            next_at = record.checked_at + timedelta(hours=interval)
            if not record.has_movement and not not_outbound:
                dates = [at for _shipped, at in targets if at]
                if dates:
                    next_at = max(next_at, max(dates) + timedelta(days=1))
            record.next_check_at = next_at


def drain_changes(shop_id, limit=200):
    changes = TrackingChange.query.filter_by(shop_id=shop_id).order_by(
        TrackingChange.updated_at, TrackingChange.order_id).with_for_update(skip_locked=True).limit(limit).all()
    if changes:
        discover(shop_id, [change.order_id for change in changes], history=False)
        for change in changes:
            db.session.delete(change)


def bootstrap(shop_id, limit=200):
    state = lock_state(shop_id)

    if state.completed:
        return
    ids = [value for (value,) in db.session.query(Order.id).filter(Order.shop_id == shop_id,
        Order.id > state.cursor, Order.id <= state.end_id).order_by(Order.id).limit(limit).all()]
    discover(shop_id, ids, history=True)
    state.cursor = ids[-1] if ids else state.end_id
    state.completed = state.cursor >= state.end_id


def lock_state(shop_id):
    # Routine logistics scheduling must not lock the Shop row used by fast warehouse scans.
    state = TrackingDiscoveryState.query.filter_by(shop_id=shop_id).populate_existing().with_for_update().first()
    if state is None:
        Shop.query.filter_by(id=shop_id).with_for_update().one()  # Once-only state creation race.
        state = TrackingDiscoveryState.query.filter_by(shop_id=shop_id).populate_existing().with_for_update().first()
        if state is None:
            state = TrackingDiscoveryState(shop_id=shop_id, end_id=db.session.query(func.max(Order.id)).filter_by(
                shop_id=shop_id).scalar() or 0)
            db.session.add(state)
            db.session.flush()
    return state


def _query(shop_id):
    return db.session.query(TrackingBinding, TrackingRecord, Order, AfterSale, ShippingParcel).options(
        Load(Order).noload('*'), Load(AfterSale).noload('*'), Load(ShippingParcel).noload('*'),
        load_only(Order.id, Order.order_number, Order.warehouse_status, Order.shipped_at),
        load_only(AfterSale.id, AfterSale.customer_reference, AfterSale.status, AfterSale.shipped_at,
                  AfterSale.deleted_at)).join(
        TrackingRecord, TrackingRecord.id == TrackingBinding.record_id).join(
        Order, Order.id == TrackingBinding.order_id).outerjoin(
        AfterSale, AfterSale.id == TrackingBinding.after_sale_id).outerjoin(
        ShippingParcel, ShippingParcel.id == TrackingBinding.parcel_id).filter(
        TrackingBinding.shop_id == shop_id, TrackingRecord.shop_id == shop_id,
        Order.shop_id == shop_id, TrackingBinding.active.is_(True),
        or_(TrackingBinding.after_sale_id.is_(None), AfterSale.deleted_at.is_(None)))


def _outbound():
    return func.coalesce(or_(and_(TrackingBinding.after_sale_id.isnot(None),
                    or_(AfterSale.shipped_at.isnot(None), AfterSale.status == 'shipped')),
               and_(TrackingBinding.after_sale_id.is_(None),
                    or_(Order.warehouse_status == 'shipped', ShippingParcel.scanned_at.isnot(None)))), False)


def _outbound_at():
    """SQL mirror of the per-row outbound timestamp used by ``serialize``."""
    return case(
        (TrackingBinding.after_sale_id.isnot(None), AfterSale.shipped_at),
        (ShippingParcel.scanned_at.isnot(None), ShippingParcel.scanned_at),
        else_=Order.shipped_at,
    )


def _preinfo_age_filter(age, now):
    cutoff = now - timedelta(days=1)
    if age == 'within24h':
        return _outbound_at() >= cutoff
    if age == 'beyond24h':
        return or_(_outbound_at().is_(None), _outbound_at() < cutoff)
    return None


def _tab_filter(tab, now):
    if tab == 'preinfo':
        return and_(_outbound(), TrackingRecord.only_preinfo.is_(True))
    if tab == 'pending':
        return and_(~_outbound(), TrackingRecord.has_movement.is_(True))
    if tab == 'overdue':
        return and_(TrackingRecord.first_preinfo_at <= now - timedelta(days=15),
                    TrackingRecord.delivered_at.is_(None))
    raise ValidationError('物流分类无效')


def serialize(row, handlers=None):
    binding, track, order, sale, parcel = row
    outbound_at = (sale.shipped_at if sale else (parcel.scanned_at if parcel else None) or order.shipped_at)
    outbound = bool((sale.status == 'shipped' or sale.shipped_at) if sale else (
        order.warehouse_status == 'shipped' or (parcel and parcel.scanned_at)))
    return {'id': binding.id, 'order_id': order.id, 'order_number': sale.customer_reference if sale else order.order_number,
        'after_sale_id': binding.after_sale_id, 'number': track.number, 'latest': track.latest,
        'event_count': track.event_count, 'checked_at': track.checked_at.isoformat() if track.checked_at else None,
        'first_preinfo_at': track.first_preinfo_at.isoformat() if track.first_preinfo_at else None,
        'delivered_at': track.delivered_at.isoformat() if track.delivered_at else None,
        'outbound': outbound, 'outbound_at': outbound_at.isoformat() if outbound_at else None,
        'observing': bool(outbound and outbound_at and outbound_at > datetime.utcnow() - timedelta(days=1)),
        'error': track.error, 'events': track.events,
        'handled': track.handled_at is not None,
        'handled_at': track.handled_at.isoformat() if track.handled_at else None,
        'handled_by': track.handled_by, 'handled_by_name': (handlers or {}).get(track.handled_by),
        'next_check_at': track.next_check_at.isoformat() if track.next_check_at else None,
        'url': 'https://track.4px.com/#/result/0/' + track.number}


def job_data(job):
    return {'id': job.id, 'scope': job.scope, 'status': job.status, 'total': len(job.record_ids or []),
        'done': job.done, 'failed': job.failed, 'skipped': job.skipped, 'error': job.error,
        'updated_at': job.updated_at.isoformat(),
        'finished_at': job.finished_at.isoformat() if job.finished_at else None} if job else None


def _order_sort(direction='asc'):
    """Numeric order before pagination, without casts/overflow on legacy labels.

    ``direction`` only flips the order number; non-numeric labels stay last and
    CF parcels still follow their original order.
    """
    number = func.ltrim(func.trim(Order.order_number), '#')
    non_digits = number
    for digit in '0123456789':
        non_digits = func.replace(non_digits, digit, '')
    numeric = and_(number != '', non_digits == '')
    significant = func.coalesce(func.nullif(func.ltrim(number, '0'), ''), '0')

    def ordered(expression):
        return expression.desc() if direction == 'desc' else expression.asc()

    return (case((numeric, 0), else_=1).asc(),
            ordered(case((numeric, func.length(significant)), else_=0)),
            ordered(case((numeric, significant), else_=number)),
            case((TrackingBinding.after_sale_id.is_(None), 0), else_=1).asc(),
            TrackingBinding.after_sale_id.asc().nullsfirst(),
            ordered(TrackingRecord.number), TrackingBinding.id.asc())


def set_handling(binding_id, payload):
    if not isinstance(payload, dict):
        raise ValidationError('处理状态请求格式错误')
    shop_id = _shop_id(payload.get('shop_id'))
    handled = payload.get('handled')
    if not isinstance(handled, bool):
        raise ValidationError('处理状态必须为已处理或未处理')
    row = _query(shop_id).filter(TrackingBinding.id == binding_id).first()
    if row is None:
        raise NotFoundError('当前店铺的有效物流记录不存在')
    record = TrackingRecord.query.filter_by(id=row[1].id, shop_id=shop_id).populate_existing().with_for_update().one()
    if handled:
        binding, track, order, sale, parcel = row
        outbound_at = (sale.shipped_at if sale else (parcel.scanned_at if parcel else None) or order.shipped_at)
        outbound = bool((sale.status == 'shipped' or sale.shipped_at) if sale else (
            order.warehouse_status == 'shipped' or (parcel and parcel.scanned_at)))
        now = datetime.utcnow()
        overdue = bool(record.first_preinfo_at and record.first_preinfo_at <= now - timedelta(days=15))
        preinfo = bool(record.only_preinfo and outbound)
        if record.delivered_at or not (overdue or preinfo):
            raise ValidationError('此包裹当前不属于「仅一条预报」或「预报满15天未送达」，无需标记处理')
        if record.handled_at is None:
            record.handled_at = datetime.utcnow()
            record.handled_by = (current_user() or {}).get('user_id')
    else:
        record.handled_at, record.handled_by = None, None
    db.session.commit()
    handler = db.session.query(User.username).filter_by(id=record.handled_by).scalar() if record.handled_by else None
    return {'id': binding_id, 'handled': record.handled_at is not None,
            'handled_at': record.handled_at.isoformat() if record.handled_at else None,
            'handled_by': record.handled_by, 'handled_by_name': handler}


def list_records(args):
    shop_id = _shop_id(args.get('shop_id'))
    tab = args.get('tab') or 'preinfo'
    try:
        page = max(1, int(args.get('page') or 1))
        size = min(100, max(1, int(args.get('per_page') or 20)))
    except (ValueError, TypeError):
        raise ValidationError('分页参数错误') from None
    now = datetime.utcnow()
    age = args.get('age') or 'all'
    if age not in ('all', 'within24h', 'beyond24h'):
        raise ValidationError('出库时间筛选无效')
    base = _query(shop_id)
    counts = {name: base.filter(_tab_filter(name, now)).count() for name in TABS}
    overdue = base.filter(_tab_filter('overdue', now))
    overdue_handled = overdue.filter(TrackingRecord.handled_at.isnot(None)).count()
    overdue_handling_counts = {'handled': overdue_handled, 'unhandled': counts['overdue']-overdue_handled}
    # One aggregate keeps the page at the same query budget as before.
    outbound_at = _outbound_at()
    cutoff = now - timedelta(days=1)
    recent = outbound_at >= cutoff
    beyond = or_(outbound_at.is_(None), outbound_at < cutoff)
    handled = TrackingRecord.handled_at.isnot(None)
    preinfo_aggregate = base.filter(_tab_filter('preinfo', now)).with_entities(
        func.coalesce(func.sum(case((recent, 1), else_=0)), 0),
        func.coalesce(func.sum(case((and_(recent, handled), 1), else_=0)), 0),
        func.coalesce(func.sum(case((beyond, 1), else_=0)), 0),
        func.coalesce(func.sum(case((and_(beyond, handled), 1), else_=0)), 0),
        func.coalesce(func.count(), 0),
        func.coalesce(func.sum(case((handled, 1), else_=0)), 0),
    ).one()
    (recent_total, recent_handled, beyond_total, beyond_handled,
     preinfo_total, preinfo_handled) = (int(value) for value in preinfo_aggregate)
    preinfo_age_counts = {'within24h': recent_total, 'beyond24h': beyond_total}
    preinfo_handling_counts = {
        'handled': recent_handled,
        'unhandled': recent_total - recent_handled,
    }
    if tab == 'preinfo':
        if age == 'within24h':
            scope_total, scope_handled = recent_total, recent_handled
        elif age == 'beyond24h':
            scope_total, scope_handled = beyond_total, beyond_handled
        else:
            scope_total, scope_handled = preinfo_total, preinfo_handled
        handling_counts = {'handled': scope_handled, 'unhandled': scope_total - scope_handled}
    else:
        handling_counts = overdue_handling_counts

    query = base.filter(_tab_filter(tab, now))
    if tab == 'preinfo' and age != 'all':
        query = query.filter(_preinfo_age_filter(age, now))
    show_handling = tab == 'overdue' or (tab == 'preinfo' and age in ('within24h', 'beyond24h'))
    if show_handling:
        handling = args.get('handling') or 'unhandled'
        if handling not in ('all', 'unhandled', 'handled'):
            raise ValidationError('处理状态筛选无效')
        if handling != 'all':
            query = query.filter(TrackingRecord.handled_at.is_(None) if handling == 'unhandled'
                                 else TrackingRecord.handled_at.isnot(None))
    search = str(args.get('search') or '').strip().lstrip('#')
    if search:
        query = query.filter(or_(Order.order_number == search, AfterSale.customer_reference == search,
                                 TrackingRecord.number == search))
    total = query.count()
    order = str(args.get('order') or 'asc').lower()
    if order not in ('asc', 'desc'):
        raise ValidationError('排序方向无效')
    rows = query.order_by(*_order_sort(order)).offset(
        (page - 1) * size).limit(size).all()
    handler_ids = {row[1].handled_by for row in rows if row[1].handled_by}
    handlers = dict(db.session.query(User.id, User.username).filter(User.id.in_(handler_ids)).all()) if handler_ids else {}
    jobs = TrackingRefreshJob.query.filter_by(shop_id=shop_id)
    job = jobs.filter(TrackingRefreshJob.scope != 'history').order_by(TrackingRefreshJob.created_at.desc()).first()
    history_job = jobs.filter_by(scope='history').order_by(TrackingRefreshJob.created_at.desc()).first()
    coverage = db.session.query(func.count(TrackingRecord.id), func.count(TrackingRecord.checked_at),
        func.max(TrackingRecord.checked_at)).filter(TrackingRecord.shop_id == shop_id, _active_record()).one()
    unqueried = TrackingRecord.query.filter(TrackingRecord.shop_id == shop_id,
        TrackingRecord.attempted_at.is_(None), _active_record()).count()
    return {'items': [serialize(row, handlers) for row in rows], 'counts': counts,
        'handling_counts': handling_counts, 'preinfo_age_counts': preinfo_age_counts,
        'preinfo_handling_counts': preinfo_handling_counts, 'total': total,
        'page': page, 'per_page': size, 'job': job_data(job), 'history_job': job_data(history_job),
        'coverage': {'total': coverage[0], 'checked': coverage[1],
                     'unqueried': unqueried, 'latest_at': coverage[2].isoformat() if coverage[2] else None}}


def dispatch(job_id, countdown=0):
    from app.tasks import refresh_logistics_batch_task, refresh_logistics_history_batch_task
    job = db.session.get(TrackingRefreshJob, job_id)
    task = refresh_logistics_history_batch_task if job and job.scope == 'history' else refresh_logistics_batch_task
    task.apply_async(args=[job_id], countdown=countdown)


def _due(now):
    return and_(or_(TrackingRecord.delivered_at.is_(None), TrackingRecord.requested_at.isnot(None)),
                TrackingRecord.next_check_at <= now, _active_record())


def create_job(shop_id, scope='due', *, force=False, binding_id=None):
    # Legacy all/force callers cannot silently bypass the per-number clock.
    if scope == 'all':
        scope = 'due'
    if scope not in SCOPES:
        raise ValidationError('刷新范围无效')
    shop = db.session.get(Shop, shop_id)
    try:
        shop.get_fourpx_credentials()
    except ShopCredentialError:
        raise ValidationError('当前店铺尚未配置可用的4PX凭据') from None
    lock_state(shop_id)
    drain_changes(shop_id)
    active_query = TrackingRefreshJob.query.filter_by(shop_id=shop_id).filter(
        TrackingRefreshJob.status.in_(('queued', 'running')))
    if scope == 'history':
        active_query = active_query.filter_by(scope='history')
    elif scope == 'single':
        active_query = active_query.filter_by(scope='single')
    else:
        active_query = active_query.filter(TrackingRefreshJob.scope.notin_(('history', 'single')))
    active = active_query.first() if scope != 'single' else None
    if active:
        db.session.commit()
        return job_data(active)
    now = datetime.utcnow()
    if scope == 'single':
        if isinstance(binding_id, bool) or not isinstance(binding_id, int):
            raise ValidationError('请选择要强制刷新的单个运单')
        binding = TrackingBinding.query.filter_by(id=binding_id, shop_id=shop_id, active=True).first()
        if binding is None:
            raise NotFoundError('当前店铺没有此运单')
        record = db.session.get(TrackingRecord, binding.record_id)
        existing = next((job for job in active_query.all() if record.id in (job.record_ids or [])), None)
        if existing:
            db.session.commit()
            return job_data(existing)
        record.requested_at = record.next_check_at = now
        ids = [record.id]
    else:
        query = TrackingRecord.query.filter(TrackingRecord.shop_id == shop_id, _due(now),
            or_(TrackingRecord.lease_until.is_(None), TrackingRecord.lease_until < now))
        if scope == 'history':
            query = query.filter(TrackingRecord.attempted_at.is_(None), TrackingRecord.requested_at.is_(None))
        else:
            query = query.filter(or_(TrackingRecord.attempted_at.isnot(None), TrackingRecord.requested_at.isnot(None)))
        if scope in TABS:
            eligible = _query(shop_id).filter(_tab_filter(scope, now)).with_entities(TrackingRecord.id)
            query = query.filter(TrackingRecord.id.in_(eligible))
        ids = [value for (value,) in query.with_entities(TrackingRecord.id).order_by(
            TrackingRecord.requested_at.asc().nullslast(), TrackingRecord.next_check_at, TrackingRecord.id
        ).limit(100 if scope == 'history' else 40).all()]
    if not ids:
        db.session.commit()
        # A quiet minute should not create thousands of empty history/job rows per day.
        return {'id': '', 'scope': scope, 'status': 'completed', 'total': 0, 'done': 0,
                'failed': 0, 'skipped': 0, 'error': None, 'updated_at': now.isoformat(),
                'finished_at': now.isoformat()}
    job = TrackingRefreshJob(id=str(uuid4()), shop_id=shop_id, scope=scope, record_ids=ids,
                             status='queued' if ids else 'completed', finished_at=None if ids else now)
    db.session.add(job)
    db.session.commit()
    if ids:
        try:
            dispatch(job.id)
        except Exception:  # noqa: BLE001 - never expose broker credentials
            job.status, job.error = 'failed', '后台队列暂不可用，请稍后重新刷新'
            db.session.commit()
    return job_data(job)


def enqueue(payload):
    return create_job(_shop_id(payload.get('shop_id')), payload.get('scope') or 'due',
                      binding_id=payload.get('binding_id'))


def fetch_facts(shop, numbers):
    try:
        key, secret = shop.get_fourpx_credentials()
    except ShopCredentialError:
        raise ValidationError('当前店铺尚未配置可用的4PX凭据') from None
    environment = shop.fourpx_environment
    def fetch(number):
        try:
            data = FourPxClient(key, secret, environment=environment).get_tracking(number)
            return normalize_tracking(data, number, include_events=True)
        except (FourPxError, ValueError, TypeError):
            return {'error': '4PX查询失败或轨迹无法核验；保留上次可靠结果，稍后重试'}
    with ThreadPoolExecutor(max_workers=min(3, max(len(numbers), 1))) as executor:
        return dict(zip(numbers, executor.map(fetch, numbers)))


def repair_saved_delivery(record):
    """One-off reclassification of reliable cached events, with no HTTP or business writes.

    Preserve the actual checked_at and first pre-advice; a local rule repair is
    not a fresh carrier observation. Explicit manual refreshes remain scheduled.
    """
    events = [event for event in [record.latest, *(record.events or [])] if isinstance(event, dict)]
    delivered = []
    for event in events:
        if not is_delivered_event(event.get('carrier_code'), event.get('message')):
            continue
        try:
            delivered.append(_event_time({'occurDatetime': event.get('happenedAt')}))
        except FourPxError:
            continue
    if not delivered:
        return False
    at = max(delivered).replace(tzinfo=None)
    record.delivered_at = max(at, record.delivered_at) if record.delivered_at else at
    record.has_movement, record.only_preinfo = True, False
    def reclassify(event):
        return {**event, 'status': 'DELIVERED'} if isinstance(event, dict) and is_delivered_event(
            event.get('carrier_code'), event.get('message')) else event
    record.latest = reclassify(record.latest)
    record.events = [reclassify(event) for event in (record.events or [])]
    record.next_check_at = record.requested_at
    return True


def save_facts(record, facts):
    now = datetime.utcnow()
    record.attempted_at = now
    if facts.get('error'):
        record.error, record.error_count = facts['error'], record.error_count + 1
        reschedule([record])
        return
    record.latest, record.events = facts['latest'], facts['events']
    record.event_count = facts['event_count']
    record.has_movement = record.has_movement or facts['has_movement']
    record.only_preinfo = facts['only_preinfo'] and not record.has_movement
    first = stamp(facts['first_preinfo_at'])
    # Carrier history windows can shrink; never reset the first reliable pre-advice date.
    if first and (record.first_preinfo_at is None or first < record.first_preinfo_at):
        record.first_preinfo_at = first
    record.delivered_at = stamp(facts['delivered_at']) or record.delivered_at
    record.checked_at, record.error, record.error_count = now, None, 0
    reschedule([record])


def process_batch(job_id):
    now, token = datetime.utcnow(), str(uuid4())
    claimed = db.session.execute(update(TrackingRefreshJob).where(
        TrackingRefreshJob.id == job_id, TrackingRefreshJob.status.in_(('queued', 'running')),
        or_(TrackingRefreshJob.lease_until.is_(None), TrackingRefreshJob.lease_until < now)).values(
        status='running', lease_token=token, lease_until=now+timedelta(minutes=4), updated_at=now)).rowcount
    db.session.commit()
    if not claimed:
        return
    job = db.session.get(TrackingRefreshJob, job_id)
    try:
        shop = db.session.get(Shop, job.shop_id)
        ids = job.record_ids[job.done:job.done + 20]
        # A persistent per-number lease deduplicates history, manual and incremental jobs.
        started = datetime.utcnow()
        db.session.execute(update(TrackingRecord).where(TrackingRecord.id.in_(ids),
            TrackingRecord.shop_id == job.shop_id, _due(started),
            or_(TrackingRecord.lease_until.is_(None), TrackingRecord.lease_until < started)).values(
                lease_token=token, lease_until=started+timedelta(minutes=4)))
        db.session.commit()
        records = TrackingRecord.query.filter_by(shop_id=job.shop_id, lease_token=token).all()
        snapshots = {record.id: record.requested_at for record in records}
        results = fetch_facts(shop, [record.number for record in records])
        # No stock/shop row lock is held during network I/O.
        db.session.rollback()
        job = TrackingRefreshJob.query.filter_by(id=job_id, lease_token=token).populate_existing().with_for_update().first()
        if job is None:
            return
        records = TrackingRecord.query.filter_by(shop_id=job.shop_id, lease_token=token).populate_existing().with_for_update().all()
        skipped = len(ids) - len(records)
        for record in records:
            facts = results[record.number]
            if record.requested_at == snapshots[record.id] and (not record.checked_at or record.checked_at <= started):
                record.requested_at = None
                save_facts(record, facts)
                job.failed += int(bool(facts.get('error')))
            else:
                skipped += 1  # A newer source change/explicit check wins over stale network work.
            record.lease_token, record.lease_until = None, None
        job.done += len(ids)
        job.skipped += skipped
        job.lease_until, job.lease_token = None, None
        job.updated_at = datetime.utcnow()
        if job.done >= len(job.record_ids):
            job.status, job.finished_at = 'completed', datetime.utcnow()
        db.session.commit()
        if job.status == 'running':
            dispatch(job.id, countdown=1)
    except Exception:  # noqa: BLE001 - preserve durable progress, sanitize secrets
        db.session.rollback()
        job = db.session.get(TrackingRefreshJob, job_id)
        if job and job.lease_token == token:
            job.status, job.error, job.lease_until = 'failed', '后台核查中断，可重新刷新；已完成结果保留', None
            db.session.commit()
        db.session.execute(update(TrackingRecord).where(TrackingRecord.lease_token == token).values(
            lease_token=None, lease_until=None))
        db.session.commit()


def scheduled_refresh(scope):
    for shop in Shop.query.filter(Shop.is_active.is_(True),
            Shop.fourpx_app_key_encrypted.isnot(None), Shop.fourpx_app_secret_encrypted.isnot(None)).all():
        try:
            lock_state(shop.id)
            drain_changes(shop.id)
            bootstrap(shop.id)
            db.session.commit()
            create_job(shop.id, scope)
        except ValidationError:
            db.session.rollback()


def recover_jobs():
    now = datetime.utcnow()
    for job in TrackingRefreshJob.query.filter(TrackingRefreshJob.status.in_(('queued', 'running')),
            TrackingRefreshJob.updated_at < now - timedelta(minutes=5),
            or_(TrackingRefreshJob.lease_until.is_(None), TrackingRefreshJob.lease_until < now)).all():
        dispatch(job.id)


def list_audits(args):
    shop_id = _shop_id(args.get('shop_id'))
    try:
        page = max(1, int(args.get('page') or 1))
        size = min(100, max(1, int(args.get('per_page') or 20)))
    except (ValueError, TypeError):
        raise ValidationError('分页参数错误') from None
    query = TrackingOutboundAudit.query.filter_by(shop_id=shop_id)
    total = query.count()
    rows = query.order_by(TrackingOutboundAudit.id.desc()).offset((page-1)*size).limit(size).all()
    return {'items': [{'id': row.id, 'order_id': row.order_id, 'after_sale_id': row.after_sale_id,
             'user_id': row.user_id, 'evidence': row.evidence, 'stock_result': row.stock_result,
             'created_at': row.created_at.isoformat()} for row in rows],
             'total': total, 'page': page, 'per_page': size}
