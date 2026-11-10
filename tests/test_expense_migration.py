from decimal import Decimal

from app import db
from app.models import Account, Consumption, Expense
from app.services.expense_migration_service import merge_legacy_expenses


def test_legacy_import_is_idempotent_and_debits_once(app):
    account = Account(shop_id=1, platform="facebook", account_name="migration-test", balance=100)
    db.session.add(account)
    db.session.commit()
    snapshot = {
        "accounts": [{"id": 99, "platform": "facebook", "account_name": "migration-test"}],
        "expenses": [{
            "id": 90001, "account_id": 99, "amount": "12.34",
            "category": "facebook_ads", "expense_date": "2026-10-07",
            "created_at": "2026-10-07T08:00:00", "description": "test",
        }],
    }
    assert merge_legacy_expenses(snapshot, 1)["new_count"] == 1
    assert account.balance == Decimal("100")
    assert merge_legacy_expenses(snapshot, 1, apply=True)["new_count"] == 1
    assert account.balance == Decimal("87.66")
    assert merge_legacy_expenses(snapshot, 1, apply=True)["new_count"] == 0
    assert account.balance == Decimal("87.66")
    assert Consumption.query.filter_by(account_id=account.id).count() == 1
    expense = Expense.query.filter_by(reference_id="legacy8000:expense:90001").one()
    assert expense.created_at.hour == 0
