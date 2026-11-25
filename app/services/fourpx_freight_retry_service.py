"""Backoff state for 4PX orders whose real fee is not billed yet."""

from datetime import datetime, timedelta

from flask import current_app

from app import db
from app.models import FourPxFreightRetry


def waiting_order_ids(shop_id):
    """Orders that must not be polled again until ``next_retry_at``."""
    now = datetime.utcnow()
    rows = db.session.query(FourPxFreightRetry.order_id).filter(
        FourPxFreightRetry.shop_id == shop_id,
        FourPxFreightRetry.next_retry_at > now,
    ).all()
    return {row[0] for row in rows}


def record_failure(shop_id, order_id, error):
    """Schedule the next attempt with capped exponential backoff."""
    now = datetime.utcnow()
    row = FourPxFreightRetry.query.filter_by(order_id=order_id).one_or_none()
    if row is None:
        row = FourPxFreightRetry(shop_id=shop_id, order_id=order_id, attempts=0,
                                 next_retry_at=now, last_error="")
        db.session.add(row)
    row.attempts = int(row.attempts or 0) + 1
    base_minutes = max(int(current_app.config.get("LOGISTICS_COST_RETRY_BASE_MINUTES", 30)), 1)
    max_minutes = max(int(current_app.config.get("LOGISTICS_COST_RETRY_MAX_HOURS", 6)) * 60, base_minutes)
    delay_minutes = min(base_minutes * (2 ** max(row.attempts - 1, 0)), max_minutes)
    row.next_retry_at = now + timedelta(minutes=delay_minutes)
    row.last_error = str(error or "4PX 尚未出账")[:500]
    db.session.commit()
    return row


def clear(order_id):
    """Forget the backoff state after a successful fee fetch."""
    row = FourPxFreightRetry.query.filter_by(order_id=order_id).one_or_none()
    if row is not None:
        db.session.delete(row)
