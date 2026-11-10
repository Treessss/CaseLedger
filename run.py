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
        "db": db,
        "Order": Order,
        "Payment": Payment,
        "Expense": Expense,
        "FeeConfig": FeeConfig,
        "Product": Product,
    }


if __name__ == "__main__":
    # 开发环境配置
    # 注意：默认关闭调试模式。调试模式会暴露 Werkzeug 调试器（可执行任意代码），
    # 只应在本地开发时通过 FLASK_DEBUG=True 临时开启，绝不能对公网暴露。
    debug_mode = os.environ.get("FLASK_DEBUG", "False").lower() == "true"
    port = int(os.environ.get("PORT", 5000))
    host = os.environ.get("HOST", "127.0.0.1")

    print(f"Starting CaseLedger on {host}:{port}")
    print(f"Debug mode: {debug_mode}")
    if debug_mode:
        print("警告: 当前处于调试模式，请勿将本端口暴露到公网！")

    app.run(host=host, port=port, debug=debug_mode, threaded=True)
