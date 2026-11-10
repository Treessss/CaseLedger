"""Beijing business dates and naive UTC database timestamp boundaries."""

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

BEIJING_ZONE = ZoneInfo("Asia/Shanghai")


def beijing_day_bounds_utc(start_date, end_date):
    """Return the half-open UTC interval for inclusive Beijing calendar dates."""
    start = datetime.combine(start_date, time.min, BEIJING_ZONE)
    end = datetime.combine(end_date + timedelta(days=1), time.min, BEIJING_ZONE)
    return (
        start.astimezone(timezone.utc).replace(tzinfo=None),
        end.astimezone(timezone.utc).replace(tzinfo=None),
    )


def beijing_date_from_utc(value):
    """Database timestamps are naive UTC; tolerate aware timestamps too."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(BEIJING_ZONE).date()


def utc_naive_from_iso(value):
    """Normalize Shopify's ISO timestamp before storing it in a naive UTC column."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)
