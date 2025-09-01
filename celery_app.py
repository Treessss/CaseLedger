from celery import Celery
from celery.schedules import crontab
from config import Config

# 创建Celery实例
celery = Celery('caseledger')

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
        'app.tasks.sync_shopify_orders_task': {'queue': 'sync'},
        'app.tasks.sync_shopify_orders_full_task': {'queue': 'sync'},
        'app.tasks.sync_shopify_orders_daily_task': {'queue': 'sync'},
        'app.tasks.sync_shopify_products_task': {'queue': 'sync'},
        'app.tasks.test_connection_task': {'queue': 'test'},
    },
    # 定时任务配置
    beat_schedule={
        # 全量同步：每天中午12点
        'sync-shopify-orders-full-noon': {
            'task': 'app.tasks.sync_shopify_orders_full_task',
            'schedule': crontab(hour=12, minute=0),  # 每天中午12:00
        },
        # 全量同步：每天晚上12点
        'sync-shopify-orders-full-midnight': {
            'task': 'app.tasks.sync_shopify_orders_full_task',
            'schedule': crontab(hour=0, minute=0),   # 每天晚上12:00（凌晨0:00）
        },
        # 增量同步：每小时同步当天订单
        'sync-shopify-orders-hourly': {
            'task': 'app.tasks.sync_shopify_orders_daily_task',
            'schedule': crontab(minute=0),  # 每小时的0分执行
        },
        # 产品同步：每天执行一次
        'sync-shopify-products': {
            'task': 'app.tasks.sync_shopify_products_task',
            'schedule': 86400.0,  # 每天执行一次
        },
        # 连接测试：每30分钟测试一次
        'test-shopify-connection': {
            'task': 'app.tasks.test_connection_task',
            'schedule': 1800.0,  # 每30分钟测试一次连接
        },
    },
)

# 自动发现任务
celery.autodiscover_tasks(['app.tasks'])

if __name__ == '__main__':
    celery.start()