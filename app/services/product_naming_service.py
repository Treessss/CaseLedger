"""Stable product identity and conservative adoption of existing merchant names."""
from collections import defaultdict
from datetime import datetime

from sqlalchemy import func

from app import db
from app.models import AfterSale, AfterSaleItem, Order, OrderItem, Product, ProductNaming, ProductTranslation


def valid_id(value):
    return str(value) if value and str(value) not in ('None', '0', 'null') else None


def source_titles(shop_id, product_ids=None):
    sources = []
    for model in (Product, OrderItem, AfterSaleItem):
        query = db.session.query(model.shopify_product_id, model.title)
        if model is AfterSaleItem:
            query = query.join(AfterSale, AfterSale.id == model.after_sale_id).filter(AfterSale.shop_id == shop_id)
        elif model is OrderItem:
            query = query.join(Order, Order.id == model.order_id).filter(Order.shop_id == shop_id)
        else:
            query = query.filter(model.shop_id == shop_id)
        if product_ids is not None:
            query = query.filter(model.shopify_product_id.in_(product_ids))
        sources.append(query.distinct())
    result = defaultdict(set)
    for pid, title in sources[0].union(*sources[1:]).all():
        if valid_id(pid) and title:
            result[str(pid)].add(title)
    return result


def record_catalog_names(shop_id, titles, *, authoritative=True):
    """No commit, no HTTP. Lock naming rows, never the warehouse's Shop lock.

    Initialize from unambiguous legacy mappings once; catalog refreshes must
    never replace names entered by the merchant, including intentional clears.
    """
    titles = {str(pid): title for pid, title in titles.items() if valid_id(pid) and title}
    if not titles:
        return []
    aliases = source_titles(shop_id)
    owners = defaultdict(set)
    for pid, values in aliases.items():
        for title in values:
            owners[title].add(pid)
    for pid, title in sorted(titles.items()):
        owners[title].add(pid)
    legacy = ProductTranslation.query.filter_by(shop_id=shop_id).order_by(
        ProductTranslation.updated_at, ProductTranslation.id).all()
    legacy_by_title = defaultdict(list)
    for row in legacy:
        legacy_by_title[row.original_title].append(row)
    initial = []
    for pid, title in sorted(titles.items()):
        names = {}
        history = aliases[pid] | {title}
        # Exact current-title mappings win; otherwise the newest edited alias wins.
        candidates = [row for alias in history for row in legacy_by_title[alias]]
        for row in sorted(candidates, key=lambda row: (row.original_title == title, row.updated_at, row.id)):
            if row.original_title in history and len(owners[row.original_title]) == 1:
                names[row.original_color] = row.chinese_name
        initial.append(dict(shop_id=shop_id, shopify_product_id=pid, current_title=title,
            aliases=sorted(history), chinese_name=names.pop('', ''), color_names=names, updated_at=datetime.utcnow()))
    if db.session.get_bind().dialect.name == 'postgresql':
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    # Another sync/webhook may discover the same product concurrently.
    for start in range(0, len(initial), 100):
        db.session.execute(insert(ProductNaming).values(initial[start:start + 100]).on_conflict_do_nothing(
            index_elements=['shop_id', 'shopify_product_id']))
    records = ProductNaming.query.filter(ProductNaming.shop_id == shop_id,
        ProductNaming.shopify_product_id.in_(titles)).order_by(ProductNaming.shopify_product_id).populate_existing().with_for_update().all()
    for row in records:
        history = sorted(set(row.aliases or []) | aliases[row.shopify_product_id] | {titles[row.shopify_product_id]})
        if row.aliases != history:
            row.aliases = history
        if authoritative and row.current_title != titles[row.shopify_product_id]:
            row.current_title = titles[row.shopify_product_id]
    return records


def reconcile_names(shop_id, canonical_titles=None):
    """One-off adoption/backfill; does not change raw orders, stock keys or legacy names."""
    titles = {}
    for pid, title, _at in db.session.query(Product.shopify_product_id, Product.title,
            func.max(Product.updated_at)).filter(Product.shop_id == shop_id).group_by(
            Product.shopify_product_id, Product.title).order_by(func.max(Product.updated_at)).all():
        if valid_id(pid):
            titles[pid] = title
    for pid, values in source_titles(shop_id).items():
        titles.setdefault(pid, sorted(values)[0])
    existing = ProductNaming.query.filter_by(shop_id=shop_id).all()
    titles.update({row.shopify_product_id: row.current_title for row in existing})
    titles.update(canonical_titles or {})
    return record_catalog_names(shop_id, titles)


def fetch_current_titles(shop, product_ids):
    """Read-only, bounded repair lookup. Never change merchant Shopify data."""
    from app.services.shopify_graphql import fetch_products_page
    result = {}
    wanted = {str(pid) for pid in product_ids if str(pid).isdigit()}
    cursor = None
    while wanted.difference(result):
        nodes, page = fetch_products_page(shop, first=25, after=cursor)
        for row in nodes:
            pid = row['id'].rsplit('/', 1)[-1]
            if pid in wanted:
                result[pid] = row['title']
        next_cursor = page.get('endCursor')
        if not nodes or not page.get('hasNextPage') or not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor
    return result
