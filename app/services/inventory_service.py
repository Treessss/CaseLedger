"""Shop-scoped stock view derived from receipts, order use and audited corrections."""

import re

from datetime import date, datetime, timedelta
from json import dumps, loads
from sqlalchemy import func, or_
from sqlalchemy.orm import load_only, noload, selectinload

from app import db
from app.models import (
    AfterSale,
    AfterSaleItem,
    InventoryAdjustment,
    InventoryReservation,
    InventoryOutboundLine,
    Order,
    OrderItem,
    Product,
    PurchaseList,
    Shop,
    ShippingParcel,
)
from app.services.purchase_service import (
    UNKNOWN_COLOR,
    _color_name,
    _group_key,
    _accessory_configuration,
    _model_name,
    _phone_model_sort_key,
    _product_key,
)
from app.utils.errors import ForbiddenError, NotFoundError, ValidationError
from app.utils.order_lines import is_tip_line
from app.utils.security import can_access_shop
from app.utils.timezone import beijing_day_bounds_utc

MAX_PER_PAGE = 100


def _catalog_stock_items(shop_id, variant_ids=None):
    """Local catalog only: picker completeness must not depend on order history."""
    query = Product.query.options(load_only(
        Product.shopify_product_id, Product.shopify_variant_id, Product.title,
        Product.variant_title, Product.sku, Product.vendor, Product.image_url,
    )).filter(Product.shop_id == shop_id)
    if variant_ids is None:
        query = query.filter(Product.status == "active")
    elif not variant_ids:
        return []
    else:
        query = query.filter(Product.shopify_variant_id.in_(variant_ids))
    return [{"group_key": dumps(_group_key(item), ensure_ascii=False),
             "shopify_product_id": item.shopify_product_id,
             "shopify_variant_id": item.shopify_variant_id,
             "title": item.title, "variant_title": item.variant_title,
             "sku": item.sku, "vendor": item.vendor, "image_url": item.image_url}
            for item in query.all() if not is_tip_line(item)]


def _stock_seed_items(shop_id, keys):
    """Resolve only requested stock identities, including SKU-only legacy items."""
    conditions = []
    variant_ids = set()
    for key in keys:
        try:
            parts = loads(key)
        except (ValueError, TypeError):
            continue
        if not isinstance(parts, list) or not parts:
            continue
        if parts[0] == "variant" and len(parts) == 2:
            variant_ids.add(str(parts[1]))
            conditions.append(OrderItem.shopify_variant_id == str(parts[1]))
        elif parts[0] == "product" and len(parts) == 3:
            conditions.append(OrderItem.shopify_product_id == str(parts[1]))
        elif parts[0] == "sku" and len(parts) == 3:
            conditions.append(func.lower(func.trim(OrderItem.sku)) == parts[1])
        elif parts[0] == "description" and len(parts) == 4:
            conditions.append(func.lower(func.trim(OrderItem.title)) == parts[2])
    if not conditions:
        return []
    items = OrderItem.query.join(Order, OrderItem.order_id == Order.id).filter(
        Order.shop_id == shop_id, or_(*conditions),
    ).order_by(OrderItem.id.desc()).all()
    result = {}
    for item in items:
        if is_tip_line(item):
            continue
        key = dumps(_group_key(item), ensure_ascii=False)
        if key in keys and key not in result:
            result[key] = {"group_key": key, "shopify_product_id": item.shopify_product_id,
                           "title": item.title, "variant_title": item.variant_title,
                           "sku": item.sku, "vendor": item.vendor, "image_url": item.image_url}
        elif key in result and not result[key]['image_url'] and item.image_url:
            result[key]['image_url'] = item.image_url
    for item in _catalog_stock_items(shop_id, variant_ids):
        previous = result.get(item["group_key"])
        if not item["image_url"] and previous:
            item["image_url"] = previous["image_url"]
        result[item["group_key"]] = item
    missing = set(keys) - set(result)
    if missing:
        for item in AfterSaleItem.query.join(AfterSale).filter(
            AfterSale.shop_id == shop_id, AfterSaleItem.group_key.in_(missing),
        ).all():
            result.setdefault(item.group_key, item.to_dict())
    return list(result.values())


def _shop(shop_id):
    try:
        shop_id = int(shop_id)
    except (TypeError, ValueError) as exc:
        raise ValidationError("请选择店铺") from exc
    if shop_id <= 0:
        raise ValidationError("请选择店铺")
    if not can_access_shop(shop_id):
        raise ForbiddenError("无权查看该店铺库存")
    shop = db.session.get(Shop, shop_id)
    if shop is None:
        raise NotFoundError("店铺不存在")
    return shop


def _stock_rows(shop_id, *, include_catalog=True, group_keys=None, seed_items=()):
    # Allocation needs balances, not the entire historical product catalog.
    # Keep the shared receipt/occupancy arithmetic (including edited quantities)
    # and project only the requested groups at the boundary.
    if group_keys is not None:
        group_keys = set(group_keys)
        if not group_keys:
            return {}
        include_catalog = False
    rows = {}

    def ensure(item):
        key = item["group_key"]
        if key not in rows:
            color = _color_name(item.get("variant_title"))
            rows[key] = {
                "group_key": key,
                "title": item.get("title") or "未命名商品",
                "product_key": dumps(_product_key(item), ensure_ascii=False),
                "color": color,
                "model": _model_name(item.get("variant_title"), color),
                "configuration": _accessory_configuration(item.get("variant_title")),
                "sku": item.get("sku") or "",
                "image_url": item.get("image_url") or "",
                "received_quantity": 0,
                "shipped_quantity": 0,
                "reserved_quantity": 0,
                "occupied_orders": {},
                "adjustment_quantity": 0,
            }
        elif not rows[key]["image_url"] and item.get("image_url"):
            rows[key]["image_url"] = item["image_url"]
        return rows[key]

    purchases = PurchaseList.query.options(load_only(
        PurchaseList.id, PurchaseList.shop_id, PurchaseList.status,
        PurchaseList.items, PurchaseList.allocations, PurchaseList.receipt,
    )).filter(
        PurchaseList.shop_id == shop_id,
        PurchaseList.status.in_(("partially_received", "received")),
    ).all()
    reservations = InventoryReservation.query.filter_by(shop_id=shop_id).all()
    adjustments = InventoryAdjustment.query.filter_by(shop_id=shop_id).all()
    outbound_lines = InventoryOutboundLine.query.filter_by(shop_id=shop_id).all()
    ledger_orders = {line.order_id for line in outbound_lines}
    partial_without_ledger = {order_id for (order_id,) in db.session.query(ShippingParcel.order_id).join(
        Order, Order.id == ShippingParcel.order_id).filter(ShippingParcel.shop_id == shop_id,
        ShippingParcel.scanned_at.isnot(None), Order.warehouse_status == 'pending').distinct().all()} - ledger_orders
    purchase_keys = {item["group_key"] for purchase in purchases for item in purchase.items or []}
    # The usual stock view only needs items referenced by receipts/reservations.
    # A manually adjusted variant outside purchase history still needs its catalog entry.
    manual_keys = {adjustment.group_key for adjustment in adjustments} - purchase_keys
    if not include_catalog:
        seed_keys = {item['group_key'] for item in seed_items}
        ledger_keys = {line.group_key for line in outbound_lines}
        missing_keys = ((manual_keys | ledger_keys) if group_keys is None else group_keys) - seed_keys - purchase_keys
        seed_items = [*seed_items, *_stock_seed_items(shop_id, missing_keys)]
    item_query = db.session.query(
        OrderItem.id, OrderItem.quantity, OrderItem.shopify_variant_id,
        OrderItem.shopify_product_id, OrderItem.title, OrderItem.variant_title,
        OrderItem.sku, OrderItem.vendor, OrderItem.image_url,
    ).join(Order, OrderItem.order_id == Order.id).filter(Order.shop_id == shop_id)
    if not include_catalog:
        relevant_ids = {int(allocation["order_item_id"]) for purchase in purchases
                        for allocation in purchase.allocations or [] if allocation.get("order_item_id")}
        relevant_ids.update(reservation.order_item_id for reservation in reservations)
        item_query = item_query.filter(OrderItem.id.in_(relevant_ids))
    items = [item for item in item_query.order_by(OrderItem.id.desc()).all() if not is_tip_line(item)]
    current_item_quantities = {item.id: max(int(item.quantity or 0), 0) for item in items}
    allocated_to_current_items = {}
    encoded_keys = {}
    if include_catalog:
        for item in _catalog_stock_items(shop_id):
            ensure(item)
    for item in items:
        identity = _group_key(item)
        key = encoded_keys.get(identity)
        if key is None:
            key = dumps(identity, ensure_ascii=False)
            encoded_keys[identity] = key
        ensure({
            "group_key": key,
            "shopify_product_id": item.shopify_product_id,
            "title": item.title,
            "variant_title": item.variant_title,
            "sku": item.sku,
            "vendor": item.vendor,
            "image_url": item.image_url,
        })
    # Manual stock can have no historical order or receipt. The current sale's
    # item metadata supplies those rows without scanning the historical catalog.
    for item in seed_items:
        if not is_tip_line(item):
            ensure(item)

    def occupy(row, order, quantity):
        if quantity <= 0:
            return
        row["reserved_quantity"] += quantity
        entry = row["occupied_orders"].setdefault(order.id, {
            "order_id": order.id,
            "order_number": order.order_number,
            "quantity": 0,
        })
        entry["quantity"] += quantity

    def current_occupancy(item_id, quantity):
        """Order edits release excess stock without rewriting purchase history."""
        remaining = current_item_quantities.get(item_id, 0) - allocated_to_current_items.get(item_id, 0)
        take = min(max(quantity, 0), max(remaining, 0))
        allocated_to_current_items[item_id] = allocated_to_current_items.get(item_id, 0) + take
        return take

    order_ids = {int(allocation["order_id"]) for purchase in purchases for allocation in purchase.allocations or []
                 if "order_id" in allocation}
    order_ids.update(reservation.order_id for reservation in reservations)
    orders = {order.id: order for order in db.session.query(
        Order.id, Order.shop_id, Order.order_number, Order.warehouse_status,
        Order.cancelled_at, Order.financial_status,
    ).filter(Order.id.in_(order_ids), Order.shop_id == shop_id).all()} if order_ids else {}
    for purchase in purchases:
        for item in purchase.items or []:
            if not is_tip_line(item):
                ensure(item)
        for receipt in purchase.receipt or []:
            key = receipt["group_key"]
            if key in rows:
                rows[key]["received_quantity"] += int(receipt.get("received_quantity") or 0)
        for allocation in purchase.allocations or []:
            if "order_id" not in allocation:
                continue
            key = allocation["group_key"]
            order = orders.get(int(allocation["order_id"]))
            if key not in rows or order is None or order.shop_id != shop_id:
                continue
            quantity = int(allocation.get("received_quantity") or 0)
            if order.warehouse_status in ("shipped", "legacy_unverified") and order.id not in ledger_orders:
                rows[key]["shipped_quantity"] += quantity
            elif (order.id in partial_without_ledger or order.warehouse_status == 'legacy_partial_unverified') and order.id not in ledger_orders:
                rows[key]['shipped_quantity'] += current_occupancy(int(allocation['order_item_id']), quantity)
            elif order.warehouse_status in ("pending", "legacy_partial_unverified") and not order.cancelled_at and order.financial_status not in (
                "refunded", "voided", "cancelled"
            ):
                occupy(rows[key], order, current_occupancy(int(allocation["order_item_id"]), quantity))

    current_item_ids = {item.id for item in items}
    for reservation in reservations:
        if reservation.order_item_id not in current_item_ids:
            continue
        row = rows.get(reservation.group_key)
        order = orders.get(reservation.order_id)
        if row is None or order is None or order.shop_id != shop_id:
            continue
        if order.warehouse_status in ("shipped", "legacy_unverified") and order.id not in ledger_orders:
            row["shipped_quantity"] += reservation.quantity
        elif (order.id in partial_without_ledger or order.warehouse_status == 'legacy_partial_unverified') and order.id not in ledger_orders:
            row['shipped_quantity'] += current_occupancy(reservation.order_item_id, reservation.quantity)
        elif order.warehouse_status in ("pending", "legacy_partial_unverified") and not order.cancelled_at and order.financial_status not in (
            "refunded", "voided", "cancelled"
        ):
            occupy(row, order, current_occupancy(reservation.order_item_id, reservation.quantity))

    for adjustment in adjustments:
        if adjustment.group_key in rows:
            rows[adjustment.group_key]["adjustment_quantity"] += adjustment.quantity_delta
    for line in outbound_lines:
        if line.group_key in rows:
            rows[line.group_key]["shipped_quantity"] -= line.quantity_delta

    # A reshipment reserves stock independently of the original shipped order.
    occupied = db.session.query(
        AfterSale.id, AfterSale.order_id, AfterSale.customer_reference,
        AfterSaleItem.group_key, AfterSaleItem.reserved_quantity,
    ).join(AfterSaleItem, AfterSaleItem.after_sale_id == AfterSale.id).filter(
        AfterSale.shop_id == shop_id, AfterSale.status != "shipped",
        AfterSale.deleted_at.is_(None), AfterSaleItem.reserved_quantity > 0,
    ).all()
    for item in occupied:
        row = rows.get(item.group_key)
        if row is None:
            continue
        row["reserved_quantity"] += item.reserved_quantity
        entry = row["occupied_orders"].setdefault(f"CF:{item.id}", {
            "order_id": item.order_id,
            "order_number": item.customer_reference,
            "quantity": 0,
        })
        entry["quantity"] += item.reserved_quantity

    for row in rows.values():
        row["on_hand_quantity"] = row["received_quantity"] - row["shipped_quantity"] + row["adjustment_quantity"]
        # Allocations remain historical receipt attribution, never physical claims.
        row["reserved_quantity"] = 0
        row["available_quantity"] = row["on_hand_quantity"]
        row["occupied_orders"] = []
    # Stock rows are seeded from historical purchase/order snapshots, whose
    # variant titles are immutable. Show the current catalog name for the same
    # variant ID so renamed Shopify variants update the inventory view too.
    variant_ids = set()
    for key in rows:
        try:
            parts = loads(key)
        except (ValueError, TypeError):
            continue
        if isinstance(parts, list) and len(parts) == 2 and parts[0] == "variant":
            variant_ids.add(str(parts[1]))
    if variant_ids:
        current = Product.query.filter(
            Product.shop_id == shop_id, Product.shopify_variant_id.in_(variant_ids),
        ).all()
        by_variant = {item.shopify_variant_id: item for item in current}
        for key, row in rows.items():
            try:
                parts = loads(key)
            except (ValueError, TypeError):
                continue
            if not (isinstance(parts, list) and len(parts) == 2 and parts[0] == "variant"):
                continue
            product = by_variant.get(str(parts[1]))
            if not product:
                continue
            color = _color_name(product.variant_title)
            row["title"] = product.title or row["title"]
            row["color"] = color
            row["model"] = _model_name(product.variant_title, color)
            row["configuration"] = _accessory_configuration(product.variant_title)
            row["sku"] = product.sku or row["sku"]
            row["image_url"] = product.image_url or row["image_url"]
    return rows if group_keys is None else {key: row for key, row in rows.items() if key in group_keys}


WRIST_CHAIN_RE = re.compile(r"\s*\+\s*wrist\s*chain\b", re.IGNORECASE)


def _without_wrist_chain(configuration):
    return WRIST_CHAIN_RE.sub("", configuration or "").strip()


def _merge_chain_variants(rows):
    """同型号的 +Wrist Chain 与普通款合并成一行（链子单独采购，不单独占用库存行）。"""
    merged = {}
    for row in rows:
        key = (row["model"], _without_wrist_chain(row.get("configuration")).casefold())
        if key not in merged:
            merged[key] = {**row, "group_keys": [row["group_key"]],
                           "components": [{k: row.get(k) for k in
                                           ("group_key", "configuration", "sku", "model",
                                            "on_hand_quantity", "demand_quantity")}]}
            continue
        target = merged[key]
        # 合并数量与占单/采购/需求明细
        for field, value in row.items():
            if isinstance(value, bool):
                continue
            if isinstance(value, (int, float)) and isinstance(target.get(field), (int, float)):
                target[field] = target.get(field, 0) + value
            elif field in ("occupied_orders", "demand_orders", "incoming_purchases",
                           "purchase_orders", "planning_orders") and isinstance(value, list):
                target.setdefault(field, []).extend(value)
        for component_field in ("group_keys", "components"):
            target[component_field].append(
                row["group_key"] if component_field == "group_keys" else
                {k: row.get(k) for k in ("group_key", "configuration", "sku", "model",
                                          "on_hand_quantity", "demand_quantity")})
        if not target.get("configuration") and row.get("configuration"):
            pass  # 已保留普通款作为主行
        elif not target.get("image_url") and row.get("image_url"):
            target["image_url"] = row["image_url"]
    result = []
    for row in merged.values():
        # 优先用普通款作为主 group_key；主行 SKU 与配置标签保持普通款
        plain = next((part for part in row["components"] if not part.get("configuration")),
                     row["components"][0])
        row["group_key"] = plain["group_key"]
        row["configuration"] = plain.get("configuration") or ""
        skus = []
        for part in row["components"]:
            if part.get("sku") and part["sku"] not in skus:
                skus.append(part["sku"])
        row["sku"] = " / ".join(skus) if skus else row.get("sku") or ""
        row["merged_configurations"] = sorted({(part.get("configuration") or "") for part in row["components"]})
        result.append(row)
    return result


def _group_stock_products(rows):
    """Use the same product identity and color/model order as purchase lists."""
    products = {}
    for row in rows:
        product = products.setdefault(row["product_key"], {
            "group_key": row["product_key"],
            "title": row["title"],
            "image_url": row["image_url"],
            "variant_count": 0,
            "color_count": 0,
            "on_hand_quantity": 0,
            "reserved_quantity": 0,
            "available_quantity": 0,
            "occupied_orders": {},
            "colors": {},
        })
        if not product["image_url"] and row["image_url"]:
            product["image_url"] = row["image_url"]
        product["variant_count"] += 1
        from app.services.inventory_pool_service import POOL_FIELDS
        for field in ("on_hand_quantity", "reserved_quantity", "available_quantity", *POOL_FIELDS):
            product[field] = product.get(field, 0) + row.get(field, 0)
        # 同一种颜色的 +Wrist Chain / +Stand 变体只是同一颜色的不同配置，
        # 库存页按「颜色」合并成一个框；具体配置在型号行的 Tag 上区分。
        configuration = row.get("configuration") or ""
        color_key = row["color"].casefold()
        color = product["colors"].setdefault(color_key, {
            "name": row["color"],
            "base_color": row["color"],
            "configuration": "",
            "image_url": row["image_url"],
            "variant_count": 0,
            "on_hand_quantity": 0,
            "reserved_quantity": 0,
            "available_quantity": 0,
            "occupied_orders": {},
            "variants": [],
        })
        if (not color["image_url"] and row["image_url"]) or (
                not configuration and row["image_url"]):
            color["image_url"] = row["image_url"]
        color["variant_count"] += 1
        for field in ("on_hand_quantity", "reserved_quantity", "available_quantity", *POOL_FIELDS):
            color[field] = color.get(field, 0) + row.get(field, 0)
        for target in (product, color):
            for occupied in row["occupied_orders"]:
                identity = ("after_sale", occupied["after_sale_id"]) if occupied.get("after_sale_id") else occupied["order_id"]
                entry = target["occupied_orders"].setdefault(identity, {
                    "order_id": occupied["order_id"],
                    "order_number": occupied["order_number"],
                    "quantity": 0,
                })
                if occupied.get("after_sale_id"):
                    entry["after_sale_id"] = occupied["after_sale_id"]
                entry["quantity"] += occupied["quantity"]
            target.setdefault("incoming_purchases", []).extend(row.get("incoming_purchases", []))
            target.setdefault("purchase_orders", []).extend(row.get("purchase_orders", []))
            target.setdefault("planning_orders", []).extend(row.get("planning_orders", []))
            target.setdefault('demand_orders', []).extend(row.get('demand_orders', []))
        color["variants"].append(row)
    result = []
    for product in products.values():
        colors = list(product["colors"].values())
        for color in colors:
            color["variants"] = _merge_chain_variants(color["variants"])
            color["variant_count"] = len(color["variants"])
            color["variants"].sort(
                key=lambda row: _phone_model_sort_key({"variant_title": row["model"]}), reverse=True
            )
            color["occupied_orders"] = sorted(color["occupied_orders"].values(), key=lambda entry: entry["order_id"], reverse=True)
            color['demand_orders'] = color['occupied_orders']
        colors.sort(key=lambda color: (color["name"] == UNKNOWN_COLOR, color["name"].casefold()))
        product["colors"] = colors
        product["occupied_orders"] = sorted(product["occupied_orders"].values(), key=lambda entry: entry["order_id"], reverse=True)
        product['demand_orders'] = product['occupied_orders']
        product["color_count"] = len(colors)
        result.append(product)
    result.sort(key=lambda product: (product["title"].casefold(), product["group_key"]))
    return result


def list_inventory(args):
    shop = _shop(args.get("shop_id"))
    page = max(args.get("page", 1, type=int) or 1, 1)
    per_page = min(max(args.get("per_page", 20, type=int) or 20, 1), MAX_PER_PAGE)
    search = str(args.get("search") or "").strip().casefold()
    include_zero = str(args.get("include_zero") or "").lower() == "true"
    from app.services.inventory_pool_service import build_pool_plan, POOL_FIELDS
    pool = build_pool_plan(shop.id)
    if include_zero:
        for key, row in _stock_rows(shop.id, include_catalog=True).items():
            if key not in pool["rows"]:
                row.update({field: 0 for field in POOL_FIELDS})
                row.update(planning_orders=[], incoming_purchases=[], purchase_orders=[])
                pool["rows"][key] = row
    rows = list(pool["rows"].values())
    for row in rows:
        row["reserved_quantity"] = 0
        row["available_quantity"] = row["on_hand_quantity"]
        row["occupied_orders"] = [{"order_id": entry["order_id"], "order_number": entry["order_number"],
                                  "quantity": entry["quantity"],
                                  **({"after_sale_id": entry["after_sale_id"]} if entry.get("after_sale_id") else {})}
                                 for entry in row.get('demand_orders', []) if entry.get("order_id")]
    if not include_zero:
        rows = [row for row in rows if row["on_hand_quantity"] != 0 or row["incoming_quantity"] > 0
                or row["purchase_needed_quantity"] > 0 or row.get('demand_quantity', 0) > 0]
    if search:
        from app.services.product_translation_service import filter_display_search
        rows = filter_display_search(rows, search, shop.id)
    rows.sort(key=lambda row: (row["title"].casefold(), row["color"].casefold(), row["model"].casefold()))
    total = len(rows)
    group_by_product = str(args.get("group_by_product") or "").lower() == "true"
    products = _group_stock_products(rows) if group_by_product else []
    page_total = len(products) if group_by_product else total
    return {
        "shop_id": shop.id,
        "items": [] if group_by_product else rows[(page - 1) * per_page:page * per_page],
        "products": products[(page - 1) * per_page:page * per_page] if group_by_product else [],
        "summary": _inventory_summary(rows, total, products if group_by_product else None),
        "pagination": {"page": page, "per_page": per_page, "total": page_total, "pages": (page_total + per_page - 1) // per_page},
    }



def _inventory_summary(rows, variant_count, products=None):
    """Aggregate pool metrics and explain the gap between net and per-variant.

    ``purchase_needed_quantity`` is summed per variant, so it can exceed
    ``demand - incoming - on_hand`` when one variant has surplus and another is
    short.  Operators add the four numbers up and think the system is wrong;
    expose the difference explicitly as ``structural_gap_quantity``.
    """
    from app.services.inventory_pool_service import POOL_FIELDS

    totals = {field: sum(row.get(field, 0) for row in rows) for field in POOL_FIELDS}
    on_hand = sum(row["on_hand_quantity"] for row in rows)
    net_shortage = max(totals["demand_quantity"] - totals["incoming_quantity"] - on_hand, 0)
    structural_gap = max(totals["purchase_needed_quantity"] - net_shortage, 0)
    return {
        **totals,
        "variant_count": variant_count,
        "product_count": len(products) if products is not None else None,
        "on_hand_quantity": on_hand,
        "reserved_quantity": sum(row["reserved_quantity"] for row in rows),
        "available_quantity": sum(row["available_quantity"] for row in rows),
        "net_shortage_quantity": net_shortage,
        "structural_gap_quantity": structural_gap,
    }

def adjust_inventory(payload):
    shop = _shop(payload.get("shop_id"))
    Shop.query.filter_by(id=shop.id).with_for_update().one()
    key = str(payload.get("group_key") or "")
    reason = str(payload.get("reason") or "").strip()
    try:
        delta = int(payload.get("quantity_delta"))
    except (TypeError, ValueError) as exc:
        raise ValidationError("调整数量必须是非零整数") from exc
    if delta == 0 or abs(delta) > 100_000 or str(payload.get("quantity_delta")).strip() != str(delta):
        raise ValidationError("调整数量必须是非零整数")
    if len(reason) > 255:
        raise ValidationError("调整原因最多 255 字")
    reason = reason or "手动调整库存"
    row = _stock_rows(shop.id, group_keys={key}).get(key)
    if row is None:
        raise NotFoundError("库存变体不存在")

    # 货损是既成事实，不能因为「这批已经被订单占用」就拦着不让录。
    # 允许扣到在库为 0；被吃掉的那部分订单占用按「后进先出」释放，
    # 这些订单随即变回缺货，自动回到采购管理的待采购里。
    on_hand_after = row["on_hand_quantity"] + delta
    if on_hand_after < 0:
        raise ValidationError("扣减数量不能超过当前在库数量")

    released_orders = []
    shortfall = row["reserved_quantity"] - on_hand_after
    if shortfall > 0:
        released_orders = _release_occupancy(shop.id, key, shortfall)

    adjustment = InventoryAdjustment(
        shop_id=shop.id, group_key=key, quantity_delta=delta, reason=reason
    )
    db.session.add(adjustment)
    db.session.flush()

    # 入库之后立刻把新库存分配给还在等货的订单，避免重复采购
    allocated = auto_allocate_stock(shop.id, {key}, commit=False) if on_hand_after > 0 else []
    # Releasing occupancy must never erase a historical outbound deduction.
    checked = _stock_rows(shop.id, group_keys={key}).get(key)
    if (checked is None or checked["on_hand_quantity"] != on_hand_after
            or checked["shipped_quantity"] != row["shipped_quantity"]
            or checked["reserved_quantity"] > checked["on_hand_quantity"]):
        db.session.rollback()
        raise ValidationError("库存调整校验失败，未保存；请刷新后核对库存占用")
    reopened = _reopen_dismissed_procurement(shop.id, {key})
    db.session.commit()
    return {
        **adjustment.to_dict(),
        "released_orders": released_orders,
        "allocated_orders": allocated,
        "reopened_procurement_orders": reopened,
    }


def _reopen_dismissed_procurement(shop_id, group_keys):
    """A queue dismissal applies only until the next relevant stock adjustment."""
    from app.services.purchase_service import _available_quantities

    orders = Order.query.options(selectinload(Order.items)).filter(
        Order.shop_id == shop_id, Order.procurement_excluded_at.isnot(None),
        Order.warehouse_status == "pending", Order.cancelled_at.is_(None),
        Order.financial_status.in_(("paid", "partially_paid", "partially_refunded")),
    ).all()
    affected = [order for order in orders if any(
        dumps(_group_key(item), ensure_ascii=False) in group_keys for item in order.items
    )]
    if not affected:
        return []
    available = _available_quantities(affected, shop_id)
    reopened = []
    for order in affected:
        if any(available.get(item.id, 0) > 0
               and dumps(_group_key(item), ensure_ascii=False) in group_keys for item in order.items):
            order.procurement_excluded_at = None
            reopened.append(order.order_number)
    return reopened


def list_receiving_products(args):
    """SQL product pagination, then full variants; never calculate historical stock."""
    from app.services.product_translation_service import catalog_search_conditions

    shop = _shop(args.get("shop_id"))
    page = max(args.get("page", 1, type=int) or 1, 1)
    per_page = min(max(args.get("per_page", 10, type=int) or 10, 1), 20)
    search = str(args.get("search") or "").strip()
    matched = db.session.query(Product.shopify_product_id, func.min(Product.title).label('title')).filter(
        Product.shop_id == shop.id,
    )
    if search:
        pattern = '%' + search.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
        matched = matched.filter(or_(
            Product.title.ilike(pattern, escape='\\'), Product.variant_title.ilike(pattern, escape='\\'),
            Product.sku.ilike(pattern, escape='\\'), *catalog_search_conditions(shop.id, search),
        ))
    matched = matched.group_by(Product.shopify_product_id)
    total = matched.count()
    ids = [row.shopify_product_id for row in matched.order_by(func.min(Product.title), Product.shopify_product_id)
           .offset((page - 1) * per_page).limit(per_page).all()]
    # Search chooses products, not variants: all colors/models remain selectable.
    products = Product.query.options(load_only(
        Product.shopify_product_id, Product.shopify_variant_id, Product.title, Product.variant_title,
        Product.sku, Product.image_url, Product.vendor,
    )).filter(Product.shop_id == shop.id, Product.shopify_product_id.in_(ids)).all() if ids else []
    rows = []
    for item in products:
        if is_tip_line(item):
            continue
        color = _color_name(item.variant_title)
        rows.append({"group_key": dumps(_group_key(item), ensure_ascii=False),
                     "product_key": dumps(_product_key({"shopify_product_id": item.shopify_product_id,
                                                        "title": item.title, "vendor": item.vendor}), ensure_ascii=False),
                     "title": item.title, "variant_title": item.variant_title, "color": color,
                     "model": _model_name(item.variant_title, color),
                     "configuration": _accessory_configuration(item.variant_title), "sku": item.sku or "",
                     "image_url": item.image_url or "", "occupied_orders": [],
                     "on_hand_quantity": 0, "reserved_quantity": 0, "available_quantity": 0})
    return {"products": _group_stock_products(rows),
            "pagination": {"page": page, "per_page": per_page, "total": total}}


def batch_receive_inventory(payload):
    """One atomic receipt; validate all lines first and allocate stock only once."""
    shop = _shop(payload.get("shop_id"))
    entries = payload.get("items")
    if not isinstance(entries, list) or not 1 <= len(entries) <= 100:
        raise ValidationError("每批请选择 1–100 个变体")
    reason = str(payload.get("reason") or "").strip() or "批量手动入库"
    if len(reason) > 255:
        raise ValidationError("入库备注最多 255 字")
    quantities = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValidationError("入库明细格式错误")
        key, quantity = entry.get("group_key"), entry.get("quantity")
        if not isinstance(key, str) or key in quantities:
            raise ValidationError("变体编号无效或重复")
        if isinstance(quantity, bool) or not isinstance(quantity, int) or not 1 <= quantity <= 100_000:
            raise ValidationError("每个变体的入库数量必须为 1–100000 的整数")
        quantities[key] = quantity
    Shop.query.filter_by(id=shop.id).with_for_update().one()
    keys = set(quantities)
    known = {item['group_key'] for item in _stock_seed_items(shop.id, keys)}
    if known != keys:
        raise NotFoundError("部分变体不存在或不属于当前店铺，请刷新商品列表")
    records = [InventoryAdjustment(shop_id=shop.id, group_key=key, quantity_delta=quantity, reason=reason)
               for key, quantity in quantities.items()]
    db.session.add_all(records)
    db.session.flush()
    allocated = auto_allocate_stock(shop.id, keys, commit=False)
    reopened = _reopen_dismissed_procurement(shop.id, keys)
    saved_items = [record.to_dict() for record in records]
    db.session.commit()
    return {"items": saved_items, "quantity": sum(quantities.values()),
            "allocated_orders": allocated, "reopened_procurement_orders": reopened}


def zero_inventory(shop_ids, *, reason):
    """Operator-only zero balance, preserving receipts and physical shipment history.

    No implicit commit: a maintenance caller must back up, validate and commit.
    Do not use ordinary occupancy release here: it can select shipped receipt
    allocations, changing historical consumption while leaving live claims.
    """
    from app.utils.security import current_user

    if (current_user() or {}).get("authority") != "admin":
        raise ForbiddenError("库存清零仅允许管理员执行")
    if not reason or len(reason) > 255:
        raise ValidationError("必须提供库存清零原因")
    shops = Shop.query.filter(Shop.id.in_(shop_ids)).order_by(Shop.id).with_for_update().all()
    if {shop.id for shop in shops} != set(shop_ids):
        raise NotFoundError("店铺不存在")
    report = []
    for shop in shops:
        live_ids = {order_id for (order_id,) in db.session.query(Order.id).filter(
            Order.shop_id == shop.id,
            Order.warehouse_status.in_(("pending", "legacy_partial_unverified")),
        ).all()}
        before = _stock_rows(shop.id, include_catalog=False)
        InventoryReservation.query.filter(
            InventoryReservation.shop_id == shop.id,
            InventoryReservation.order_id.in_(live_ids),
        ).delete(synchronize_session="fetch")
        for purchase in PurchaseList.query.filter(
            PurchaseList.shop_id == shop.id,
            PurchaseList.status.in_(("partially_received", "received")),
        ).all():
            allocations = [dict(row) for row in purchase.allocations or []]
            changed = False
            for allocation in allocations:
                if allocation.get("order_id") in live_ids and int(allocation.get("received_quantity") or 0):
                    good = int(allocation["received_quantity"])
                    allocation["received_quantity"] = 0
                    allocation["damaged_quantity"] = int(allocation.get("damaged_quantity") or 0) + good
                    changed = True
            if changed:
                purchase.allocations = allocations
        for sale in AfterSale.query.options(selectinload(AfterSale.items)).filter(
            AfterSale.shop_id == shop.id, AfterSale.deleted_at.is_(None), AfterSale.status != "shipped",
        ).all():
            for item in sale.items:
                item.reserved_quantity = 0
            sale.status = "purchasing" if sale.purchased_at else "pending_purchase"
        db.session.flush()
        balances = _stock_rows(shop.id, include_catalog=False)
        for key, row in balances.items():
            if row["reserved_quantity"]:
                raise ValidationError("库存仍有未解除的占用，请核对")
            if row["on_hand_quantity"]:
                db.session.add(InventoryAdjustment(shop_id=shop.id, group_key=key,
                    quantity_delta=-row["on_hand_quantity"], reason=reason))
        db.session.flush()
        final = _stock_rows(shop.id, include_catalog=False)
        if any(row["on_hand_quantity"] or row["reserved_quantity"] or row["available_quantity"]
               for row in final.values()):
            raise ValidationError("库存清零复核失败")
        report.append({"shop_id": shop.id, "shop_name": shop.name,
                       "previous_on_hand": sum(row["on_hand_quantity"] for row in before.values()),
                       "previous_reserved": sum(row["reserved_quantity"] for row in before.values()),
                       "on_hand": 0, "reserved": 0, "available": 0})
    return report


def _release_occupancy(shop_id, group_key, quantity, *, include_after_sales=True):
    """释放指定数量的库存占用，返回受影响的订单（供前端提示「要重新采购」）。

    占用有三种来源，按「越新的订单越先释放」的顺序逐个削减：
        1. 手工库存预留（InventoryReservation）
        2. 采购到货后分配给订单的数量（purchase.allocations[].received_quantity）
        3. 售后补发的预留（AfterSaleItem.reserved_quantity）

    先释放最新占用的订单：发货队列按下单时间从早到晚排，越早的订单越该优先保住。
    """
    from sqlalchemy.orm.attributes import flag_modified

    released = []
    remaining = int(quantity)
    from app.services.inventory_pool_service import active_packing_sessions
    sessions = active_packing_sessions(shop_id)
    held_orders = {session.order_id for session in sessions if session.order_id}
    held_sales = {session.after_sale_id for session in sessions if session.after_sale_id}
    held_orders.update(value for (value,) in ShippingParcel.query.with_entities(ShippingParcel.order_id).filter(
        ShippingParcel.shop_id == shop_id, ShippingParcel.scanned_at.isnot(None),
    ).distinct().all())
    # Only live demand occupies stock. Shipped reservations/allocations are
    # historical deductions, not releasable claims (even when newest by date).
    live_orders = Order.query.filter(
        Order.shop_id == shop_id,
        Order.warehouse_status.in_(("pending", "legacy_partial_unverified")),
        Order.cancelled_at.is_(None),
        Order.financial_status.notin_(("refunded", "voided", "cancelled")),
        Order.id.notin_(held_orders),
    )

    # 1) 手工库存预留，后进先出
    reservations = (
        InventoryReservation.query.filter_by(shop_id=shop_id, group_key=group_key)
        .filter(InventoryReservation.order_id.in_(live_orders.with_entities(Order.id)))
        .order_by(InventoryReservation.id.desc())
        .all()
    )
    for reservation in reservations:
        if remaining <= 0:
            break
        take = min(int(reservation.quantity or 0), remaining)
        if take <= 0:
            continue
        reservation.quantity -= take
        remaining -= take
        released.append({"order_id": reservation.order_id, "quantity": take})
        if reservation.quantity <= 0:
            db.session.delete(reservation)

    # 2) 采购到货分配给订单的部分
    if remaining > 0:
        claims = []
        live_dates = {order.id: (order.order_date or order.created_at) for order in live_orders.all()}
        for purchase in PurchaseList.query.filter(
            PurchaseList.shop_id == shop_id,
            PurchaseList.status.in_(("received", "partially_received")),
        ).all():
            for allocation in purchase.allocations or []:
                if allocation.get("group_key") != group_key or "order_id" not in allocation:
                    continue
                if int(allocation["order_id"]) not in live_dates:
                    continue
                good = int(allocation.get("received_quantity") or 0)
                if good > 0:
                    claims.append((purchase, allocation, good))
        if claims:
            claims.sort(
                key=lambda item: live_dates[int(item[1]["order_id"])],
                reverse=True,
            )
            for purchase, allocation, good in claims:
                if remaining <= 0:
                    break
                take = min(good, remaining)
                allocation["received_quantity"] = good - take
                remaining -= take
                released.append({"order_id": int(allocation["order_id"]), "quantity": take})
                flag_modified(purchase, "allocations")

    # 3) 售后补发的预留
    if remaining > 0 and include_after_sales:
        after_sale_items = (
            AfterSaleItem.query.join(AfterSale, AfterSaleItem.after_sale_id == AfterSale.id)
            .filter(
                AfterSale.shop_id == shop_id,
                AfterSaleItem.group_key == group_key,
                AfterSaleItem.reserved_quantity > 0,
                AfterSale.deleted_at.is_(None),
                AfterSale.status != "shipped",
                AfterSale.id.notin_(held_sales),
            )
            .order_by(AfterSaleItem.id.desc())
            .all()
        )
        for item in after_sale_items:
            if remaining <= 0:
                break
            take = min(int(item.reserved_quantity or 0), remaining)
            if take <= 0:
                continue
            item.reserved_quantity -= take
            item.after_sale.status = "purchasing" if item.after_sale.purchased_at else "pending_purchase"
            remaining -= take
            released.append({"order_id": item.after_sale.order_id, "quantity": take})

    # 合并同一张订单的多次释放，并补上订单号给前端展示
    merged = {}
    for item in released:
        merged[item["order_id"]] = merged.get(item["order_id"], 0) + item["quantity"]
    if not merged:
        return []

    numbers = {
        order.id: order.order_number
        for order in Order.query.filter(Order.id.in_(list(merged))).all()
    }
    return [
        {"order_id": order_id, "order_number": numbers.get(order_id), "quantity": qty}
        for order_id, qty in merged.items()
    ]


def _parse_day(value, default):
    if not value:
        return default
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError as exc:
        raise ValidationError("日期格式应为 YYYY-MM-DD") from exc


def _parse_stamp(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _item_catalog(shop_id, group_keys=None):
    """group_key → 商品展示信息（名称/型号/SKU/图片）。

    传了 ``group_keys`` 就只查这些变体：流水一次可能只涉及几十个变体，
    没必要把整个店铺的上万条订单明细都读出来。
    """
    variant_ids = None
    if group_keys is not None:
        variant_ids = set()
        for key in group_keys:
            try:
                parts = loads(key)
            except (TypeError, ValueError):
                continue
            if isinstance(parts, list) and len(parts) >= 2 and parts[0] == "variant":
                variant_ids.add(str(parts[1]))
        if not variant_ids:
            return {}

    catalog = {item["group_key"]: {
        "title": item["title"] or "未命名商品", "variant_title": item["variant_title"] or "",
        "sku": item["sku"] or "", "image_url": item["image_url"] or "",
    } for item in _catalog_stock_items(shop_id, variant_ids)}
    query = OrderItem.query.join(Order, OrderItem.order_id == Order.id).filter(
        Order.shop_id == shop_id
    )
    if variant_ids is not None:
        query = query.filter(OrderItem.shopify_variant_id.in_(variant_ids))
    items = query.order_by(OrderItem.id.desc()).all()
    for item in items:
        if is_tip_line(item):
            continue
        key = dumps(_group_key(item), ensure_ascii=False)
        if key not in catalog:
            catalog[key] = {
                "title": item.title or "未命名商品",
                "variant_title": item.variant_title or "",
                "sku": item.sku or "",
                "image_url": item.image_url or "",
            }
        elif not catalog[key]["image_url"] and item.image_url:
            catalog[key]["image_url"] = item.image_url
    return catalog


def _movement(direction, at, group_key, quantity, source):
    """搭好一条流水记录，商品信息稍后按需批量补（避免逐条查库）。"""
    # 入库/出库按北京时间归日，前端直接按天分组展示
    beijing_at = at + timedelta(hours=8)
    return {
        "date": beijing_at.date().isoformat(),
        "at": at.isoformat(timespec="seconds") + "Z",
        "direction": direction,
        "quantity": int(quantity),
        "group_key": group_key,
        "source": source,
    }


def list_movements(args):
    from app.services.inventory_movement_service import list_movements as query_movements

    return query_movements(args)


def list_adjustments(args):
    shop = _shop(args.get("shop_id"))
    key = args.get("group_key")
    query = InventoryAdjustment.query.filter_by(shop_id=shop.id)
    if key:
        query = query.filter_by(group_key=key)
    return [row.to_dict() for row in query.order_by(InventoryAdjustment.id.desc()).limit(100).all()]


def auto_allocate_stock(shop_id, group_keys=None, *, commit=True):
    """Return soft allocations after stock changes, without binding physical goods.

    Physical counts are deducted only by scanning. Keep this hook
    for stock/sync callers, but never mutate historical receipt allocations.
    """
    from app.services.inventory_pool_service import build_pool_plan

    pool = build_pool_plan(shop_id)
    return [{"order_id": order.id, "order_number": order.order_number,
             "group_key": line["group_key"], "quantity": line["quantity"]}
            for order in pool["orders"] for line in pool["allocations"][order.id]
            if group_keys is None or line["group_key"] in group_keys]


def reserve_for_order(payload):
    """Retired endpoint: legacy ownership cannot be created in quantity-only mode."""
    shop = _shop(payload.get("shop_id"))
    raise ValidationError(f'店铺 {shop.name} 已使用公共数量库存，不再支持绑定订单；请扫码时按实际库存出库')
