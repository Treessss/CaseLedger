"""Fetch every selected order before making an immutable procurement snapshot."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from flask import current_app

from app import db
from sqlalchemy import func, select
from sqlalchemy.orm import noload, selectinload

from app.models import InventoryReservation, Order, Product, Shop
from app.services.shopify_graphql import fetch_orders_sync_by_ids, gid_for_order
from app.services.shopify_service import ShopifyService
from app.utils.errors import ValidationError

DETAIL_REFRESH_TTL = timedelta(hours=1)
DETAIL_BATCH_SIZE = 5


def lock_purchase_detail_refresh(shop_id):
    """Serialize procurement refreshes across PostgreSQL web workers.

    This uses a separate advisory-lock namespace, not the shop's stock/receipt
    row lock. Waiting requests re-read persisted timestamps after the previous
    request commits, so two employees do not fetch the same orders twice.
    """
    if db.session.get_bind().dialect.name == "postgresql":
        db.session.execute(select(func.pg_advisory_xact_lock(7122026, shop_id)))


def needs_detail_refresh(order, now=None):
    now = now or datetime.utcnow()
    last_success = order.shopify_details_synced_at
    return last_success is None or not now - DETAIL_REFRESH_TTL < last_success <= now


def fetch_purchase_orders(shop_id, orders):
    """Parallel, read-only API calls; no partially refreshed procurement lists."""
    if not orders:
        return []
    application = current_app._get_current_object()
    now = datetime.utcnow()
    identities = [(order.id, order.shopify_order_id, order.order_number)
                  for order in orders if needs_detail_refresh(order, now)]
    if not identities:
        application.logger.info("采购详情缓存命中：%s 笔订单，未请求 Shopify", len(orders))
        return []
    batches = [identities[index:index + DETAIL_BATCH_SIZE]
               for index in range(0, len(identities), DETAIL_BATCH_SIZE)]

    def fetch(batch):
        try:
            with application.app_context():
                shop = db.session.get(Shop, shop_id)
                nodes = fetch_orders_sync_by_ids(shop, [shopify_id for _, shopify_id, _ in batch])
                by_gid = {node["id"]: node for node in nodes if isinstance(node, dict) and node.get("id")}
                results = []
                for order_id, shopify_id, number in batch:
                    try:
                        node = by_gid.get(gid_for_order(shopify_id)) or {}
                        connection = node.get("lineItems") or {}
                        page = connection.get("pageInfo") or {}
                        if (not isinstance(connection.get("nodes"), list)
                                or not isinstance(page.get("hasNextPage"), bool)
                                or (page.get("hasNextPage") and not page.get("endCursor"))):
                            raise ValueError("Shopify 返回的订单商品详情不完整")
                        resource = ShopifyService()._normalize_graphql_orders(shop, [node])[0]
                        results.append((order_id, resource, None))
                    except Exception:  # noqa: BLE001 - incomplete orders cannot be cached
                        application.logger.exception("采购前更新 Shopify 订单失败：%s", number)
                        results.append((order_id, None, str(number)))
                return results
        except Exception:  # noqa: BLE001 - fail closed and report every failed order, not credentials
            application.logger.exception("采购前批量更新 Shopify 订单失败")
            return [(order_id, None, str(number)) for order_id, _, number in batch]

    with ThreadPoolExecutor(max_workers=min(2, len(batches))) as pool:
        results = [result for batch in pool.map(fetch, batches) for result in batch]
    failed = [number for _, _, number in results if number is not None]
    if failed:
        raise ValidationError(
            "以下订单从 Shopify 更新详情失败，未生成采购清单，请重试：" + "、".join(failed),
            extra={"failed_order_numbers": failed},
        )
    application.logger.info("采购前详情更新：缓存 %s 笔，回源 %s 笔，首轮请求 %s 批",
                            len(orders) - len(identities), len(identities), len(batches))
    return [(order_id, resource) for order_id, resource, _ in results]


def apply_purchase_order_updates(shop_id, resources, orders=None):
    """Reuse canonical sync, in the same transaction as purchase creation."""
    if not resources:
        return
    if orders is None:
        ids = [order_id for order_id, _ in resources]
        orders = Order.query.options(selectinload(Order.items), noload(Order.parcels)).filter(Order.id.in_(ids)).all()
    service = ShopifyService()
    service.app = current_app._get_current_object()
    service.shop_id = shop_id
    service._prefetched_orders = {order.shopify_order_id: order for order in orders}
    # Another successful sync may have completed while remote requests ran.
    by_id = {order.id: order for order in orders}
    resources = [(order_id, resource) for order_id, resource in resources
                 if needs_detail_refresh(by_id[order_id])]
    if not resources:
        return
    service._prefetched_reservations = {
        reservation.order_item_id: reservation for reservation in InventoryReservation.query.filter(
            InventoryReservation.shop_id == shop_id,
            InventoryReservation.order_id.in_([order.id for order in orders]),
        ).all()
    }
    # GraphQL normalization stores currentQuantity in quantity. REST resources
    # may instead provide current_quantity; match the canonical sync fallback.
    variant_ids = set()
    for _, resource in resources:
        for line in resource.line_items:
            quantity = getattr(line, "current_quantity", None)
            if quantity is None:
                quantity = getattr(line, "quantity", 0)
            if line.variant_id and int(quantity or 0) > 0:
                variant_ids.add(str(line.variant_id))
    service._prefetched_products = {
        (product.shopify_product_id, product.shopify_variant_id): product
        for product in Product.query.filter(Product.shop_id == shop_id,
                                            Product.shopify_variant_id.in_(variant_ids)).all()
    }
    for order_id, resource in resources:
        updated = service._process_order(resource)
        if updated.id != order_id:
            raise ValidationError("Shopify 订单匹配发生变化，未生成采购清单，请刷新后重试")
    db.session.flush()
