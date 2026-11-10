"""Tests for the account reconciliation script."""
import importlib.util
from datetime import date
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[1] / 'scripts' / 'reconcile_accounts.py'


def _load_script_module():
    spec = importlib.util.spec_from_file_location('reconcile_accounts', SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_account(app, name, balance, recharges=(), consumptions=(), opening=0):
    from app import db
    from app.models import Account, Consumption, Recharge

    account = Account(
        shop_id=1,
        platform='facebook',
        account_name=name,
        balance=balance,
        opening_balance=opening,
        currency='CNY',
    )
    db.session.add(account)
    db.session.flush()
    for amount, status in recharges:
        db.session.add(Recharge(account_id=account.id, amount=amount, status=status))
    for amount in consumptions:
        db.session.add(Consumption(account_id=account.id, amount=amount,
                                   consumption_date=date(2026, 1, 5)))
    db.session.commit()
    return account.id


def test_reconciliation_classifies_accounts(app):
    module = _load_script_module()

    with app.app_context():
        # 余额正好等于「充值 - 消耗」→ 完全对账
        ok_id = _make_account(app, '对账一致', balance=300,
                              recharges=[(500, 'completed')], consumptions=[200])
        # 余额高于流水 → 可能是开户时的期初余额
        opening_id = _make_account(app, '有期初余额', balance=150,
                                   recharges=[(100, 'completed')])
        # 余额低于流水 → 不可能由期初余额解释
        anomaly_id = _make_account(app, '流水异常', balance=10,
                                   recharges=[(500, 'completed')])

        rows = {row['id']: row for row in module.collect_rows()}

    assert rows[ok_id]['status'] == 'OK'
    assert rows[ok_id]['difference'] == 0.0

    assert rows[opening_id]['status'] == 'UNCONFIRMED'
    assert rows[opening_id]['difference'] == 50.0

    assert rows[anomaly_id]['status'] == 'ANOMALY'
    assert rows[anomaly_id]['difference'] == -490.0


def test_opening_balance_makes_the_ledger_reconcile(app):
    """有期初余额的账户应当对账一致，而不是一直被标成「需确认」。"""
    module = _load_script_module()

    with app.app_context():
        account_id = _make_account(
            app, '期初余额账户', balance=1300, opening=1000,
            recharges=[(500, 'completed')], consumptions=[200],
        )
        row = next(item for item in module.collect_rows() if item['id'] == account_id)

    assert row['opening_balance'] == 1000.0
    assert row['expected_balance'] == 1300.0
    assert row['difference'] == 0.0
    assert row['status'] == 'OK'


def test_pending_recharge_is_not_counted_as_income(app):
    """A pending recharge must not explain an existing balance."""
    module = _load_script_module()

    with app.app_context():
        account_id = _make_account(app, '待确认充值账户', balance=0,
                                   recharges=[(500, 'pending')])
        row = next(item for item in module.collect_rows() if item['id'] == account_id)

    assert row['completed_recharge'] == 0.0
    assert row['pending_recharge'] == 500.0
    assert row['status'] == 'OK'
