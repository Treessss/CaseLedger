import json
import requests
import time
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from decimal import Decimal
from flask import current_app
from sqlalchemy.exc import OperationalError
from app.models.order import Order
from app.models.product import Product
from app.models.payment import Payment
from app.models.order_item import OrderItem
from app.models.fee_config import FeeConfig
from app.services.gateway_fee_service import apply_estimated_fee, apply_gateway_fee, _refresh_order_settlement
from app.services.shopify_auth import get_access_token
from app.services.shopify_graphql import (
    fetch_orders_page,
    fetch_updated_orders_page,
    fetch_order_sync_by_id,
    fetch_remaining_order_line_items,
    fetch_products_page,
    fetch_remaining_variants,
    fetch_shop_info,
    normalize_fulfillment_status,
    line_item_image,
    require_all_orders_access,
    ShopifyGraphQLError,
)
from app.utils.gateway_fees import extract_paypal_breakdown
from app.utils.shopify_resources import (
    WebhookResource,
    extract_customer_info,
    extract_fulfillments,
    extract_line_items,
)
from app.utils.timezone import utc_naive_from_iso
from app import db


def _gid_number(value):
    return str(value).rsplit("/", 1)[-1] if value else None


def _shop_money(value):
    return (value or {}).get("shopMoney") or {}


def graphql_order_to_rest(node):
    """Map validated GraphQL order fields onto the existing order normalizer."""
    address = node.get("shippingAddress") or {}
    customer = node.get("customer") or {}
    line_items = []
    for item in (node.get("lineItems") or {}).get("nodes") or []:
        variant = item.get("variant") or {}
        price = _shop_money(item.get("originalUnitPriceSet")).get("amount") or "0"
        discounted = _shop_money(item.get("discountedTotalSet")).get("amount")
        quantity = int(item.get("currentQuantity", item.get("quantity")) or 0)
        original_quantity = int(item.get("quantity") or 0)
        if original_quantity and discounted is not None:
            original_gross = Decimal(str(price)) * original_quantity
            original_discount = max(Decimal("0"), original_gross - Decimal(str(discounted)))
            discount = original_discount * quantity / original_quantity
        else:
            discount = Decimal("0")
        line_items.append(
            {
                "id": _gid_number(item.get("id")),
                "product_id": _gid_number((variant.get("product") or {}).get("id")),
                "variant_id": _gid_number(variant.get("id")),
                "title": item.get("title"),
                "variant_title": item.get("variantTitle"),
                "sku": item.get("sku"),
                "vendor": item.get("vendor"),
                "image_url": line_item_image(variant),
                "quantity": quantity,
                "price": price,
                "total_discount": str(discount),
            }
        )
    transactions = []
    for transaction in node.get("transactions") or []:
        amount_set = transaction.get("amountSet") or {}
        buyer_money = amount_set.get("presentmentMoney") or amount_set.get("shopMoney") or {}
        transactions.append(
            {
                "id": _gid_number(transaction.get("id")),
                "kind": str(transaction.get("kind") or "").lower(),
                "status": str(transaction.get("status") or "").lower(),
                "gateway": transaction.get("gateway"),
                "amount": buyer_money.get("amount") or "0",
                "currency": buyer_money.get("currencyCode"),
                "shop_amount": (amount_set.get("shopMoney") or {}).get("amount"),
                "shop_currency": (amount_set.get("shopMoney") or {}).get("currencyCode"),
                "processed_at": transaction.get("processedAt"),
            }
        )
    fulfillments = []
    for fulfillment in node.get("fulfillments") or []:
        tracking = fulfillment.get("trackingInfo") or []
        fulfillments.append(
            {
                "id": _gid_number(fulfillment.get("id")),
                "status": str(fulfillment.get("status") or "").lower(),
                "tracking_company": tracking[0].get("company") if tracking else None,
                "tracking_numbers": [row["number"] for row in tracking if row.get("number")],
                "tracking_urls": [row["url"] for row in tracking if row.get("url")],
                "created_at": fulfillment.get("createdAt"),
                "updated_at": fulfillment.get("updatedAt"),
            }
        )
    return {
        "id": _gid_number(node.get("id")),
        "order_number": str(node.get("name") or "").lstrip("#"),
        "created_at": node.get("createdAt"),
        "updated_at": node.get("updatedAt"),
        "cancelled_at": node.get("cancelledAt"),
        "currency": node.get("currencyCode"),
        "presentment_currency": node.get("presentmentCurrencyCode"),
        "financial_status": str(node.get("displayFinancialStatus") or "").lower(),
        "fulfillment_status": normalize_fulfillment_status(node.get("displayFulfillmentStatus")),
        "note": node.get("note"),
        "email": node.get("email"),
        "phone": node.get("phone"),
        "customer": {
            "first_name": customer.get("firstName"),
            "last_name": customer.get("lastName"),
            "email": customer.get("email"),
            "phone": customer.get("phone"),
        },
        "shipping_address": {
            **{key: value for key, value in address.items() if key not in ("provinceCode", "countryCode")},
            "province_code": address.get("provinceCode"),
            "country_code": address.get("countryCode"),
        },
        "total_price": _shop_money(node.get("currentTotalPriceSet")).get("amount") or "0",
        "subtotal_price": _shop_money(node.get("currentSubtotalPriceSet")).get("amount") or "0",
        "total_tax": _shop_money(node.get("currentTotalTaxSet")).get("amount") or "0",
        "total_shipping_price_set": {"shop_money": _shop_money(node.get("totalShippingPriceSet"))},
        "line_items": line_items,
        "transactions": transactions,
        "fulfillments": fulfillments,
    }


class ShopifyService:
    """Shopify API集成服务"""

    def __init__(self, app=None):
        self.app = None
        self.api_key = None
        self.api_secret = None
        self.shop_url = None
        self.access_token = None
        self.api_timeout = 30
        self.shop_id = None  # 绑定的店铺 id（多店铺场景）
        self._last_shop_info = None

        if app is not None:
            self.init_app(app)

    def init_shop(self, shop, app=None):
        """把服务实例绑定到某个具体店铺（多店铺同步使用该入口）。"""
        from app.models.shop import Shop

        if app is not None:
            self.app = app
        self.shop_id = shop.id
        self._last_shop_info = None
        self.shop_url = Shop.normalize_shop_url(shop.shop_url)
        if self.app is not None:
            self.api_timeout = int(self.app.config.get("SHOPIFY_API_TIMEOUT", 30) or 30)

        if not self.shop_url:
            raise ValueError(f"店铺 {shop.name or shop.shop_url} 缺少店铺域名，无法同步")

        # 统一用「域名 + Client ID + Client Secret」换临时令牌，不再依赖永久 access_token
        self.access_token = get_access_token(shop, timeout=self.api_timeout)

        return self

    def _scope_filter(self, model):
        """构造按店铺过滤的查询（未绑定店铺时保持单店铺旧行为）。"""
        query = model.query
        if self.shop_id is not None:
            query = query.filter(model.shop_id == self.shop_id)
        return query

    def init_app(self, app):
        """把服务挂到 Flask 应用上。

        凭据按店铺管理（域名 + Client ID + Client Secret），这里仅登记基础配置。
        """
        self.app = app
        self.api_timeout = int(app.config.get("SHOPIFY_API_TIMEOUT", 30) or 30)
        app.logger.info("Shopify 凭据按店铺配置，同步时按店铺换取访问令牌")

    def test_connection(self) -> bool:
        """测试Shopify API连接"""
        try:
            shop = self.get_shop_info()
            if self.app:
                self.app.logger.info(f"Connected to shop: {shop.get('name')}")
            return bool(shop.get("name"))
        except Exception as e:
            if self.app:
                self.app.logger.error(f"Shopify connection test failed: {str(e)}")
            return False

    def _normalize_graphql_orders(self, shop, nodes):
        orders = []
        for node in nodes:
            line_connection = node.get("lineItems") or {}
            line_page = line_connection.get("pageInfo") or {}
            if line_page.get("hasNextPage") and line_page.get("endCursor"):
                line_connection["nodes"] = list(line_connection.get("nodes") or []) + (
                    fetch_remaining_order_line_items(shop, node["id"], line_page["endCursor"])
                )
            payload = graphql_order_to_rest(node)
            payload["_details_refreshed_at"] = datetime.utcnow()
            orders.append(WebhookResource(payload))
        return orders

    def _fetch_graphql_orders(self, since_date, limit):
        from app.models import Shop

        shop = db.session.get(Shop, self.shop_id)
        if shop is None:
            raise ValueError("店铺不存在，无法同步订单")
        orders = []
        cursor = None
        while len(orders) < limit:
            nodes, page_info = fetch_orders_page(
                shop, since_date, first=min(25, limit - len(orders)), after=cursor
            )
            orders.extend(self._normalize_graphql_orders(shop, nodes))
            cursor = page_info.get("endCursor")
            if not nodes or not page_info.get("hasNextPage") or not cursor:
                break
        return orders

    def _fetch_updated_graphql_orders(self, since_date):
        from app.models import Shop

        shop = db.session.get(Shop, self.shop_id)
        if shop is None:
            raise ValueError("店铺不存在，无法同步订单")
        orders = []
        cursor = None
        while True:
            nodes, page_info = fetch_updated_orders_page(shop, since_date, first=25, after=cursor)
            if not nodes and page_info.get("hasNextPage"):
                raise ShopifyGraphQLError("Shopify 返回空更新页，但仍提示有下一页")
            orders.extend(self._normalize_graphql_orders(shop, nodes))
            if not page_info.get("hasNextPage"):
                break
            next_cursor = page_info.get("endCursor")
            if not next_cursor or next_cursor == cursor:
                raise ShopifyGraphQLError("Shopify 更新订单分页游标未前进")
            cursor = next_cursor
        return orders

    def sync_order_by_id(self, order):
        """Refresh one selected order with the same normalization as bulk sync."""
        from app.models import Shop

        shop = db.session.get(Shop, self.shop_id)
        if shop is None or order.shop_id != shop.id:
            raise ValueError("订单与店铺不匹配")
        node = fetch_order_sync_by_id(shop, order.shopify_order_id, order.order_number)
        normalized = self._normalize_graphql_orders(shop, [node])
        return self.sync_orders(_orders=normalized)

    def sync_all_orders(self) -> Dict[str, int]:
        """遍历全部历史订单，逐页落库，不按日期或数量截断。"""
        from app.models import Shop

        shop = db.session.get(Shop, self.shop_id)
        if shop is None:
            raise ValueError("店铺不存在，无法同步订单")
        require_all_orders_access(shop)

        totals = {"total_fetched": 0, "new_orders": 0, "updated_orders": 0, "errors": 0}
        cursor = None
        page_number = 0
        while True:
            nodes, page_info = fetch_orders_page(shop, None, first=25, after=cursor)
            if not nodes and page_info.get("hasNextPage"):
                raise ShopifyGraphQLError("Shopify 返回了空订单页，但仍提示有下一页")
            orders = self._normalize_graphql_orders(shop, nodes)
            page_number += 1
            if self.app:
                self.app.logger.info("全量同步第 %s 页：%s 笔订单", page_number, len(orders))
            # 全量同步每页都跑一次绑定太浪费，等全部页跑完再统一补一次
            stats = self.sync_orders(_orders=orders, _auto_bind=False)
            for key in totals:
                totals[key] += stats[key]

            if not page_info.get("hasNextPage"):
                break
            next_cursor = page_info.get("endCursor")
            if not next_cursor or next_cursor == cursor:
                raise ShopifyGraphQLError("Shopify 订单分页游标未前进，已停止以避免漏单或无限循环")
            cursor = next_cursor
        self._auto_bind_available_stock(totals.get("new_orders", 0))
        return totals

    def sync_orders(
        self, days_back: int = 30, limit: int = 250, _orders=None, _auto_bind: bool = True
    ) -> Dict[str, int]:
        """同步订单数据

        Args:
            days_back: 同步多少天前的订单
            limit: 每次请求的订单数量限制

        Returns:
            Dict包含同步统计信息
        """
        try:
            if _orders is None:
                since_date = datetime.utcnow() - timedelta(days=days_back)
                if self.app:
                    self.app.logger.info(
                        f"开始同步订单：从 {since_date.strftime('%Y-%m-%d')} 开始，限制 {limit} 个订单"
                    )
                orders = self._fetch_graphql_orders(since_date, limit)
            else:
                orders = _orders

            if self.app:
                self.app.logger.info(f"从Shopify获取到 {len(orders)} 个订单")

            stats = {"total_fetched": len(orders), "new_orders": 0, "updated_orders": 0, "errors": 0}

            if len(orders) == 0:
                if self.app:
                    self.app.logger.info("没有找到需要同步的订单")
                return stats

            # 分批处理订单，避免长时间锁定数据库
            batch_size = 5  # 减少批次大小
            total_batches = (len(orders) + batch_size - 1) // batch_size

            if self.app:
                self.app.logger.info(f"开始分批处理订单：共 {total_batches} 批，每批 {batch_size} 个")

            for i in range(0, len(orders), batch_size):
                batch = orders[i : i + batch_size]
                current_batch = (i // batch_size) + 1

                if self.app:
                    self.app.logger.info(
                        f"处理第 {current_batch}/{total_batches} 批订单 ({len(batch)} 个订单)"
                    )

                # 在每个批次之间添加短暂延迟
                if i > 0:
                    time.sleep(0.1)

                for shopify_order in batch:
                    retry_count = 0
                    max_retries = 5  # 增加重试次数
                    if self.app:
                        self.app.logger.debug(f"Processing order {shopify_order.id}")

                    while retry_count < max_retries:
                        try:
                            # 在每次重试前创建新的事务
                            if retry_count > 0:
                                db.session.rollback()
                                time.sleep(0.5 * retry_count)  # 增加延迟时间

                            # 检查是否为新订单
                            existing_order = Order.query.filter_by(
                                shopify_order_id=str(shopify_order.id)
                            ).first()
                            is_new_order = existing_order is None

                            self._process_order(shopify_order)

                            if is_new_order:
                                stats["new_orders"] += 1
                            else:
                                stats["updated_orders"] += 1
                            break

                        except OperationalError as e:
                            if "database is locked" in str(e).lower() and retry_count < max_retries - 1:
                                retry_count += 1
                                if self.app:
                                    self.app.logger.warning(
                                        f"Database locked, retrying order {shopify_order.id} (attempt {retry_count})"
                                    )
                                continue
                            else:
                                if self.app:
                                    self.app.logger.error(
                                        f"Error processing order {shopify_order.id}: {str(e)}"
                                    )
                                stats["errors"] += 1
                                db.session.rollback()
                                break
                        except Exception as e:
                            if self.app:
                                self.app.logger.error(f"Error processing order {shopify_order.id}: {str(e)}")
                            stats["errors"] += 1
                            db.session.rollback()
                            break

                # 每批次提交一次，带重试机制
                commit_retry_count = 0
                max_commit_retries = 3

                while commit_retry_count < max_commit_retries:
                    try:
                        db.session.commit()
                        if self.app:
                            self.app.logger.info(f"第 {current_batch} 批订单处理完成")
                        break
                    except OperationalError as e:
                        if (
                            "database is locked" in str(e).lower()
                            and commit_retry_count < max_commit_retries - 1
                        ):
                            commit_retry_count += 1
                            time.sleep(0.5 * commit_retry_count)  # 递增延迟
                            db.session.rollback()
                            if self.app:
                                self.app.logger.warning(
                                    f"Database locked during batch commit (attempt {commit_retry_count})"
                                )
                        else:
                            db.session.rollback()
                            if self.app:
                                self.app.logger.error(
                                    f"Failed to commit batch after {max_commit_retries} attempts: {str(e)}"
                                )
                            raise

            if self.app:
                self.app.logger.info(f"Order sync completed: {stats}")
            # 新同步进来的订单如果正好有仓库现货，立刻绑定上去，
            # 否则它会以「缺货」身份出现在采购管理的待采购里，导致重复采购同一件商品。
            if _auto_bind:
                self._auto_bind_available_stock(stats.get("new_orders", 0))
            return stats

        except Exception as e:
            db.session.rollback()
            if self.app:
                self.app.logger.error(f"Order sync failed: {str(e)}")
            raise

    def _auto_bind_available_stock(self, new_orders: int) -> None:
        """同步完订单后，把现有可用库存分配给还缺货的订单。

        只在有**新订单**时才做（没有新需求就是白跑一遍全店铺库存）。
        绑定失败不影响订单同步本身，只记录日志——订单已经落库了，
        下次同步或者手工入库时还会再试。
        """
        if not new_orders:
            return
        try:
            from app.services.inventory_service import auto_allocate_stock

            allocated = auto_allocate_stock(self.shop_id)
            if allocated and self.app:
                self.app.logger.info(
                    "同步后自动绑定库存：%s 笔订单 / %s 件",
                    len({row["order_id"] for row in allocated}),
                    sum(row["quantity"] for row in allocated),
                )
        except Exception as exc:  # noqa: BLE001 - 绑定失败不能连累订单同步
            db.session.rollback()
            if self.app:
                self.app.logger.warning(f"同步后自动绑定库存失败：{exc}")

    def _process_order(self, shopify_order) -> Order:
        """处理单个订单数据"""
        # 使用no_autoflush避免自动刷新导致的数据库锁定
        with db.session.no_autoflush:
            # 检查订单是否已存在
            prefetched_orders = getattr(self, "_prefetched_orders", None)
            existing_order = prefetched_orders.get(str(shopify_order.id)) if prefetched_orders is not None else (
                self._scope_filter(Order).filter_by(shopify_order_id=str(shopify_order.id)).first()
            )

            if existing_order:
                order = existing_order
            else:
                order = Order()
                order.shopify_order_id = str(shopify_order.id)
                order.shop_id = self.shop_id

            # 更新订单基本信息
            from decimal import Decimal

            order.order_number = shopify_order.order_number
            customer_info = extract_customer_info(shopify_order)
            order.customer_email = customer_info["email"]
            order.customer_name = customer_info["name"]
            order.customer_phone = customer_info["phone"]
            order.shipping_address = customer_info["shipping_address"]
            order.note = getattr(shopify_order, "note", None)
            order.fulfillments = extract_fulfillments(shopify_order)
            order.total_price = Decimal(str(shopify_order.total_price))
            order.subtotal_price = (
                Decimal(str(shopify_order.subtotal_price))
                if hasattr(shopify_order, "subtotal_price") and shopify_order.subtotal_price
                else Decimal(str(shopify_order.total_price))
            )
            order.total_tax = (
                Decimal(str(shopify_order.total_tax))
                if hasattr(shopify_order, "total_tax") and shopify_order.total_tax
                else Decimal("0")
            )
            order.shipping_price = (
                Decimal(str(shopify_order.total_shipping_price_set.shop_money.amount))
                if hasattr(shopify_order, "total_shipping_price_set")
                and shopify_order.total_shipping_price_set
                else Decimal("0")
            )
            order.currency = shopify_order.currency
            order.presentment_currency = (
                getattr(shopify_order, "presentment_currency", None) or shopify_order.currency
            )
            order.financial_status = shopify_order.financial_status
            cancelled_at = getattr(shopify_order, "cancelled_at", None)
            order.cancelled_at = utc_naive_from_iso(cancelled_at) if cancelled_at else None
            order.fulfillment_status = shopify_order.fulfillment_status or "unfulfilled"
            order.order_date = utc_naive_from_iso(shopify_order.created_at)
            order.created_at = utc_naive_from_iso(shopify_order.created_at)
            order.updated_at = utc_naive_from_iso(shopify_order.updated_at)

            # 处理订单商品
            total_cost = Decimal("0")
            # 确保line_items是可迭代的
            line_items = shopify_order.line_items
            if hasattr(line_items, "__call__"):
                line_items = line_items()
            elif not hasattr(line_items, "__iter__"):
                line_items = []

            for line_item in line_items:
                current_quantity = getattr(line_item, "current_quantity", None)
                if current_quantity is None:
                    current_quantity = getattr(line_item, "quantity", 0)
                current_quantity = int(current_quantity or 0)
                if current_quantity <= 0:
                    continue
                # 同步商品信息
                product = (
                    self._sync_product(line_item)
                    if getattr(line_item, "product_id", None) and getattr(line_item, "variant_id", None)
                    else None
                )
                if product and product.cost:
                    total_cost += Decimal(str(product.cost)) * Decimal(current_quantity)

            order.product_cost = total_cost

            # 商品明细（页面上的「商品明细」表就是读这张表）
            attributes = getattr(shopify_order, "attributes", None)
            if isinstance(attributes, dict) and "line_items" in attributes:
                self._sync_order_items(order, extract_line_items(shopify_order))

            if not existing_order:
                db.session.add(order)
                # 新订单需要先flush以获取order.id供payment关联
                db.session.flush()
                # 新订单额外补齐变体图/商品主图；失败不阻断主同步。
                self._enrich_order_details(order)

        # 处理支付信息
        transactions = getattr(shopify_order, "transactions", [])
        if hasattr(transactions, "__call__"):
            transactions = transactions()
        elif not hasattr(transactions, "__iter__"):
            transactions = []

        if self.app:
            self.app.logger.debug(f"订单 {order.order_number} 交易对象类型: {type(transactions).__name__}")

        if transactions:
            if self.app:
                self.app.logger.info(f"订单 {order.order_number} 有 {len(transactions)} 个交易")

            for transaction in transactions:
                if self.app:
                    self.app.logger.info(
                        f"交易详情: status={transaction.status}, kind={transaction.kind}, gateway={getattr(transaction, 'gateway', 'unknown')}"
                    )

                # 处理成功的sale交易（直接支付）或capture交易（PayPal授权后捕获）
                if transaction.status == "success" and transaction.kind in ["sale", "capture"]:
                    payment = self._process_payment(order, transaction)
                    # 将支付方式同步到订单表
                    if payment and payment.payment_method:
                        order.payment_method = payment.payment_method
                        # 计算并更新支付手续费
                        if payment.total_fee:
                            from decimal import Decimal

                            order.payment_fee = Decimal(str(payment.total_fee))
                        if self.app:
                            self.app.logger.info(
                                f"订单 {order.order_number} 设置支付方式: {payment.payment_method}, 手续费: {payment.total_fee}"
                            )
                elif transaction.kind == "refund":
                    self._process_refund(order, transaction)
        else:
            if self.app:
                self.app.logger.warning(f"订单 {order.order_number} 没有交易数据")

        # 退款交易可能仍在处理中，网关结算流水要稍后才出现。
        # 先写入退款标记，再按当前订单金额限制仍为正数的旧结算金额。
        db.session.flush()
        _refresh_order_settlement(order)
        order.calculate_actual_received()
        order.calculate_profit()

        fetched_at = getattr(shopify_order, "_details_refreshed_at", None)
        if isinstance(fetched_at, datetime):
            order.shopify_details_synced_at = fetched_at

        return order

    def _process_refund(self, order: Order, transaction):
        """保存退款交易状态；真正结算金额仍以网关流水为准。"""
        transaction_id = str(transaction.id)
        payment = Payment.query.filter_by(order_id=order.id, transaction_id=transaction_id).first()
        if payment is None:
            payment = Payment(order_id=order.id, shop_id=order.shop_id or self.shop_id)
            payment.transaction_id = transaction_id
            db.session.add(payment)
        payment.payment_method = "shopify_refund"
        payment.amount = -abs(Decimal(str(transaction.amount or 0)))
        payment.currency = getattr(transaction, "currency", None) or order.currency
        payment.status = transaction.status
        payment.fee_source = "estimated"
        payment.payment_date = (
            utc_naive_from_iso(transaction.processed_at) if transaction.processed_at else datetime.utcnow()
        )

    def _enrich_order_details(self, order):
        """为新订单补齐 REST 不提供的图片及详情字段。"""
        if not self.shop_id:
            return
        try:
            from app.models import Shop
            from app.services.shopify_graphql import fetch_order_details

            shop = db.session.get(Shop, self.shop_id)
            if shop is None:
                return
            details = fetch_order_details(shop, order.shopify_order_id)
            order.note = details.get("note")
            order.fulfillments = details.get("fulfillments") or order.fulfillments or []
            if details.get("fulfillment_status"):
                order.fulfillment_status = details["fulfillment_status"]
            item_by_id = {item.shopify_line_item_id: item for item in order.items}
            for row in details.get("line_items") or []:
                item = item_by_id.get(row.get("shopify_line_item_id"))
                if item is not None and row.get("image_url"):
                    item.image_url = row["image_url"]
        except Exception as exc:  # noqa: BLE001 - 图片补充失败不应让订单同步整体失败
            if self.app:
                self.app.logger.warning("订单 %s 的图片/物流详情补充失败: %s", order.order_number, exc)

    def _sync_order_items(self, order: Order, rows):
        """Keep current quantities while retaining removed rows for historical links."""
        existing = {item.shopify_line_item_id: item for item in order.items}
        returned_ids = {row["shopify_line_item_id"] for row in rows}
        for item in order.items:
            if item.shopify_line_item_id not in returned_ids:
                item.quantity = 0
        for row in rows:
            item = existing.get(row["shopify_line_item_id"])
            if item is None:
                item = OrderItem(
                    shop_id=order.shop_id or self.shop_id,
                    shopify_line_item_id=row["shopify_line_item_id"],
                )
                order.items.append(item)
            variant_changed = item.shopify_variant_id != row["shopify_variant_id"]
            item.shopify_product_id = row["shopify_product_id"]
            item.shopify_variant_id = row["shopify_variant_id"]
            item.title = row["title"]
            item.variant_title = row["variant_title"]
            item.sku = row["sku"]
            item.vendor = row["vendor"]
            if variant_changed or row.get("image_url"):
                item.image_url = row["image_url"]
            item.quantity = row["quantity"]
            item.price = row["price"]
            item.total_discount = row["total_discount"]

        # An edited-away item no longer needs its manually reserved warehouse stock.
        from app.models import InventoryReservation
        from app.services.purchase_service import _group_key

        for item in order.items:
            if item.id is None:
                continue
            prefetched_reservations = getattr(self, "_prefetched_reservations", None)
            reservation = prefetched_reservations.get(item.id) if prefetched_reservations is not None else (
                InventoryReservation.query.filter_by(
                shop_id=order.shop_id or self.shop_id, order_item_id=item.id
                ).first()
            )
            if reservation and reservation.group_key != json.dumps(_group_key(item), ensure_ascii=False):
                db.session.delete(reservation)
            elif reservation and reservation.quantity > int(item.quantity or 0):
                if int(item.quantity or 0) == 0:
                    db.session.delete(reservation)
                else:
                    reservation.quantity = int(item.quantity)

    def _sync_product(self, line_item) -> Optional[Product]:
        """同步商品信息"""
        try:
            # 使用no_autoflush避免自动刷新导致的数据库锁定
            with db.session.no_autoflush:
                # 检查商品是否已存在（包括当前会话中的未提交对象）
                product_cache = getattr(self, "_prefetched_products", None)
                product_key = (str(line_item.product_id), str(line_item.variant_id))
                existing_product = product_cache.get(product_key) if product_cache is not None else None
                # A cache miss is not proof of absence (e.g. another sync has
                # inserted the variant after preloading).
                if existing_product is None:
                    existing_product = self._scope_filter(Product)
                    existing_product = existing_product.filter_by(
                        shopify_product_id=str(line_item.product_id),
                        shopify_variant_id=str(line_item.variant_id),
                    ).first()

                if existing_product:
                    product = existing_product
                else:
                    # 使用merge来避免重复插入
                    product = Product(
                        shop_id=self.shop_id,
                        shopify_product_id=str(line_item.product_id),
                        shopify_variant_id=str(line_item.variant_id),
                        cost=0.0,  # 默认成本，需要手动配置
                        product_type="phone_case",  # 默认为手机壳
                    )

                # 更新商品信息
                # Order line titles are immutable purchase-time snapshots, not
                # authoritative catalog names. Old orders must not undo a rename.
                if not existing_product:
                    product.title = line_item.title
                product.variant_title = line_item.variant_title
                product.sku = line_item.sku
                product.price = Decimal(str(line_item.price))
                product.vendor = line_item.vendor

            # 使用merge来处理新增或更新，添加重试机制
            retry_count = 0
            max_retries = 3

            while retry_count < max_retries:
                try:
                    product = db.session.merge(product)
                    if product_cache is not None:
                        product_cache[product_key] = product
                    return product
                except OperationalError as e:
                    if "database is locked" in str(e) and retry_count < max_retries - 1:
                        retry_count += 1
                        time.sleep(0.05 * retry_count)
                        db.session.rollback()
                        current_app.logger.warning(
                            f"Database locked while syncing product {line_item.product_id}, retrying (attempt {retry_count})"
                        )
                        continue
                    else:
                        current_app.logger.error(f"Error syncing product {line_item.product_id}: {str(e)}")
                        return None
                except Exception as e:
                    current_app.logger.error(f"Error syncing product {line_item.product_id}: {str(e)}")
                    return None

            return None

        except Exception as e:
            if self.app:
                self.app.logger.error(f"Error syncing product {line_item.product_id}: {str(e)}")
            return None

    def _process_payment(self, order: Order, transaction) -> Optional[Payment]:
        """处理支付信息"""
        try:
            # 使用no_autoflush避免自动刷新导致的数据库锁定
            with db.session.no_autoflush:
                # 检查支付记录是否已存在
                existing_payment = Payment.query.filter_by(
                    order_id=order.id, transaction_id=str(transaction.id)
                ).first()

                if existing_payment:
                    payment = existing_payment
                else:
                    payment = Payment()
                    payment.order_id = order.id
                    payment.transaction_id = str(transaction.id)
                    payment.shop_id = order.shop_id if order.shop_id else self.shop_id

                # 更新支付信息
                gateway = transaction.gateway or "unknown"
                # 统一支付方式命名
                if "stripe" in gateway.lower():
                    payment.payment_method = "stripe"
                elif "paypal" in gateway.lower():
                    payment.payment_method = "paypal"
                elif "shopify" in gateway.lower():
                    payment.payment_method = "shopify_payment"
                else:
                    payment.payment_method = gateway

                payment.amount = float(transaction.amount)
                # 买家实际扣款币种（可能与订单/店铺币种不同，例如订单记 USD、买家付 AUD）
                payment.currency = getattr(transaction, "currency", None) or order.currency
                shop_amount = getattr(transaction, "shop_amount", None)
                if shop_amount is not None and getattr(transaction, "shop_currency", None) == order.currency:
                    # 原始收款的 Shopify 店铺币种金额；退款后的 currentTotalPrice
                    # 已变小，不能再拿它倒推原始收款金额。
                    payment.amount_in_order_currency = Decimal(str(shop_amount))
                payment.status = transaction.status
                payment.payment_date = (
                    utc_naive_from_iso(transaction.processed_at)
                    if transaction.processed_at
                    else datetime.utcnow()
                )

                # 已有真实费用的支付不再重算，避免覆盖网关数据
                if (
                    existing_payment
                    and existing_payment.fee_source == "gateway"
                    and existing_payment.real_fee is not None
                ):
                    return existing_payment

                if not existing_payment:
                    # 先入会话，稍后 calculate_actual_received 才能查到这条支付
                    db.session.add(payment)

                # 优先用网关回执里的真实费用（PayPal 会把明细放在 receipt 里）
                breakdown = extract_paypal_breakdown(getattr(transaction, "receipt", None))
                if breakdown:
                    if apply_gateway_fee(
                        payment,
                        order,
                        fee=breakdown["fee"],
                        fee_currency=breakdown["fee_currency"],
                        net=breakdown["net"],
                        settlement_amount=breakdown["settlement_amount"],
                        settlement_currency=breakdown["settlement_currency"],
                        exchange_rate=breakdown["exchange_rate"],
                        capture_id=breakdown["capture_id"],
                        gateway_reference=breakdown["gateway_reference"],
                        amount_in_order_currency=payment.amount_in_order_currency,
                    ):
                        return payment

                # 根据支付方式从FeeConfig获取费用配置
                fee_config = None
                if payment.payment_method == "paypal":
                    fee_config = FeeConfig.query.filter_by(
                        fee_type="payment", fee_name="PayPal手续费", is_active=True
                    ).first()
                elif payment.payment_method == "stripe":
                    fee_config = FeeConfig.query.filter_by(
                        fee_type="payment", fee_name="Stripe手续费", is_active=True
                    ).first()
                elif payment.payment_method == "shopify_payment":
                    fee_config = FeeConfig.query.filter_by(
                        fee_type="payment", fee_name="Shopify支付手续费", is_active=True
                    ).first()

                # 设置手续费配置
                if fee_config:
                    if fee_config.calculation_method == "percentage":
                        payment.fee_percentage = float(fee_config.percentage_rate)
                        payment.fee_fixed = float(fee_config.fixed_amount or 0)
                    elif fee_config.calculation_method == "fixed":
                        payment.fee_fixed = float(fee_config.fixed_amount)
                        payment.fee_percentage = 0
                    elif fee_config.calculation_method == "percentage_plus_fixed":
                        payment.fee_percentage = float(fee_config.percentage_rate)
                        payment.fee_fixed = float(fee_config.fixed_amount or 0)

                    # 使用FeeConfig的多币种计算方法
                    try:
                        # 费率表按「买家扣款币种」计价（PayPal 实测就是扣款币种收 4.40% + 固定费），
                        # 所以先在扣款币种下算费用，再折合到订单币种。
                        calculated_fee = round(
                            float(payment.amount) * float(payment.fee_percentage or 0) / 100
                            + float(payment.fee_fixed or 0),
                            2,
                        )
                        if not apply_estimated_fee(payment, order, calculated_fee, payment.currency):
                            if self.app:
                                self.app.logger.warning(
                                    "订单 %s 的 %s -> %s 汇率不可用，暂不估算手续费",
                                    order.order_number,
                                    payment.currency,
                                    order.currency,
                                )
                    except Exception as fee_error:
                        if self.app:
                            self.app.logger.error(f"费用计算失败: {str(fee_error)}，使用原始金额计算费用")
                        # 如果费用计算失败，使用原有的计算方法
                        payment.calculate_fee()
                else:
                    # 如果没有费用配置，使用原有的计算方法
                    payment.calculate_fee()
                    payment.fee_source = "estimated"

            return payment

        except Exception as e:
            if self.app:
                self.app.logger.error(f"Error processing payment {transaction.id}: {str(e)}")
            # 数据库一旦报错，事务就进入 aborted 状态；必须抛出去让上层 rollback，
            # 否则后续每个订单都会连锁失败（历史上就踩过这个坑）。
            raise

    def sync_products(self, limit: Optional[int] = None) -> Dict[str, int]:
        """同步商品数据"""
        try:
            from app.models import Shop

            shop = db.session.get(Shop, self.shop_id)
            if shop is None:
                raise ValueError("店铺不存在，无法同步商品")
            products = []
            cursor = None
            while limit is None or len(products) < limit:
                nodes, page_info = fetch_products_page(
                    shop, first=25 if limit is None else min(25, limit - len(products)), after=cursor
                )
                for node in nodes:
                    variant_connection = node.get("variants") or {}
                    variant_rows = list(variant_connection.get("nodes") or [])
                    variant_page = variant_connection.get("pageInfo") or {}
                    if variant_page.get("hasNextPage") and variant_page.get("endCursor"):
                        variant_rows.extend(
                            fetch_remaining_variants(shop, node["id"], variant_page["endCursor"])
                        )
                    products.append(
                        WebhookResource(
                            {
                                "id": _gid_number(node.get("id")),
                                "title": node.get("title"),
                                "product_type": node.get("productType"),
                                "vendor": node.get("vendor"),
                                "status": str(node.get("status") or "").lower(),
                                "image_url": line_item_image({"product": node}),
                                "variants": [
                                    {
                                        "id": _gid_number(variant.get("id")),
                                        "title": variant.get("title"),
                                        "sku": variant.get("sku"),
                                        "price": variant.get("price"),
                                        "inventory_quantity": variant.get("inventoryQuantity"),
                                        "image_url": line_item_image({**variant, "product": node}),
                                    }
                                    for variant in variant_rows
                                ],
                            }
                        )
                    )
                cursor = page_info.get("endCursor")
                if not nodes or not page_info.get("hasNextPage") or not cursor:
                    break

            stats = {"total_fetched": len(products), "new_products": 0, "updated_products": 0, "errors": 0}

            from app.services.product_naming_service import record_catalog_names
            record_catalog_names(self.shop_id, {str(product.id): product.title for product in products})

            for shopify_product in products:
                try:
                    # 确保variants是可迭代的
                    variants = shopify_product.variants
                    if hasattr(variants, "__call__"):
                        variants = variants()
                    elif not hasattr(variants, "__iter__"):
                        variants = []

                    existing_variants = {
                        item.shopify_variant_id: item for item in self._scope_filter(Product).filter_by(
                            shopify_product_id=str(shopify_product.id),
                        ).all()
                    }

                    for variant in variants:
                        existing_product = existing_variants.get(str(variant.id))

                        if existing_product:
                            product = existing_product
                            stats["updated_products"] += 1
                        else:
                            product = Product()
                            product.shop_id = self.shop_id
                            product.shopify_product_id = str(shopify_product.id)
                            product.shopify_variant_id = str(variant.id)
                            existing_variants[product.shopify_variant_id] = product
                            stats["new_products"] += 1

                        # 更新商品信息
                        product.title = shopify_product.title
                        product.variant_title = variant.title
                        product.image_url = variant.image_url or shopify_product.image_url or product.image_url
                        product.sku = variant.sku
                        product.price = float(variant.price)
                        product.inventory_quantity = variant.inventory_quantity
                        product.product_type = shopify_product.product_type or "phone_case"
                        product.vendor = shopify_product.vendor
                        product.status = "active" if shopify_product.status == "active" else "inactive"

                        if not existing_product:
                            product.cost = 0.0  # 默认成本，需要手动配置
                            db.session.add(product)

                except Exception as e:
                    if self.app:
                        self.app.logger.error(f"Error processing product {shopify_product.id}: {str(e)}")
                    stats["errors"] += 1

            db.session.commit()
            if self.app:
                self.app.logger.info(f"Product sync completed: {stats}")
            return stats

        except Exception as e:
            db.session.rollback()
            if self.app:
                self.app.logger.error(f"Product sync failed: {str(e)}")
            raise

    def get_shop_info(self) -> Dict:
        """获取店铺信息"""
        try:
            if self._last_shop_info is not None:
                return self._last_shop_info
            from app.models import Shop

            linked_shop = db.session.get(Shop, self.shop_id)
            if linked_shop is None:
                return {}
            shop = fetch_shop_info(linked_shop)
            self._last_shop_info = {
                "name": shop.get("name"),
                "email": shop.get("email"),
                "domain": (shop.get("primaryDomain") or {}).get("host"),
                "currency": shop.get("currencyCode"),
                "timezone": shop.get("ianaTimezone"),
                "plan_name": (shop.get("plan") or {}).get("publicDisplayName"),
            }
            return self._last_shop_info
        except Exception as e:
            if self.app:
                self.app.logger.error(f"Failed to get shop info: {str(e)}")
            return {}

    def sync_recent_orders(self, hours: int = 24) -> Dict[str, int]:
        """同步最近的订单（用于定时任务）

        Args:
            hours: 同步多少小时内的订单

        Returns:
            Dict包含同步统计信息
        """
        try:
            # 计算同步的起始时间
            since_date = datetime.utcnow() - timedelta(hours=hours)

            if self.app:
                self.app.logger.info(
                    f"开始同步最近订单：从 {since_date.strftime('%Y-%m-%d %H:%M:%S')} 开始，最近 {hours} 小时"
                )

            # 按更新时间拉取：旧订单被编辑、退款或改商品时也能被再次同步。
            orders = self._fetch_updated_graphql_orders(since_date)

            if self.app:
                self.app.logger.info(f"从Shopify获取到 {len(orders)} 个最近订单")

            stats = {"total_fetched": len(orders), "new_orders": 0, "updated_orders": 0, "errors": 0}

            if len(orders) == 0:
                if self.app:
                    self.app.logger.info("没有找到需要同步的最近订单")
                return stats

            # 分批处理并分批提交，降低SQLite锁冲突
            batch_size = 5
            total_batches = (len(orders) + batch_size - 1) // batch_size

            if self.app:
                self.app.logger.info(f"开始分批处理最近订单：共 {total_batches} 批，每批 {batch_size} 个")

            for i in range(0, len(orders), batch_size):
                batch = orders[i : i + batch_size]
                current_batch = (i // batch_size) + 1

                if self.app:
                    self.app.logger.info(
                        f"处理最近订单第 {current_batch}/{total_batches} 批 ({len(batch)} 个订单)"
                    )

                if i > 0:
                    time.sleep(0.1)

                for shopify_order in batch:
                    retry_count = 0
                    max_retries = 5

                    while retry_count < max_retries:
                        try:
                            if retry_count > 0:
                                db.session.rollback()
                                time.sleep(0.5 * retry_count)

                            existing_order = Order.query.filter_by(
                                shopify_order_id=str(shopify_order.id)
                            ).first()
                            is_new_order = existing_order is None
                            self._process_order(shopify_order)

                            if is_new_order:
                                stats["new_orders"] += 1
                            else:
                                stats["updated_orders"] += 1
                            break
                        except OperationalError as e:
                            if "database is locked" in str(e).lower() and retry_count < max_retries - 1:
                                retry_count += 1
                                if self.app:
                                    self.app.logger.warning(
                                        f"Database locked, retrying recent order {shopify_order.id} (attempt {retry_count})"
                                    )
                                continue

                            if self.app:
                                self.app.logger.error(
                                    f"Error processing recent order {shopify_order.id}: {str(e)}"
                                )
                            stats["errors"] += 1
                            db.session.rollback()
                            break
                        except Exception as e:
                            if self.app:
                                self.app.logger.error(
                                    f"Error processing recent order {shopify_order.id}: {str(e)}"
                                )
                            stats["errors"] += 1
                            db.session.rollback()
                            break

                commit_retry_count = 0
                max_commit_retries = 3

                while commit_retry_count < max_commit_retries:
                    try:
                        db.session.commit()
                        break
                    except OperationalError as e:
                        if (
                            "database is locked" in str(e).lower()
                            and commit_retry_count < max_commit_retries - 1
                        ):
                            commit_retry_count += 1
                            time.sleep(0.5 * commit_retry_count)
                            db.session.rollback()
                            if self.app:
                                self.app.logger.warning(
                                    f"Database locked during recent batch commit (attempt {commit_retry_count})"
                                )
                        else:
                            db.session.rollback()
                            if self.app:
                                self.app.logger.error(
                                    f"Failed to commit recent batch after {max_commit_retries} attempts: {str(e)}"
                                )
                            raise

            if self.app:
                self.app.logger.info(f"Recent orders sync completed: {stats}")
            return stats

        except Exception as e:
            db.session.rollback()
            if self.app:
                self.app.logger.error(f"Recent orders sync failed: {str(e)}")
            raise


# 创建全局服务实例（延迟初始化）
shopify_service = ShopifyService()
