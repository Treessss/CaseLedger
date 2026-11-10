from celery import Celery
from celery.schedules import crontab
from config import Config

# 创建Celery实例
celery = Celery("caseledger")

# 配置Celery
celery.conf.update(
    broker_url=Config.CELERY_BROKER_URL,
    result_backend=Config.CELERY_RESULT_BACKEND,
    task_serializer=Config.CELERY_TASK_SERIALIZER,
    result_serializer=Config.CELERY_RESULT_SERIALIZER,
    accept_content=Config.CELERY_ACCEPT_CONTENT,
    timezone=Config.CELERY_TIMEZONE,
    enable_utc=Config.CELERY_ENABLE_UTC,
    # 任务路由
    task_routes={
        'app.tasks.refresh_logistics_batch_task': {'queue': 'tracking'},
        'app.tasks.refresh_logistics_history_batch_task': {'queue': 'tracking_history'},
        'app.tasks.schedule_logistics_refresh_task': {'queue': 'tracking'},
        'app.tasks.auto_reconcile_tracking_outbound_task': {'queue': 'tracking'},
        'app.tasks.recover_logistics_jobs_task': {'queue': 'tracking'},
        'app.tasks.run_fourpx_declaration_job_task': {'queue': 'declaration'},
        'app.tasks.generate_mail_draft_job_task': {'queue': 'mail'},
        'app.tasks.recover_mail_draft_jobs_task': {'queue': 'mail'},
        "app.tasks.sync_shopify_orders_task": {"queue": "sync"},
        "app.tasks.sync_shopify_orders_full_task": {"queue": "sync_full"},
        "app.tasks.sync_shopify_orders_daily_task": {"queue": "sync"},
        "app.tasks.sync_shopify_products_task": {"queue": "sync"},
        "app.tasks.test_connection_task": {"queue": "test"},
        "app.tasks.sync_gateway_fees_task": {"queue": "sync"},
        "app.tasks.reconcile_fulfillment_status_task": {"queue": "sync"},
        "app.tasks.sync_logistics_costs_task": {"queue": "sync"},
        "app.tasks.sync_after_sale_barcodes_task": {"queue": "sync"},
        "app.tasks.daily_logistics_expense_task": {"queue": "sync"},
    },
    # 定时任务配置
    beat_schedule={
        'logistics-incremental-due': {
            'task': 'app.tasks.schedule_logistics_refresh_task',
            'schedule': 60.0, 'args': ['due'],
        },
        'logistics-history-once': {
            'task': 'app.tasks.schedule_logistics_refresh_task',
            'schedule': 60.0, 'args': ['history'],
        },
        # 4PX 真实物流费：每 10 分钟补同步一批未记费订单；每天按订单日期生成/更新日账单
        'sync-after-sale-barcodes': {
            'task': 'app.tasks.sync_after_sale_barcodes_task', 'schedule': 600.0,
        },
        'sync-logistics-costs': {
            'task': 'app.tasks.sync_logistics_costs_task', 'schedule': 600.0,
        },
        'daily-logistics-expense': {
            'task': 'app.tasks.daily_logistics_expense_task',
            'schedule': crontab(hour=19, minute=10),  # 北京时间 03:10
        },
        'tracking-auto-outbound': {
            'task': 'app.tasks.auto_reconcile_tracking_outbound_task',
            'schedule': 120.0,
        },
        'recover-logistics-jobs': {
            'task': 'app.tasks.recover_logistics_jobs_task', 'schedule': 300.0,
        },
        'recover-requested-mail-jobs': {
            'task': 'app.tasks.recover_mail_draft_jobs_task', 'schedule': 60.0,
        },
        # 全量同步：每天中午12点
        "sync-shopify-orders-full-noon": {
            "task": "app.tasks.sync_shopify_orders_full_task",
            "schedule": crontab(hour=12, minute=0),  # 每天中午12:00
        },
        # 全量同步：每天晚上12点
        "sync-shopify-orders-full-midnight": {
            "task": "app.tasks.sync_shopify_orders_full_task",
            "schedule": crontab(hour=0, minute=0),  # 每天晚上12:00（凌晨0:00）
        },
        # 增量同步：每小时同步当天订单
        "sync-shopify-orders-hourly": {
            "task": "app.tasks.sync_shopify_orders_daily_task",
            "schedule": crontab(minute=0),  # 每小时的0分执行
        },
        # 产品同步：每天执行一次
        "sync-shopify-products": {
            "task": "app.tasks.sync_shopify_products_task",
            "schedule": 86400.0,  # 每天执行一次
        },
        # 连接测试：每30分钟测试一次
        "test-shopify-connection": {
            "task": "app.tasks.test_connection_task",
            "schedule": 1800.0,  # 每30分钟测试一次连接
        },
        # 网关真实手续费：每小时同步结算流水（PayPal 回执要逐单拉，默认关闭）
        "sync-gateway-fees-hourly": {
            "task": "app.tasks.sync_gateway_fees_task",
            "schedule": crontab(minute=20),
            "kwargs": {"days_back": 3, "include_paypal": False},
        },
        # 发货状态校准：每 30 分钟的 10 分和 40 分执行。
        # 订单增量同步按创建时间抓最近几天，订单一发货就可能滚出窗口，
        # 本地 fulfillment_status 会停在 unfulfilled，导致「已发货」筛选漏单。
        "reconcile-fulfillment-status": {
            "task": "app.tasks.reconcile_fulfillment_status_task",
            "schedule": crontab(minute="10,40"),
            "kwargs": {"days_back": 30},
        },
    },
)

# 自动发现任务
celery.autodiscover_tasks(["app.tasks"])

if __name__ == "__main__":
    celery.start()
