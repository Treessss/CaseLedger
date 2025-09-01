import shopify
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
from app.models.fee_config import FeeConfig
from app import db


class ShopifyService:
    """Shopify API集成服务"""
    
    def __init__(self, app=None):
        self.app = None
        self.api_key = None
        self.api_secret = None
        self.shop_url = None
        self.access_token = None
        
        if app is not None:
            self.init_app(app)
    
    def init_app(self, app):
        """Initialize the service with Flask app"""
        self.app = app
        self.api_key = app.config.get('SHOPIFY_API_KEY')
        self.api_secret = app.config.get('SHOPIFY_API_SECRET')
        self.shop_url = app.config.get('SHOPIFY_SHOP_URL')
        self.access_token = app.config.get('SHOPIFY_ACCESS_TOKEN')
        
        if not all([self.api_key, self.api_secret, self.shop_url, self.access_token]):
            app.logger.warning("Shopify configuration incomplete")
        else:
            self._init_shopify_session()
    
    def _init_shopify_session(self):
        """初始化Shopify API会话"""
        try:
            # 确保shop_url格式正确
            if not self.shop_url.endswith('.myshopify.com'):
                if '.' not in self.shop_url:
                    # 如果只是shop名称，添加.myshopify.com
                    formatted_shop_url = f"{self.shop_url}.myshopify.com"
                else:
                    # 如果已经有域名，直接使用
                    formatted_shop_url = self.shop_url
            else:
                formatted_shop_url = self.shop_url
            
            # 使用正确的Shopify API认证方式 - 只使用访问令牌
            shopify.ShopifyResource.set_site(f"https://{formatted_shop_url}/admin/api/2023-10")
            shopify.ShopifyResource.activate_session(shopify.Session(formatted_shop_url, '2023-10', self.access_token))
            if self.app:
                self.app.logger.info(f"Shopify API session initialized successfully for {formatted_shop_url}")
        except Exception as e:
            if self.app:
                self.app.logger.error(f"Failed to initialize Shopify session: {str(e)}")
            raise
    
    def test_connection(self) -> bool:
        """测试Shopify API连接"""
        try:
            shop = shopify.Shop.current()
            if self.app:
                self.app.logger.info(f"Connected to shop: {shop.name}")
            return True
        except Exception as e:
            if self.app:
                self.app.logger.error(f"Shopify connection test failed: {str(e)}")
            return False
    
    def sync_orders(self, days_back: int = 30, limit: int = 250) -> Dict[str, int]:
        """同步订单数据
        
        Args:
            days_back: 同步多少天前的订单
            limit: 每次请求的订单数量限制
            
        Returns:
            Dict包含同步统计信息
        """
        try:
            # 计算同步的起始日期
            since_date = datetime.now() - timedelta(days=days_back)
            
            if self.app:
                self.app.logger.info(f"开始同步订单：从 {since_date.strftime('%Y-%m-%d')} 开始，限制 {limit} 个订单")
            
            # 获取订单
            orders = shopify.Order.find(
                status='any',
                created_at_min=since_date.isoformat(),
                limit=limit
            )
            
            if self.app:
                self.app.logger.info(f"从Shopify获取到 {len(orders)} 个订单")
            
            stats = {
                'total_fetched': len(orders),
                'new_orders': 0,
                'updated_orders': 0,
                'errors': 0
            }
            
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
                batch = orders[i:i + batch_size]
                current_batch = (i // batch_size) + 1
                
                if self.app:
                    self.app.logger.info(f"处理第 {current_batch}/{total_batches} 批订单 ({len(batch)} 个订单)")
                
                # 在每个批次之间添加短暂延迟
                if i > 0:
                    time.sleep(0.1)
                
                for shopify_order in batch:
                    retry_count = 0
                    max_retries = 5  # 增加重试次数
                    
                    while retry_count < max_retries:
                        try:
                            # 在每次重试前创建新的事务
                            if retry_count > 0:
                                db.session.rollback()
                                time.sleep(0.5 * retry_count)  # 增加延迟时间
                            
                            # 检查是否为新订单
                            existing_order = Order.query.filter_by(shopify_order_id=shopify_order.id).first()
                            is_new_order = existing_order is None
                            
                            self._process_order(shopify_order)
                            
                            if is_new_order:
                                stats['new_orders'] += 1
                            else:
                                stats['updated_orders'] += 1
                            break
                            
                        except OperationalError as e:
                            if "database is locked" in str(e) and retry_count < max_retries - 1:
                                retry_count += 1
                                if self.app:
                                    self.app.logger.warning(f"Database locked, retrying order {shopify_order.id} (attempt {retry_count})")
                                continue
                            else:
                                if self.app:
                                    self.app.logger.error(f"Error processing order {shopify_order.id}: {str(e)}")
                                stats['errors'] += 1
                                db.session.rollback()
                                break
                        except Exception as e:
                            if self.app:
                                self.app.logger.error(f"Error processing order {shopify_order.id}: {str(e)}")
                            stats['errors'] += 1
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
                        if "database is locked" in str(e) and commit_retry_count < max_commit_retries - 1:
                            commit_retry_count += 1
                            time.sleep(0.5 * commit_retry_count)  # 递增延迟
                            db.session.rollback()
                            if self.app:
                                self.app.logger.warning(f"Database locked during batch commit (attempt {commit_retry_count})")
                        else:
                            db.session.rollback()
                            if self.app:
                                self.app.logger.error(f"Failed to commit batch after {max_commit_retries} attempts: {str(e)}")
                            break
            
            if self.app:
                self.app.logger.info(f"Order sync completed: {stats}")
            return stats
            
        except Exception as e:
            db.session.rollback()
            if self.app:
                self.app.logger.error(f"Order sync failed: {str(e)}")
            raise
    
    def _process_order(self, shopify_order) -> Order:
        """处理单个订单数据"""
        # 使用no_autoflush避免自动刷新导致的数据库锁定
        with db.session.no_autoflush:
            # 检查订单是否已存在
            existing_order = Order.query.filter_by(shopify_order_id=shopify_order.id).first()
            
            if existing_order:
                order = existing_order
            else:
                order = Order()
                order.shopify_order_id = shopify_order.id
            
            # 更新订单基本信息
            from decimal import Decimal
            order.order_number = shopify_order.order_number
            order.customer_email = shopify_order.email
            order.customer_name = f"{shopify_order.billing_address.first_name if shopify_order.billing_address else ''} {shopify_order.billing_address.last_name if shopify_order.billing_address else ''}".strip()
            order.total_price = Decimal(str(shopify_order.total_price))
            order.subtotal_price = Decimal(str(shopify_order.subtotal_price)) if hasattr(shopify_order, 'subtotal_price') and shopify_order.subtotal_price else Decimal(str(shopify_order.total_price))
            order.total_tax = Decimal(str(shopify_order.total_tax)) if hasattr(shopify_order, 'total_tax') and shopify_order.total_tax else Decimal('0')
            order.shipping_price = Decimal(str(shopify_order.total_shipping_price_set.shop_money.amount)) if hasattr(shopify_order, 'total_shipping_price_set') and shopify_order.total_shipping_price_set else Decimal('0')
            order.currency = shopify_order.currency
            order.financial_status = shopify_order.financial_status
            order.fulfillment_status = shopify_order.fulfillment_status or 'unfulfilled'
            order.order_date = datetime.fromisoformat(shopify_order.created_at.replace('Z', '+00:00'))
            order.created_at = datetime.fromisoformat(shopify_order.created_at.replace('Z', '+00:00'))
            order.updated_at = datetime.fromisoformat(shopify_order.updated_at.replace('Z', '+00:00'))
            
            # 处理订单商品
            total_cost = Decimal('0')
            # 确保line_items是可迭代的
            line_items = shopify_order.line_items
            if hasattr(line_items, '__call__'):
                line_items = line_items()
            elif not hasattr(line_items, '__iter__'):
                line_items = []
                
            for line_item in line_items:
                # 同步商品信息
                product = self._sync_product(line_item)
                if product and product.cost:
                    total_cost += Decimal(str(product.cost)) * Decimal(str(line_item.quantity))
            
            order.product_cost = total_cost
            
            # 计算利润
            order.calculate_profit()
            
            if not existing_order:
                db.session.add(order)
        
        # 手动flush订单以获取order.id，但不提交事务
        db.session.flush()
        
        # 处理支付信息
        transactions = shopify_order.transactions
        if self.app:
            self.app.logger.info(f"订单 {order.order_number} 的交易数据: {transactions}")
        
        if transactions:
            # 确保transactions是可迭代的
            if hasattr(transactions, '__call__'):
                transactions = transactions()
            elif not hasattr(transactions, '__iter__'):
                transactions = []
            
            if self.app:
                self.app.logger.info(f"订单 {order.order_number} 有 {len(transactions)} 个交易")
                
            for transaction in transactions:
                if self.app:
                    self.app.logger.info(f"交易详情: status={transaction.status}, kind={transaction.kind}, gateway={getattr(transaction, 'gateway', 'unknown')}")
                
                # 处理成功的sale交易（直接支付）或capture交易（PayPal授权后捕获）
                if transaction.status == 'success' and transaction.kind in ['sale', 'capture']:
                    payment = self._process_payment(order, transaction)
                    # 将支付方式同步到订单表
                    if payment and payment.payment_method:
                        order.payment_method = payment.payment_method
                        # 计算并更新支付手续费
                        if payment.total_fee:
                            from decimal import Decimal
                            order.payment_fee = Decimal(str(payment.total_fee))
                        if self.app:
                            self.app.logger.info(f"订单 {order.order_number} 设置支付方式: {payment.payment_method}, 手续费: {payment.total_fee}")
        else:
            if self.app:
                self.app.logger.warning(f"订单 {order.order_number} 没有交易数据")
        

        
        # 重新计算实际到账金额
        order.calculate_actual_received()
        
        return order
    
    def _sync_product(self, line_item) -> Optional[Product]:
        """同步商品信息"""
        try:
            # 使用no_autoflush避免自动刷新导致的数据库锁定
            with db.session.no_autoflush:
                # 检查商品是否已存在（包括当前会话中的未提交对象）
                existing_product = Product.query.filter_by(
                    shopify_product_id=line_item.product_id,
                    shopify_variant_id=line_item.variant_id
                ).first()
                
                if existing_product:
                    product = existing_product
                else:
                    # 使用merge来避免重复插入
                    product = Product(
                        shopify_product_id=line_item.product_id,
                        shopify_variant_id=line_item.variant_id,
                        cost=0.0,  # 默认成本，需要手动配置
                        product_type='phone_case'  # 默认为手机壳
                    )
                
                # 更新商品信息
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
                    return product
                except OperationalError as e:
                    if "database is locked" in str(e) and retry_count < max_retries - 1:
                        retry_count += 1
                        time.sleep(0.05 * retry_count)
                        db.session.rollback()
                        current_app.logger.warning(f"Database locked while syncing product {line_item.product_id}, retrying (attempt {retry_count})")
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
                    order_id=order.id,
                    transaction_id=transaction.id
                ).first()
                
                if existing_payment:
                    payment = existing_payment
                else:
                    payment = Payment()
                    payment.order_id = order.id
                    payment.transaction_id = transaction.id
                
                # 更新支付信息
                gateway = transaction.gateway or 'unknown'
                # 统一支付方式命名
                if 'stripe' in gateway.lower():
                    payment.payment_method = 'stripe'
                elif 'paypal' in gateway.lower():
                    payment.payment_method = 'paypal'
                else:
                    payment.payment_method = gateway
                
                payment.amount = float(transaction.amount)
                payment.currency = order.currency  # 设置支付货币与订单货币一致
                payment.status = transaction.status
                payment.payment_date = datetime.fromisoformat(transaction.processed_at.replace('Z', '+00:00')) if transaction.processed_at else datetime.utcnow()
                
                # 根据支付方式从FeeConfig获取费用配置
                fee_config = None
                if payment.payment_method == 'paypal':
                    fee_config = FeeConfig.query.filter_by(
                        fee_type='payment',
                        fee_name='PayPal手续费',
                        is_active=True
                    ).first()
                elif payment.payment_method == 'stripe':
                    fee_config = FeeConfig.query.filter_by(
                        fee_type='payment',
                        fee_name='Stripe手续费',
                        is_active=True
                    ).first()
                
                # 设置手续费配置
                if fee_config:
                    if fee_config.calculation_method == 'percentage':
                        payment.fee_percentage = float(fee_config.percentage_rate)
                        payment.fee_fixed = float(fee_config.fixed_amount or 0)
                    elif fee_config.calculation_method == 'fixed':
                        payment.fee_fixed = float(fee_config.fixed_amount)
                        payment.fee_percentage = 0
                    elif fee_config.calculation_method == 'percentage_plus_fixed':
                        payment.fee_percentage = float(fee_config.percentage_rate)
                        payment.fee_fixed = float(fee_config.fixed_amount or 0)
                    
                    # 使用FeeConfig的多币种计算方法
                    try:
                        calculated_fee = fee_config.calculate_fee(payment.amount, order.currency)
                        payment.total_fee = calculated_fee
                        payment.net_amount = payment.amount - payment.total_fee
                    except Exception as fee_error:
                        if self.app:
                            self.app.logger.error(f"费用计算失败: {str(fee_error)}，使用原始金额计算费用")
                        # 如果费用计算失败，使用原有的计算方法
                        payment.calculate_fee()
                else:
                    # 如果没有费用配置，使用原有的计算方法
                    payment.calculate_fee()
                
                if not existing_payment:
                    db.session.add(payment)
            
            return payment
            
        except Exception as e:
            if self.app:
                self.app.logger.error(f"Error processing payment {transaction.id}: {str(e)}")
            return None
    
    def sync_products(self, limit: int = 250) -> Dict[str, int]:
        """同步商品数据"""
        try:
            products = shopify.Product.find(limit=limit)
            
            stats = {
                'total_fetched': len(products),
                'new_products': 0,
                'updated_products': 0,
                'errors': 0
            }
            
            for shopify_product in products:
                try:
                    # 确保variants是可迭代的
                    variants = shopify_product.variants
                    if hasattr(variants, '__call__'):
                        variants = variants()
                    elif not hasattr(variants, '__iter__'):
                        variants = []
                        
                    for variant in variants:
                        existing_product = Product.query.filter_by(
                            shopify_product_id=shopify_product.id,
                            shopify_variant_id=variant.id
                        ).first()
                        
                        if existing_product:
                            product = existing_product
                            stats['updated_products'] += 1
                        else:
                            product = Product()
                            product.shopify_product_id = shopify_product.id
                            product.shopify_variant_id = variant.id
                            stats['new_products'] += 1
                        
                        # 更新商品信息
                        product.title = shopify_product.title
                        product.variant_title = variant.title
                        product.sku = variant.sku
                        product.price = float(variant.price)
                        product.inventory_quantity = variant.inventory_quantity
                        product.product_type = shopify_product.product_type or 'phone_case'
                        product.vendor = shopify_product.vendor
                        product.status = 'active' if shopify_product.status == 'active' else 'inactive'
                        
                        if not existing_product:
                            product.cost = 0.0  # 默认成本，需要手动配置
                            db.session.add(product)
                        
                except Exception as e:
                    if self.app:
                        self.app.logger.error(f"Error processing product {shopify_product.id}: {str(e)}")
                    stats['errors'] += 1
            
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
            shop = shopify.Shop.current()
            return {
                'name': shop.name,
                'email': shop.email,
                'domain': shop.domain,
                'currency': shop.currency,
                'timezone': shop.timezone,
                'plan_name': shop.plan_name
            }
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
            since_date = datetime.now() - timedelta(hours=hours)
            
            if self.app:
                self.app.logger.info(f"开始同步最近订单：从 {since_date.strftime('%Y-%m-%d %H:%M:%S')} 开始，最近 {hours} 小时")
            
            # 获取最近的订单
            orders = shopify.Order.find(
                status='any',
                created_at_min=since_date.isoformat(),
                limit=250  # 增量同步可以使用更大的限制
            )
            
            if self.app:
                self.app.logger.info(f"从Shopify获取到 {len(orders)} 个最近订单")
            
            stats = {
                'total_fetched': len(orders),
                'new_orders': 0,
                'updated_orders': 0,
                'errors': 0
            }
            
            if len(orders) == 0:
                if self.app:
                    self.app.logger.info("没有找到需要同步的最近订单")
                return stats
            
            # 处理订单
            for shopify_order in orders:
                try:
                    # 检查是否为新订单
                    existing_order = Order.query.filter_by(shopify_order_id=shopify_order.id).first()
                    is_new_order = existing_order is None
                    
                    self._process_order(shopify_order)
                    
                    if is_new_order:
                        stats['new_orders'] += 1
                    else:
                        stats['updated_orders'] += 1
                        
                except Exception as e:
                    if self.app:
                        self.app.logger.error(f"Error processing recent order {shopify_order.id}: {str(e)}")
                    stats['errors'] += 1
                    db.session.rollback()
            
            # 提交所有更改
            db.session.commit()
            
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