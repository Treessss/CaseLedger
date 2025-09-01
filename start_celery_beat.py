#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
启动Celery Beat定时任务调度器
"""

import os
import sys
from datetime import datetime

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

print("=== Celery Beat 定时任务调度器 ===")
print(f"启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("定时任务配置:")
print("- 全量同步: 每天中午12:00和晚上12:00")
print("- 增量同步: 每小时同步当天订单")
print("- 产品同步: 每天一次")
print("- 连接测试: 每30分钟一次")
print()
print("按 Ctrl+C 停止调度器")
print("=" * 50)

# 启动Celery Beat
if __name__ == '__main__':
    from celery_app import celery
    
    # 启动beat调度器
    celery.start(['beat', '--loglevel=info'])