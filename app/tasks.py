import logging
import os
from functools import lru_cache
from datetime import datetime, timedelta
from dotenv import load_dotenv
from celery_app import celery
from app.services.shopify_service import ShopifyService
from app import create_app, db

# 加载环境变量
load_dotenv()

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _logistics_app():
    # Reuse one engine per tracking worker process, not a new pool for every batch.
    return create_app()


@celery.task(acks_late=True, reject_on_worker_lost=True, soft_time_limit=180, time_limit=210)
def refresh_logistics_batch_task(job_id):
    from app.services.logistics_service import process_batch
    with _logistics_app().app_context():
        process_batch(job_id)


@celery.task(acks_late=True, reject_on_worker_lost=True, soft_time_limit=180, time_limit=210)
def refresh_logistics_history_batch_task(job_id):
    from app.services.logistics_service import process_batch
    with _logistics_app().app_context():
        process_batch(job_id)


@celery.task
def schedule_logistics_refresh_task(scope):
    from app.services.logistics_service import scheduled_refresh
    with _logistics_app().app_context():
        scheduled_refresh(scope)


@celery.task(acks_late=True, reject_on_worker_lost=True, soft_time_limit=240, time_limit=270)
def auto_reconcile_tracking_outbound_task():
    from app.services.logistics_outbound_service import auto_reconcile
    with _logistics_app().app_context():
        return auto_reconcile()


@celery.task
def sync_logistics_costs_task():
    from flask import g
    from app.services.logistics_cost_automation_service import (sync_missing_after_sale_costs,
                                                                sync_missing_costs,
                                                                write_daily_expenses)
    app = _logistics_app()
    with app.app_context():
        g.current_user = {"authority": "admin", "user_id": None, "permissions": []}
        result = sync_missing_costs()
        result["after_sale"] = sync_missing_after_sale_costs()
        # 同步完立刻回写日账单金额，避免当天费用金额滞后到次日 03:10
        result["daily"] = write_daily_expenses(days=14)
        return result


@celery.task
def daily_logistics_expense_task():
    from app.services.logistics_cost_automation_service import write_daily_expenses
    with _logistics_app().app_context():
        return write_daily_expenses()


@celery.task
def recover_logistics_jobs_task():
    from app.services.logistics_service import recover_jobs
    with _logistics_app().app_context():
        recover_jobs()


@celery.task(acks_late=True, reject_on_worker_lost=True, soft_time_limit=540, time_limit=570)
def generate_mail_draft_job_task(job_id):
    from app.services.mail_draft_job_service import process_one
    with create_app().app_context():
        process_one(job_id)


@celery.task
def recover_mail_draft_jobs_task():
    from app.services.mail_draft_job_service import recover
    with create_app().app_context():
        recover()


@celery.task(acks_late=True, reject_on_worker_lost=True, soft_time_limit=1800, time_limit=1860)
def run_fourpx_declaration_job_task(job_id):
    from app.services.fourpx_declaration_service import process_declaration_job
    with _logistics_app().app_context():
        process_declaration_job(job_id)


@celery.task
def sync_after_sale_barcodes_task():
    from app.models import Shop
    from app.services.fourpx_barcode_sync_service import sync_after_sale_barcodes
    app = create_app()
    with app.app_context():
        results = {}
        for shop in Shop.query.filter(Shop.is_active.is_(True),
                                      Shop.fourpx_app_key_encrypted.isnot(None),
                                      Shop.fourpx_app_secret_encrypted.isnot(None)).all():
            results[shop.id] = sync_after_sale_barcodes(shop.id, limit=100)
        return results


@celery.task(bind=True, max_retries=3)
def sync_fourpx_label_barcodes_task(self, shop_id, parcel_ids):
    """Triggered after binding, never creates labels or performs warehouse scans."""
    from app.services.fourpx_barcode_sync_service import sync_barcodes

    app = create_app()
    with app.app_context():
        result = sync_barcodes(shop_id, parcel_ids)
        if result["failed"]:
            logger.warning("4PX barcode registration incomplete for shop %s: %s", shop_id, result["failed"])
            raise self.retry(exc=RuntimeError("4PX physical barcode registration incomplete"), countdown=60)
        return result


def _active_shops():
    """当前启用的店铺列表（定时同步按店铺逐个执行）。"""
    from app.models import Shop

    return Shop.query.filter(Shop.is_active.is_(True)).order_by(Shop.id).all()


def _shop_service(shop, app):
    service = ShopifyService()
    service.init_shop(shop, app)
    return service


def _sync_fees_after_orders(shops, *, days_back=7):
    """订单同步后顺手补真实手续费；失败不影响订单同步结果。"""
    try:
        from app.services.sync_service import refresh_fees_for_shops

        return refresh_fees_for_shops(shops, days_back=days_back)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"订单同步后的手续费补取失败: {exc}")
        return {"error": str(exc)}


def _run_order_sync(shop, app, runner):
    """Run one scheduled order sync and record its outcome for the shop UI."""
    from app import db
    from app.services.sync_service import record_order_sync_result

    try:
        result = runner(_shop_service(shop, app))
        error_count = int(result.get("errors", 0) or 0) if isinstance(result, dict) else 0
        record_order_sync_result(shop, f"{error_count} 笔订单处理失败" if error_count else None)
        return result
    except Exception as exc:
        db.session.rollback()
        record_order_sync_result(shop, exc)
        raise


@celery.task(bind=True, autoretry_for=(Exception,), retry_kwargs={"max_retries": 3, "countdown": 60})
def sync_shopify_orders_task(self, hours_back=1):
    """同步Shopify订单的Celery任务"""
    app = create_app()
    with app.app_context():
        try:
            results = {}
            shops = _active_shops()
            for shop in shops:
                try:
                    results[shop.name] = _run_order_sync(
                        shop, app, lambda service: service.sync_recent_orders(hours_back)
                    )
                except Exception as shop_error:
                    logger.error(f"店铺 {shop.name} 同步失败: {shop_error}")
                    results[shop.name] = {"error": str(shop_error)}
            results["_fee_sync"] = _sync_fees_after_orders(shops)
            logger.info(f"订单同步完成: {results}")
            return results
        except Exception as e:
            logger.error(f"订单同步失败: {str(e)}")
            raise


@celery.task(bind=True, autoretry_for=(Exception,), retry_kwargs={"max_retries": 3, "countdown": 300})
def sync_shopify_products_task(self, limit=None):
    """同步Shopify产品的Celery任务"""
    app = create_app()
    with app.app_context():
        try:
            results = {}
            for shop in _active_shops():
                try:
                    service = _shop_service(shop, app)
                    results[shop.name] = service.sync_products(limit)
                except Exception as shop_error:
                    logger.error(f"店铺 {shop.name} 产品同步失败: {shop_error}")
                    results[shop.name] = {"error": str(shop_error)}
            logger.info(f"产品同步完成: {results}")
            return results
        except Exception as e:
            logger.error(f"产品同步失败: {str(e)}")
            raise


@celery.task(bind=True, autoretry_for=(Exception,), retry_kwargs={"max_retries": 3, "countdown": 300})
def sync_shopify_orders_full_task(self):
    """逐页同步 Shopify 全部历史订单，不按日期或笔数截断。"""
    app = create_app()
    with app.app_context():
        try:
            results = {}
            shops = _active_shops()
            for shop in shops:
                try:
                    results[shop.name] = _run_order_sync(
                        shop, app, lambda service: service.sync_all_orders()
                    )
                except Exception as shop_error:
                    logger.error(f"店铺 {shop.name} 全量同步失败: {shop_error}")
                    results[shop.name] = {"error": str(shop_error)}
            results["_fee_sync"] = _sync_fees_after_orders(shops, days_back=None)
            logger.info(f"全量订单同步完成: {results}")
            return results
        except Exception as e:
            logger.error(f"全量订单同步失败: {str(e)}")
            raise


@celery.task(bind=True, autoretry_for=(Exception,), retry_kwargs={"max_retries": 3, "countdown": 60})
def sync_shopify_orders_daily_task(self):
    """同步当天Shopify订单的Celery任务"""
    app = create_app()
    with app.app_context():
        try:
            results = {}
            shops = _active_shops()
            for shop in shops:
                try:
                    results[shop.name] = _run_order_sync(
                        shop, app, lambda service: service.sync_recent_orders(hours=24)
                    )
                except Exception as shop_error:
                    logger.error(f"店铺 {shop.name} 当天同步失败: {shop_error}")
                    results[shop.name] = {"error": str(shop_error)}
            results["_fee_sync"] = _sync_fees_after_orders(shops)
            logger.info(f"当天订单同步完成: {results}")
            return results
        except Exception as e:
            logger.error(f"当天订单同步失败: {str(e)}")
            raise


@celery.task(bind=True, autoretry_for=(Exception,), retry_kwargs={"max_retries": 2, "countdown": 30})
def test_connection_task(self):
    """测试Shopify连接的Celery任务"""
    app = create_app()
    with app.app_context():
        try:
            results = {}
            for shop in _active_shops():
                try:
                    service = _shop_service(shop, app)
                    results[shop.name] = service.test_connection()
                except Exception as shop_error:
                    results[shop.name] = str(shop_error)
            logger.info(f"连接测试完成: {results}")
            return results
        except Exception as e:
            logger.error(f"连接测试失败: {str(e)}")
            raise


@celery.task(bind=True, autoretry_for=(Exception,), retry_kwargs={"max_retries": 2, "countdown": 300})
def sync_gateway_fees_task(self, days_back=3, include_paypal=False):
    """同步网关真实手续费（结算流水 + 可选的 PayPal 回执）。"""
    from app.services.gateway_fee_service import refresh_paypal_payments, sync_shopify_payments_charges

    app = create_app()
    with app.app_context():
        since = (datetime.utcnow() - timedelta(days=days_back)).date()
        results = {}
        for shop in _active_shops():
            entry = {}
            try:
                stats = sync_shopify_payments_charges(shop, since_date=since)
                entry["settlement"] = {
                    "fetched": stats["fetched"],
                    "updated": stats["updated"],
                    "unmatched": stats["unmatched"],
                }
            except Exception as shop_error:
                db.session.rollback()
                logger.error(f"店铺 {shop.name} 结算流水同步失败: {shop_error}")
                entry["settlement_error"] = str(shop_error)
            finally:
                # Release per-shop locks before the next shop's network calls.
                db.session.commit()

            if include_paypal:
                try:
                    paypal_stats = refresh_paypal_payments(shop, only_missing=True)
                    entry["paypal"] = {
                        "orders": paypal_stats["orders"],
                        "updated": paypal_stats["updated"],
                        "failed": paypal_stats["failed"],
                    }
                except Exception as shop_error:
                    db.session.rollback()
                    logger.error(f"店铺 {shop.name} PayPal 回执同步失败: {shop_error}")
                    entry["paypal_error"] = str(shop_error)
                finally:
                    db.session.commit()

            results[shop.name] = entry

        logger.info(f"网关真实手续费同步完成: {results}")
        return results


@celery.task(bind=True, autoretry_for=(Exception,), retry_kwargs={"max_retries": 2, "countdown": 300})
def reconcile_fulfillment_status_task(self, days_back=30):
    """校准订单级发货状态。

    订单增量同步按创建时间抓最近几天，订单一发货就可能滚出窗口，
    本地状态会永远停在 unfulfilled，「已发货」筛选因此漏单。
    """
    from app.services.sync_service import reconcile_fulfillment_status

    app = create_app()
    with app.app_context():
        result = reconcile_fulfillment_status({"days_back": days_back})
        logger.info(f"发货状态校准完成: {result['totals']}")
        return result["totals"]
