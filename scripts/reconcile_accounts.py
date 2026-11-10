#!/usr/bin/env python3
"""Account reconciliation report.

For every account it checks the ledger identity:

    balance == opening_balance + completed_recharges - consumptions

Result classification:
    OK           balance matches the ledger
    UNCONFIRMED  balance is higher than the ledger implies (needs a look)
    ANOMALY      balance is lower than the ledger implies (money is missing)

Accounts created before the opening-balance column existed start at 0; run
``python migrate_add_account_opening_balance.py`` once to backfill them.

Use ``--detail <account_id>`` to list the most recent transactions of a single
account while investigating a difference.

Read-only: it never modifies data. Exit code is 1 when any ANOMALY exists, so
it can be used as a monitoring check.

Usage:
    python scripts/reconcile_accounts.py
    python scripts/reconcile_accounts.py --json
"""

import argparse
import json
import os
import sys
from decimal import Decimal

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app, db  # noqa: E402
from app.models import Account, Consumption, Recharge  # noqa: E402

TOLERANCE = Decimal("0.01")


def classify(diff):
    """diff = balance - (opening + recharges - consumptions)."""
    if abs(diff) <= TOLERANCE:
        return "OK"
    return "UNCONFIRMED" if diff > 0 else "ANOMALY"


def collect_rows():
    rows = []
    accounts = Account.query.order_by(Account.id).all()

    recharge_totals = dict(
        db.session.query(Recharge.account_id, db.func.sum(Recharge.amount))
        .filter(Recharge.status == "completed")
        .group_by(Recharge.account_id)
        .all()
    )
    pending_totals = dict(
        db.session.query(Recharge.account_id, db.func.sum(Recharge.amount))
        .filter(Recharge.status == "pending")
        .group_by(Recharge.account_id)
        .all()
    )
    consumption_totals = dict(
        db.session.query(Consumption.account_id, db.func.sum(Consumption.amount))
        .group_by(Consumption.account_id)
        .all()
    )

    for account in accounts:
        balance = Decimal(str(account.balance or 0))
        opening = Decimal(str(account.opening_balance or 0))
        recharged = Decimal(str(recharge_totals.get(account.id) or 0))
        pending = Decimal(str(pending_totals.get(account.id) or 0))
        consumed = Decimal(str(consumption_totals.get(account.id) or 0))
        expected = opening + recharged - consumed
        diff = balance - expected

        rows.append(
            {
                "id": account.id,
                "platform": account.platform,
                "account_name": account.account_name,
                "currency": account.currency,
                "balance": float(balance),
                "completed_recharge": float(recharged),
                "pending_recharge": float(pending),
                "consumption": float(consumed),
                "opening_balance": float(opening),
                "expected_balance": float(expected),
                "difference": float(diff),
                "status": classify(diff),
            }
        )

    return rows


def print_table(rows):
    header = (
        f'{"ID":>4} {"账户":22s} {"平台":10s} {"余额":>12s} {"期初":>10s} '
        f'{"已完成充值":>12s} {"消耗":>12s} {"差额":>10s}  状态'
    )
    print(header)
    print("-" * len(header.encode("gbk", errors="replace")))
    for row in rows:
        print(
            f'{row["id"]:>4} {row["account_name"][:20]:22s} {row["platform"][:8]:10s} '
            f'{row["balance"]:12.2f} {row["opening_balance"]:10.2f} '
            f'{row["completed_recharge"]:12.2f} {row["consumption"]:12.2f} '
            f'{row["difference"]:10.2f}  {row["status"]}'
        )


def main():
    parser = argparse.ArgumentParser(description="Account reconciliation report")
    parser.add_argument("--json", action="store_true", help="print machine readable JSON")
    parser.add_argument(
        "--detail",
        type=int,
        metavar="ACCOUNT_ID",
        help="list recent transactions of one account (for investigating a difference)",
    )
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        if args.detail:
            return print_detail(args.detail)
        rows = collect_rows()
        database_url = db.engine.url.render_as_string(hide_password=True)

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        print(f"数据库: {database_url}")
        print()
        print_table(rows)

        ok = sum(1 for row in rows if row["status"] == "OK")
        unconfirmed = sum(1 for row in rows if row["status"] == "UNCONFIRMED")
        anomaly = sum(1 for row in rows if row["status"] == "ANOMALY")

        print()
        print(f"合计 {len(rows)} 个账户: 完全对账 {ok} 个 / 需确认期初 {unconfirmed} 个 / 异常 {anomaly} 个")
        if anomaly:
            print()
            print("异常账户（余额低于「期初 + 已完成充值 - 消耗」，说明流水缺失或余额被改动）：")
            for row in rows:
                if row["status"] == "ANOMALY":
                    print(f'  - {row["account_name"]}: 差额 {row["difference"]:.2f}')
        if unconfirmed:
            print()
            print("需确认账户：余额高于流水应有金额，建议核对。")

    return 1 if any(row["status"] == "ANOMALY" for row in rows) else 0


def print_detail(account_id, limit=20):
    """List recent transactions of one account to locate a difference."""
    account = db.session.get(Account, account_id)
    if account is None:
        print(f"账户 {account_id} 不存在")
        return 2

    recharged = Decimal(
        str(
            db.session.query(db.func.sum(Recharge.amount))
            .filter(Recharge.account_id == account_id, Recharge.status == "completed")
            .scalar()
            or 0
        )
    )
    consumed = Decimal(
        str(
            db.session.query(db.func.sum(Consumption.amount))
            .filter(Consumption.account_id == account_id)
            .scalar()
            or 0
        )
    )
    opening = Decimal(str(account.opening_balance or 0))
    balance = Decimal(str(account.balance or 0))

    print(f"账户 #{account.id} {account.account_name}（{account.platform}）")
    print(
        f"  期初 {opening} + 已完成充值 {recharged} - 消耗 {consumed} "
        f"= 应有余额 {opening + recharged - consumed}"
    )
    print(f"  实际余额 {balance}，差额 {balance - (opening + recharged - consumed)}")
    print()

    print(f"最近 {limit} 条消耗记录:")
    for item in (
        Consumption.query.filter_by(account_id=account_id)
        .order_by(Consumption.consumption_date.desc(), Consumption.id.desc())
        .limit(limit)
        .all()
    ):
        flag = "" if item.reference_id else "  [无外部关联]"
        print(
            f"  {item.consumption_date} {float(item.amount):>12.2f} "
            f"{item.consumption_type or '-':<12s} {(item.description or '')[:30]}{flag}"
        )

    print()
    print(f"最近 {limit} 条充值记录:")
    for item in (
        Recharge.query.filter_by(account_id=account_id)
        .order_by(Recharge.recharge_date.desc(), Recharge.id.desc())
        .limit(limit)
        .all()
    ):
        print(
            f"  {item.recharge_date:%Y-%m-%d} {float(item.amount):>12.2f} "
            f"{item.status:<10s} {(item.description or '')[:30]}"
        )

    print()
    print("提示: 差额为负通常意味着有消耗被删除但余额未回补，或余额被手工改动过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
