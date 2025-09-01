#!/usr/bin/env python3
"""
数据库迁移脚本：为Payment表添加currency字段
"""

import sqlite3
import os
from datetime import datetime

def migrate_database():
    """执行数据库迁移"""
    # 数据库文件路径
    db_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'instance', 'caseledger.db')
    
    if not os.path.exists(db_path):
        print(f"数据库文件不存在: {db_path}")
        return False
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # 检查currency字段是否已存在
        cursor.execute("PRAGMA table_info(payments)")
        columns = [column[1] for column in cursor.fetchall()]
        
        if 'currency' not in columns:
            print("正在为payments表添加currency字段...")
            
            # 添加currency字段，默认值为USD
            cursor.execute("""
                ALTER TABLE payments 
                ADD COLUMN currency VARCHAR(3) DEFAULT 'USD'
            """)
            
            # 检查orders表是否有currency字段
            cursor.execute("PRAGMA table_info(orders)")
            order_columns = [column[1] for column in cursor.fetchall()]
            
            if 'currency' in order_columns:
                # 如果orders表有currency字段，从关联的订单中获取货币信息
                cursor.execute("""
                    UPDATE payments 
                    SET currency = (
                        SELECT COALESCE(orders.currency, 'USD') 
                        FROM orders 
                        WHERE orders.id = payments.order_id
                    )
                    WHERE payments.currency IS NULL OR payments.currency = ''
                """)
                print("✅ 已从订单表同步货币信息")
            else:
                # 如果orders表没有currency字段，设置所有记录为USD
                cursor.execute("""
                    UPDATE payments 
                    SET currency = 'USD'
                    WHERE payments.currency IS NULL OR payments.currency = ''
                """)
                print("✅ 已设置所有支付记录货币为USD（订单表暂无货币字段）")
            
            conn.commit()
            print("✅ 成功添加currency字段并更新现有数据")
        else:
            print("currency字段已存在，跳过迁移")
        
        conn.close()
        return True
        
    except Exception as e:
        print(f"❌ 数据库迁移失败: {str(e)}")
        if 'conn' in locals():
            conn.rollback()
            conn.close()
        return False

if __name__ == '__main__':
    print(f"开始数据库迁移 - {datetime.now()}")
    success = migrate_database()
    if success:
        print("✅ 数据库迁移完成")
    else:
        print("❌ 数据库迁移失败")
        exit(1)