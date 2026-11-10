#!/usr/bin/env python3
"""Initialize default data (fee configs) and, if needed, create tables.

Schema changes are managed by Alembic. Recommended order:
    flask db upgrade     # create / upgrade tables
    python init_db.py    # seed default data (idempotent)

For a brand new database without Alembic this script still falls back to
db.create_all() so local development keeps working.
"""

import os
import sys

from dotenv import load_dotenv
from sqlalchemy import inspect

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app, db  # noqa: E402
from app.models import FeeConfig  # noqa: E402


def _is_alembic_managed():
    """True when the database already has an alembic_version table."""
    return "alembic_version" in inspect(db.engine).get_table_names()


def init_database():
    app = create_app()

    with app.app_context():
        print(f"数据库: {db.engine.url.render_as_string(hide_password=True)}")

        if _is_alembic_managed():
            print("检测到 Alembic 管理的数据库，跳过建表。")
            print("如需升级表结构，请执行: flask db upgrade")
        else:
            print("未检测到迁移版本表，使用 db.create_all() 创建表结构...")
            db.create_all()
            print("提示: 正式环境请改用 `flask db upgrade` 管理表结构。")

        print("正在初始化默认手续费配置...")
        FeeConfig.init_default_configs()
        print("初始化完成。")

        configs = FeeConfig.query.all()
        print(f"\n当前手续费配置 {len(configs)} 条:")
        for config in configs:
            print(
                f"- {config.fee_type}: {config.fee_name} "
                f"(固定 ${config.fixed_amount}, 百分比 {config.percentage_rate}%)"
            )


if __name__ == "__main__":
    init_database()
