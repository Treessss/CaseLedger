"""Idempotent import of legacy expense snapshots, including account debits."""

from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal

from app import db
from app.models import Account, Consumption, Expense, Order


def _number(value):
    return str(Decimal(str(value or 0)).normalize())


def _fingerprint(row, account_id):
    fields = ("category", "description", "currency", "vendor", "submitter", "status")
    return (
        str(row.get("expense_date")),
        *(str(row.get(key) or "").strip() for key in fields),
        row.get("record_type") or "expense",
        _number(row.get("amount")),
        _number(row.get("original_amount")),
        row.get("original_currency") or "",
        _number(row.get("exchange_rate") or 1),
        account_id,
    )


def merge_legacy_expenses(snapshot, shop_id, *, apply=False):
    """Match by account identity and expense multiset; debit only inserted rows."""
    accounts = Account.query.filter_by(shop_id=shop_id).all()
    account_map = {}
    for source in snapshot["accounts"]:
        matches = [
            row for row in accounts
            if row.platform == source["platform"]
            and row.account_name == source["account_name"]
            and (row.account_id or "") == (source.get("account_id") or "")
        ]
        if len(matches) == 1:
            account_map[source["id"]] = matches[0]

    existing = defaultdict(list)
    markers = {}
    for row in Expense.query.filter_by(shop_id=shop_id).all():
        values = {column.name: getattr(row, column.name) for column in Expense.__table__.columns}
        existing[_fingerprint(values, row.account_id)].append(row)
        if (row.reference_id or "").startswith("legacy8000:expense:"):
            markers[row.reference_id.split("|", 1)[0]] = row

    planned = []
    matched = 0
    for source in snapshot["expenses"]:
        account = account_map.get(source.get("account_id"))
        if source.get("account_id") and account is None:
            raise ValueError(f"No unique account match for expense {source['id']}")
        marker = f"legacy8000:expense:{source['id']}"
        key = _fingerprint(source, account.id if account else None)
        candidates = existing[key]
        marked = markers.get(marker)
        if marked is not None:
            if marked in candidates:
                candidates.remove(marked)
            matched += 1
        elif candidates:
            candidates.pop(0)
            matched += 1
        else:
            planned.append((source, account, marker))

    total = sum((Decimal(str(row[0]["amount"])) for row in planned), Decimal("0"))
    report = {
        "source_count": len(snapshot["expenses"]), "matched": matched,
        "new_count": len(planned), "new_amount": str(total),
        "source_ids": [row[0]["id"] for row in planned], "applied": apply,
    }
    if not apply:
        return report

    try:
        for source, account, marker in planned:
            created = datetime.fromisoformat(source["created_at"]) - timedelta(hours=8)
            updated = datetime.fromisoformat(source["updated_at"]) if source.get("updated_at") else created
            expense = Expense(
                shop_id=shop_id, record_type=source.get("record_type") or "expense",
                category=source["category"], description=source.get("description"),
                amount=Decimal(str(source["amount"])), currency=source.get("currency") or "CNY",
                original_amount=source.get("original_amount"),
                original_currency=source.get("original_currency"),
                exchange_rate=source.get("exchange_rate") or 1,
                vendor=source.get("vendor"), submitter=source.get("submitter"),
                status=source.get("status") or "confirmed",
                expense_date=date.fromisoformat(source["expense_date"]),
                account_id=account.id if account else None,
                reference_id=marker + ("|" + source["reference_id"] if source.get("reference_id") else ""),
                created_at=created, updated_at=updated,
            )
            db.session.add(expense)
            db.session.flush()
            for shopify_id in source.get("shopify_order_ids", []):
                order = Order.query.filter_by(shop_id=shop_id, shopify_order_id=shopify_id).one_or_none()
                if order is None:
                    raise ValueError(f"Missing linked order for expense {source['id']}")
                expense.orders.append(order)
            if account:
                account.balance = (account.balance or Decimal("0")) - expense.amount
                account.updated_at = datetime.utcnow()
                db.session.add(Consumption(
                    account_id=account.id, amount=expense.amount, currency=expense.currency,
                    description=f"费用支出: {expense.description}",
                    reference_id=f"expense:{expense.id}", consumption_date=expense.expense_date,
                    created_at=created, updated_at=updated,
                ))
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return report
