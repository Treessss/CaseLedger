"""Manual procurement edits change incoming supply, never historical physical stock."""
import copy
import hashlib
import json
from collections import defaultdict
from sqlalchemy.orm import selectinload

from app import db
from app.models import AfterSale, Order, Product, PurchaseList, PurchaseListEdit, Shop
from app.services import purchase_service as purchases
from app.utils.errors import ConflictError, ValidationError
from app.utils.order_lines import is_tip_line
from app.utils.security import current_user

EDITABLE = ('draft', 'ordered', 'partially_received')


def snapshot(purchase):
    return copy.deepcopy({field: getattr(purchase, field) for field in (
        'status', 'name', 'items', 'allocations', 'receipt', 'receipt_batches',
        'order_ids', 'after_sale_id', 'order_count', 'total_quantity')})


def edit_token(purchase):
    return hashlib.sha256(json.dumps(snapshot(purchase), sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')).encode()).hexdigest()


def catalog_products(args):
    from app.services.after_sale_service import search_catalog_products
    return search_catalog_products(args)


def catalog_variants(args):
    from app.services.after_sale_service import search_catalog
    return search_catalog(args)


def create_manual(payload):
    if not isinstance(payload, dict):
        raise ValidationError('采购参数必须为对象')
    return purchases.create_stock_purchase_list(payload.get('shop_id'), payload.get('items'),
                                               payload.get('name') or '手动采购清单')


def _counted(row):
    return sum(int(row.get(field) or 0) for field in (
        'received_quantity', 'damaged_quantity', 'confirmed_missing_quantity'))


def remove_deleted_sale_from_drafts(record):
    """Caller holds the shop lock; cleanup and archive commit in one transaction.

    Never rewrite confirmed purchases, receipts, physical inventory or another
    customer's quantity, even when they share the same variant.
    """
    if not record.deleted_at:
        raise ValidationError('只能清理已删除售后单的草稿采购需求')
    changed = []
    for purchase in PurchaseList.query.filter_by(shop_id=record.shop_id, status='draft').order_by(
            PurchaseList.id).with_for_update().all():
        removed = [row for row in purchase.allocations or []
                   if int(row.get('after_sale_id') or 0) == record.id]
        if not removed:
            continue
        if purchase.receipt or purchase.receipt_batches or any(_counted(row) for row in removed):
            raise ValidationError(f'采购清单 #{purchase.id} 已有清点记录，请先核对，不能自动移除售后商品')
        original_quantities = defaultdict(int)
        for row in purchase.allocations:
            original_quantities[row['group_key']] += int(row['quantity'])
        if dict(original_quantities) != {row['group_key']: int(row['quantity']) for row in purchase.items or []}:
            raise ValidationError(f'采购清单 #{purchase.id} 商品数量与分配记录不一致，请先核对')
        before = snapshot(purchase)
        remaining = [copy.deepcopy(row) for row in purchase.allocations
                     if int(row.get('after_sale_id') or 0) != record.id]
        quantities = defaultdict(int)
        for row in remaining:
            quantities[row['group_key']] += int(row['quantity'])
        purchase.items = [dict(copy.deepcopy(row), quantity=quantities[row['group_key']])
                          for row in purchase.items or [] if quantities[row['group_key']]]
        purchase.allocations = remaining
        order_ids = {int(row['order_id']) for row in remaining if row.get('order_id')}
        sale_ids = {int(row['after_sale_id']) for row in remaining if row.get('after_sale_id')}
        purchase.order_ids = [oid for oid in purchase.order_ids or [] if int(oid) in order_ids]
        purchase.after_sale_id = next(iter(sale_ids)) if len(sale_ids) == 1 and not order_ids else None
        purchase.order_count = len(order_ids) + len(sale_ids)
        purchase.total_quantity = sum(int(row['quantity']) for row in purchase.items)
        # Rebuild references from surviving allocations: the same CF reference
        # may already have been reused by a newly-created case.
        numbers = dict(db.session.query(Order.id, Order.order_number).filter(Order.id.in_(order_ids)).all())
        sales = dict(db.session.query(AfterSale.id, AfterSale.customer_reference).filter(AfterSale.id.in_(sale_ids)).all())
        references = defaultdict(set)
        for row in remaining:
            number = sales.get(int(row.get('after_sale_id') or 0)) if row.get('after_sale_id') else numbers.get(
                int(row.get('order_id') or 0))
            if number:
                references[row['group_key']].add(number)
        updated_items = copy.deepcopy(purchase.items)
        for row in updated_items:
            row['order_numbers'] = sorted(references[row['group_key']])
        purchase.items = updated_items
        if not remaining:
            purchase.status = 'cancelled'
        db.session.add(PurchaseListEdit(purchase_id=purchase.id, user_id=(current_user() or {}).get('user_id'),
                                       before=before, after=snapshot(purchase)))
        changed.append(purchase.id)
    return changed


def update_items(purchase_id, payload):
    purchase = purchases._purchase_or_error(purchase_id)
    Shop.query.filter_by(id=purchase.shop_id).with_for_update().one()
    db.session.refresh(purchase, with_for_update=True)
    if purchase.status not in EDITABLE:
        raise ValidationError('已结束到货或已取消的清单不能编辑，请另建采购单')
    if not isinstance(payload, dict):
        raise ValidationError('编辑参数必须为对象')
    if payload.get('edit_token') != edit_token(purchase):
        raise ConflictError('采购单已被修改或发生入库，请刷新详情后重新编辑，避免重复添加')
    entries, additions = payload.get('items'), payload.get('additions', [])
    if not isinstance(entries, list) or not isinstance(additions, list) or len(additions) > 1000:
        raise ValidationError('请填写采购商品和数量')
    original = {row['group_key']: row for row in purchase.items or []}
    targets = {}
    for row in entries:
        if not isinstance(row, dict) or not isinstance(row.get('group_key'), str) or row['group_key'] in targets:
            raise ValidationError('采购变体格式无效或重复')
        key = row['group_key']
        quantity = purchases._nonnegative_int(row.get('quantity'), '采购数量')
        if key not in original or quantity > 100_000:
            raise ValidationError('采购变体不存在或数量超过 100000')
        targets[key] = quantity
    if set(targets) != set(original):
        raise ValidationError('请提交完整清单；移除未清点商品请将数量设为 0')
    allocations = copy.deepcopy(purchase.allocations or [])
    if not allocations:
        orders = Order.query.options(selectinload(Order.items)).filter(Order.id.in_(purchase.order_ids)).all()
        allocations = purchases._legacy_allocations(purchase, orders,
            purchases._available_quantities(orders, purchase.shop_id, exclude_purchase_id=purchase.id))
    receipts = {row['group_key']: copy.deepcopy(row) for row in purchase.receipt or []}
    allocations_by_key = defaultdict(list)
    for row in allocations:
        allocations_by_key[row['group_key']].append(row)
    new_items = copy.deepcopy(original)
    for key, quantity in targets.items():
        rows = allocations_by_key[key]
        if sum(int(row['quantity']) for row in rows) != int(original[key]['quantity']):
            raise ValidationError('清单分配与采购数量不一致，请先核对')
        minimum = max(_counted(receipts.get(key, {})), sum(_counted(row) for row in rows))
        if quantity < minimum:
            raise ValidationError(f'采购数量不能少于已清点的 {minimum} 件（含合格、货损、确认缺货）')
        reduction = max(int(original[key]['quantity']) - quantity, 0)
        # Drop extra stock first, then newest linked demand. Never touch counted units.
        for row in sorted(reversed(rows), key=lambda row: not bool(row.get('stock_only'))):
            take = min(reduction, int(row['quantity']) - _counted(row))
            row['quantity'] -= take
            reduction -= take
        if reduction:
            raise ValidationError('剩余未清点数量不足，不能减少历史入库数量')
        increase = max(quantity - int(original[key]['quantity']), 0)
        if increase:
            allocations.append({'stock_only': True, 'group_key': key, 'quantity': increase, 'received_quantity': 0})
        new_items[key]['quantity'] = quantity
    product_quantities = {}
    for entry in additions:
        if not isinstance(entry, dict):
            raise ValidationError('新增商品格式无效')
        product_id = purchases._positive_int(entry.get('product_id'), 'product_id')
        quantity = purchases._positive_int(entry.get('quantity'), '新增数量')
        if product_id in product_quantities or quantity > 100_000:
            raise ValidationError('新增变体重复或数量超过 100000')
        product_quantities[product_id] = quantity
    products = Product.query.filter(Product.shop_id == purchase.shop_id, Product.id.in_(product_quantities)).all()
    if len(products) != len(product_quantities) or any(is_tip_line(product) for product in products):
        raise ValidationError('新增商品不存在、不属于当前店铺或不是实物商品')
    for product in products:
        key = json.dumps(purchases._group_key(product), ensure_ascii=False)
        row = new_items.setdefault(key, {'group_key': key, 'shopify_product_id': product.shopify_product_id,
            'shopify_variant_id': product.shopify_variant_id, 'title': product.title,
            'variant_title': product.variant_title or '', 'sku': product.sku or '', 'vendor': product.vendor or '',
            'image_url': product.image_url or '', 'quantity': 0, 'order_numbers': []})
        row['quantity'] += product_quantities[product.id]
        allocations.append({'stock_only': True, 'catalog_product_id': product.id, 'group_key': key,
                            'quantity': product_quantities[product.id], 'received_quantity': 0})
    if not any(row['quantity'] for row in new_items.values()) or len(new_items) > 1000:
        raise ValidationError('清单须保留至少一个商品，且不超过 1000 个变体')
    if any(row['quantity'] > 100_000 for row in new_items.values()):
        raise ValidationError('合并后单个变体数量不能超过 100000')
    before = snapshot(purchase)
    purchase.items = [row for row in new_items.values() if row['quantity']]
    purchase.allocations = [row for row in allocations if row['quantity']]
    order_ids = {row['order_id'] for row in purchase.allocations if row.get('order_id')}
    sale_ids = {row['after_sale_id'] for row in purchase.allocations if row.get('after_sale_id')}
    purchase.order_ids = [order_id for order_id in purchase.order_ids if order_id in order_ids]
    purchase.after_sale_id = next(iter(sale_ids)) if len(sale_ids) == 1 and not order_ids else None
    purchase.order_count = len(order_ids) + len(sale_ids)
    purchase.total_quantity = sum(row['quantity'] for row in purchase.items)
    order_numbers = dict(db.session.query(Order.id, Order.order_number).filter(Order.id.in_(order_ids)).all())
    sale_numbers = dict(db.session.query(AfterSale.id, AfterSale.customer_reference).filter(AfterSale.id.in_(sale_ids)).all())
    references_by_key = defaultdict(set)
    for line in purchase.allocations:
        if line.get('order_id'):
            references_by_key[line['group_key']].add(order_numbers.get(line['order_id']))
        if line.get('after_sale_id'):
            references_by_key[line['group_key']].add(sale_numbers.get(line['after_sale_id']))
    for row in purchase.items:
        key = row['group_key']
        if key in receipts:
            receipt = receipts[key]
            receipt['planned_quantity'] = row['quantity']
            receipt['missing_quantity'] = row['quantity'] - int(receipt['received_quantity']) - int(receipt['damaged_quantity'])
            receipt['pending_quantity'] = row['quantity'] - _counted(receipt)
        # Only retain true linked order references, never fabricate a binding for manual stock.
        row['order_numbers'] = [number for number in row.get('order_numbers', []) if number in references_by_key[key]]
    purchase.receipt = [receipts[row['group_key']] for row in purchase.items if row['group_key'] in receipts]
    after = snapshot(purchase)
    if before != after:
        db.session.add(PurchaseListEdit(purchase_id=purchase.id, user_id=(current_user() or {}).get('user_id'),
                                        before=before, after=after))
    db.session.commit()
    return purchases._serialize_purchase(purchase, include_items=True)
