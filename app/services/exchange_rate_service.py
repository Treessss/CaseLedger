import fcntl
import json
import os
import tempfile
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Optional

import requests
from flask import current_app


class ExchangeRateUnavailableError(RuntimeError):
    """No recent, verified exchange rate is available."""


class ExchangeRateService:
    """汇率服务 - 获取实时汇率"""

    def __init__(self):
        self.cache = {}
        self.cache_duration = 3600  # 成功汇率缓存1小时
        self.fallback_cache_duration = 300  # 最近成功汇率临时兜底 5 分钟，再尝试在线刷新
        self.request_timeout = 10
        self.max_stale_days = 7
        # 支持的货币列表
        self.supported_currencies = ["USD", "EUR", "GBP", "CAD", "AUD", "JPY", "HKD", "CNY"]
        self._verified_snapshot = ContextVar("exchange_rate_verified_snapshot", default=None)

    @contextmanager
    def use_verified_snapshot(self):
        """Use recent verified rates for one response and report stale currencies.

        ContextVar keeps concurrent requests isolated; normal report/rate calls
        continue to use the live exchange-rate policy.
        """
        stale_currencies = set()
        token = self._verified_snapshot.set(stale_currencies)
        try:
            yield stale_currencies
        finally:
            self._verified_snapshot.reset(token)

    def _get_cached_rate(self, cache_key: str, current_time: float) -> Optional[Decimal]:
        """读取缓存汇率，并根据成功/回退类型应用不同的过期时间。"""
        cached_data = self.cache.get(cache_key)
        if not cached_data:
            return None

        ttl = self.fallback_cache_duration if cached_data.get("is_fallback") else self.cache_duration
        if current_time - cached_data["timestamp"] < ttl:
            return cached_data["rate"]

        self.cache.pop(cache_key, None)
        return None

    def _cache_rate(
        self, cache_key: str, rate: Decimal, current_time: float, *, is_fallback=False, source=None
    ) -> None:
        self.cache[cache_key] = {
            "rate": rate,
            "timestamp": current_time,
            "is_fallback": is_fallback,
            "source": source,
        }

    def _get_request_timeout(self) -> int:
        try:
            return int(current_app.config.get("EXCHANGE_RATE_REQUEST_TIMEOUT", self.request_timeout))
        except RuntimeError:
            return self.request_timeout

    def _get_cache_path(self) -> Optional[str]:
        try:
            return current_app.config.get("EXCHANGE_RATE_CACHE_PATH") or os.path.join(
                current_app.instance_path, "exchange_rates.json"
            )
        except RuntimeError:
            return None

    def _get_max_stale_days(self) -> int:
        try:
            return max(1, int(current_app.config.get("EXCHANGE_RATE_MAX_STALE_DAYS", self.max_stale_days)))
        except RuntimeError:
            return self.max_stale_days

    def _read_stored_rates(self, path: str) -> dict:
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
            return data if isinstance(data, dict) else {}
        except (FileNotFoundError, OSError, ValueError):
            return {}

    def _load_recent_rate(self, cache_key: str) -> Optional[tuple[Decimal, str]]:
        path = self._get_cache_path()
        if not path:
            return None
        entry = self._read_stored_rates(path).get(cache_key)
        if not isinstance(entry, dict):
            return None
        try:
            rate = Decimal(str(entry["rate"]))
            fetched_at = datetime.fromisoformat(str(entry["fetched_at"]))
        except (KeyError, ValueError, InvalidOperation, TypeError):
            return None
        age = datetime.utcnow() - fetched_at
        if (
            not rate.is_finite()
            or rate <= 0
            or age < timedelta(0)
            or age > timedelta(days=self._get_max_stale_days())
        ):
            return None
        return rate, str(entry["fetched_at"])

    def _store_rate(self, cache_key: str, rate: Decimal, source: str) -> None:
        """Atomically merge a verified rate across Flask and Celery processes."""
        path = self._get_cache_path()
        if not path:
            return
        directory = os.path.dirname(path)
        temp_path = None
        try:
            os.makedirs(directory, exist_ok=True)
            with open(f"{path}.lock", "a+", encoding="utf-8") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                data = self._read_stored_rates(path)
                data[cache_key] = {
                    "rate": str(rate),
                    "fetched_at": datetime.utcnow().isoformat(),
                    "source": source,
                }
                fd, temp_path = tempfile.mkstemp(prefix="exchange_rates-", dir=directory)
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(data, handle, ensure_ascii=False)
                os.replace(temp_path, path)
                temp_path = None
        except OSError as exc:
            current_app.logger.warning("无法持久化最近成功汇率: %s", exc)
        finally:
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)

    def get_exchange_rate(self, from_currency: str, to_currency: str) -> Optional[Decimal]:
        """获取两种货币之间的汇率"""
        snapshot = self._verified_snapshot.get()
        if snapshot is not None:
            rate, needs_refresh = self.get_exchange_rate_for_list(from_currency, to_currency)
            if needs_refresh:
                snapshot.add(from_currency)
            return rate
        if from_currency == to_currency:
            return Decimal("1")

        cache_key = f"{from_currency}_{to_currency}"
        current_time = time.time()

        # 检查缓存
        cached_rate = self._get_cached_rate(cache_key, current_time)
        if cached_rate is not None:
            return cached_rate

        # 先查在线来源；多个进程共享最近成功汇率，接口暂时不可用时仍可连续服务。
        result = self._fetch_rate_from_apis(from_currency, to_currency)
        if result:
            rate, source = result
            self._cache_rate(cache_key, rate, current_time, source=source)
            self._store_rate(cache_key, rate, source)
            return rate

        stored = self._load_recent_rate(cache_key)
        if stored:
            rate, fetched_at = stored
            self._cache_rate(cache_key, rate, current_time, is_fallback=True, source="last_successful")
            current_app.logger.warning(
                "实时汇率暂不可用，使用 %s 的最近成功汇率 %s -> %s: %s",
                fetched_at,
                from_currency,
                to_currency,
                rate,
            )
            return rate

        # 两个经过验证的人民币报价可以推导交叉汇率；不刷新原报价时间，避免陈旧值无限续命。
        if from_currency != "CNY" and to_currency != "CNY":
            from_cny = self._load_recent_rate(f"{from_currency}_CNY")
            to_cny = self._load_recent_rate(f"{to_currency}_CNY")
            if from_cny and to_cny:
                rate = from_cny[0] / to_cny[0]
                self._cache_rate(cache_key, rate, current_time, is_fallback=True, source="cross-cny")
                current_app.logger.warning(
                    "实时交叉汇率暂不可用，使用最近成功的人民币报价推导 %s -> %s: %s",
                    from_currency,
                    to_currency,
                    rate,
                )
                return rate

        raise ExchangeRateUnavailableError(
            f"{from_currency} -> {to_currency} 暂无可用汇率，请稍后重试；未使用固定或 1:1 兜底汇率"
        )

    def get_exchange_rate_for_list(self, from_currency: str, to_currency: str) -> tuple[Decimal, bool]:
        """Return a verified rate without blocking list loading on an online refresh.

        The boolean tells the client to request a fresh rate in the background.
        With no verified rate available we still use the normal live lookup so
        financial figures never silently fall back to a fabricated rate.
        """
        if from_currency == to_currency:
            return Decimal("1"), False
        key = f"{from_currency}_{to_currency}"
        now = time.time()
        cached = self._get_cached_rate(key, now)
        if cached is not None and not self.cache[key].get("is_fallback"):
            return cached, False
        stored = self._load_recent_rate(key)
        if stored:
            age = (datetime.utcnow() - datetime.fromisoformat(stored[1])).total_seconds()
            return stored[0], age >= self.cache_duration
        # No verified snapshot exists: perform the normal live lookup. Clear
        # the context briefly to avoid recursively entering this quick path.
        token = self._verified_snapshot.set(None)
        try:
            return self.get_exchange_rate(from_currency, to_currency), False
        finally:
            self._verified_snapshot.reset(token)

    def get_usd_to_cny_rate(self) -> Optional[Decimal]:
        """获取USD到CNY的汇率（保持向后兼容）"""
        return self.get_exchange_rate("USD", "CNY")

    def _fetch_rate_from_apis(self, from_currency: str, to_currency: str) -> Optional[tuple[Decimal, str]]:
        """从多个API尝试获取汇率"""
        apis = [
            ("exchangerate-api", lambda: self._fetch_from_exchangerate_api(from_currency, to_currency)),
            ("frankfurter", lambda: self._fetch_from_frankfurter(from_currency, to_currency)),
            ("fixer", lambda: self._fetch_from_fixer_api(from_currency, to_currency)),
        ]

        for source, api_func in apis:
            try:
                rate = api_func()
                if rate is not None and rate.is_finite() and rate > 0:
                    return rate, source
            except Exception as e:
                try:
                    current_app.logger.warning(f"汇率API调用失败: {e}")
                except RuntimeError:
                    # 在应用上下文外部运行时
                    print(f"警告: 汇率API调用失败: {e}")
                continue

        return None

    def _fetch_from_frankfurter(self, from_currency: str, to_currency: str) -> Optional[Decimal]:
        """Independent central-bank-based reference rate source."""
        response = requests.get(
            "https://api.frankfurter.dev/v1/latest",
            params={"base": from_currency, "symbols": to_currency},
            timeout=self._get_request_timeout(),
        )
        response.raise_for_status()
        data = response.json()
        if data.get("base") != from_currency:
            return None
        value = (data.get("rates") or {}).get(to_currency)
        return Decimal(str(value)) if value is not None else None

    def _fetch_from_exchangerate_api(self, from_currency: str, to_currency: str) -> Optional[Decimal]:
        """从 exchangerate-api.com 获取汇率"""
        try:
            url = f"https://api.exchangerate-api.com/v4/latest/{from_currency}"
            response = requests.get(url, timeout=self._get_request_timeout())
            response.raise_for_status()

            data = response.json()
            if "rates" in data and to_currency in data["rates"]:
                rate = Decimal(str(data["rates"][to_currency]))
                try:
                    current_app.logger.info(
                        f"从 exchangerate-api 获取汇率 {from_currency}->{to_currency}: {rate}"
                    )
                except RuntimeError:
                    print(f"信息: 从 exchangerate-api 获取汇率 {from_currency}->{to_currency}: {rate}")
                return rate
        except Exception as e:
            try:
                current_app.logger.warning(f"exchangerate-api 调用失败: {e}")
            except RuntimeError:
                print(f"警告: exchangerate-api 调用失败: {e}")

        return None

    def _fetch_from_fixer_api(self, from_currency: str, to_currency: str) -> Optional[Decimal]:
        """从 fixer.io 获取汇率（需要API key）"""
        try:
            # 这里需要配置API key
            try:
                api_key = current_app.config.get("FIXER_API_KEY")
            except RuntimeError:
                api_key = None

            if not api_key:
                return None

            url = f"http://data.fixer.io/api/latest?access_key={api_key}&base={from_currency}&symbols={to_currency}"
            response = requests.get(url, timeout=self._get_request_timeout())
            response.raise_for_status()

            data = response.json()
            if data.get("success") and "rates" in data and to_currency in data["rates"]:
                rate = Decimal(str(data["rates"][to_currency]))
                try:
                    current_app.logger.info(f"从 fixer.io 获取汇率 {from_currency}->{to_currency}: {rate}")
                except RuntimeError:
                    print(f"信息: 从 fixer.io 获取汇率 {from_currency}->{to_currency}: {rate}")
                return rate
        except Exception as e:
            try:
                current_app.logger.warning(f"fixer.io 调用失败: {e}")
            except RuntimeError:
                print(f"警告: fixer.io 调用失败: {e}")

        return None

    def convert_currency(self, amount: float, from_currency: str, to_currency: str) -> Decimal:
        """将金额从一种货币转换为另一种货币"""
        if not amount:
            return Decimal("0")

        # 如果目标货币为空或None，直接返回原始金额
        if not to_currency:
            return Decimal(str(amount)).quantize(Decimal("0.01"))

        if from_currency == to_currency:
            return Decimal(str(amount)).quantize(Decimal("0.01"))

        rate = self.get_exchange_rate(from_currency, to_currency)

        amount_decimal = Decimal(str(amount))
        converted_amount = amount_decimal * rate

        return converted_amount.quantize(Decimal("0.01"))  # 保留两位小数

    def convert_to_cny(self, amount: float, from_currency: str) -> Decimal:
        """将任意货币金额转换为CNY"""
        return self.convert_currency(amount, from_currency, "CNY")

    def convert_usd_to_cny(self, usd_amount: float) -> Decimal:
        """将USD金额转换为CNY（保持向后兼容）"""
        return self.convert_currency(usd_amount, "USD", "CNY")

    def get_supported_currencies(self) -> list:
        """获取支持的货币列表"""
        return self.supported_currencies.copy()

    def is_currency_supported(self, currency: str) -> bool:
        """检查货币是否被支持"""
        return currency.upper() in self.supported_currencies


# 创建全局实例
exchange_rate_service = ExchangeRateService()
