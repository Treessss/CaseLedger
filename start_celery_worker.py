#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
启动Celery Worker工作进程
"""

import os
import sys
from datetime import datetime

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

print("=== Celery Worker 工作进程 ===")
print(f"启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("支持的任务队列:")
print("- sync: 订单和产品同步任务")
print("- test: 连接测试任务")
print()
print("按 Ctrl+C 停止工作进程")
print("=" * 50)

# 启动Celery Worker
if __name__ == '__main__':
    from celery_app import celery
    from config import Config

    worker_args = ['worker', '--loglevel=info', '--hostname=caseledger-worker@%h', '--queues=sync,test']
    is_sqlite = str(Config.SQLALCHEMY_DATABASE_URI).startswith('sqlite')

    if is_sqlite:
        # SQLite不适合多进程并发写入，使用单进程避免database is locked
        worker_args.extend(['--pool=solo', '--concurrency=1'])

    # 启动worker进程，监听所有队列
    celery.start(worker_args)
