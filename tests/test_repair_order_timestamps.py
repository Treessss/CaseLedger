"""The one-off repair must never shift an unverified or already-correct order."""

from datetime import datetime
from types import SimpleNamespace

import pytest

from scripts.repair_order_timestamps import plan_payment_timestamp_update, plan_timestamp_update


REMOTE = {"createdAt": "2026-09-19T08:00:00Z", "updatedAt": "2026-09-20T09:00:00Z"}


def _order(order_date, created_at, updated_at):
    return SimpleNamespace(
        id=1,
        order_number="10001",
        order_date=order_date,
        created_at=created_at,
        updated_at=updated_at,
    )


def test_repair_normalizes_verified_eight_hour_offset():
    order = _order(datetime(2026, 9, 19, 16), datetime(2026, 9, 19, 16), datetime(2026, 9, 20, 17))
    mapping, category, updated_category = plan_timestamp_update(order, REMOTE)
    assert mapping == {
        "id": 1,
        "order_date": datetime(2026, 9, 19, 8),
        "created_at": datetime(2026, 9, 19, 8),
        "updated_at": datetime(2026, 9, 20, 9),
    }
    assert category == "shifted_8h"
    assert updated_category == "shifted_8h"


def test_repair_preserves_local_update_and_skips_already_correct_order():
    order = _order(datetime(2026, 9, 19, 8), datetime(2026, 9, 19, 8), datetime(2026, 9, 21, 12))
    mapping, category, updated_category = plan_timestamp_update(order, REMOTE)
    assert mapping is None
    assert category == "already_utc"
    assert updated_category == "other"


def test_repair_rejects_unexpected_offset():
    order = _order(datetime(2026, 9, 19, 9), datetime(2026, 9, 19, 9), datetime(2026, 9, 20, 17))
    with pytest.raises(RuntimeError, match="不是 0 或 8 小时"):
        plan_timestamp_update(order, REMOTE)


def test_payment_repair_requires_exact_transaction_time_match():
    transaction = {"processedAt": "2026-09-19T08:00:00Z"}
    shifted = SimpleNamespace(id=7, payment_date=datetime(2026, 9, 19, 16), updated_at=datetime(2026, 9, 20))
    mapping, category = plan_payment_timestamp_update(shifted, transaction)
    assert mapping == {
        "id": 7,
        "payment_date": datetime(2026, 9, 19, 8),
        "updated_at": datetime(2026, 9, 20),
    }
    assert category == "shifted_8h"

    unrelated = SimpleNamespace(id=8, payment_date=datetime(2026, 9, 19, 10), updated_at=datetime(2026, 9, 20))
    assert plan_payment_timestamp_update(unrelated, transaction) == (None, "other")
    assert plan_payment_timestamp_update(shifted, None) == (None, "without_shopify_time")
