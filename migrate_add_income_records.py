#!/usr/bin/env python3
"""Add extra-income support to the expenses table (idempotent).

Adds:
    expenses.record_type  VARCHAR(20) NOT NULL DEFAULT 'expense'  + index

Existing rows are backfilled to 'expense', so nothing changes for current data.

Run after backing up the database:
    ./scripts/backup_db.sh
    python migrate_add_income_records.py
"""

import os
import sys

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import text  # noqa: E402

from app import create_app, db  # noqa: E402

TABLE = "expenses"
COLUMN = "record_type"
INDEX = "ix_expenses_record_type"


def _is_sqlite():
    return db.engine.dialect.name == "sqlite"


def column_exists():
    if _is_sqlite():
        rows = db.session.execute(text(f'PRAGMA table_info("{TABLE}")')).fetchall()
        return any(row[1] == COLUMN for row in rows)
    count = db.session.execute(
        text(
            "SELECT COUNT(*) FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = :t AND column_name = :c"
        ),
        {"t": TABLE, "c": COLUMN},
    ).scalar()
    return bool(count)


def index_exists():
    if _is_sqlite():
        rows = db.session.execute(text(f'PRAGMA index_list("{TABLE}")')).fetchall()
        return any(row[1] == INDEX for row in rows)
    count = db.session.execute(
        text(
            "SELECT COUNT(*) FROM information_schema.statistics "
            "WHERE table_schema = DATABASE() AND table_name = :t AND index_name = :i"
        ),
        {"t": TABLE, "i": INDEX},
    ).scalar()
    return bool(count)


def main():
    app = create_app()
    with app.app_context():
        if column_exists():
            print(f"  [跳过] {TABLE}.{COLUMN} 已存在")
        else:
            db.session.execute(
                text(f"ALTER TABLE {TABLE} ADD COLUMN {COLUMN} VARCHAR(20) " f"NOT NULL DEFAULT 'expense'")
            )
            db.session.commit()
            print(f"  [新增] {TABLE}.{COLUMN}")

        # 历史数据全部视为支出
        updated = db.session.execute(
            text(f"UPDATE {TABLE} SET {COLUMN} = 'expense' WHERE {COLUMN} IS NULL")
        ).rowcount
        db.session.commit()
        if updated:
            print(f"  [回填] {updated} 条历史记录标记为支出")

        if index_exists():
            print(f"  [跳过] 索引 {INDEX} 已存在")
        else:
            db.session.execute(text(f"CREATE INDEX {INDEX} ON {TABLE} ({COLUMN})"))
            db.session.commit()
            print(f"  [新增] 索引 {INDEX}")

        total = db.session.execute(text(f"SELECT COUNT(*) FROM {TABLE}")).scalar()
        expenses = db.session.execute(
            text(f"SELECT COUNT(*) FROM {TABLE} WHERE {COLUMN} = 'expense'")
        ).scalar()
        incomes = db.session.execute(text(f"SELECT COUNT(*) FROM {TABLE} WHERE {COLUMN} = 'income'")).scalar()
        print(f"\n完成：共 {total} 条流水（支出 {expenses} / 收入 {incomes}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
