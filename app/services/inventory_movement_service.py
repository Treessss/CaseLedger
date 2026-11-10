"""Shop-scoped stock movements, with explicit origins and filtered pagination."""

import re
from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy.orm import load_only, raiseload

from app.models import InventoryAdjustment, InventoryOutboundLine, InventoryReservation, Order, PurchaseList, ShippingParcel
from app.services.product_translation_service import display_payload
from app.utils.errors import ValidationError
from app.utils.timezone import beijing_day_bounds_utc

TYPES = {
    'purchase_receipt': '采购入库', 'scan_outbound': '扫码出库',
    'manual_adjustment': '手动调整', 'stock_transfer': '库存调配', 'scan_undo': '撤回出库',
}


def _positive_integer(value, default, maximum):
    try:
        number = int(value if value not in (None, '') else default)
    except (ValueError, TypeError) as exc:
        raise ValidationError('分页参数必须为正整数') from exc
    if number < 1 or number > maximum:
        raise ValidationError('分页参数超出范围')
    return number


def list_movements(args):
    from app.services.inventory_service import _item_catalog, _movement, _parse_day, _parse_stamp, _shop

    shop = _shop(args.get('shop_id'))
    today = (datetime.utcnow() + timedelta(hours=8)).date()
    end = _parse_day(args.get('end_date'), today)
    start = _parse_day(args.get('start_date'), end - timedelta(days=6))
    if start > end:
        raise ValidationError('开始日期不能晚于结束日期')
    kind = args.get('movement_type') or 'all'
    direction = args.get('direction') or 'all'
    if kind not in (*TYPES, 'all') or direction not in ('in', 'out', 'all'):
        raise ValidationError('流水类型或出入库方向无效')
    page = _positive_integer(args.get('page'), 1, 1000000)
    per_page = _positive_integer(args.get('per_page'), 50, 200)
    start_utc, _ = beijing_day_bounds_utc(start, start)
    _, end_utc = beijing_day_bounds_utc(end, end)
    rows = []
    ledger_order_ids = {value for (value,) in InventoryOutboundLine.query.with_entities(
        InventoryOutboundLine.order_id).filter_by(shop_id=shop.id).distinct().all()}

    def add(identifier, movement_type, at, key, quantity, source, order_number=None):
        row = _movement('in' if quantity > 0 else 'out', at, key, abs(quantity), source)
        row.update(id=identifier, movement_type=movement_type,
                   movement_type_label=TYPES[movement_type], order_number=order_number)
        rows.append(row)

    # Only completed orders consume stock: a first scan of a multi-parcel order does not.
    scanned = ShippingParcel.query.filter(
        ShippingParcel.order_id == Order.id, ShippingParcel.shop_id == shop.id,
        ShippingParcel.scanned_at.is_not(None),
    ).exists()
    orders = Order.query.options(load_only(Order.id, Order.order_number, Order.shipped_at), raiseload('*')).filter(
        Order.shop_id == shop.id, Order.warehouse_status == 'shipped',
        Order.shipped_at >= start_utc, Order.shipped_at < end_utc, scanned,
        Order.id.notin_(ledger_order_ids),
    ).all()
    by_id = {order.id: order for order in orders}
    consumed = defaultdict(int)
    purchases = PurchaseList.query.options(load_only(
        PurchaseList.id, PurchaseList.name, PurchaseList.status,
        PurchaseList.receipt_batches, PurchaseList.allocations,
    )).filter_by(shop_id=shop.id).all()
    for purchase in purchases:
        for batch_index, batch in enumerate(purchase.receipt_batches or []):
            at = _parse_stamp(batch.get('received_at'))
            if at is None or not start_utc <= at < end_utc:
                continue
            for item_index, item in enumerate(batch.get('items') or []):
                good = int(item.get('received_quantity') or 0)
                if good > 0:
                    add(f'receipt:{purchase.id}:{batch_index}:{item_index}', 'purchase_receipt',
                        at, item['group_key'], good, f'采购到货 · {purchase.name}')
        if purchase.status not in ('partially_received', 'received'):
            continue
        for allocation in purchase.allocations or []:
            order_id = allocation.get('order_id')
            good = int(allocation.get('received_quantity') or 0)
            if order_id in by_id and good > 0:
                consumed[(order_id, allocation['group_key'])] += good
    for reservation in InventoryReservation.query.filter(
        InventoryReservation.shop_id == shop.id, InventoryReservation.order_id.in_(by_id),
    ).all():
        if reservation.quantity > 0:
            consumed[(reservation.order_id, reservation.group_key)] += reservation.quantity
    # Purchase arrivals + manual reservations are distinct portions; merge by variant once.
    for (order_id, key), quantity in consumed.items():
        order = by_id[order_id]
        add(f'scan:{order_id}:{key}', 'scan_outbound', order.shipped_at, key, -quantity,
            f'扫码出库 · 订单 {order.order_number}', order.order_number)
    ledger = InventoryOutboundLine.query.join(Order, Order.id == InventoryOutboundLine.order_id).with_entities(
        InventoryOutboundLine.id, InventoryOutboundLine.group_key, InventoryOutboundLine.quantity_delta,
        InventoryOutboundLine.action, InventoryOutboundLine.created_at, Order.order_number,
    ).filter(InventoryOutboundLine.shop_id == shop.id, InventoryOutboundLine.created_at >= start_utc,
             InventoryOutboundLine.created_at < end_utc).all()
    for line in ledger:
        if line.quantity_delta == 0:
            continue  # Carrier reconciliation markers do not fabricate stock movements.
        movement_type = 'scan_outbound' if line.action == 'outbound' else 'scan_undo'
        add(f'physical:{line.id}', movement_type, line.created_at, line.group_key,
            line.quantity_delta, f'{TYPES[movement_type]} · 订单 {line.order_number}', line.order_number)

    for adjustment in InventoryAdjustment.query.filter(
        InventoryAdjustment.shop_id == shop.id,
        InventoryAdjustment.created_at >= start_utc, InventoryAdjustment.created_at < end_utc,
    ).all():
        delta = int(adjustment.quantity_delta or 0)
        if not delta:
            continue
        reason = adjustment.reason or ''
        # Existing automatic adjustments have fixed service-generated descriptions.
        reship = re.fullmatch(r'售后 (CF\S+) 扫码出库', reason)
        undo = re.fullmatch(r'撤回售后 (CF\S+) 出库', reason)
        transfer = re.match(r'通用型号调配至(?:订单|售后) ([^：]+)：', reason)
        movement_type = 'scan_outbound' if reship else 'scan_undo' if undo else 'stock_transfer' if transfer else 'manual_adjustment'
        reference = reship.group(1) if reship else undo.group(1) if undo else transfer.group(1) if transfer else None
        add(f'adjustment:{adjustment.id}', movement_type, adjustment.created_at,
            adjustment.group_key, delta, f'{TYPES[movement_type]} · {reason}', reference)

    search = str(args.get('search') or '').strip().casefold()
    rows = [row for row in rows if (kind == 'all' or row['movement_type'] == kind)
            and (direction == 'all' or row['direction'] == direction)]

    def enrich(selected):
        catalog = _item_catalog(shop.id, {row['group_key'] for row in selected})
        for row in selected:
            info = catalog.get(row['group_key']) or {}
            row.update(title=info.get('title') or '未命名商品', variant_title=info.get('variant_title') or '',
                       sku=info.get('sku') or '', image_url=info.get('image_url') or '')
        return display_payload(selected, shop.id)

    if search:
        rows = [row for row in enrich(rows) if any(search in str(row.get(field) or '').casefold() for field in (
            'title', 'original_title', 'variant_title', 'original_variant_title', 'sku', 'source', 'order_number',
        ))]
    rows.sort(key=lambda row: (row['at'], row['id']), reverse=True)
    days = {}
    for row in rows:
        day = days.setdefault(row['date'], {'date': row['date'], 'in_quantity': 0, 'out_quantity': 0,
                                            'in_records': 0, 'out_records': 0})
        day[f"{row['direction']}_quantity"] += row['quantity']
        day[f"{row['direction']}_records"] += 1
    page_rows = rows[(page - 1) * per_page:page * per_page]
    # Totals use all matching movements; names/images are only needed on this
    # page unless a name/SKU search requires resolving them before filtering.
    page_rows = page_rows if search else enrich(page_rows)
    return {
        'period': {'start_date': start.isoformat(), 'end_date': end.isoformat()},
        'days': sorted(days.values(), key=lambda row: row['date'], reverse=True),
        'movements': page_rows,
        'pagination': {'page': page, 'per_page': per_page, 'total': len(rows),
                       'pages': (len(rows) + per_page - 1) // per_page},
        'totals': {f'{direction}_quantity': sum(row['quantity'] for row in rows if row['direction'] == direction)
                   for direction in ('in', 'out')},
    }
