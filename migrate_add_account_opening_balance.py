#!/usr/bin/env python3
"""Add accounts.opening_balance and reconcile existing balances (idempotent).

The ledger identity is:

    balance == opening_balance + completed_recharges - consumptions

The old schema had no opening balance, so historical accounts could not be
reconciled. This script adds the column and backfills each account with the
*implied* opening balance:

    implied = balance - (completed_recharges - consumptions)

    implied >= 0  -> stored as the opening balance (the books now balance)
    implied < 0   -> opening balance stays 0 and the gap is reported, because a
                     negative opening balance is impossible and means the
                     ledger and the balance genuinely disagree.

Run after backing up:
    ./scripts/backup_db.sh
    python migrate_add_account_opening_balance.py
"""

import os
import sys
from decimal import Decimal

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import text  # noqa: E402

from app import create_app, db  # noqa: E402
from app.models import Account, Consumption, Recharge  # noqa: E402

TABLE = "accounts"
COLUMN = "opening_balance"


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


def main():
    app = create_app()
    with app.app_context():
        if column_exists():
            print(f"  [跳过] {TABLE}.{COLUMN} 已存在")
        else:
            db.session.execute(text(f"ALTER TABLE {TABLE} ADD COLUMN {COLUMN} NUMERIC(10, 2) DEFAULT 0"))
            db.session.commit()
            print(f"  [新增] {TABLE}.{COLUMN}")

        recharged = dict(
            db.session.query(Recharge.account_id, db.func.sum(Recharge.amount))
            .filter(Recharge.status == "completed")
            .group_by(Recharge.account_id)
            .all()
        )
        consumed = dict(
            db.session.query(Consumption.account_id, db.func.sum(Consumption.amount))
            .group_by(Consumption.account_id)
            .all()
        )

        filled = 0
        gaps = []
        for account in Account.query.order_by(Account.id).all():
            balance = Decimal(str(account.balance or 0))
            total_in = Decimal(str(recharged.get(account.id) or 0))
            total_out = Decimal(str(consumed.get(account.id) or 0))
            implied = balance - (total_in - total_out)

            if implied >= 0:
                account.opening_balance = implied
                filled += 1
            else:
                account.opening_balance = Decimal("0")
                gaps.append((account.id, account.account_name, implied))

        db.session.commit()
        print(f"  [回填] {filled} 个账户写入期初余额")

        if gaps:
            print(f"\n  [异常] {len(gaps)} 个账户的余额低于流水应有金额，无法用期初余额解释：")
            for account_id, name, implied in gaps:
                print(f"    - #{account_id} {name}: 差额 {implied}")
            print("    期初余额保持 0，请人工核对流水（可运行 scripts/reconcile_accounts.py 查看）")
        else:
            print("  [OK] 全部账户对账一致")

    return 0


if __name__ == "__main__":
    sys.exit(main())
