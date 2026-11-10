"""Small 4PX direct-shipping API transport; deliberately no database side effects.

The caller supplies credentials from a shop-scoped, encrypted configuration.
This module never reads environment variables, logs payloads, or retries a
creation request: a timeout after submission may still mean a parcel exists.
"""

import hashlib
import json
import re
import time

import requests


ENDPOINTS = {
    "test": "https://open-test.4px.com/router/api/service",
    "production": "https://open.4px.com/router/api/service",
}
METHODS = frozenset({"ds.xms.order.create", "ds.xms.order.get", "ds.xms.label.get", "ds.xms.label.getlist",
                     "tr.order.tracking.get", "ds.xms.order.getFreight"})


class FourPxError(RuntimeError):
    """A transport, authorization, or 4PX business error without request secrets."""


def compact_json(body):
    """Serialize once and use these exact bytes for both signing and POST."""
    return json.dumps(body, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def sign_request(params, body_text, app_secret):
    """4PX MD5: sorted public key/value pairs + compact body + app secret."""
    signed = "".join(
        f"{key}{value}"
        for key, value in sorted(params.items())
        if key not in {"sign", "access_token", "language"}
    )
    return hashlib.md5(f"{signed}{body_text}{app_secret}".encode("utf-8")).hexdigest()


class FourPxClient:
    def __init__(self, app_key, app_secret, *, environment="test", access_token=None, session=None):
        if environment not in ENDPOINTS:
            raise ValueError("Unknown 4PX environment")
        if not app_key or not app_secret:
            raise ValueError("4PX credentials are required")
        self.app_key = app_key
        self.app_secret = app_secret
        self.endpoint = ENDPOINTS[environment]
        self.access_token = access_token
        self.session = session or requests.Session()

    def call(self, method, body, *, version="1.0", timestamp_ms=None, language=None, timeout=30):
        if method not in METHODS:
            raise ValueError("Unsupported 4PX method")
        if not isinstance(body, dict):
            raise ValueError("4PX request body must be an object")
        params = {
            "app_key": self.app_key,
            "format": "json",
            "method": method,
            "timestamp": str(timestamp_ms if timestamp_ms is not None else time.time_ns() // 1_000_000),
            "v": version,
        }
        if self.access_token:
            params["access_token"] = self.access_token
        if language:
            params["language"] = language
        body_text = compact_json(body)
        params["sign"] = sign_request(params, body_text, self.app_secret)
        try:
            response = self.session.post(
                self.endpoint,
                params=params,
                data=body_text.encode("utf-8"),
                headers={"Content-Type": "application/json"},
                timeout=timeout,
            )
            response.raise_for_status()
            result = response.json()
        except (requests.RequestException, ValueError):
            # Never chain the exception: requests may embed signed URLs and PII.
            raise FourPxError("4PX 请求失败或响应无效，请核对平台状态后再重试") from None
        if not isinstance(result, dict) or str(result.get("result")) != "1":
            if method == "tr.order.tracking.get":
                raise FourPxError("4PX 尚未返回可核验的物流轨迹，请稍后重试")
            error = FourPxError("4PX 未确认操作成功，请核对平台记录；不要直接重复预报")
            errors = result.get("errors") if isinstance(result, dict) else None
            if isinstance(errors, list):
                messages = [str(row.get("error_msg") or "").strip()
                            for row in errors if isinstance(row, dict) and row.get("error_msg")]
                if messages:
                    error.details = "；".join(messages)[:600]
            raise error
        return result.get("data")

    def get_tracking(self, number):
        """Read the official carrier history, not Shopify fulfillment events."""
        if not isinstance(number, str) or not re.fullmatch(r'[A-Za-z0-9-]{1,120}', number):
            raise ValueError('4PX tracking number is invalid')
        return self.call('tr.order.tracking.get', {'deliveryOrderNo': number},
                         version='1.0.0', language='en', timeout=12)

    def create_order(self, declaration):
        """Submit a complete, externally validated declaration exactly once.

        The caller must persist a stable ref_no and check 4PX by that number
        before retrying a timed-out create operation.
        """
        if not declaration.get("ref_no"):
            raise ValueError("4PX declaration requires ref_no")
        return self.call("ds.xms.order.create", declaration, version="1.1.0")

    def get_order(self, request_no):
        if not request_no:
            raise ValueError("4PX request_no is required")
        return self.call("ds.xms.order.get", {"request_no": request_no}, version="1.1.0")

    def get_predeclared_created_between(self, start_ms, end_ms):
        """Read only: 4PX limits a consignment creation-time query to 7 days."""
        if start_ms >= end_ms or end_ms - start_ms > 7 * 24 * 60 * 60 * 1000:
            raise ValueError("4PX query window must be positive and at most seven days")
        # 不按状态过滤：售后补发/对单需要同时看到已预报、已交接、库内作业、
        # 已出库的委托单（CF 单经常在我们查之前就已经出库了）。
        return self.call("ds.xms.order.get", {
            "start_time_of_create_consignment": start_ms,
            "end_time_of_create_consignment": end_ms,
        }, version="1.1.0")

    def get_freight(self, request_no):
        """4PX 实际出账费用（计费重 + 费用明细 + 总费用）；未出账时 4PX 返回失败。"""
        if not request_no:
            raise ValueError("4PX request_no is required")
        return self.call("ds.xms.order.getFreight", {"request_no": request_no}, version="1.0.0")

    def get_label(self, request_no):
        if not request_no:
            raise ValueError("4PX request_no is required")
        return self.call("ds.xms.label.get", {
            "request_no": request_no,
            "response_label_format": "PDF",
        }, version="1.1.0")

    def get_labels_pdf(self, request_numbers, logistics_product_code):
        """Official batch-label API: at most 30 parcels of one logistics product."""
        if not 1 <= len(request_numbers) <= 30 or not logistics_product_code:
            raise ValueError("4PX batch labels require 1-30 numbers and a logistics product code")
        return self.call("ds.xms.label.getlist", {
            "request_no": request_numbers,
            "logistics_product_code": logistics_product_code,
        }, version="1.0.0")
