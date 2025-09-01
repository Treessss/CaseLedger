#!/usr/bin/env python3
"""
数据库迁移脚本：修改Product表的唯一约束
从单一的shopify_product_id唯一约束改为shopify_product_id和shopify_variant_id的复合唯一约束
"""

import sqlite3
import os
from datetime import datetime

def migrate_product_constraints():
    """迁移Product表的约束"""
    
    # 数据库文件路径
    db_paths = [
        'instance/caseLedger.db',
        'instance/case_ledger.db'
    ]
    
    for db_path in db_paths:
        if os.path.exists(db_path):
            print(f"正在迁移数据库: {db_path}")
            migrate_single_db(db_path)
        else:
            print(f"数据库文件不存在: {db_path}")

def migrate_single_db(db_path):
    """迁移单个数据库文件"""
    try:
        # 备份原数据库
        backup_path = f"{db_path}.backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        import shutil
        shutil.copy2(db_path, backup_path)
        print(f"已创建备份: {backup_path}")
        
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # 检查当前表结构
        cursor.execute("PRAGMA table_info(products)")
        columns = cursor.fetchall()
        print("当前表结构:")
        for col in columns:
            print(f"  {col}")
        
        # 检查是否存在旧的唯一约束
        cursor.execute("PRAGMA index_list(products)")
        indexes = cursor.fetchall()
        print("\n当前索引:")
        for idx in indexes:
            print(f"  {idx}")
        
        # 创建新表结构
        cursor.execute("""
        CREATE TABLE products_new (
            id INTEGER PRIMARY KEY,
            shopify_product_id VARCHAR(50) NOT NULL,
            shopify_variant_id VARCHAR(50) NOT NULL,
            title VARCHAR(255) NOT NULL,
            sku VARCHAR(100),
            variant_title VARCHAR(255),
            price DECIMAL(10, 2) NOT NULL,
            cost DECIMAL(10, 2) DEFAULT 0,
            inventory_quantity INTEGER DEFAULT 0,
            product_type VARCHAR(100),
            vendor VARCHAR(100),
            status VARCHAR(50) DEFAULT 'active',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(shopify_product_id, shopify_variant_id)
        )
        """)
        
        # 复制数据到新表
        cursor.execute("""
        INSERT INTO products_new 
        SELECT * FROM products
        """)
        
        # 删除旧表
        cursor.execute("DROP TABLE products")
        
        # 重命名新表
        cursor.execute("ALTER TABLE products_new RENAME TO products")
        
        # 创建索引
        cursor.execute("CREATE INDEX ix_products_shopify_product_id ON products (shopify_product_id)")
        cursor.execute("CREATE INDEX ix_products_shopify_variant_id ON products (shopify_variant_id)")
        
        conn.commit()
        print("数据库迁移完成!")
        
        # 验证新结构
        cursor.execute("PRAGMA table_info(products)")
        new_columns = cursor.fetchall()
        print("\n新表结构:")
        for col in new_columns:
            print(f"  {col}")
            
        cursor.execute("PRAGMA index_list(products)")
        new_indexes = cursor.fetchall()
        print("\n新索引:")
        for idx in new_indexes:
            print(f"  {idx}")
        
        conn.close()
        
    except Exception as e:
        print(f"迁移失败: {str(e)}")
        if 'conn' in locals():
            conn.rollback()
            conn.close()
        raise

if __name__ == '__main__':
    print("开始数据库迁移...")
    migrate_product_constraints()
    print("迁移完成!")