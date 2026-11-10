#!/usr/bin/env python3
"""多店铺 + 管理员/子账户改造的数据库迁移脚本（幂等，可重复执行）。

执行内容：
    1. 新建 shops（店铺）、users（账号）、user_shops（子账户-店铺授权）三张表；
    2. 给 orders / products / payments / expenses / order_costs 增加 shop_id 列与索引；
    3. 根据原有 shopify_configs（或 .env）创建默认店铺；
    4. 把历史数据全部归属到默认店铺；
    5. 根据 .env 中的 ADMIN_USERNAME / ADMIN_PASSWORD 创建管理员账号。

执行前请先备份数据库（脚本不会自动备份）：
    cp instance/caseLedger.db "backups/caseLedger_$(date +%Y%m%d_%H%M%S).db"
"""

import os
import sys

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import text  # noqa: E402

from app import create_app, db  # noqa: E402
from app.models import Shop, ShopifyConfig, User  # noqa: E402

# 需要按店铺隔离的业务表
SCOPED_TABLES = ("orders", "products", "payments", "expenses", "order_costs")


def _is_sqlite():
    return db.engine.dialect.name == "sqlite"


def column_exists(table, column):
    if _is_sqlite():
        rows = db.session.execute(text(f'PRAGMA table_info("{table}")')).fetchall()
        return any(row[1] == column for row in rows)
    count = db.session.execute(
        text(
            "SELECT COUNT(*) FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = :t AND column_name = :c"
        ),
        {"t": table, "c": column},
    ).scalar()
    return bool(count)


def index_exists(table, index_name):
    if _is_sqlite():
        rows = db.session.execute(text(f'PRAGMA index_list("{table}")')).fetchall()
        return any(row[1] == index_name for row in rows)
    count = db.session.execute(
        text(
            "SELECT COUNT(*) FROM information_schema.statistics "
            "WHERE table_schema = DATABASE() AND table_name = :t AND index_name = :i"
        ),
        {"t": table, "i": index_name},
    ).scalar()
    return bool(count)


def ensure_tables():
    """创建新增的表（已存在的表不会受影响）。"""
    db.create_all()
    print("  [OK] 已确保 shops / users / user_shops 表存在")


def ensure_shop_columns():
    for table in SCOPED_TABLES:
        if column_exists(table, "shop_id"):
            print(f"  [跳过] {table}.shop_id 已存在")
        else:
            db.session.execute(text(f"ALTER TABLE {table} ADD COLUMN shop_id INTEGER"))
            db.session.commit()
            print(f"  [新增] {table}.shop_id")

        index_name = f"ix_{table}_shop_id"
        if index_exists(table, index_name):
            continue
        db.session.execute(text(f"CREATE INDEX {index_name} ON {table} (shop_id)"))
        db.session.commit()
        print(f"  [新增] 索引 {index_name}")


def ensure_default_shop():
    """把原来的单店铺配置升级成一条 Shop 记录。"""
    existing = Shop.query.order_by(Shop.id).first()
    if existing is not None:
        print(f"  [跳过] 已存在店铺: {existing.name} ({existing.shop_url})")
        return existing

    config = ShopifyConfig.query.first()
    if config is not None:
        raw_url = config.shop_url
        shop = Shop(
            name=(raw_url or "default").replace("https://", "").split(".")[0] or "默认店铺",
            shop_url=Shop.normalize_shop_url(raw_url),
            api_key=config.api_key,
            api_secret=config.api_secret,
            access_token=config.access_token,
            webhook_secret=os.environ.get("SHOPIFY_WEBHOOK_SECRET"),
            is_active=bool(config.is_active),
            last_sync=config.last_sync,
        )
    elif os.environ.get("SHOPIFY_SHOP_URL"):
        raw_url = os.environ["SHOPIFY_SHOP_URL"]
        shop = Shop(
            name=raw_url.split(".")[0] or "默认店铺",
            shop_url=Shop.normalize_shop_url(raw_url),
            api_key=os.environ.get("SHOPIFY_API_KEY"),
            api_secret=os.environ.get("SHOPIFY_API_SECRET"),
            access_token=os.environ.get("SHOPIFY_ACCESS_TOKEN"),
            webhook_secret=os.environ.get("SHOPIFY_WEBHOOK_SECRET"),
        )
    else:
        print("  [警告] 未找到原有店铺配置，请稍后通过「店铺管理」新增店铺")
        return None

    db.session.add(shop)
    db.session.commit()
    print(f"  [新增] 默认店铺: {shop.name} ({shop.shop_url})")
    return shop


def backfill_shop_id(shop):
    if shop is None:
        print("  [跳过] 没有默认店铺，历史数据暂不归属任何店铺")
        return

    for table in SCOPED_TABLES:
        result = db.session.execute(
            text(f"UPDATE {table} SET shop_id = :sid WHERE shop_id IS NULL"), {"sid": shop.id}
        )
        db.session.commit()
        print(f"  [回填] {table}: {result.rowcount} 条记录归属店铺 #{shop.id}")


def ensure_admin_user():
    username = os.environ.get("ADMIN_USERNAME") or "admin"
    password = os.environ.get("ADMIN_PASSWORD")

    user = User.get_by_username(username)
    if user is not None:
        if not user.is_admin:
            user.role = User.ROLE_ADMIN
            db.session.commit()
        print(f"  [跳过] 管理员账号已存在: {username}")
        return

    if not password:
        print("  [警告] 未配置 ADMIN_PASSWORD，跳过管理员账号创建")
        return

    user = User(username=username, display_name=username, role=User.ROLE_ADMIN, is_active=True)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    print(f"  [新增] 管理员账号: {username}")


def main():
    app = create_app()
    with app.app_context():
        print("1/5 创建新增数据表...")
        ensure_tables()

        print("2/5 为业务表增加 shop_id 列...")
        ensure_shop_columns()

        print("3/5 创建默认店铺...")
        shop = ensure_default_shop()

        print("4/5 回填历史数据的店铺归属...")
        backfill_shop_id(shop)

        print("5/5 创建管理员账号...")
        ensure_admin_user()

        print("\n迁移完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
