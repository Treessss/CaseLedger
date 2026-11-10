"""Read-only shared-stock planning; soft suggestions never mutate receipt history."""
import json
from collections import Counter, defaultdict
from datetime import datetime
from types import SimpleNamespace

from sqlalchemy.orm import load_only, noload, selectinload
from sqlalchemy.orm.attributes import set_committed_value
from sqlalchemy import func

from app import db
from app.models import AfterSale, InventoryOutboundLine, Order, OrderItem, PackingSession, Product, PurchaseList, ShippingParcel
from app.utils.order_lines import is_tip_line

POOL_FIELDS = ("incoming_quantity", "purchase_needed_quantity", "ready_quantity",
               "waiting_quantity", "packing_quantity", "idle_quantity", "demand_quantity")


def active_packing_sessions(shop_id, now=None):
    now = now or datetime.utcnow()
    sessions = PackingSession.query.filter_by(shop_id=shop_id, state="packing").all()
    partial = {order_id for (order_id,) in ShippingParcel.query.with_entities(ShippingParcel.order_id).filter(
        ShippingParcel.shop_id == shop_id, ShippingParcel.scanned_at.isnot(None),
    ).distinct().all()}
    return [session for session in sessions if session.expires_at > now or session.order_id in partial]


def build_pool_plan(shop_id, *, include_order_ids=(), exclude_purchase_id=None,
                    respect_procurement_exclusion=True):
    from app.services.inventory_service import _stock_rows, _stock_seed_items
    from app.services.outbound_inventory_service import _compatible_signature
    from app.services.purchase_service import _group_key

    orders = Order.query.options(noload(Order.parcels), noload(Order.after_sales),
        load_only(Order.id, Order.order_number, Order.shop_id, Order.order_date, Order.created_at,
                  Order.fulfillment_priority, Order.financial_status, Order.warehouse_status,
                  Order.cancelled_at, Order.procurement_excluded_at)).filter(
        Order.shop_id == shop_id, Order.warehouse_status == "pending", Order.cancelled_at.is_(None),
        Order.financial_status.in_(("paid", "partially_paid", "partially_refunded")),
    ).all()
    # A preceding lightweight list may have marked items as an empty noload
    # relationship in this session. Bulk-load only these pending order lines;
    # never turn that projection into a false zero-demand inventory forecast.
    lines_by_order = defaultdict(list)
    for item in OrderItem.query.filter(OrderItem.order_id.in_([order.id for order in orders])).all():
        lines_by_order[item.order_id].append(item)
    for order in orders:
        set_committed_value(order, 'items', lines_by_order[order.id])
    order_numbers = {order.id: order.order_number for order in orders}
    # Queue dismissal suppresses procurement demand, not the right to ship actual stock.
    # Keep all pending orders for individual physical readiness. Batch printing uses
    # an inclusive forecast so its suggested set also budgets dismissed orders.
    physical_orders = orders
    if respect_procurement_exclusion:
        orders = [order for order in orders if order.procurement_excluded_at is None or order.id in include_order_ids]
    orders.sort(key=lambda order: (-order.fulfillment_priority, order.order_date or order.created_at, order.id))
    sales = AfterSale.query.options(selectinload(AfterSale.items), noload(AfterSale.purchase_lists)).filter(
        AfterSale.shop_id == shop_id, AfterSale.deleted_at.is_(None), AfterSale.status != "shipped",
    ).order_by(AfterSale.created_at, AfterSale.id).all()
    purchases = PurchaseList.query.filter(
        PurchaseList.shop_id == shop_id, PurchaseList.status.in_(("ordered", "partially_received")),
        PurchaseList.id != exclude_purchase_id if exclude_purchase_id is not None else True,
    ).all()
    rows = _stock_rows(shop_id, include_catalog=False)
    debited_ids = {order_id for order_id, total in db.session.query(
        InventoryOutboundLine.order_id, func.sum(InventoryOutboundLine.quantity_delta)).filter_by(
        shop_id=shop_id).group_by(InventoryOutboundLine.order_id).all() if total < 0}
    carrier_markers = {}
    for order_id, action in db.session.query(InventoryOutboundLine.order_id, InventoryOutboundLine.action).filter(
            InventoryOutboundLine.shop_id == shop_id,
            InventoryOutboundLine.action.in_(('carrier_confirm', 'carrier_undo'))).order_by(InventoryOutboundLine.id):
        carrier_markers[order_id] = action
    debited_ids.update(order_id for order_id, action in carrier_markers.items() if action == 'carrier_confirm')
    metadata = {}
    needs = {}
    for order in orders:
        needs[order.id] = Counter()
        for item in order.items:
            if not is_tip_line(item) and item.quantity > 0:
                key = json.dumps(_group_key(item), ensure_ascii=False)
                if order.id not in debited_ids:
                    needs[order.id][key] += item.quantity
                metadata[key] = item
    for sale in sales:
        for item in sale.items:
            metadata[item.group_key] = item
    incoming = Counter()
    snapshots = {}
    def purchase_quantity(purchase, item):
        return int(item.get("quantity") or sum(int(line.get("quantity") or 0)
                    for line in purchase.allocations or [] if line.get("group_key") == item["group_key"]))
    for purchase in purchases:
        received = {row["group_key"]: row for row in purchase.receipt or []}
        for item in purchase.items or []:
            key = item["group_key"]
            receipt = received.get(key, {})
            outstanding = max(purchase_quantity(purchase, item) - int(receipt.get("received_quantity") or 0)
                              - int(receipt.get("damaged_quantity") or 0)
                              - int(receipt.get("confirmed_missing_quantity") or 0), 0)
            if outstanding and not is_tip_line(item):
                incoming[key] += outstanding
                snapshots[key] = item
    # Include stockless demand/incoming variants, never the entire shop catalog.
    missing = (set(metadata) | set(incoming)) - set(rows)
    seeds = _stock_seed_items(shop_id, missing)
    from app.services.purchase_service import _color_name, _model_name, _product_key
    for seed in seeds:
        snapshots.setdefault(seed["group_key"], seed)
    for key in missing:
        source = metadata.get(key)
        item = snapshots.get(key) or ({field: getattr(source, field, None) for field in (
            "title", "variant_title", "sku", "image_url", "shopify_product_id", "vendor")} if source else {})
        color = _color_name(item.get("variant_title"))
        rows[key] = {"group_key": key, "title": item.get("title") or "未命名商品",
                     "color": color, "model": _model_name(item.get("variant_title"), color),
                     "product_key": json.dumps(_product_key(item), ensure_ascii=False),
                     "sku": item.get("sku") or "", "image_url": item.get("image_url") or "",
                     "received_quantity": 0, "shipped_quantity": 0, "adjustment_quantity": 0,
                     "on_hand_quantity": 0, "reserved_quantity": 0, "available_quantity": 0,
                     "occupied_orders": []}
    variant_ids = []
    for key in rows:
        try:
            parts = json.loads(key)
        except (ValueError, TypeError):
            continue  # Legacy free-form stock identities are still exact-matchable.
        if isinstance(parts, list) and len(parts) == 2 and parts[0] == "variant":
            variant_ids.append(parts[1])
    for product in Product.query.filter(Product.shop_id == shop_id, Product.shopify_variant_id.in_(variant_ids)).all():
        metadata.setdefault(json.dumps(_group_key(product), ensure_ascii=False), product)
    for key, snapshot in snapshots.items():
        metadata.setdefault(key, SimpleNamespace(shopify_product_id=snapshot.get("shopify_product_id"),
                                                 variant_title=snapshot.get("variant_title") or ""))
    signatures = {key: _compatible_signature(item) for key, item in metadata.items()}
    by_signature = defaultdict(list)
    for key, signature in signatures.items():
        if signature:
            by_signature[signature].append(key)
    by_signature = {signature: sorted(keys) for signature, keys in by_signature.items()}

    def choices(key):
        signature = signatures.get(key)
        return [key, *sorted(other for other in by_signature.get(signature, []) if other != key)]

    for row in rows.values():
        for field in POOL_FIELDS:
            row[field] = 0
        row["planning_orders"] = []
        row["incoming_purchases"] = []
        row["purchase_orders"] = []
    for purchase in purchases:
        received = {r["group_key"]: r for r in purchase.receipt or []}
        for item in purchase.items or []:
            key = item["group_key"]
            receipt = received.get(key, {})
            qty = max(purchase_quantity(purchase, item) - int(receipt.get("received_quantity") or 0)
                      - int(receipt.get("damaged_quantity") or 0)
                      - int(receipt.get("confirmed_missing_quantity") or 0), 0)
            if qty and key in rows:
                rows[key]["incoming_purchases"].append({"purchase_id": purchase.id, "quantity": qty})
    for key, quantity in incoming.items():
        rows[key]["incoming_quantity"] = quantity
    budget = Counter({key: max(row["on_hand_quantity"], 0) for key, row in rows.items()})
    legacy_partial = {value for (value,) in Order.query.with_entities(Order.id).filter(
        Order.shop_id == shop_id, Order.warehouse_status == "legacy_partial_unverified",
    ).all()}
    # Historical partial shipments cannot donate goods until explicitly
    # reconciled. They are not normal pending demand, but their claims are real.
    for key, row in rows.items():
        for entry in row["occupied_orders"]:
            if entry["order_id"] in legacy_partial:
                take = min(entry["quantity"], budget[key])
                budget[key] -= take
                row["packing_quantity"] += take
                row["planning_orders"].append({**entry, "quantity": take, "kind": "packing"})
    # Packing tasks are informational, not stock holds. Only scan ledgers deduct quantities.
    procurement_stock = Counter(budget)
    sessions = []
    held_orders = {}
    held_sales = {session.after_sale_id for session in sessions if session.after_sale_id}
    insufficient_holds = set()
    held_allocations = defaultdict(list)
    for session in sessions:
        if session.order_id:
            held_orders[session.order_id] = session
        for line in session.lines:
            key, quantity = line["group_key"], int(line["quantity"])
            if key in rows:
                take = min(quantity, budget[key])
                if take < quantity:
                    insufficient_holds.add(session.id)
                budget[key] -= take
                rows[key]["packing_quantity"] += take
                if take:
                    held_allocations[session.id].append({"group_key": key, "target_key": key, "quantity": take})
                rows[key]["planning_orders"].append({"order_id": session.order_id or next(
                    (sale.order_id for sale in sales if sale.id == session.after_sale_id), None),
                    "after_sale_id": session.after_sale_id, "quantity": take, "kind": "packing",
                    "order_number": order_numbers.get(session.order_id) or next(
                        (sale.customer_reference for sale in sales if sale.id == session.after_sale_id), "打包任务")})
    # Protect already prepared reshipments and partial original packages.
    # They are independent customer promises, not donor stock for a new order.
    cf_remaining = {sale.id: Counter() for sale in sales}
    cf_allocations = {sale.id: [] for sale in sales}
    for sale in sales:
        for item in sale.items:
            reserved = 0  # Historical CF fields must not preclaim shared physical inventory.
            if sale.id not in held_sales:
                take = min(reserved, budget[item.group_key])
                budget[item.group_key] -= take
                if take:
                    cf_allocations[sale.id].append({"group_key":item.group_key,"target_key":item.group_key,"quantity":take})
                cf_remaining[sale.id][item.group_key] += max(item.quantity - take, 0)
    partial_ids = {value for (value,) in ShippingParcel.query.with_entities(ShippingParcel.order_id).filter(
        ShippingParcel.shop_id == shop_id, ShippingParcel.scanned_at.isnot(None),
    ).distinct().all()}
    allocations = {order.id: [] for order in orders}
    remaining = {order_id: Counter(value) for order_id, value in needs.items()}
    ready_orders = {order.id for order in orders if order.id in debited_ids}
    for order in orders:
        if order.id in held_orders:
            session = held_orders[order.id]
            allocations[order.id] = held_allocations[session.id]
            held_quantities = Counter()
            for line in allocations[order.id]:
                held_quantities[line["group_key"]] += line["quantity"]
            remaining[order.id] = Counter({key: max(quantity-held_quantities[key], 0)
                                          for key, quantity in needs[order.id].items()})
            if session.id not in insufficient_holds and held_quantities == needs[order.id]:
                ready_orders.add(order.id)
        elif order.id in partial_ids:
            # Legacy partial scans have no packing session. Their existing
            # physical claims remain protected until finished/explicitly undone.
            for key, row in rows.items():
                owned = sum(entry["quantity"] for entry in row["occupied_orders"] if entry["order_id"] == order.id)
                take = min(owned, budget[key])
                if take:
                    budget[key] -= take
                    row["packing_quantity"] += take
                    remaining[order.id][key] = max(remaining[order.id][key] - take, 0)
                    allocations[order.id].append({"group_key": key, "target_key": key, "quantity": take})
            if not any(remaining[order.id].values()):
                ready_orders.add(order.id)

    def allocate(order, pool, *, all_or_nothing):
        trial = Counter(pool)
        lines = []
        unmet = Counter()
        for target_key, quantity in remaining[order.id].items():
            outstanding = quantity
            for source_key in choices(target_key):
                take = min(outstanding, trial[source_key])
                if take:
                    trial[source_key] -= take
                    outstanding -= take
                    lines.append({"group_key": source_key, "target_key": target_key, "quantity": take})
            unmet[target_key] = outstanding
        if all_or_nothing and any(unmet.values()):
            return False
        pool.clear()
        pool.update(trial)
        remaining[order.id] = unmet
        allocations[order.id].extend(lines)
        return not any(unmet.values())

    # Fill whole shippable orders first. An incomplete old order cannot strand
    # a newer complete one; an old nearly complete order wins when it can ship.
    for order in orders:
        if order.id not in held_orders and order.id not in partial_ids and needs[order.id] and order.financial_status != "partially_paid":
            if allocate(order, budget, all_or_nothing=True):
                ready_orders.add(order.id)
    cf_ready_ids = {session.after_sale_id for session in sessions
                    if session.after_sale_id and session.id not in insufficient_holds}
    # Newly free stock can also complete a reshipment; existing claims are not
    # required to make a physical unit count against its procurement shortage.
    for sale in sorted(sales, key=lambda row: (row.created_at, row.id)):
        if sale.id in held_sales:
            continue
        trial = Counter(budget)
        extra = []
        unmet = Counter()
        for target, quantity in cf_remaining[sale.id].items():
            need = quantity
            for source in choices(target):
                take = min(need, trial[source])
                if take:
                    trial[source] -= take
                    need -= take
                    extra.append({"group_key":source,"target_key":target,"quantity":take})
            unmet[target] = need
        if not any(unmet.values()):
            budget = trial
            cf_allocations[sale.id].extend(extra)
            cf_remaining[sale.id] = unmet
            cf_ready_ids.add(sale.id)
    for order in orders:
        if order.id not in ready_orders and order.id not in held_orders and order.id not in partial_ids:
            allocate(order, budget, all_or_nothing=False)
    for sale in sales:
        if sale.id in cf_ready_ids:
            continue
        for target, quantity in cf_remaining[sale.id].items():
            need = quantity
            for source in choices(target):
                take = min(need, budget[source])
                if take:
                    budget[source] -= take
                    need -= take
                    cf_allocations[sale.id].append({"group_key":source,"target_key":target,"quantity":take})
            cf_remaining[sale.id][target] = need
    for sale in sales:
        if sale.id in held_sales:
            continue
        category = "ready_quantity" if sale.id in cf_ready_ids else "waiting_quantity"
        for line in cf_allocations[sale.id]:
            rows[line["group_key"]][category] += line["quantity"]
            rows[line["group_key"]]["planning_orders"].append({"order_id":sale.order_id,
                "after_sale_id":sale.id,"order_number":sale.customer_reference,
                "quantity":line["quantity"],"kind":"ready" if sale.id in cf_ready_ids else "waiting"})
    for order in orders:
        if order.id in held_orders or order.id in partial_ids:
            continue
        category = "ready_quantity" if order.id in ready_orders else "waiting_quantity"
        for line in allocations[order.id]:
            row = rows[line["group_key"]]
            row[category] += line["quantity"]
            row["planning_orders"].append({"order_id": order.id, "order_number": order.order_number,
                "quantity": line["quantity"], "kind": "ready" if order.id in ready_orders else "waiting",
                "priority": order.fulfillment_priority})
    shortages = Counter()
    # Purchasing and warehouse readiness are different forecasts. Honor existing
    # procurement promises before sharing surplus, without locking scan stock.
    from app.services.procurement_coverage_service import forecast_coverage

    owners = [('order', order.id) for order in orders] + [('sale', sale.id) for sale in sales]
    owner_indexes = {owner: index for index, owner in enumerate(owners)}
    demands = [needs[order.id] for order in orders] + [
        Counter({key: sum(item.quantity for item in sale.items if item.group_key == key)
                 for key in {item.group_key for item in sale.items}}) for sale in sales]
    promised = [Counter() for _ in owners]
    accepted = [Counter() for _ in owners]
    history = db.session.query(PurchaseList.status, PurchaseList.allocations).filter(
        PurchaseList.shop_id == shop_id,
        PurchaseList.status.in_(("ordered", "partially_received", "received")),
        PurchaseList.id != exclude_purchase_id if exclude_purchase_id is not None else True,
    ).all()
    for purchase in history:
        for line in purchase.allocations or []:
            owner = ('sale', int(line['after_sale_id'])) if line.get('after_sale_id') else (
                'order', int(line.get('order_id') or 0))
            index = owner_indexes.get(owner)
            key = line.get('group_key')
            if index is None or key not in demands[index]:
                continue
            good = max(int(line.get('received_quantity') or 0), 0)
            accepted[index][key] += good
            if purchase.status in ('ordered', 'partially_received'):
                promised[index][key] += max(int(line.get('quantity') or 0) - good
                    - int(line.get('damaged_quantity') or 0)
                    - int(line.get('confirmed_missing_quantity') or 0), 0)
    incoming_uncovered, incoming_pending = forecast_coverage(
        demands, incoming, procurement_stock, promised, accepted, choices,
        [allocations[order.id] for order in orders] + [cf_allocations[sale.id] for sale in sales])
    uncovered_by_order = {order.id: Counter() for order in orders}
    pending_by_order = {order.id: Counter() for order in orders}
    for order, uncovered in zip(orders, incoming_uncovered[:len(orders)]):
        for target in needs[order.id]:
            need = uncovered[target]
            shortages[target] += need
            if need:
                rows[target]["purchase_orders"].append({"order_id": order.id,
                    "order_number": order.order_number, "quantity": need})
            uncovered_by_order[order.id][target] = need
            pending_by_order[order.id][target] = incoming_pending[owner_indexes[('order', order.id)]][target]
    cf_uncovered = {sale.id: Counter() for sale in sales}
    for sale, uncovered in zip(sales, incoming_uncovered[len(orders):]):
        for target in demands[owner_indexes[('sale', sale.id)]]:
            need = uncovered[target]
            shortages[target] += need
            cf_uncovered[sale.id][target] = need
            if need:
                rows[target]["purchase_orders"].append({"order_id":sale.order_id,"after_sale_id":sale.id,
                    "order_number":sale.customer_reference,"quantity":need})
    # Invert demands once rather than rewalking every order for every variant.
    demand_totals = Counter()
    demand_references = defaultdict(list)
    for order in orders:
        for key, quantity in needs[order.id].items():
            demand_totals[key] += quantity
            if quantity > 0:
                demand_references[key].append({'order_id': order.id, 'order_number': order.order_number,
                                               'quantity': quantity})
    for sale in sales:
        for item in sale.items:
            demand_totals[item.group_key] += item.quantity
            if item.quantity > 0:
                demand_references[item.group_key].append({'order_id': sale.order_id, 'after_sale_id': sale.id,
                    'order_number': sale.customer_reference, 'quantity': item.quantity})
    for key, row in rows.items():
        row["purchase_needed_quantity"] = shortages[key]
        row["idle_quantity"] = budget[key]
        row['demand_quantity'] = demand_totals[key]
        row['demand_orders'] = demand_references[key]
    # Readiness is individual physical feasibility, NEVER a priority/ownership gate.
    suggested_ready_orders = set(ready_orders)
    suggested_ready_sales = set(cf_ready_ids)
    # Several orders can be ready for the same last unit; the atomic scan decides the winner.
    from app.services.outbound_inventory_service import physical_context, quantity_plan
    from app.utils.errors import ValidationError
    physical_allocations = {}
    planned_ids = {order.id for order in orders}
    excluded_context = physical_context(order for order in physical_orders
                                        if order.id not in planned_ids and order.id not in debited_ids)
    ready_orders = {order.id for order in physical_orders if order.id in debited_ids}
    for order in physical_orders:
        context = {'rows': rows, 'metadata': metadata, 'compatible_keys': by_signature}
        if order.id not in planned_ids:
            context = excluded_context
        try:
            plan = quantity_plan(order, **context) if order.id not in debited_ids else []
            physical_allocations[order.id] = [{'group_key': step['source_key'], 'quantity': step['quantity']}
                                             for step in plan]
            if order.financial_status != 'partially_paid':
                ready_orders.add(order.id)
        except ValidationError:
            physical_allocations[order.id] = [{'group_key': step['source_key'], 'quantity': step['quantity']}
                for step in quantity_plan(order, **context, allow_partial=True)]
    cf_ready_ids = set()
    for sale in sales:
        try:
            quantity_plan(sale, rows=rows, metadata=metadata, compatible_keys=by_signature)
            cf_ready_ids.add(sale.id)
        except ValidationError:
            pass
    return {"rows": rows, "orders": orders, "allocations": allocations, "remaining": remaining,
            "ready_order_ids": ready_orders, "sessions": sessions, "incoming": incoming,
            "uncovered_by_order": uncovered_by_order, "pending_by_order": pending_by_order,
            "cf_ready_ids":cf_ready_ids,"cf_allocations":cf_allocations,"cf_uncovered":cf_uncovered,
            'physical_allocations': physical_allocations, 'debited_order_ids': debited_ids,
            'metadata': metadata, 'suggested_ready_order_ids': suggested_ready_orders,
            'suggested_cf_ready_ids': suggested_ready_sales}
