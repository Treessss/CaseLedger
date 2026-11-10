"""Cross-midnight Beijing calendar boundaries and Shopify UTC normalization."""

from datetime import date, datetime

from app.utils.timezone import beijing_date_from_utc, beijing_day_bounds_utc, utc_naive_from_iso


def test_beijing_calendar_day_is_half_open_utc_interval():
    assert beijing_day_bounds_utc(date(2026, 1, 2), date(2026, 1, 2)) == (
        datetime(2026, 1, 1, 16),
        datetime(2026, 1, 2, 16),
    )
    assert beijing_date_from_utc(datetime(2026, 1, 1, 15, 59, 59)) == date(2026, 1, 1)
    assert beijing_date_from_utc(datetime(2026, 1, 1, 16)) == date(2026, 1, 2)


def test_shopify_offset_timestamp_is_stored_as_naive_utc():
    assert utc_naive_from_iso("2026-01-01T11:30:00-05:00") == datetime(2026, 1, 1, 16, 30)
    assert utc_naive_from_iso("2026-01-01T16:30:00Z") == datetime(2026, 1, 1, 16, 30)
