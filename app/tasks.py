import logging
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv
from celery_app import celery
from app.services.shopify_service import ShopifyService
from app import create_app

# 加载环境变量
load_dotenv()

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@celery.task(bind=True, autoretry_for=(Exception,), retry_kwargs={'max_retries': 3, 'countdown': 60})
def sync_shopify_orders_task(self, hours_back=1):
    """同步Shopify订单的Celery任务"""
    app = create_app()
    with app.app_context():
        try:
            shopify_service = ShopifyService()
            shopify_service.init_app(app)
            result = shopify_service.sync_recent_orders(hours_back)
            logger.info(f"订单同步完成: {result}")
            return result
        except Exception as e:
            logger.error(f"订单同步失败: {str(e)}")
            raise


@celery.task(bind=True, autoretry_for=(Exception,), retry_kwargs={'max_retries': 3, 'countdown': 300})
def sync_shopify_products_task(self, limit=250):
    """同步Shopify产品的Celery任务"""
    app = create_app()
    with app.app_context():
        try:
            shopify_service = ShopifyService()
            shopify_service.init_app(app)
            result = shopify_service.sync_products(limit)
            logger.info(f"产品同步完成: {result}")
            return result
        except Exception as e:
            logger.error(f"产品同步失败: {str(e)}")
            raise


@celery.task(bind=True, autoretry_for=(Exception,), retry_kwargs={'max_retries': 3, 'countdown': 300})
def sync_shopify_orders_full_task(self, days_back=365):
    """全量同步Shopify订单的Celery任务"""
    app = create_app()
    with app.app_context():
        try:
            shopify_service = ShopifyService()
            shopify_service.init_app(app)
            result = shopify_service.sync_orders(days_back=days_back, limit=250)
            logger.info(f"全量订单同步完成: {result}")
            return result
        except Exception as e:
            logger.error(f"全量订单同步失败: {str(e)}")
            raise


@celery.task(bind=True, autoretry_for=(Exception,), retry_kwargs={'max_retries': 3, 'countdown': 60})
def sync_shopify_orders_daily_task(self):
    """同步当天Shopify订单的Celery任务"""
    app = create_app()
    with app.app_context():
        try:
            shopify_service = ShopifyService()
            shopify_service.init_app(app)
            # 同步当天的订单（24小时内）
            result = shopify_service.sync_recent_orders(hours=24)
            logger.info(f"当天订单同步完成: {result}")
            return result
        except Exception as e:
            logger.error(f"当天订单同步失败: {str(e)}")
            raise


@celery.task(bind=True, autoretry_for=(Exception,), retry_kwargs={'max_retries': 2, 'countdown': 30})
def test_connection_task(self):
    """测试Shopify连接的Celery任务"""
    app = create_app()
    with app.app_context():
        try:
            shopify_service = ShopifyService()
            shopify_service.init_app(app)
            result = shopify_service.test_connection()
            logger.info(f"连接测试完成: {result}")
            return result
        except Exception as e:
            logger.error(f"连接测试失败: {str(e)}")
            raise