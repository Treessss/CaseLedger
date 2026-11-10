#!/usr/bin/env python3
"""Migrate existing data from SQLite to PostgreSQL.

Usage:
    1. Point DATABASE_URL in .env at the PostgreSQL database;
    2. Run: python migrate_to_postgres.py

Options:
    --source sqlite:////abs/path/caseLedger.db   source database (default local SQLite)
    --target postgresql://user:pw@host:5432/db   target database (default: DATABASE_URL)
    --truncate                                   clear target tables before copying
    --dry-run                                    only report source row counts

The script copies rows in foreign-key order, verifies row counts and money sums,
then resets PostgreSQL sequences so later inserts do not collide.
"""

import argparse
import os
import sys
from decimal import Decimal

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import DECIMAL, Numeric, String, create_engine, func, select, text  # noqa: E402

from app import db  # noqa: E402
from app import models  # noqa: F401,E402

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SOURCE = f"sqlite:///{os.path.join(PROJECT_DIR, 'instance', 'caseLedger.db')}"

SKIP_TABLES = {"alembic_version"}

CHECK_SUMS = {
    "orders": ["total_price", "actual_received"],
    "payments": ["amount"],
    "expenses": ["amount"],
}


def parse_args():
    parser = argparse.ArgumentParser(description="SQLite to PostgreSQL data migration")
    parser.add_argument("--source", default=DEFAULT_SOURCE)
    parser.add_argument("--target", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--truncate", action="store_true")
    parser.add_argument(
        "--recreate", action="store_true", help="drop and recreate target tables (schema drift / fresh run)"
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--batch-size", type=int, default=1000)
    return parser.parse_args()


def normalize_url(url):
    if not url:
        return url
    if url.startswith("postgres://"):
        return "postgresql+psycopg2://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg2://" + url[len("postgresql://") :]
    if url.startswith("mysql://"):
        return "mysql+pymysql://" + url[len("mysql://") :]
    return url


def existing_tables(engine):
    from sqlalchemy import inspect

    return set(inspect(engine).get_table_names())


def source_count(conn, table):
    return conn.execute(select(func.count()).select_from(table)).scalar() or 0


def source_sum(conn, table, column):
    # 金额统一按 2 位小数汇总，避免 SQLite 浮点存储带来的尾数误差
    value = conn.execute(select(func.sum(func.round(column, 2)))).scalar()
    return Decimal(str(value or 0))


def preflight(conn, tables, source_tables):
    """迁移前预检：SQLite 不校验长度/精度，PostgreSQL 会直接拒绝。

    返回问题列表 [(表, 字段, 说明), ...]
    """
    issues = []
    for table in tables:
        if table.name not in source_tables:
            continue
        for column in table.columns:
            column_type = column.type

            length = getattr(column_type, "length", None)
            if isinstance(column_type, String) and length:
                max_length = conn.execute(select(func.max(func.length(column)))).scalar()
                if max_length and max_length > length:
                    issues.append((table.name, column.name, f"最长 {max_length} 字符 > 模型定义 {length}"))
                continue

            precision = getattr(column_type, "precision", None)
            if isinstance(column_type, (Numeric, DECIMAL)) and precision:
                scale = column_type.scale or 0
                integer_digits = precision - scale
                max_abs = conn.execute(select(func.max(func.abs(column)))).scalar()
                if max_abs is not None:
                    digits = len(str(int(abs(max_abs))))
                    if digits > integer_digits:
                        issues.append((table.name, column.name, f"整数位 {digits} > 允许 {integer_digits}"))
    return issues


def truncate_target(engine, tables):
    dialect = engine.dialect.name
    with engine.begin() as conn:
        if dialect == "postgresql":
            names = ", ".join(f'"{table.name}"' for table in reversed(tables))
            conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
        else:
            for table in reversed(tables):
                conn.execute(table.delete())


def copy_table(source_conn, target_engine, table, batch_size):
    total = 0
    result = source_conn.execution_options(stream_results=True).execute(select(table))
    batch = []
    for row in result:
        batch.append(dict(row._mapping))
        if len(batch) >= batch_size:
            with target_engine.begin() as conn:
                conn.execute(table.insert(), batch)
            total += len(batch)
            batch = []
    if batch:
        with target_engine.begin() as conn:
            conn.execute(table.insert(), batch)
        total += len(batch)
    return total


def reset_postgres_sequences(engine, tables):
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as conn:
        for table in tables:
            if "id" not in table.columns:
                continue
            conn.execute(
                text(
                    f"SELECT setval(pg_get_serial_sequence('{table.name}', 'id'), "
                    f"COALESCE((SELECT MAX(id) FROM {table.name}), 1), "
                    f"(SELECT MAX(id) FROM {table.name}) IS NOT NULL)"
                )
            )


def main():
    args = parse_args()
    source_url = normalize_url(args.source)
    target_url = normalize_url(args.target)

    if not target_url:
        print("ERROR: no target database. Set DATABASE_URL or pass --target.")
        return 1
    if source_url == target_url:
        print("ERROR: source and target are the same database.")
        return 1

    print(f"source: {source_url}")
    print(f"target: {target_url}")
    print()

    source_engine = create_engine(source_url)
    source_tables = existing_tables(source_engine)
    ordered_tables = [t for t in db.metadata.sorted_tables if t.name not in SKIP_TABLES]

    print("Source data overview:")
    plan = []
    with source_engine.connect() as source_conn:
        for table in ordered_tables:
            if table.name not in source_tables:
                print(f"  {table.name:28s} not in source, skipped")
                continue
            count = source_count(source_conn, table)
            plan.append((table, count))
            print(f"  {table.name:28s} {count} rows")

        if args.dry_run:
            print("\n[--dry-run] nothing written.")
            return 0

        print("\nPre-flight check (length / precision):")
        issues = preflight(source_conn, ordered_tables, source_tables)
        if issues:
            for table_name, column_name, detail in issues:
                print(f"  [PROBLEM] {table_name}.{column_name}: {detail}")
            print("\nERROR: source data does not fit the declared column sizes.")
            print("SQLite ignores VARCHAR length, PostgreSQL enforces it.")
            print("Please widen the column in app/models/*.py and re-run.")
            return 4
        print("  [OK] all values fit")

    target_engine = create_engine(target_url)

    print("\n1/4 creating schema on target...")
    if args.recreate:
        print("  [--recreate] dropping existing tables...")
        db.metadata.drop_all(target_engine)
    db.metadata.create_all(target_engine)
    print("  [OK] schema ready")

    target_tables = existing_tables(target_engine)
    non_empty = []
    with target_engine.connect() as conn:
        for table, _ in plan:
            if table.name in target_tables:
                n = conn.execute(select(func.count()).select_from(table)).scalar() or 0
                if n:
                    non_empty.append(f"{table.name}({n})")

    if non_empty and not args.truncate:
        print("\nERROR: target already has data: " + ", ".join(non_empty))
        print("Re-run with --truncate to overwrite (it clears target tables).")
        return 2

    if non_empty:
        print("\n2/4 clearing target tables...")
        truncate_target(target_engine, [t for t, _ in plan])
        print("  [OK] cleared")
    else:
        print("\n2/4 target is empty, nothing to clear")

    print("\n3/4 copying data...")
    with source_engine.connect() as src:
        for table, expected in plan:
            copied = copy_table(src, target_engine, table, args.batch_size)
            flag = "OK" if copied == expected else f"MISMATCH (source {expected})"
            print(f"  {table.name:28s} {copied} rows  [{flag}]")

    print("\n4/4 verifying...")
    ok = True
    with source_engine.connect() as src, target_engine.connect() as dst:
        for table, expected in plan:
            actual = dst.execute(select(func.count()).select_from(table)).scalar() or 0
            if actual != expected:
                ok = False
                print(f"  [COUNT MISMATCH] {table.name}: source {expected} / target {actual}")

        for table_name, columns in CHECK_SUMS.items():
            table = db.metadata.tables.get(table_name)
            if table is None or table_name not in source_tables:
                continue
            for column_name in columns:
                column = table.columns.get(column_name)
                if column is None:
                    continue
                src_sum = source_sum(src, table, column)
                dst_sum = source_sum(dst, table, column)
                delta = abs(src_sum - dst_sum)
                if delta > Decimal("0.01"):
                    ok = False
                    print(
                        f"  [SUM MISMATCH] {table_name}.{column_name}: "
                        f"source {src_sum} / target {dst_sum} (delta {delta})"
                    )
                else:
                    print(
                        f"  [SUM OK] {table_name}.{column_name} = {dst_sum}"
                        + (f" (delta {delta})" if delta else "")
                    )

    reset_postgres_sequences(target_engine, [t for t, _ in plan])
    print("  [OK] PostgreSQL sequences reset")

    if not ok:
        print("\nVerification failed, please review the mismatches above.")
        return 3

    print("\nMigration finished, all checks passed.")
    print("Next: run `flask db stamp head` against the target database.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
