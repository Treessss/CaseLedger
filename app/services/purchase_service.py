"""Build and read shop-scoped procurement lists from selected orders."""

import json
import re
from collections import Counter, OrderedDict, defaultdict
from datetime import datetime
from functools import lru_cache

from sqlalchemy import or_
from sqlalchemy.orm import joinedload, load_only, noload, selectinload

from app import db
from app.models import AfterSale, InventoryReservation, Order, OrderItem, Product, PurchaseList, Shop
from app.utils.errors import ForbiddenError, NotFoundError, ValidationError
from app.utils.order_lines import is_tip_line
from app.utils.security import apply_shop_scope, can_access_shop, is_admin

MAX_SELECTED_ORDERS = 1000
MAX_PER_PAGE = 100
UNKNOWN_COLOR = "未标注颜色"
PURCHASABLE_FINANCIAL_STATUSES = ("paid", "partially_paid", "partially_refunded")
OPEN_PURCHASE_STATUSES = ("ordered", "partially_received")
COLOR_WORD_RE = re.compile(
    r"(?<![a-z])(?:black|white|grey|gray|silver|silve|gold|red|pink|blue|green|yellow|"
    r"purple|orange|brown|beige|mint|lavender|lilac|rose|coral|lemon|sky|aqua|mocha|"
    r"cream|cherry|pitaya|blush|pearl|chrome|navy|teal|cyan|magenta|burgundy|"
    r"maroon|ivory|khaki|tan|lime|rainbow|titanium|champagne|olive|scarlet|carbon|coffee|"
    r"transparent|clear|nude|chocolate)(?![a-z])|[黑白灰银金红粉蓝绿黄紫橙棕褐米青彩]色?",
    re.IGNORECASE,
)
ACCESSORY_SUFFIX_RE = re.compile(r"\s*\+\s*(?:wrist chain|stand|lanyard|strap)\b.*$", re.IGNORECASE)
COLOR_LABEL_RE = re.compile(r"^(?:color|colour|颜色)\s*[:：]\s*", re.IGNORECASE)
PHONE_MODEL_RE = re.compile(
    r"\biphone\s*(\d{1,2})\s*(pro\s*max|pro|air|plus|max|mini)?\b"
    r"|\b(?:galaxy\s*[sa]|pixel)\s*(\d{1,2})\s*(ultra|pro|plus)?\b",
    re.IGNORECASE,
)
MODEL_TIER = {"pro max": 6, "ultra": 6, "pro": 5, "air": 4, "plus": 3, "max": 2, "mini": 0}


def _positive_int(value, field):
    if isinstance(value, bool):
        raise ValidationError(f"{field}必须是正整数")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{field}必须是正整数") from exc
    if number <= 0 or str(value).strip() != str(number):
        raise ValidationError(f"{field}必须是正整数")
    return number


def _group_key(item):
    if item.shopify_variant_id:
        return ("variant", str(item.shopify_variant_id))
    variant = (item.variant_title or "").strip().casefold()
    if item.shopify_product_id:
        return ("product", str(item.shopify_product_id), variant)
    if item.sku:
        return ("sku", item.sku.strip().casefold(), variant)
    return ("description", (item.vendor or "").strip().casefold(), (item.title or "").strip().casefold(), variant)


def _aggregate_items(orders, quantities=None):
    groups = OrderedDict()
    for order in orders:
        for item in order.items:
            if is_tip_line(item):
                continue
            quantity = int(quantities.get(item.id, 0) if quantities is not None else item.quantity or 0)
            if quantity <= 0:
                continue
            key = _group_key(item)
            if key not in groups:
                groups[key] = {
                    "group_key": json.dumps(key, ensure_ascii=False),
                    "shopify_product_id": item.shopify_product_id,
                    "shopify_variant_id": item.shopify_variant_id,
                    "title": item.title or "未命名商品",
                    "variant_title": item.variant_title or "",
                    "sku": item.sku or "",
                    "vendor": item.vendor or "",
                    "image_url": item.image_url or "",
                    "quantity": 0,
                    "order_numbers": [],
                }
            group = groups[key]
            group["quantity"] += quantity
            if not group["image_url"] and item.image_url:
                group["image_url"] = item.image_url
            if order.order_number not in group["order_numbers"]:
                group["order_numbers"].append(order.order_number)
    return list(groups.values())


def _allocated_quantities(shop_id, exclude_purchase_id=None, item_group_keys=None):
    """Count stock already accepted and demand reserved by confirmed lists."""
    received = {}
    pending = {}
    lists = db.session.query(PurchaseList.id, PurchaseList.status, PurchaseList.allocations).filter(
        PurchaseList.shop_id == shop_id,
        PurchaseList.status.in_((*OPEN_PURCHASE_STATUSES, "received")),
    ).all()
    for purchase in lists:
        if purchase.id == exclude_purchase_id:
            continue
        for allocation in purchase.allocations or []:
            if "order_item_id" not in allocation:
                continue
            item_id = int(allocation["order_item_id"])
            if item_group_keys is not None and allocation.get("group_key") != item_group_keys.get(item_id):
                continue
            good = int(allocation.get("received_quantity") or 0)
            received[item_id] = received.get(item_id, 0) + good
            if purchase.status in OPEN_PURCHASE_STATUSES:
                outstanding = max(int(allocation["quantity"]) - good - int(allocation.get("damaged_quantity") or 0)
                                  - int(allocation.get("confirmed_missing_quantity") or 0), 0)
                pending[item_id] = pending.get(item_id, 0) + outstanding
    for reservation in InventoryReservation.query.filter_by(shop_id=shop_id).all():
        if item_group_keys is not None and reservation.group_key != item_group_keys.get(reservation.order_item_id):
            continue
        received[reservation.order_item_id] = received.get(reservation.order_item_id, 0) + reservation.quantity
    return received, pending


def _available_quantities(orders, shop_id, exclude_purchase_id=None):
    if not orders:
        return {}
    from app.services.inventory_pool_service import build_pool_plan
    pool = build_pool_plan(shop_id, include_order_ids=[order.id for order in orders],
                           exclude_purchase_id=exclude_purchase_id)
    result = {}
    for order in orders:
        remaining = Counter(pool["uncovered_by_order"].get(order.id, {}))
        for item in order.items:
            key = json.dumps(_group_key(item), ensure_ascii=False)
            take = 0 if is_tip_line(item) else min(int(item.quantity or 0), remaining[key])
            result[item.id] = take
            remaining[key] -= take
    return result


def _build_allocations(orders, quantities):
    return [
        {
            "order_id": order.id,
            "order_item_id": item.id,
            "group_key": json.dumps(_group_key(item), ensure_ascii=False),
            "quantity": quantities.get(item.id, 0),
            "received_quantity": 0,
        }
        for order in orders for item in order.items if quantities.get(item.id, 0) > 0 and not is_tip_line(item)
    ]


def _purchase_or_error(purchase_id):
    purchase = db.session.get(PurchaseList, purchase_id)
    if purchase is None:
        raise NotFoundError("采购清单不存在")
    if not can_access_shop(purchase.shop_id):
        raise ForbiddenError("无权操作该采购清单")
    return purchase


def remove_draft_tip_lines(purchase_id, shop_id):
    """Repair an explicitly selected draft; never rewrite receipt history."""
    Shop.query.filter_by(id=shop_id).with_for_update().one()
    purchase = PurchaseList.query.filter_by(id=purchase_id, shop_id=shop_id).with_for_update().one()
    if purchase.status != "draft" or purchase.confirmed_at or purchase.receipt or purchase.receipt_batches:
        raise ValidationError("只能清理尚未发起采购、尚未入库的草稿")
    removed = [item for item in purchase.items or [] if is_tip_line(item)]
    if not removed:
        return 0
    keys = {item["group_key"] for item in removed}
    purchase.items = [item for item in purchase.items if item["group_key"] not in keys]
    purchase.allocations = [row for row in purchase.allocations or [] if row.get("group_key") not in keys]
    purchase.total_quantity = sum(int(item.get("quantity") or 0) for item in purchase.items)
    return len(removed)


def _product_key(item):
    """Keep every variant of one Shopify product under the same parent."""
    if item.get("shopify_product_id"):
        return ("product", str(item["shopify_product_id"]))
    return (
        "description",
        (item.get("vendor") or "").strip().casefold(),
        (item.get("title") or "").strip().casefold(),
    )


@lru_cache(maxsize=8192)
def _color_name(variant_title):
    """Extract an explicit color from either side of Shopify's slash-separated options."""
    for part in (variant_title or "").split("/"):
        candidate = COLOR_LABEL_RE.sub("", part.strip())
        candidate = ACCESSORY_SUFFIX_RE.sub("", candidate).strip()
        if COLOR_WORD_RE.search(candidate):
            return re.sub(r"\s*([-–])\s*", r"\1", candidate)
        if COLOR_LABEL_RE.match(part.strip()) and candidate:
            return candidate
    return UNKNOWN_COLOR


def _phone_model_sort_key(item):
    """Sort newest phone generations first, then higher tiers within a generation."""
    title = (item.get("variant_title") or "").strip()
    match = PHONE_MODEL_RE.search(title)
    if not match:
        return (0, 0, 0, title.casefold())
    generation = int(match.group(1) or match.group(3))
    tier = (match.group(2) or match.group(4) or "").lower()
    tier = re.sub(r"\s+", " ", tier).strip()
    return (1, generation, MODEL_TIER.get(tier, 1), title.casefold())


def _accessory_configuration(variant_title):
    """Physical bundle suffix (wrist chain, stand, lanyard, strap) or ``""``.

    Shopify keeps these as a separate variant with its own SKU and stock, while
    the phone model is identical.  Inventory must expose the suffix, otherwise
    two genuinely different products render as identical rows.
    """
    return " / ".join(
        match.group(0).strip()
        for part in (variant_title or "").split("/")
        if (match := ACCESSORY_SUFFIX_RE.search(part))
    )


@lru_cache(maxsize=8192)
def _model_name(variant_title, color_name):
    """Show only the model within a color group, preserving unusual variants."""
    title = (variant_title or "").strip()
    if color_name != UNKNOWN_COLOR:
        parts = [part.strip() for part in title.split("/")]
        remaining = [
            part for part in parts
            if COLOR_LABEL_RE.sub("", part).strip().casefold()
            != color_name.casefold()
        ]
        if remaining and len(remaining) < len(parts):
            return " / ".join(remaining)
    match = PHONE_MODEL_RE.search(title)
    return match.group(0) if match else title or "默认款"


def _group_colors(product):
    colors = OrderedDict()
    for item in product["variants"]:
        base_color = _color_name(item.get("variant_title"))
        # A bundle is a separate physical configuration, with its own image.
        # Keep the base color for Chinese mappings, but never share a picture
        # or model bucket with the plain case.
        configuration = _accessory_configuration(item.get("variant_title"))
        name = f"{base_color} {configuration}" if configuration else base_color
        key = name.casefold()
        if key not in colors:
            colors[key] = {
                "name": name,
                "base_color": base_color,
                "configuration": configuration,
                "quantity": 0,
                "variant_count": 0,
                "image_url": item.get("image_url") or "",
                "order_numbers": [],
                "variants": [],
            }
        color = colors[key]
        color["quantity"] += item.get("quantity") or 0
        color["variant_count"] += 1
        if not color["image_url"] and item.get("image_url"):
            color["image_url"] = item["image_url"]
        for order_number in item.get("order_numbers") or []:
            if order_number not in color["order_numbers"]:
                color["order_numbers"].append(order_number)
        model_title = _model_name(item.get("variant_title"), name)
        if base_color == UNKNOWN_COLOR:
            sku = (item.get("sku") or "").strip()
            model_title = f"{model_title} · SKU: {sku or '未设置'}"
        color["variants"].append({**item, "model_title": model_title})
    for color in colors.values():
        color["variants"].sort(key=_phone_model_sort_key, reverse=True)
    return sorted(colors.values(), key=lambda color: (color["base_color"] == UNKNOWN_COLOR, color["name"].casefold()))


def _group_products(items):
    """Build a product → color → variant view, including old flat snapshots."""
    products = OrderedDict()
    for item in items or []:
        key = _product_key(item)
        if key not in products:
            products[key] = {
                "group_key": json.dumps(key, ensure_ascii=False),
                "shopify_product_id": item.get("shopify_product_id"),
                "title": item.get("title") or "未命名商品",
                "vendor": item.get("vendor") or "",
                "image_url": item.get("image_url") or "",
                "quantity": 0,
                "variant_count": 0,
                "order_numbers": [],
                "variants": [],
            }
        product = products[key]
        product["quantity"] += item.get("quantity") or 0
        product["variant_count"] += 1
        if not product["image_url"] and item.get("image_url"):
            product["image_url"] = item["image_url"]
        for order_number in item.get("order_numbers") or []:
            if order_number not in product["order_numbers"]:
                product["order_numbers"].append(order_number)
        product["variants"].append(item)
    for product in products.values():
        product["colors"] = _group_colors(product)
        product["color_count"] = len(product["colors"])
    # Stable partition: keep product order within each section, and preserve
    # product identities for per-product PDF exports and stock allocations.
    return sorted(products.values(), key=lambda product: any(
        color["base_color"] == UNKNOWN_COLOR for color in product["colors"]
    ))


def _receipt_totals(purchase):
    """Return the four quantities operators need to read a list at a glance.

    ``shortage`` is confirmed missing (supplier did not send), ``pending`` is the
    part nobody has counted yet.  Legacy rows without ``confirmed_missing_quantity``
    only treat their missing quantity as confirmed once the list is closed.
    """
    receipt = purchase.receipt or []
    received = sum(int(row.get("received_quantity") or 0) for row in receipt)
    damaged = sum(int(row.get("damaged_quantity") or 0) for row in receipt)
    shortage = 0
    for row in receipt:
        confirmed = row.get("confirmed_missing_quantity")
        if confirmed is None:
            confirmed = (row.get("missing_quantity") or 0) if purchase.status == "received" else 0
        shortage += int(confirmed or 0)
    pending = max(int(purchase.total_quantity or 0) - received - damaged - shortage, 0)
    if purchase.status in ("draft", "cancelled"):
        # Not ordered yet: nothing is "awaiting arrival".
        pending = 0
    return {"received_quantity": received, "damaged_quantity": damaged,
            "shortage_quantity": shortage, "pending_quantity": pending}


def _replenishment_ids(purchase):
    """Lists already created from this list's confirmed shortages/damage."""
    if purchase.status not in ("partially_received", "received"):
        return []
    rows = PurchaseList.query.filter(
        PurchaseList.shop_id == purchase.shop_id,
        PurchaseList.status.in_(("draft", "ordered", "partially_received", "received")),
    ).all()
    return sorted({
        candidate.id for candidate in rows
        if candidate.id != purchase.id and any(
            int(row.get("source_purchase_id") or 0) == purchase.id
            for row in candidate.allocations or []
        )
    })


def _serialize_purchase(purchase, include_items=False, after_sales=None):
    data = purchase.to_dict(include_items=include_items)
    products = _group_products(purchase.items)
    totals = _receipt_totals(purchase)
    data.update(totals)
    data["product_count"] = len(products)
    data["color_count"] = sum(product["color_count"] for product in products)
    if purchase.after_sale_id:
        record = after_sales.get(purchase.after_sale_id) if after_sales is not None else db.session.get(AfterSale, purchase.after_sale_id)
        data["customer_reference"] = record.customer_reference if record else None
    after_sale_ids = sorted({int(row["after_sale_id"]) for row in purchase.allocations or []
                             if row.get("after_sale_id")})
    data["after_sale_ids"] = after_sale_ids
    data["customer_references"] = [record.customer_reference + ('（已删除）' if record.deleted_at else '') for record in
                                   ((after_sales.get(item_id) if after_sales is not None else db.session.get(AfterSale, item_id))
                                    for item_id in after_sale_ids) if record]
    if include_items:
        from app.services.purchase_edit_service import EDITABLE, edit_token
        from app.services.purchase_links_service import after_sale_links
        data['after_sale_orders'] = after_sale_links(purchase)
        data['replenishment_ids'] = _replenishment_ids(purchase)
        data['edit_token'] = edit_token(purchase)
        data['can_edit_items'] = purchase.status in EDITABLE
        data["products"] = products
        # 关联订单（订单详情/采购详情之间交叉跳转用）
        order_ids = [int(item) for item in (purchase.order_ids or []) if str(item).isdigit()]
        if order_ids:
            rows = Order.query.filter(
                Order.shop_id == purchase.shop_id, Order.id.in_(order_ids)
            ).all()
            by_id = {row.id: row for row in rows}
            data["orders"] = [
                {"id": order_id, "order_number": by_id[order_id].order_number}
                for order_id in order_ids
                if order_id in by_id
            ]
        else:
            data["orders"] = []
    else:
        data.pop("receipt", None)
        data.pop("receipt_batches", None)
    return data


def ensure_after_sale_purchase_draft(record, *, confirmed=False):
    """Put unfilled after-sale demand into the existing purchase-list queue."""
    if record.status == "shipped" or record.deleted_at:
        return None
    open_lists = PurchaseList.query.filter(
        PurchaseList.after_sale_id == record.id,
        PurchaseList.status.in_(("draft", *OPEN_PURCHASE_STATUSES)),
    ).all()
    pending = Counter()
    for purchase in open_lists:
        for allocation in purchase.allocations or []:
            pending[int(allocation["after_sale_item_id"])] += max(
                int(allocation["quantity"]) - int(allocation.get("received_quantity") or 0)
                - int(allocation.get("damaged_quantity") or 0)
                - int(allocation.get("confirmed_missing_quantity") or 0), 0,
            )
    groups = OrderedDict()
    allocations = []
    for item in record.items:
        needed = max(int(item.quantity) - int(item.reserved_quantity or 0) - pending[item.id], 0)
        if not needed:
            continue
        catalog = db.session.get(OrderItem, item.catalog_order_item_id) if item.catalog_order_item_id else None
        group = groups.setdefault(item.group_key, {
            "group_key": item.group_key,
            "shopify_product_id": item.shopify_product_id or (catalog.shopify_product_id if catalog else None),
            "shopify_variant_id": item.shopify_variant_id or (catalog.shopify_variant_id if catalog else None),
            "title": item.title, "variant_title": item.variant_title or "",
            "sku": item.sku or "", "vendor": item.vendor or (catalog.vendor if catalog else ""),
            "image_url": item.image_url or "", "quantity": 0,
            "order_numbers": [record.customer_reference],
        })
        group["quantity"] += needed
        allocations.append({
            "after_sale_id": record.id, "after_sale_item_id": item.id,
            "group_key": item.group_key, "quantity": needed, "received_quantity": 0,
        })
    if not allocations:
        return None
    purchase = PurchaseList(
        shop_id=record.shop_id, after_sale_id=record.id,
        name=f"售后采购 {record.customer_reference}", order_ids=[], items=list(groups.values()),
        order_count=1, total_quantity=sum(row["quantity"] for row in allocations),
        allocations=allocations, status="ordered" if confirmed else "draft",
        confirmed_at=datetime.utcnow() if confirmed else None,
    )
    db.session.add(purchase)
    db.session.flush()
    return purchase


def create_after_sale_purchase_list(payload):
    """Compatibility endpoint for a single after-sale selection."""
    after_sale_id = _positive_int(payload.get("after_sale_id"), "after_sale_id")
    record = db.session.get(AfterSale, after_sale_id)
    if record is None:
        raise NotFoundError("售后单不存在")
    return create_purchase_list({"shop_id": record.shop_id, "order_ids": [], "after_sale_ids": [after_sale_id]})


def create_stock_purchase_list(shop_id, entries, name):
    """Create a catalog-backed replenishment draft with no order commitments."""
    shop_id = _positive_int(shop_id, "shop_id")
    if not can_access_shop(shop_id):
        raise ForbiddenError("无权为该店铺生成采购清单")
    if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_SELECTED_ORDERS:
        raise ValidationError("请指定采购变体和数量")
    name = str(name or "").strip()
    if not name or len(name) > 120:
        raise ValidationError("请填写不超过 120 字的采购清单名称")
    quantities = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValidationError("采购变体格式错误")
        product_id = _positive_int(entry.get("product_id"), "product_id")
        quantity = _positive_int(entry.get("quantity"), "quantity")
        if product_id in quantities or quantity > 100_000:
            raise ValidationError("采购变体不能重复，单个数量不能超过 100000")
        quantities[product_id] = quantity
    shop = Shop.query.filter_by(id=shop_id).with_for_update().first()
    if shop is None:
        raise NotFoundError("店铺不存在")
    products = Product.query.filter(Product.shop_id == shop_id, Product.id.in_(quantities)).all()
    if len(products) != len(quantities) or any(is_tip_line(product) for product in products):
        raise ValidationError("采购商品不存在、不属于当前店铺或不是实物商品")
    by_id = {product.id: product for product in products}
    items = []
    allocations = []
    for product_id, quantity in quantities.items():
        product = by_id[product_id]
        key = json.dumps(_group_key(product), ensure_ascii=False)
        items.append({"group_key": key, "shopify_product_id": product.shopify_product_id,
                      "shopify_variant_id": product.shopify_variant_id, "title": product.title,
                      "variant_title": product.variant_title or "", "sku": product.sku or "",
                      "vendor": product.vendor or "", "image_url": product.image_url or "",
                      "quantity": quantity, "order_numbers": []})
        allocations.append({"stock_only": True, "catalog_product_id": product_id,
                            "group_key": key, "quantity": quantity, "received_quantity": 0})
    purchase = PurchaseList(shop_id=shop_id, name=name, order_ids=[], order_count=0,
                            items=items, allocations=allocations, total_quantity=sum(quantities.values()))
    db.session.add(purchase)
    db.session.commit()
    return _serialize_purchase(purchase, include_items=True)


def create_purchase_list(payload):
    shop_id = _positive_int(payload.get("shop_id"), "shop_id")
    if not can_access_shop(shop_id):
        raise ForbiddenError("无权为该店铺生成采购清单")
    if db.session.get(Shop, shop_id) is None:
        raise NotFoundError("店铺不存在")

    raw_ids = payload.get("order_ids", [])
    raw_after_sale_ids = payload.get("after_sale_ids", [])
    if not isinstance(raw_ids, list) or not isinstance(raw_after_sale_ids, list) or not (raw_ids or raw_after_sale_ids):
        raise ValidationError("请至少选择一笔订单或售后单")
    if len(raw_ids) + len(raw_after_sale_ids) > MAX_SELECTED_ORDERS:
        raise ValidationError(f"每份清单最多选择 {MAX_SELECTED_ORDERS} 笔订单")
    order_ids = [_positive_int(value, "order_id") for value in raw_ids]
    after_sale_ids = [_positive_int(value, "after_sale_id") for value in raw_after_sale_ids]
    if len(set(order_ids)) != len(order_ids):
        raise ValidationError("订单不能重复选择")
    if len(set(after_sale_ids)) != len(after_sale_ids):
        raise ValidationError("售后单不能重复选择")

    orders = Order.query.options(selectinload(Order.items), noload(Order.parcels)).filter(Order.id.in_(order_ids)).all()
    by_id = {order.id: order for order in orders}
    if len(by_id) != len(order_ids):
        raise NotFoundError("所选订单中有订单不存在")
    if any(order.shop_id != shop_id for order in orders):
        raise ForbiddenError("所选订单必须全部属于同一个店铺")

    selected_orders = [by_id[order_id] for order_id in order_ids]
    # Scope-check both sources BEFORE contacting Shopify; after-sale replacement
    # items are explicit user choices and must not be overwritten from the original.
    after_sales = AfterSale.query.options(selectinload(AfterSale.items)).filter(AfterSale.id.in_(after_sale_ids)).all()
    by_after_sale_id = {record.id: record for record in after_sales}
    if len(by_after_sale_id) != len(after_sale_ids):
        raise NotFoundError("所选售后单中有售后单不存在")
    if any(record.shop_id != shop_id for record in after_sales):
        raise ForbiddenError("所选售后单必须属于当前店铺")
    from app.services.purchase_order_refresh_service import (
        apply_purchase_order_updates, fetch_purchase_orders, lock_purchase_detail_refresh,
    )

    lock_purchase_detail_refresh(shop_id)
    orders = Order.query.options(selectinload(Order.items), noload(Order.parcels)).filter(
        Order.id.in_(order_ids), Order.shop_id == shop_id,
    ).execution_options(populate_existing=True).all()
    if len(orders) != len(order_ids):
        raise ValidationError("所选订单已发生变化，请刷新采购列表后重试")
    by_id = {order.id: order for order in orders}
    selected_orders = [by_id[order_id] for order_id in order_ids]

    resources = fetch_purchase_orders(shop_id, selected_orders)
    # Network waits must not hold the shop's procurement/receipt transaction lock.
    Shop.query.filter_by(id=shop_id).with_for_update().one()
    # Concurrent sync may have updated these identities while API calls ran.
    current_orders = Order.query.options(selectinload(Order.items), noload(Order.parcels)).filter(
        Order.id.in_(order_ids), Order.shop_id == shop_id,
    ).execution_options(populate_existing=True).all()
    if len(current_orders) != len(order_ids):
        raise ValidationError("所选订单已发生变化，请刷新采购列表后重试")
    current_by_id = {order.id: order for order in current_orders}
    selected_orders = [current_by_id[order_id] for order_id in order_ids]
    apply_purchase_order_updates(shop_id, resources, selected_orders)
    _validate_order_eligibility(selected_orders)
    for order in selected_orders:
        order.procurement_excluded_at = None
    quantities = _available_quantities(selected_orders, shop_id)
    items = _aggregate_items(selected_orders, quantities)
    allocations = _build_allocations(selected_orders, quantities)
    pending_by_item = Counter()
    from app.services.inventory_pool_service import build_pool_plan
    cf_pool = build_pool_plan(shop_id) if after_sale_ids else None
    pending_lists = db.session.query(PurchaseList.allocations).filter(
        PurchaseList.shop_id == shop_id, PurchaseList.status == "draft",
    ).all() if after_sale_ids else []
    for existing in pending_lists:
        for allocation in existing.allocations or []:
            if allocation.get("after_sale_item_id"):
                pending_by_item[int(allocation["after_sale_item_id"])] += max(
                    int(allocation["quantity"]) - int(allocation.get("received_quantity") or 0)
                    - int(allocation.get("damaged_quantity") or 0)
                    - int(allocation.get("confirmed_missing_quantity") or 0), 0,
                )
    by_key = {item["group_key"]: item for item in items}
    for after_sale_id in after_sale_ids:
        record = by_after_sale_id[after_sale_id]
        if record.shop_id != shop_id:
            raise ForbiddenError("所选售后单必须属于当前店铺")
        if record.status == "shipped" or record.deleted_at:
            raise ValidationError(f"售后单 {record.customer_reference} 已出库")
        selected_quantity = 0
        cf_needed = Counter(cf_pool["cf_uncovered"].get(record.id, {}))
        for item in record.items:
            needed = max(min(int(item.quantity), cf_needed[item.group_key]) - pending_by_item[item.id], 0)
            cf_needed[item.group_key] -= needed
            if not needed:
                continue
            selected_quantity += needed
            catalog = db.session.get(OrderItem, item.catalog_order_item_id) if item.catalog_order_item_id else None
            group = by_key.get(item.group_key)
            if group is None:
                group = {"group_key": item.group_key,
                         "shopify_product_id": item.shopify_product_id or (catalog.shopify_product_id if catalog else None),
                         "shopify_variant_id": item.shopify_variant_id or (catalog.shopify_variant_id if catalog else None),
                         "title": item.title, "variant_title": item.variant_title or "",
                         "sku": item.sku or "", "vendor": item.vendor or (catalog.vendor if catalog else ""),
                         "image_url": item.image_url or "", "quantity": 0, "order_numbers": []}
                by_key[item.group_key] = group
                items.append(group)
            group["quantity"] += needed
            if record.customer_reference not in group["order_numbers"]:
                group["order_numbers"].append(record.customer_reference)
            allocations.append({"after_sale_id": record.id, "after_sale_item_id": item.id,
                                "group_key": item.group_key, "quantity": needed, "received_quantity": 0})
        if not selected_quantity:
            raise ValidationError(f"售后单 {record.customer_reference} 已无待采购商品或已加入其他清单")
    if not items:
        raise ValidationError("所选订单没有待采购商品；请确认是否已发起采购或已入库")

    name = str(payload.get("name") or "").strip()
    if len(name) > 120:
        raise ValidationError("采购清单名称不能超过 120 字")
    purchase = PurchaseList(
        shop_id=shop_id,
        after_sale_id=after_sale_ids[0] if len(after_sale_ids) == 1 and not order_ids else None,
        name=name or "采购清单",
        order_ids=order_ids,
        items=items,
        order_count=len(order_ids) + len(after_sale_ids),
        total_quantity=sum(item["quantity"] for item in items),
        allocations=allocations,
    )
    db.session.add(purchase)
    db.session.flush()
    if not name:
        purchase.name = f"采购清单 #{purchase.id}"
    db.session.commit()
    return _serialize_purchase(purchase, include_items=True)


def list_purchase_lists(args):
    page = max(args.get("page", 1, type=int) or 1, 1)
    per_page = min(max(args.get("per_page", 20, type=int) or 20, 1), MAX_PER_PAGE)
    query = apply_shop_scope(PurchaseList.query, PurchaseList).options(
        joinedload(PurchaseList.shop)
    ).filter(PurchaseList.status != "cancelled")
    pagination = query.order_by(PurchaseList.created_at.desc(), PurchaseList.id.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )
    after_sale_ids = {item.after_sale_id for item in pagination.items if item.after_sale_id}
    after_sale_ids.update(int(row["after_sale_id"]) for item in pagination.items
                          for row in item.allocations or [] if row.get("after_sale_id"))
    after_sales = {record.id: record for record in AfterSale.query.filter(AfterSale.id.in_(after_sale_ids)).all()} if after_sale_ids else {}
    return {
        "purchase_lists": [_serialize_purchase(item, after_sales=after_sales) for item in pagination.items],
        "pagination": {
            "page": pagination.page,
            "per_page": pagination.per_page,
            "total": pagination.total,
            "pages": pagination.pages,
        },
    }


def get_purchase_list(purchase_id):
    purchase = _purchase_or_error(purchase_id)
    return _serialize_purchase(purchase, include_items=True)


def purchase_lists_for_order(order_id):
    """该订单出现在哪些采购清单里（订单详情反向跳转用）。

    ``PurchaseList.order_ids`` 是 JSON 数组，跨库可移植的做法是按店铺取出后
    在 Python 里比对——采购清单数量很少，开销可以忽略。
    """
    order = db.session.get(Order, order_id)
    if order is None:
        raise NotFoundError("订单不存在")
    if not can_access_shop(order.shop_id):
        raise ForbiddenError("无权查看该订单")

    rows = (
        PurchaseList.query.filter_by(shop_id=order.shop_id)
        .order_by(PurchaseList.id.desc())
        .all()
    )
    matched = [row for row in rows if order.id in (row.order_ids or [])]
    return [_serialize_purchase(row) for row in matched]


def exclude_order_procurement(shop_id, order_ids):
    """Dismiss this queue occurrence, without forbidding future procurement."""
    if not is_admin() or not can_access_shop(shop_id):
        raise ForbiddenError("只有管理员可以移除采购需求")
    if not order_ids or len(set(order_ids)) != len(order_ids):
        raise ValidationError("请指定不重复的订单")
    Shop.query.filter_by(id=shop_id).with_for_update().one()
    orders = Order.query.filter(Order.shop_id == shop_id, Order.id.in_(order_ids)).with_for_update().all()
    if len(orders) != len(order_ids):
        raise ValidationError("订单不属于当前店铺或已不存在")
    if any(order.warehouse_status != "pending" or order.financial_status != "paid"
           or order.fulfillment_status != "fulfilled" or order.cancelled_at for order in orders):
        raise ValidationError("订单状态已变化，请重新核对移除范围")
    now = datetime.utcnow()
    for order in orders:
        order.procurement_excluded_at = order.procurement_excluded_at or now
    db.session.commit()
    return {"order_numbers": [order.order_number for order in orders], "excluded_at": now.isoformat()}


def _validate_order_eligibility(orders):
    for order in orders:
        if order.cancelled_at or order.financial_status not in PURCHASABLE_FINANCIAL_STATUSES:
            raise ValidationError(f"订单 {order.order_number} 未付款、已退款或已取消，不能发起采购")
        if order.warehouse_status != "pending":
            raise ValidationError(f"订单 {order.order_number} 已出库或历史出库状态待核实，不能发起采购")


def list_purchase_candidates(args):
    """Classify unshipped paid orders; selection is limited to outstanding demand."""
    shop_id = _positive_int(args.get("shop_id"), "shop_id")
    if not can_access_shop(shop_id):
        raise ForbiddenError("无权查看该店铺的采购订单")
    if db.session.get(Shop, shop_id) is None:
        raise NotFoundError("店铺不存在")

    page = max(args.get("page", 1, type=int) or 1, 1)
    per_page = min(max(args.get("per_page", 20, type=int) or 20, 1), MAX_PER_PAGE)
    view = str(args.get("view") or "needs_purchase")
    if view not in ("needs_purchase", "unprocured", "replenish", "purchased", "purchasing", "stocked", "after_sale"):
        raise ValidationError("采购分类不支持")
    # One fresh shop plan per request, shared by originals and after-sales.
    # Load it before the table's narrower projection to avoid deferred item fields.
    from app.services.inventory_pool_service import build_pool_plan
    cf_pool = build_pool_plan(shop_id)
    query = Order.query.options(
        load_only(Order.id, Order.shop_id, Order.order_number, Order.customer_name,
                  Order.financial_status, Order.fulfillment_status, Order.warehouse_status, Order.order_date,
                  Order.shopify_details_synced_at),
        selectinload(Order.items).load_only(OrderItem.id, OrderItem.order_id, OrderItem.quantity,
            OrderItem.title, OrderItem.shopify_product_id, OrderItem.shopify_variant_id,
            OrderItem.sku, OrderItem.variant_title),
        noload(Order.parcels), noload(Order.after_sales),
    ).filter(
        Order.shop_id == shop_id,
        Order.financial_status.in_(PURCHASABLE_FINANCIAL_STATUSES),
        Order.cancelled_at.is_(None),
        Order.warehouse_status == "pending",
        Order.procurement_excluded_at.is_(None),
    )
    search = str(args.get("search") or "").strip()
    if search:
        pattern = f"%{search}%"
        query = query.filter(or_(Order.order_number.ilike(pattern), Order.customer_name.ilike(pattern)))

    orders = query.order_by(Order.order_date.desc(), Order.id.desc()).all()
    rows = [
        {
            "id": order.id,
            "shop_id": order.shop_id,
            "order_number": order.order_number,
            "customer_name": order.customer_name,
            "financial_status": order.financial_status,
            "fulfillment_status": order.fulfillment_status,
            "warehouse_status": order.warehouse_status,
            "order_date": order.order_date.isoformat(),
            "shopify_details_synced_at": (
                order.shopify_details_synced_at.isoformat() if order.shopify_details_synced_at else None
            ),
        }
        for order in orders if any(int(item.quantity or 0) > 0 and not is_tip_line(item) for item in order.items)
    ]
    attach_procurement_status(rows, order_items={order.id: order.items for order in orders}, pools={shop_id: cf_pool})
    for row in rows:
        remaining = row["procurement_needed_quantity"]
        if remaining > 0:
            row["procurement_category"] = (
                "replenish" if row["purchase_initiated"] or row["procurement_stocked_quantity"] > 0
                else "unprocured"
            )
        elif row["procurement_pending_quantity"] > 0:
            row["procurement_category"] = "purchasing"
        else:
            row["procurement_category"] = "stocked"
    counts = Counter(row["procurement_category"] for row in rows)
    counts["needs_purchase"] = counts["unprocured"] + counts["replenish"]
    counts["purchased"] = sum(bool(row["purchase_initiated"]) for row in rows)
    after_sale_rows = []
    after_sales = AfterSale.query.options(
        selectinload(AfterSale.items), joinedload(AfterSale.order),
    ).filter(
        AfterSale.shop_id == shop_id, AfterSale.status != "shipped", AfterSale.deleted_at.is_(None),
    ).order_by(AfterSale.created_at.desc(), AfterSale.id.desc()).all()
    open_purchases = PurchaseList.query.filter(
        PurchaseList.shop_id == shop_id,
        PurchaseList.status == "draft",
    ).all()
    pending_by_item = Counter()
    for purchase in open_purchases:
        for allocation in purchase.allocations or []:
            if not allocation.get("after_sale_item_id"):
                continue
            pending_by_item[int(allocation["after_sale_item_id"])] += max(
                int(allocation["quantity"]) - int(allocation.get("received_quantity") or 0)
                - int(allocation.get("damaged_quantity") or 0)
                - int(allocation.get("confirmed_missing_quantity") or 0), 0,
            )
    for record in after_sales:
        if search and search.casefold() not in record.customer_reference.casefold() and search.casefold() not in record.order.order_number.casefold():
            continue
        needed = max(sum(cf_pool["cf_uncovered"].get(record.id, {}).values())
                     - sum(pending_by_item[item.id] for item in record.items), 0)
        if needed:
            after_sale_rows.append({
                "id": record.id, "customer_reference": record.customer_reference,
                "order_id": record.order_id, "order_number": record.order.order_number,
                "kind": record.kind, "status": record.status, "needed_quantity": needed,
                "created_at": record.created_at.isoformat(),
            })
    counts["after_sale"] = len(after_sale_rows)
    counts["needs_purchase"] += counts["after_sale"]
    if view == "after_sale":
        total = len(after_sale_rows)
        return {
            "orders": [], "after_sales": after_sale_rows[(page - 1) * per_page:page * per_page],
            "all_order_ids": [], "all_after_sale_ids": [row["id"] for row in after_sale_rows],
            "category_counts": {key: counts[key] for key in ("needs_purchase", "unprocured", "replenish", "purchased", "purchasing", "stocked", "after_sale")},
            "selection_limit": MAX_SELECTED_ORDERS,
            "pagination": {"page": page, "per_page": per_page, "total": total,
                           "pages": (total + per_page - 1) // per_page},
        }
    selected = [
        row for row in rows
        if row["procurement_category"] == view
        or (view == "needs_purchase" and row["procurement_needed_quantity"] > 0)
        or (view == "purchased" and row["purchase_initiated"])
    ]
    total = len(selected)
    page_rows = selected[(page - 1) * per_page:page * per_page]
    return {
        "orders": page_rows,
        "after_sales": after_sale_rows if view == "needs_purchase" else [],
        "all_order_ids": [row["id"] for row in selected if row["procurement_needed_quantity"] > 0],
        "all_after_sale_ids": [row["id"] for row in after_sale_rows] if view == "needs_purchase" else [],
        "category_counts": {key: counts[key] for key in ("needs_purchase", "unprocured", "replenish", "purchased", "purchasing", "stocked", "after_sale")},
        "selection_limit": MAX_SELECTED_ORDERS,
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total": total,
            "pages": (total + per_page - 1) // per_page,
        },
    }


def delete_purchase_list(purchase_id):
    purchase = _purchase_or_error(purchase_id)
    if any(row.get("after_sale_id") for row in purchase.allocations or []) and purchase.status != "draft":
        raise ValidationError("已发起的售后采购清单不能删除")
    Shop.query.filter_by(id=purchase.shop_id).with_for_update().one()
    db.session.refresh(purchase, with_for_update=True)
    if purchase.status in ("received", "partially_received"):
        raise ValidationError("已入库清单关联库存记录，不能删除")
    if purchase.status == "cancelled":
        raise NotFoundError("采购清单不存在")
    if purchase.status == "ordered":
        # Keep an audit trail but release the pending allocation immediately.
        purchase.status = "cancelled"
    else:
        db.session.delete(purchase)
    db.session.commit()
    # 清单取消/删除后这些订单又变成缺货了；如果仓库本来就有现货，立刻补上，
    # 免得它们重新出现在待采购里被重复采购。
    from app.services.inventory_service import auto_allocate_stock

    auto_allocate_stock(purchase.shop_id)
    return {"id": purchase_id, "deleted": True}


def _legacy_allocations(purchase, orders, available):
    """Support draft lists saved before allocation snapshots existed."""
    needed = {item["group_key"]: int(item["quantity"]) for item in purchase.items or []}
    allocations = []
    for order in orders:
        for item in order.items:
            key = json.dumps(_group_key(item), ensure_ascii=False)
            take = min(needed.get(key, 0), available.get(item.id, 0))
            if take:
                allocations.append({
                    "order_id": order.id, "order_item_id": item.id, "group_key": key,
                    "quantity": take, "received_quantity": 0,
                })
                needed[key] -= take
    if any(needed.values()):
        raise ValidationError("旧清单的订单商品已变化或已被其他清单占用，请重新生成采购清单")
    return allocations


def confirm_purchase_list(purchase_id):
    purchase = _purchase_or_error(purchase_id)
    # Serialize procurement decisions per shop on PostgreSQL.
    Shop.query.filter_by(id=purchase.shop_id).with_for_update().one()
    db.session.refresh(purchase, with_for_update=True)
    if purchase.status != "draft":
        raise ValidationError("只有草稿清单可以确认发起采购")
    orders = Order.query.options(selectinload(Order.items)).filter(Order.id.in_(purchase.order_ids)).all()
    by_id = {order.id: order for order in orders if order.shop_id == purchase.shop_id}
    selected = [by_id[order_id] for order_id in purchase.order_ids if order_id in by_id]
    # 采购清单是人工确认过的快照：之后订单/售后发生任何变化都不再拦截确认。
    # 多采购或少采购的数量都以清单为准，差额到货后计入库存，不修改清单商品和数量。
    allocations = purchase.allocations or []
    if not allocations and selected:
        try:
            allocations = _legacy_allocations(purchase, selected, _available_quantities(selected, purchase.shop_id))
        except ValidationError:
            allocations = []
    after_sale_ids = {int(row["after_sale_id"]) for row in allocations
                      if row.get("after_sale_item_id") and row.get("after_sale_id")}
    after_sales = AfterSale.query.options(selectinload(AfterSale.items)).filter(
        AfterSale.id.in_(after_sale_ids)).all() if after_sale_ids else []
    after_sales = [record for record in after_sales if record.shop_id == purchase.shop_id and not record.deleted_at]
    purchase.allocations = allocations
    purchase.status = "ordered"
    purchase.confirmed_at = datetime.utcnow()
    for record in after_sales:
        record.purchased_at = record.purchased_at or purchase.confirmed_at
        if record.status != "shipped" and not record.shipped_at:
            record.status = "purchasing"
    db.session.commit()
    return _serialize_purchase(purchase, include_items=True)


def receive_purchase_list(purchase_id, payload):
    purchase = _purchase_or_error(purchase_id)
    Shop.query.filter_by(id=purchase.shop_id).with_for_update().one()
    db.session.refresh(purchase, with_for_update=True)
    finalize = payload.get("finalize", True)
    if not isinstance(finalize, bool):
        raise ValidationError("finalize 必须是布尔值")
    batch_id = payload.get("batch_id")
    if batch_id is not None and (not isinstance(batch_id, str) or not batch_id.strip() or len(batch_id) > 80):
        raise ValidationError("批次编号无效")
    if not finalize and batch_id is None:
        raise ValidationError("分批入库必须提供批次编号，避免重复入库")
    entries = payload.get("items")
    if not isinstance(entries, list):
        raise ValidationError("请填写本批次各变体的合格数和货损数")
    planned = {item["group_key"]: int(item["quantity"]) for item in purchase.items or []}
    if (not entries or any(not isinstance(entry, dict) for entry in entries)
            or len(entries) != len({entry.get("group_key") for entry in entries})
            or any(entry.get("group_key") not in planned for entry in entries)):
        raise ValidationError("本批次变体不能为空、重复或不属于此采购清单")
    if batch_id:
        previous = next((row for row in purchase.receipt_batches or [] if row.get("batch_id") == batch_id), None)
        if previous:
            requested = sorted((row["group_key"], _nonnegative_int(row.get("received_quantity"), "合格数量"),
                                _nonnegative_int(row.get("damaged_quantity"), "货损数量"),
                                _nonnegative_int(row.get("missing_quantity", 0), "缺货数量")) for row in entries)
            recorded = sorted((row["group_key"], row["received_quantity"], row["damaged_quantity"],
                               row.get("missing_quantity", 0))
                              for row in previous["items"])
            if previous["finalize"] != finalize or requested != recorded:
                raise ValidationError("该批次编号已用于不同的清点结果")
            return _serialize_purchase(purchase, include_items=True)
    if purchase.status not in OPEN_PURCHASE_STATUSES:
        raise ValidationError("只有已发起采购的清单可以确认入库")
    cumulative = {row["group_key"]: dict(row) for row in purchase.receipt or []}
    for key, quantity in planned.items():
        cumulative.setdefault(key, {
            "group_key": key, "planned_quantity": quantity,
            "received_quantity": 0, "damaged_quantity": 0, "missing_quantity": quantity,
        })
    batch = []
    good_by_key = {}
    damaged_by_key = {}
    missing_by_key = {}
    for entry in entries:
        key = entry["group_key"]
        good = _nonnegative_int(entry.get("received_quantity"), "合格数量")
        damaged = _nonnegative_int(entry.get("damaged_quantity"), "货损数量")
        missing = _nonnegative_int(entry.get("missing_quantity", 0), "缺货数量")
        prior = cumulative[key]
        remaining = (planned[key] - int(prior["received_quantity"]) - int(prior["damaged_quantity"])
                     - int(prior.get("confirmed_missing_quantity") or 0))
        if good + damaged + missing > remaining:
            raise ValidationError("本批次合格、货损与缺货数之和不能超过剩余未清点数量")
        good_by_key[key] = good
        damaged_by_key[key] = damaged
        missing_by_key[key] = missing
        prior["received_quantity"] = int(prior["received_quantity"]) + good
        prior["damaged_quantity"] = int(prior["damaged_quantity"]) + damaged
        prior["confirmed_missing_quantity"] = int(prior.get("confirmed_missing_quantity") or 0) + missing
        prior["missing_quantity"] = planned[key] - prior["received_quantity"] - prior["damaged_quantity"]
        batch.append({"group_key": key, "received_quantity": good, "damaged_quantity": damaged,
                      "missing_quantity": missing})
    if not finalize and not any(row["received_quantity"] or row["damaged_quantity"] or row["missing_quantity"]
                                for row in batch):
        raise ValidationError("本批次至少清点一件商品")
    # Confirmed shortages release incoming supply immediately, without closing
    # other variants still awaiting a later delivery. Keep legacy missing_quantity
    # as the total not received/damaged for older clients and saved receipts.
    for key, prior in cumulative.items():
        if finalize:
            uncounted = int(prior["missing_quantity"]) - int(prior.get("confirmed_missing_quantity") or 0)
            missing_by_key[key] = missing_by_key.get(key, 0) + uncounted
            prior["confirmed_missing_quantity"] = int(prior["missing_quantity"])
        prior["pending_quantity"] = int(prior["missing_quantity"]) - int(prior.get("confirmed_missing_quantity") or 0)
    previous_allocations = [dict(row) for row in purchase.allocations or []]
    allocations = [dict(row) for row in previous_allocations]
    order_dates = {
        order.id: order.order_date
        for order in Order.query.filter(Order.id.in_({row["order_id"] for row in allocations if "order_id" in row})).all()
    }
    allocations.sort(key=lambda row: (
        order_dates.get(int(row.get("order_id") or 0)) or datetime.max,
        int(row.get("order_id") or 0), int(row.get("order_item_id") or row.get("after_sale_item_id") or 0),
    ))
    for allocation in allocations:
        key = allocation["group_key"]
        remaining = (int(allocation["quantity"]) - int(allocation.get("received_quantity") or 0)
                     - int(allocation.get("damaged_quantity") or 0)
                     - int(allocation.get("confirmed_missing_quantity") or 0))
        assigned = min(remaining, good_by_key.get(key, 0))
        allocation["received_quantity"] = int(allocation.get("received_quantity") or 0) + assigned
        good_by_key[key] = good_by_key.get(key, 0) - assigned
    if any(good_by_key.values()):
        raise ValidationError("合格数量超过清单分配量")
    # Put damaged units against newest outstanding demand so older orders receive stock first.
    for allocation in reversed(allocations):
        key = allocation["group_key"]
        remaining = (int(allocation["quantity"]) - int(allocation.get("received_quantity") or 0)
                     - int(allocation.get("damaged_quantity") or 0)
                     - int(allocation.get("confirmed_missing_quantity") or 0))
        assigned = min(remaining, damaged_by_key.get(key, 0))
        allocation["damaged_quantity"] = int(allocation.get("damaged_quantity") or 0) + assigned
        damaged_by_key[key] = damaged_by_key.get(key, 0) - assigned
    if any(damaged_by_key.values()):
        raise ValidationError("货损数量超过清单分配量")
    for allocation in reversed(allocations):
        key = allocation["group_key"]
        remaining = (int(allocation["quantity"]) - int(allocation.get("received_quantity") or 0)
                     - int(allocation.get("damaged_quantity") or 0)
                     - int(allocation.get("confirmed_missing_quantity") or 0))
        assigned = min(remaining, missing_by_key.get(key, 0))
        allocation["confirmed_missing_quantity"] = int(allocation.get("confirmed_missing_quantity") or 0) + assigned
        missing_by_key[key] = missing_by_key.get(key, 0) - assigned
    if any(missing_by_key.values()):
        raise ValidationError("缺货数量超过清单分配量")
    purchase.allocations = allocations
    purchase.receipt = list(cumulative.values())
    purchase.receipt_batches = [*(purchase.receipt_batches or []), {
        "batch_id": batch_id, "received_at": datetime.utcnow().isoformat(), "items": batch, "finalize": finalize,
    }]
    purchase.status = "received" if finalize else "partially_received"
    if finalize:
        purchase.received_at = datetime.utcnow()
    after_sale_allocations = [row for row in allocations if row.get("after_sale_item_id")]
    if after_sale_allocations:
        after_sale_ids = {int(row["after_sale_id"]) for row in after_sale_allocations}
        records = {record.id: record for record in AfterSale.query.options(selectinload(AfterSale.items)).filter(
            AfterSale.id.in_(after_sale_ids),
        ).all()}
        if len(records) != len(after_sale_ids) or any(
            record.shop_id != purchase.shop_id for record in records.values()
        ):
            raise ValidationError("关联售后记录缺失或店铺归属不一致，请核对采购明细，不能入库")
        # A case may already have shipped from other shared physical stock.
        # These goods still belong in warehouse stock; never reopen the case or
        # deduct outbound inventory again. _allocate preserves shipped/archived cases.
        previous_by_item = {int(row["after_sale_item_id"]): row for row in previous_allocations
                            if row.get("after_sale_item_id")}
        for allocation in after_sale_allocations:
            record = records[int(allocation["after_sale_id"])]
            item = next((item for item in record.items if item.id == int(allocation["after_sale_item_id"])), None)
            previous = previous_by_item.get(int(allocation["after_sale_item_id"]))
            if item is None or previous is None:
                raise ValidationError("售后采购分配异常")
            item.received_quantity += int(allocation.get("received_quantity") or 0) - int(previous.get("received_quantity") or 0)
            item.damaged_quantity += int(allocation.get("damaged_quantity") or 0) - int(previous.get("damaged_quantity") or 0)
        db.session.flush()
        from app.services.after_sale_service import _allocate

        for record in records.values():
            _allocate(record)
    db.session.flush()
    # 到货后如果这个变体还有富余库存，顺手分给还在等货的其它订单，
    # 避免「仓库有货、别的订单却还在重复采购同一个商品」。
    from app.services.inventory_service import auto_allocate_stock

    allocated = auto_allocate_stock(
        purchase.shop_id, set(good_by_key) | set(damaged_by_key), commit=False
    )

    db.session.commit()
    result = _serialize_purchase(purchase, include_items=True)
    result["allocated_orders"] = allocated
    return result


def create_replenishment(purchase_id):
    """Create a draft purchase list for confirmed shortages and damaged units.

    Quantities are capped by what each order still needs *now*: if stock or
    another open purchase already covered the order, we must not buy it twice.
    The new allocations carry ``source_purchase_id`` so the detail page can show
    the follow-up list and repeated clicks stay idempotent.
    """
    purchase = _purchase_or_error(purchase_id)
    existing = _replenishment_ids(purchase)
    if existing:
        return _serialize_purchase(db.session.get(PurchaseList, existing[0]), include_items=True)
    if purchase.status not in ("partially_received", "received"):
        raise ValidationError("只有已开始清点的采购清单可以补采")

    outstanding = []
    for row in purchase.allocations or []:
        quantity = int(row.get("confirmed_missing_quantity") or 0) + int(row.get("damaged_quantity") or 0)
        if quantity > 0:
            outstanding.append((dict(row), quantity))
    if not outstanding:
        raise ValidationError("这张清单没有需要补采的缺货或破损")

    order_ids = {int(row["order_id"]) for row, _ in outstanding if row.get("order_id")}
    orders = Order.query.options(selectinload(Order.items)).filter(
        Order.shop_id == purchase.shop_id, Order.id.in_(order_ids),
    ).all() if order_ids else []
    available = _available_quantities(orders, purchase.shop_id) if orders else {}
    pool = {}
    if any(row.get("after_sale_id") for row, _ in outstanding):
        from app.services.inventory_pool_service import build_pool_plan
        pool = build_pool_plan(purchase.shop_id)
    cf_uncovered = pool.get("cf_uncovered", {})

    metadata = {item["group_key"]: item for item in purchase.items or []}
    items = OrderedDict()
    allocations = []
    new_order_ids = set()
    new_after_sale_ids = set()
    used_order_items = Counter()
    used_after_sale_items = Counter()

    for row, quantity in outstanding:
        group_key = row["group_key"]
        if row.get("order_id"):
            item_id = int(row.get("order_item_id") or 0)
            cap = max(int(available.get(item_id, 0)) - used_order_items[item_id], 0)
            quantity = min(quantity, cap)
            if quantity <= 0:
                continue
            used_order_items[item_id] += quantity
            new_order_ids.add(int(row["order_id"]))
        elif row.get("after_sale_id"):
            after_sale_id = int(row["after_sale_id"])
            item_id = int(row.get("after_sale_item_id") or 0)
            cap = max(int(cf_uncovered.get(after_sale_id, {}).get(group_key, 0))
                      - used_after_sale_items[item_id], 0)
            quantity = min(quantity, cap)
            if quantity <= 0:
                continue
            used_after_sale_items[item_id] += quantity
            new_after_sale_ids.add(after_sale_id)

        meta = metadata.get(group_key)
        if meta is None:
            raise ValidationError("补采商品信息缺失，请刷新后重试")
        entry = items.setdefault(group_key, {**meta, "quantity": 0, "order_numbers": []})
        entry["quantity"] += quantity
        for number in meta.get("order_numbers") or []:
            if number not in entry["order_numbers"]:
                entry["order_numbers"].append(number)

        allocation = {field: row[field] for field in
                      ("order_id", "order_item_id", "after_sale_id", "after_sale_item_id")
                      if row.get(field)}
        allocation.update({
            "group_key": group_key,
            "quantity": quantity,
            "received_quantity": 0,
            "damaged_quantity": 0,
            "confirmed_missing_quantity": 0,
            "source_purchase_id": purchase.id,
        })
        allocations.append(allocation)

    if not allocations:
        raise ValidationError("缺货商品已被其它库存或采购覆盖，暂时不需要补采")

    name = f"补采 · {purchase.name}"[:120]
    replenishment = PurchaseList(
        shop_id=purchase.shop_id,
        after_sale_id=next(iter(new_after_sale_ids)) if len(new_after_sale_ids) == 1 and not new_order_ids else None,
        name=name,
        order_ids=sorted(new_order_ids),
        items=list(items.values()),
        order_count=len(new_order_ids) + len(new_after_sale_ids),
        total_quantity=sum(item["quantity"] for item in items.values()),
        allocations=allocations,
    )
    db.session.add(replenishment)
    db.session.flush()
    db.session.commit()
    return _serialize_purchase(replenishment, include_items=True)


def _nonnegative_int(value, field):
    if isinstance(value, bool):
        raise ValidationError(f"{field}必须是非负整数")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{field}必须是非负整数") from exc
    if number < 0 or str(value).strip() != str(number):
        raise ValidationError(f"{field}必须是非负整数")
    return number


def attach_procurement_status(orders, *, order_items=None, pools=None):
    """Annotate serialized orders without treating damaged/missing goods as stock."""
    if not orders:
        return
    ids = {order["id"] for order in orders}
    shops = {order["shop_id"] for order in orders}
    received = {}
    pending = {}
    initiated = set()
    lists = db.session.query(PurchaseList.status, PurchaseList.allocations).filter(
        PurchaseList.shop_id.in_(shops), PurchaseList.status.in_((*OPEN_PURCHASE_STATUSES, "received"))
    ).all()
    for purchase in lists:
        for allocation in purchase.allocations or []:
            if "order_id" not in allocation:
                continue
            order_id = int(allocation["order_id"])
            if order_id not in ids:
                continue
            initiated.add(order_id)
            item_id = int(allocation["order_item_id"])
            good = int(allocation.get("received_quantity") or 0)
            received[item_id] = received.get(item_id, 0) + good
            if purchase.status in OPEN_PURCHASE_STATUSES:
                outstanding = max(int(allocation["quantity"]) - good - int(allocation.get("damaged_quantity") or 0)
                                  - int(allocation.get("confirmed_missing_quantity") or 0), 0)
                pending[item_id] = pending.get(item_id, 0) + outstanding
    for reservation in InventoryReservation.query.filter(InventoryReservation.order_id.in_(ids)).all():
        received[reservation.order_item_id] = received.get(reservation.order_item_id, 0) + reservation.quantity
    if order_items is None:
        items = Order.query.options(selectinload(Order.items)).filter(Order.id.in_(ids)).all()
        by_order = {order.id: order.items for order in items}
    else:
        by_order = order_items
    from app.services.inventory_pool_service import build_pool_plan
    if pools is None:
        pools = {shop_id: build_pool_plan(shop_id) for shop_id in shops
                 if any(row.get("warehouse_status") == "pending" for row in orders if row["shop_id"] == shop_id)}
    for order in orders:
        lines = [item for item in by_order.get(order["id"], [])
                 if int(item.quantity or 0) > 0 and not is_tip_line(item)]
        needed = sum(int(item.quantity or 0) for item in lines)
        stocked = sum(min(received.get(item.id, 0), int(item.quantity or 0)) for item in lines)
        in_purchase = sum(pending.get(item.id, 0) for item in lines)
        pool = pools.get(order["shop_id"])
        if order.get("warehouse_status") == "pending" and pool and order["id"] in pool["physical_allocations"]:
            stocked = needed if order['id'] in pool['debited_order_ids'] else sum(
                line['quantity'] for line in pool['physical_allocations'][order['id']])
            in_purchase = sum(pool["pending_by_order"].get(order["id"], {}).values())
        fully_stocked = bool(lines) and stocked >= needed
        ready = (
            fully_stocked
            and order.get("warehouse_status") == "pending"
            and order.get("financial_status") in ("paid", "partially_refunded")
            and not order.get("cancelled_at")
            and (not pool or order["id"] in pool["ready_order_ids"])
        )
        order["purchase_initiated"] = order["id"] in initiated
        remaining = max(needed - stocked - in_purchase, 0)
        if pool and order['warehouse_status'] == 'pending':
            # Procurement covers aggregate demand once; readiness is independently physical.
            remaining = sum(pool['uncovered_by_order'].get(order['id'], {}).values())
        order["procurement_status"] = (
            "ready_to_ship" if ready else
            "stocked" if fully_stocked else
            "shortage" if remaining and order["id"] in initiated else
            "purchasing" if in_purchase else
            "partially_stocked" if stocked > 0 else
            "not_started"
        )
        order["procurement_needed_quantity"] = remaining
        order["procurement_stocked_quantity"] = stocked
        order["procurement_pending_quantity"] = in_purchase
