# Celery 定时任务配置说明

## 概述

本系统已配置了自动订单同步的定时任务，包括全量同步和增量同步功能。

## 定时任务配置

### 1. 全量同步任务
- **任务名称**: `sync-shopify-orders-full-noon` 和 `sync-shopify-orders-full-midnight`
- **执行时间**: 每天中午12:00和晚上12:00（凌晨0:00）
- **功能**: 同步历史所有订单（默认365天内）
- **任务函数**: `app.tasks.sync_shopify_orders_full_task`

### 2. 增量同步任务
- **任务名称**: `sync-shopify-orders-hourly`
- **执行时间**: 每小时的0分执行
- **功能**: 只同步当天的订单（24小时内）
- **任务函数**: `app.tasks.sync_shopify_orders_daily_task`

### 3. 其他任务
- **产品同步**: 每天执行一次
- **连接测试**: 每30分钟执行一次

## 启动方式

### 方法一：使用提供的脚本

1. **启动Worker进程**（在一个终端中）:
```bash
python start_celery_worker.py
```

2. **启动Beat调度器**（在另一个终端中）:
```bash
python start_celery_beat.py
```

### 方法二：使用Celery命令

1. **启动Worker进程**:
```bash
celery -A celery_app worker --loglevel=info --queues=sync,test
```

2. **启动Beat调度器**:
```bash
celery -A celery_app beat --loglevel=info
```

## 测试配置

运行测试脚本验证配置是否正确：
```bash
python test_celery_schedule.py
```

## 任务队列说明

- **sync队列**: 处理订单同步和产品同步任务
- **test队列**: 处理连接测试任务

## 日志监控

所有任务执行情况都会记录在日志中，包括：
- 同步开始和结束时间
- 同步的订单数量
- 新增和更新的订单统计
- 错误信息（如果有）

## 注意事项

1. **确保Redis/RabbitMQ运行**: Celery需要消息代理服务
2. **数据库连接**: 确保数据库配置正确
3. **Shopify API配置**: 确保Shopify API密钥和访问令牌有效
4. **时区设置**: 定时任务使用系统时区，确保时区设置正确

## 手动触发任务

如果需要手动触发同步任务，可以使用以下方式：

```python
from app.tasks import sync_shopify_orders_full_task, sync_shopify_orders_daily_task

# 手动触发全量同步
result = sync_shopify_orders_full_task.delay()

# 手动触发增量同步
result = sync_shopify_orders_daily_task.delay()
```

## 故障排除

1. **任务不执行**: 检查Beat调度器是否运行
2. **任务执行失败**: 检查Worker日志和数据库连接
3. **重复执行**: 确保只有一个Beat调度器实例在运行
4. **时间不准确**: 检查系统时区设置

## 配置文件位置

- **主配置**: `celery_app.py`
- **任务定义**: `app/tasks.py`
- **服务实现**: `app/services/shopify_service.py`