#!/usr/bin/env python3
"""数据库初始化脚本"""

import os
import sys
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate, init, migrate, upgrade
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app, db
from app.models import Order, Payment, Expense, FeeConfig, Product, Account, Recharge, Consumption, OrderCost, OrderCostBatch, ShopifyConfig

def init_database():
    """初始化数据库"""
    app = create_app()
    
    with app.app_context():
        print("正在创建数据库表...")
        
        # 创建所有表
        db.create_all()
        
        # 初始化默认手续费配置
        print("正在初始化默认手续费配置...")
        FeeConfig.init_default_configs()
        
        print("数据库初始化完成！")
        
        # 显示创建的表
        print("\n已创建的表:")
        print("- orders (订单表)")
        print("- payments (支付记录表)")
        print("- expenses (费用支出表)")
        print("- fee_configs (手续费配置表)")
        print("- products (商品表)")
        print("- accounts (账户表)")
        print("- recharges (充值记录表)")
        print("- consumptions (消耗记录表)")
        print("- order_costs (订单费用表)")
        print("- order_cost_batches (订单费用批次表)")
        print("- shopify_configs (Shopify配置表)")
        
        # 显示默认配置
        configs = FeeConfig.query.all()
        print("\n默认手续费配置:")
        for config in configs:
            print(f"- {config.fee_type}: {config.fee_name} (固定费用: ${config.fixed_amount}, 百分比: {config.percentage_rate}%)")

if __name__ == '__main__':
    init_database()