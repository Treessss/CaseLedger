"""Quantity-only shared inventory. Readiness never assigns goods to an order."""
import re
from collections import Counter
from json import dumps
from collections import defaultdict

from app import db
from app.models import OrderItem, Product
from app.services.physical_stock_service import physical_stock_rows
from app.services.purchase_service import _group_key
from app.utils.errors import ValidationError
from app.utils.order_lines import is_tip_line

PHONE_PAIR = re.compile(r"\biphone\s*(17|18)\s*pro\s*(max)?\b", re.I)


def _key(item):
    return dumps(_group_key(item), ensure_ascii=False)


def _compatible_signature(item):
    raw = item.variant_title or ''
    matches = list(PHONE_PAIR.finditer(raw))
    if not item.shopify_product_id or len(matches) != 1:
        return None
    match = matches[0]
    if raw[match.end():].lstrip().lower().startswith(('plus', 'mini', 'air')):
        return None
    options = re.sub(r'\s+', ' ', raw[:match.start()] + '@phone@' + raw[match.end():]).strip().casefold()
    return str(item.shopify_product_id), bool(match.group(2)), options


def physical_context(records):
    """One live stock read for a page of records, without a whole-shop forecast."""
    records = list(records)
    if not records:
        return {'rows': {}, 'metadata': {}, 'compatible_keys': {}}
    shop_id = records[0].shop_id
    if any(record.shop_id != shop_id for record in records):
        raise ValidationError('库存校验不能跨店铺混合')
    lines = [item for record in records for item in record.items
             if int(item.quantity or 0) > 0 and not is_tip_line(item)]
    products = {item.shopify_product_id for item in lines if item.shopify_product_id}
    metadata = {}
    for source in db.session.query(Product.shopify_product_id, Product.shopify_variant_id,
            Product.title, Product.variant_title, Product.sku, Product.vendor).filter(
                Product.shop_id == shop_id, Product.shopify_product_id.in_(products)).all():
        metadata[_key(source)] = source
    for source in db.session.query(OrderItem.shopify_product_id, OrderItem.shopify_variant_id,
            OrderItem.title, OrderItem.variant_title, OrderItem.sku, OrderItem.vendor).filter(
                OrderItem.shop_id == shop_id, OrderItem.shopify_product_id.in_(products)).distinct().all():
        metadata.setdefault(_key(source), source)
    for item in lines:
        metadata.setdefault(getattr(item, 'group_key', None) or _key(item), item)
    signatures = defaultdict(list)
    for key, item in metadata.items():
        signature = _compatible_signature(item)
        if signature:
            signatures[signature].append(key)
    signatures = {signature: sorted(keys) for signature, keys in signatures.items()}
    keys = {getattr(item, 'group_key', None) or _key(item) for item in lines}
    for item in lines:
        keys.update(signatures.get(_compatible_signature(item), ()))
    rows = physical_stock_rows(shop_id, keys, seed_items=[{
        'group_key': getattr(item, 'group_key', None) or _key(item),
        'shopify_product_id': item.shopify_product_id, 'title': item.title,
        'variant_title': item.variant_title, 'sku': item.sku} for item in lines])
    return {'rows': rows, 'metadata': metadata, 'compatible_keys': signatures}


def quantity_plan(record, *, rows=None, metadata=None, allow_partial=False, compatible_keys=None):
    """All-or-nothing dry plan against physical counts, including repeated lines."""
    lines = [item for item in record.items if int(item.quantity or 0) > 0 and not is_tip_line(item)]
    if not lines:
        if allow_partial:
            return []
        raise ValidationError('订单没有可出库商品')
    metadata = dict(metadata or {})
    product_ids = {item.shopify_product_id for item in lines if item.shopify_product_id}
    if rows is None:
        for item in db.session.query(Product.shopify_product_id, Product.shopify_variant_id,
                Product.title, Product.variant_title, Product.sku, Product.vendor).filter(Product.shop_id == record.shop_id,
                                         Product.shopify_product_id.in_(product_ids)).all():
            metadata[_key(item)] = item
        # Deleted/old catalog variants can still have physical stock and historical metadata.
        historical = db.session.query(OrderItem.shopify_product_id, OrderItem.shopify_variant_id,
            OrderItem.title, OrderItem.variant_title, OrderItem.sku, OrderItem.vendor).filter(
            OrderItem.shop_id == record.shop_id, OrderItem.shopify_product_id.in_(product_ids)).distinct().all()
        for item in historical:
            metadata.setdefault(_key(item), item)
    for item in lines:
        metadata.setdefault(getattr(item, 'group_key', None) or _key(item), item)
    keys = set()
    choices = {}
    for item in lines:
        key = getattr(item, 'group_key', None) or _key(item)
        signature = _compatible_signature(item)
        if compatible_keys is not None and rows is not None:
            others = compatible_keys.get(signature, ()) if signature else ()
            choices[key] = [key, *(other for other in others if other != key)]
        else:
            choices[key] = [key, *sorted(other for other, source in metadata.items()
                if other != key and signature and _compatible_signature(source) == signature)]
        keys.update(choices[key])
    if rows is None:
        rows = physical_stock_rows(record.shop_id, keys,
            seed_items=[{'group_key': getattr(item, 'group_key', None) or _key(item),
                         'shopify_product_id': item.shopify_product_id, 'title': item.title,
                         'variant_title': item.variant_title, 'sku': item.sku} for item in lines])
    budget = Counter({key: max(int(rows.get(key, {}).get('on_hand_quantity', 0)), 0) for key in keys})
    plan = []
    for item in lines:
        target = getattr(item, 'group_key', None) or _key(item)
        need = int(item.quantity)
        for source in choices[target]:
            take = min(need, budget[source])
            if take:
                plan.append({'item': item, 'source_key': source, 'target_key': target,
                             'quantity': take, 'claim': None})
                budget[source] -= take
                need -= take
            if not need:
                break
        if need and not allow_partial:
            raise ValidationError(f'实际库存不足：{item.variant_title or item.title} 缺 {need} 件（已计入通用型号库存）')
    return plan


def plan_outbound_stock(order):
    return quantity_plan(order)


def apply_outbound_stock(order, plan):
    """Compatibility info only. Actual deductions are immutable outbound lines."""
    return [{'from_order_number': None, 'quantity': step['quantity'],
             'source_group_key': step['source_key'], 'target_group_key': step['target_key']}
            for step in plan if step['source_key'] != step['target_key']]
