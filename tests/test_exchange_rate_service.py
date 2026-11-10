"""Exchange rates must not silently fall back to hard-coded accounting values."""

import json
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from app.services.exchange_rate_service import ExchangeRateService, ExchangeRateUnavailableError


def test_recent_successful_rate_survives_a_new_process(app, monkeypatch, tmp_path):
    cache_path = tmp_path / "rates.json"
    app.config["EXCHANGE_RATE_CACHE_PATH"] = str(cache_path)
    first = ExchangeRateService()
    monkeypatch.setattr(first, "_fetch_rate_from_apis", lambda *_: (Decimal("6.72"), "exchangerate-api"))

    with app.app_context():
        assert first.get_exchange_rate("USD", "CNY") == Decimal("6.72")
        assert json.loads(cache_path.read_text())["USD_CNY"]["rate"] == "6.72"

        second = ExchangeRateService()
        monkeypatch.setattr(second, "_fetch_rate_from_apis", lambda *_: None)
        assert second.get_exchange_rate("USD", "CNY") == Decimal("6.72")
        assert second.cache["USD_CNY"]["is_fallback"] is True


def test_list_rate_uses_verified_snapshot_without_waiting_for_network(app, monkeypatch, tmp_path):
    cache_path = tmp_path / "rates.json"
    app.config["EXCHANGE_RATE_CACHE_PATH"] = str(cache_path)
    service = ExchangeRateService()
    monkeypatch.setattr(
        service, "_fetch_rate_from_apis",
        lambda *_: pytest.fail("list should not block on an online exchange-rate request"),
    )
    with app.app_context():
        cache_path.write_text(json.dumps({"HKD_CNY": {
            "rate": "0.91",
            "fetched_at": (datetime.utcnow() - timedelta(hours=2)).isoformat(),
            "source": "verified-provider",
        }}))
        assert service.get_exchange_rate_for_list("HKD", "CNY") == (Decimal("0.91"), True)
        cache_path.write_text(json.dumps({"HKD_CNY": {
            "rate": "0.92",
            "fetched_at": datetime.utcnow().isoformat(),
            "source": "verified-provider",
        }}))
        assert service.get_exchange_rate_for_list("HKD", "CNY") == (Decimal("0.92"), False)


def test_detail_snapshot_is_scoped_and_reports_stale_currencies(app, monkeypatch, tmp_path):
    cache_path = tmp_path / "rates.json"
    app.config["EXCHANGE_RATE_CACHE_PATH"] = str(cache_path)
    cache_path.write_text(json.dumps({"USD_CNY": {
        "rate": "6.72",
        "fetched_at": (datetime.utcnow() - timedelta(hours=2)).isoformat(),
        "source": "verified-provider",
    }}))
    service = ExchangeRateService()
    monkeypatch.setattr(service, "_fetch_rate_from_apis", lambda *_: None)

    with app.app_context():
        with service.use_verified_snapshot() as stale:
            assert service.get_exchange_rate("USD", "CNY") == Decimal("6.72")
            assert stale == {"USD"}
        assert service._verified_snapshot.get() is None
        assert service.get_exchange_rate("USD", "CNY") == Decimal("6.72")
        assert service.cache["USD_CNY"]["is_fallback"] is True


def test_expired_or_missing_rate_fails_instead_of_returning_static_value(app, monkeypatch, tmp_path):
    cache_path = tmp_path / "rates.json"
    app.config["EXCHANGE_RATE_CACHE_PATH"] = str(cache_path)
    cache_path.write_text(
        json.dumps(
            {
                "USD_CNY": {
                    "rate": "6.72",
                    "fetched_at": (datetime.utcnow() - timedelta(days=8)).isoformat(),
                    "source": "exchangerate-api",
                }
            }
        )
    )
    service = ExchangeRateService()
    monkeypatch.setattr(service, "_fetch_rate_from_apis", lambda *_: None)

    with app.app_context():
        with pytest.raises(ExchangeRateUnavailableError):
            service.get_exchange_rate("USD", "CNY")
        with pytest.raises(ExchangeRateUnavailableError):
            service.convert_to_cny(100, "USD")


def test_independent_provider_is_used_when_primary_fails(app, monkeypatch, tmp_path):
    app.config["EXCHANGE_RATE_CACHE_PATH"] = str(tmp_path / "rates.json")
    service = ExchangeRateService()
    monkeypatch.setattr(service, "_fetch_from_exchangerate_api", lambda *_: None)
    monkeypatch.setattr(service, "_fetch_from_frankfurter", lambda *_: Decimal("0.856"))
    monkeypatch.setattr(
        service, "_fetch_from_fixer_api", lambda *_: pytest.fail("third provider should not be used")
    )

    with app.app_context():
        assert service.get_exchange_rate("HKD", "CNY") == Decimal("0.856")
        assert service.cache["HKD_CNY"]["source"] == "frankfurter"
        assert service._get_request_timeout() == 10
