"""Shop-scoped catalog naming and non-destructive display projection."""

from flask import g, has_request_context, request
import re
from sqlalchemy import and_, case, func, literal, or_

from app import db
from app.models import AfterSale, AfterSaleItem, Order, OrderItem, Product, ProductNaming, ProductTranslation
from app.services.shipping_queue_service import _shop_id
from app.utils.errors import ValidationError


class DisplayNames(dict):
    identities = None


def _names(shop_ids):
    if not shop_ids:
        return {}
    key = tuple(sorted(shop_ids))
    if has_request_context() and g.get("product_names_request") is not request._get_current_object():
        g.product_names_request = request._get_current_object()
        g.product_display_names = {}
    cache = g.setdefault("product_display_names", {}) if has_request_context() else {}
    if key not in cache:
        names = DisplayNames({(row.shop_id, row.original_title, row.original_color): row.chinese_name
                              for row in ProductTranslation.query.filter(ProductTranslation.shop_id.in_(shop_ids)).all()})
        identities = ProductNaming.query.filter(ProductNaming.shop_id.in_(shop_ids)).all()
        names.identities = {(row.shop_id, row.shopify_product_id): row for row in identities}
        owners = {}
        legacy_keys = {}
        for old_key in names:
            legacy_keys.setdefault(old_key[:2], []).append(old_key)
        for row in identities:
            for title in set(row.aliases or []) | {row.current_title}:
                owners.setdefault((row.shop_id, title), []).append(row)
        for (shop, title), records in owners.items():
            for old_key in legacy_keys.get((shop, title), []):
                names.pop(old_key, None)
            if len(records) == 1:
                row = records[0]
                if row.chinese_name:
                    names[(shop, title, '')] = row.chinese_name
                names.update({(shop, title, color): value for color, value in (row.color_names or {}).items() if value})
        cache[key] = names
    return cache[key]


def filter_display_search(rows, search, shop_id):
    displayed = display_payload(rows, shop_id)
    fields = ("title", "color", "model", "variant_title", "sku")
    return [raw for raw, translated in zip(rows, displayed) if search in " ".join(
        str(row.get(field) or "") for row in (raw, translated) for field in fields
    ).casefold()]


def catalog_search_conditions(shop_id, search):
    conditions = []
    names = _names({shop_id})
    for (_, title, color), translated in names.items():
        if search.casefold() in translated.casefold():
            condition = Product.title == title
            if color:
                condition = and_(condition, Product.variant_title.ilike(f"%{color}%"))
            conditions.append(condition)
    for (shop, pid), row in (getattr(names, 'identities', {}) or {}).items():
        if search.casefold() in row.chinese_name.casefold():
            conditions.append(Product.shopify_product_id == pid)
        for color, translated in (row.color_names or {}).items():
            if search.casefold() in translated.casefold():
                conditions.append(and_(Product.shopify_product_id == pid, Product.variant_title.ilike(f'%{color}%')))
    return conditions


def _catalog_source(shop_id, model, rank, selected=None):
    pid = case((model.shopify_product_id.in_(('', 'None', '0', 'null')), None),
               else_=model.shopify_product_id)
    title = func.coalesce(ProductNaming.current_title, model.title)
    image = func.max(model.image_url)
    query = db.session.query(func.coalesce(pid, '').label('pid'), title.label('title'),
        model.title.label('raw_title'), model.variant_title.label('variant'), image.label('image'), literal(rank).label('rank')).select_from(model)
    if model is OrderItem:
        query = query.join(Order, Order.id == model.order_id).filter(Order.shop_id == shop_id).outerjoin(
            Product, and_(Product.shop_id == shop_id, Product.shopify_product_id == model.shopify_product_id,
                          Product.shopify_variant_id == model.shopify_variant_id))
        query = query.with_entities(func.coalesce(pid, '').label('pid'), title.label('title'),
            model.title.label('raw_title'), model.variant_title.label('variant'),
            func.coalesce(func.max(func.nullif(model.image_url, '')), func.max(Product.image_url)).label('image'), literal(rank).label('rank'))
    elif model is AfterSaleItem:
        query = query.join(AfterSale, AfterSale.id == model.after_sale_id).filter(AfterSale.shop_id == shop_id)
    else:
        query = query.filter(model.shop_id == shop_id)
    if selected is not None:
        ids = [pid for pid, title in selected if pid]
        legacy_titles = [title for pid, title in selected if not pid]
        query = query.filter(or_(model.shopify_product_id.in_(ids),
            and_(or_(model.shopify_product_id.is_(None), model.shopify_product_id.in_(('', 'None', '0', 'null'))),
                 model.title.in_(legacy_titles))))
    return query.outerjoin(ProductNaming, and_(ProductNaming.shop_id == shop_id,
        ProductNaming.shopify_product_id == pid)).group_by(pid, title, model.title, model.variant_title)


def _catalog_source_rows(shop_id, selected=None):
    return _catalog_source(shop_id, Product, 0, selected).union_all(
        _catalog_source(shop_id, OrderItem, 1, selected), _catalog_source(shop_id, AfterSaleItem, 2, selected)).subquery()


def _catalog_title_rows(shop_id):
    # Pagination/search must not aggregate images or join the variant cache for
    # every historical order. Fetch those only for the selected product IDs.
    queries = []
    for model in (Product, OrderItem, AfterSaleItem):
        pid = case((model.shopify_product_id.in_(('', 'None', '0', 'null')), None), else_=model.shopify_product_id)
        query = db.session.query(func.coalesce(pid, '').label('pid'),
            func.coalesce(ProductNaming.current_title, model.title).label('title'),
            model.title.label('raw_title'), model.variant_title.label('variant')).select_from(model)
        if model is OrderItem:
            query = query.join(Order, Order.id == model.order_id).filter(Order.shop_id == shop_id)
        elif model is AfterSaleItem:
            query = query.join(AfterSale, AfterSale.id == model.after_sale_id).filter(AfterSale.shop_id == shop_id)
        else:
            query = query.filter(model.shop_id == shop_id)
        queries.append(query.outerjoin(ProductNaming, and_(ProductNaming.shop_id == shop_id, ProductNaming.shopify_product_id == pid)))
    return queries[0].union_all(*queries[1:]).subquery()


def _catalog_titles(shop_id, search, names, mapped_only, *, page=None, per_page=20):
    source = _catalog_title_rows(shop_id)
    identities = getattr(names, 'identities', {}) or {}
    mapped_ids = {pid for (shop, pid), row in identities.items() if row.chinese_name or row.color_names}
    matched_ids = {pid for (shop, pid), row in identities.items()
                   if search in ' '.join([row.chinese_name, *(row.color_names or {}).values(), *(row.aliases or [])]).casefold()}
    mapped_titles = {title for _, title, _ in names}
    matched_titles = {title for (_, title, _), value in names.items() if search in value.casefold()}
    legacy = source.c.pid.notin_({pid for shop, pid in identities})
    query = db.session.query(source.c.pid, source.c.title).filter(source.c.title.isnot(None), source.c.title != '')
    if mapped_only:
        query = query.filter(or_(source.c.pid.in_(mapped_ids), and_(legacy, source.c.raw_title.in_(mapped_titles))))
    if search:
        escaped = search.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        pattern = f'%{escaped}%'
        query = query.filter(or_(source.c.title.ilike(pattern, escape='\\'), source.c.raw_title.ilike(pattern, escape='\\'),
            source.c.variant.ilike(pattern, escape='\\'), source.c.pid.in_(matched_ids),
            and_(legacy, source.c.raw_title.in_(matched_titles))))
    titles = query.distinct().subquery()
    query = db.session.query(titles.c.pid, titles.c.title).order_by(func.lower(titles.c.title), titles.c.pid)
    if page is None:
        return query.all()
    total = query.order_by(None).count()
    return query.offset((page - 1) * per_page).limit(per_page).all(), total


def _catalog(shop_id, titles=None):
    source = _catalog_source_rows(shop_id, titles)
    query = db.session.query(source)
    if titles is not None:
        query = query.filter(or_(*(and_(source.c.pid == pid, source.c.title == title) for pid, title in titles)))
    from app.services.purchase_service import UNKNOWN_COLOR, _color_name

    products = {}
    # Current variant cache wins over historical snapshots, which may contain a
    # product-main fallback saved before the exact variant image was available.
    for pid, title, raw_title, variant, image, rank in query.order_by(source.c.rank).all():
        if not title:
            continue
        product = products.setdefault((pid, title), {"original_title": title, "shopify_product_id": pid or None,
                                                   "image_url": "", "variants": set(), "colors": {}})
        product["variants"].add(variant or "")
        if image and not product["image_url"]:
            product["image_url"] = image
        color = _color_name(variant)
        if color != UNKNOWN_COLOR:
            group = product["colors"].setdefault(color, {"original_color": color, "image_url": "", "variants": set()})
            group["variants"].add(variant or "")
            if image and not group["image_url"]:
                group["image_url"] = image
    return products


def list_products(args):
    shop_id = _shop_id(args.get("shop_id"))
    names = _names({shop_id})
    search = str(args.get("search") or "").strip().casefold()
    mapped_only = args.get("mapped") == "yes"
    try:
        page = max(1, int(args.get("page", 1)))
        per_page = min(100, max(1, int(args.get("per_page", 20))))
    except (ValueError, TypeError) as exc:
        raise ValidationError("分页参数不正确") from exc
    selected, total = _catalog_titles(shop_id, search, names, mapped_only, page=page, per_page=per_page)
    rows = []
    for (pid, title), source in _catalog(shop_id, selected).items() if selected else []:
        identity = (getattr(names, 'identities', {}) or {}).get((shop_id, pid))
        chinese_title = identity.chinese_name if identity else names.get((shop_id, title, ""), "")
        colors = [{"original_color": color, "chinese_name": (identity.color_names or {}).get(color, '') if identity else names.get((shop_id, title, color), ""),
                   "image_url": group["image_url"], "variant_count": len(group["variants"])}
                  for color, group in sorted(source["colors"].items())]
        mapped = bool(chinese_title or any(color["chinese_name"] for color in colors))
        rows.append({"shop_id": shop_id, "shopify_product_id": pid or None, "original_title": title, "chinese_name": chinese_title,
                     "display_title": chinese_title or title, "image_url": source["image_url"],
                     "variant_count": len(source["variants"]), "colors": colors, "mapped": mapped})
    rows.sort(key=lambda row: row["original_title"].casefold())
    return {"items": rows, "pagination": {"total": total, "page": page, "per_page": per_page}}


def save_names(payload):
    shop_id = _shop_id(payload.get("shop_id"))
    title = payload.get("original_title")
    if not isinstance(title, str):
        raise ValidationError("请选择当前店铺已有商品")
    selected = _catalog_titles(shop_id, '', _names({shop_id}), False)
    pid = payload.get('shopify_product_id')
    if pid:
        selected = [pair for pair in selected if pair.pid == str(pid)]
    else:
        selected = [pair for pair in selected if pair.title == title]
    if len(selected) != 1:
        raise ValidationError("请选择当前店铺已有商品")
    title = selected[0].title
    catalog = _catalog(shop_id, selected)
    source = catalog[tuple(selected[0])]
    changes = {"": payload.get("chinese_name", "")}
    colors = payload.get("colors", [])
    if not isinstance(colors, list) or len(colors) > 200:
        raise ValidationError("颜色映射格式不正确")
    known_colors = source["colors"]
    for color in colors:
        if not isinstance(color, dict) or color.get("original_color") not in known_colors:
            raise ValidationError("颜色不属于该商品")
        original = color["original_color"]
        if original in changes:
            raise ValidationError("颜色映射不能重复")
        changes[original] = color.get("chinese_name", "")
    for original, value in changes.items():
        if not isinstance(value, str) or len(value.strip()) > 255:
            raise ValidationError("中文名称必须为文字，最多 255 个字符")
    from app.models import Shop
    if selected[0].pid:
        from app.services.product_naming_service import record_catalog_names
        record = record_catalog_names(shop_id, {selected[0].pid: title}, authoritative=False)[0]
        record.chinese_name = changes[''].strip()
        color_names = dict(record.color_names or {})
        for color, value in changes.items():
            if color and value.strip():
                color_names[color] = value.strip()
            elif color:
                color_names.pop(color, None)
        record.color_names = color_names
        db.session.commit()
        if has_request_context():
            g.product_display_names = {}
        return {"shop_id": shop_id, "shopify_product_id": record.shopify_product_id, "original_title": title, "saved": True}
    db.session.query(Shop).filter_by(id=shop_id).with_for_update().one()
    existing = {row.original_color: row for row in ProductTranslation.query.filter_by(
        shop_id=shop_id, original_title=title,
    ).all()}
    for original, value in changes.items():
        value = value.strip()
        row = existing.get(original)
        if not value:
            if row:
                db.session.delete(row)
        elif row:
            row.chinese_name = value
        else:
            db.session.add(ProductTranslation(shop_id=shop_id, original_title=title,
                                               original_color=original, chinese_name=value))
    db.session.commit()
    if has_request_context():
        g.product_display_names = {}
    return {"shop_id": shop_id, "original_title": title, "saved": True}


def display_payload(data, shop_id=None):
    """Translate only product fields; IDs, keys, models, notes and raw snapshots stay intact."""
    shops = set()

    def collect(value):
        if isinstance(value, dict):
            if isinstance(value.get("shop_id"), int):
                shops.add(value["shop_id"])
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)
    collect(data)
    if shop_id:
        shops.add(int(shop_id))
    names = _names(shops)
    identities = getattr(names, 'identities', {}) or {}
    if not names and not identities:
        return data

    def lookup(shop, title, color, pid):
        identity = identities.get((shop, str(pid))) if pid else None
        if identity:
            return identity.chinese_name if not color else (identity.color_names or {}).get(color)
        return names.get((shop, title, color))

    def project(value, shop, title="", color_group=False, pid=None):
        if isinstance(value, list):
            return [project(child, shop, title, color_group, pid) for child in value]
        if not isinstance(value, dict):
            return value
        shop = value.get("shop_id") or shop
        pid = value.get('shopify_product_id') or pid
        title = value.get("original_title") or value.get("title") or title
        result = {key: project(child, shop, title, key == "colors", pid) for key, child in value.items()}
        translated = lookup(shop, title, '', pid)
        if "title" in value and translated:
            result["original_title"] = title
            result["title"] = translated
        for field in (("color", "name") if color_group else ("color",)):
            original = value.get(f"original_{field}") or value.get(field)
            translated_color = lookup(shop, title, original, pid)
            if color_group and field == "name" and not translated_color and value.get("base_color"):
                translated_base = lookup(shop, title, value["base_color"], pid)
                if translated_base:
                    translated_color = f"{translated_base} {value.get('configuration') or ''}".strip()
            if translated_color:
                result[f"original_{field}"] = original
                result[field] = translated_color
        if isinstance(value.get("variant_title"), str):
            from app.services.purchase_service import ACCESSORY_SUFFIX_RE, COLOR_LABEL_RE
            raw = value.get("original_variant_title") or value["variant_title"]
            options = raw.split("/")
            translated_options = []
            for part in options:
                base = re.sub(r"\s*([-–])\s*", r"\1", COLOR_LABEL_RE.sub(
                    "", ACCESSORY_SUFFIX_RE.sub("", part.strip()),
                ).strip())
                translated_option = lookup(shop, title, base, pid)
                suffix = ACCESSORY_SUFFIX_RE.search(part)
                translated_options.append(
                    f"{translated_option} {suffix.group(0).strip()}" if translated_option and suffix
                    else translated_option or part.strip()
                )
            if translated_options != [part.strip() for part in options]:
                result["original_variant_title"] = raw
                result["variant_title"] = " / ".join(translated_options)
        return result
    return project(data, shop_id)


def display_response(data):
    """One batched mapping lookup at the API boundary, never inside sync or allocation."""
    if not has_request_context() or request.path.startswith("/api/products"):
        return data
    if not request.path.startswith(("/api/orders", "/api/purchase-lists", "/api/purchase-candidates",
                                    "/api/inventory", "/api/shipping", "/api/after-sales")):
        return data
    raw = request.args.get("shop_id")
    if raw is None and request.is_json:
        raw = (request.get_json(silent=True) or {}).get("shop_id")
    shop_id = int(raw) if str(raw).isdigit() else None
    return display_payload(data, shop_id)
