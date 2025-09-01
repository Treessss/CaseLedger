#!/usr/bin/env python3
"""
CaseLedger Flask应用启动脚本
"""

import os
from dotenv import load_dotenv
from app import create_app, db
from app.models.order import Order
from app.models.payment import Payment
from app.models.expense import Expense
from app.models.fee_config import FeeConfig
from app.models.product import Product

# 加载环境变量
load_dotenv()

# 创建Flask应用实例
app = create_app()

# 添加shell上下文处理器
@app.shell_context_processor
def make_shell_context():
    return {
        'db': db,
        'Order': Order,
        'Payment': Payment,
        'Expense': Expense,
        'FeeConfig': FeeConfig,
        'Product': Product
    }

if __name__ == '__main__':
    # 开发环境配置
    debug_mode = os.environ.get('FLASK_DEBUG', 'True').lower() == 'true'
    port = int(os.environ.get('PORT', 5000))
    host = os.environ.get('HOST', '127.0.0.1')
    
    print(f"Starting CaseLedger on {host}:{port}")
    print(f"Debug mode: {debug_mode}")
    
    app.run(
        host=host,
        port=port,
        debug=debug_mode,
        threaded=True
    )