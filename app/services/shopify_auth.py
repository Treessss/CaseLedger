"""Shopify 授权：统一用「域名 + Client ID + Client Secret」换访问令牌。

绑定口径（全项目唯一）：
    每个店铺只保存 ``shop_url`` / ``oauth_client_id`` / ``oauth_client_secret``，
    通过 ``client_credentials`` 换临时令牌，不再保存永久 access_token。

为什么不用后台自建应用的 shpat_ 永久令牌：
    Shopify 只把客户隐私数据（姓名、收货地址、电话、邮箱）开放给已获批
    PII 权限的应用，自建应用拿不到，订单详情只剩国家/省份。

换来的令牌在进程内缓存，到期前自动续，401 时清缓存重换。
"""

import threading
import time

import requests

from app.models.shop import Shop as ShopModel

# 接口版本（GraphQL 与 REST 都用这个版本）
API_VERSION = "2026-07"
TIMEOUT = 30

# 提前 5 分钟视为过期，留出重试余量
_TOKEN_SAFETY_SECONDS = 300
_token_cache = {}
_token_lock = threading.Lock()


class ShopifyAuthError(RuntimeError):
    """店铺缺少凭据，或换票失败。"""


def _global_credentials():
    """全局兜底（.env 的 SHOPIFY_OAUTH_CLIENT_ID / SECRET），仅用于单店部署。"""
    try:
        from flask import current_app

        client_id = current_app.config.get("SHOPIFY_OAUTH_CLIENT_ID")
        client_secret = current_app.config.get("SHOPIFY_OAUTH_CLIENT_SECRET")
    except (RuntimeError, ImportError):
        return None
    if client_id and client_secret:
        return client_id, client_secret
    return None


def credentials_for(shop):
    """返回该店铺的 ``(client_id, client_secret)``；没配置返回 None。"""
    if shop is None:
        return _global_credentials()

    if getattr(shop, "oauth_client_id", None):
        try:
            secret = shop.get_oauth_client_secret()
        except Exception as exc:  # noqa: BLE001 - 解密失败要给可执行提示
            raise ShopifyAuthError(str(exc)) from exc
        if secret:
            return shop.oauth_client_id, secret

    return _global_credentials()


def has_credentials(shop):
    """该店铺能否换到令牌（用于列表状态展示，不触发网络请求）。"""
    try:
        return bool(credentials_for(shop))
    except ShopifyAuthError:
        return False


def _cache_key(shop, client_id):
    return f"{ShopModel.normalize_shop_url(shop.shop_url)}::{client_id}"


def _mint_token(shop, client_id, client_secret, timeout=TIMEOUT):
    """走 client_credentials 换一个临时令牌，返回 ``(token, 过期时间戳)``。"""
    shop_url = ShopModel.normalize_shop_url(shop.shop_url)
    if not shop_url:
        raise ShopifyAuthError("店铺缺少域名，无法换取访问令牌")

    try:
        response = requests.post(
            f"https://{shop_url}/admin/oauth/access_token",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
            },
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise ShopifyAuthError(f"连接 Shopify 换取令牌失败：{exc}") from exc

    if response.status_code != 200:
        raise ShopifyAuthError(
            f"店铺「{shop.name or shop_url}」换票失败：Client ID / Client Secret 不正确，"
            f"或该应用未安装到 {shop_url}（HTTP {response.status_code}）"
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise ShopifyAuthError("换票接口返回了非 JSON 内容") from exc

    token = payload.get("access_token")
    if not token:
        raise ShopifyAuthError(
            f"换票失败：{payload.get('error_description') or payload.get('error') or '未知原因'}"
        )

    expires_in = payload.get("expires_in") or 86400
    return token, time.time() + max(expires_in - _TOKEN_SAFETY_SECONDS, 60)


def get_access_token(shop, timeout=TIMEOUT):
    """取该店铺的访问令牌（带进程内缓存与并发保护）。"""
    credentials = credentials_for(shop)
    if not credentials:
        raise ShopifyAuthError(
            f"店铺「{shop.name or shop.shop_url}」尚未绑定应用凭据，"
            "请在店铺管理里填写 Client ID 与 Client Secret"
        )

    client_id, client_secret = credentials
    key = _cache_key(shop, client_id)

    cached = _token_cache.get(key)
    if cached and cached[1] > time.time():
        return cached[0]

    with _token_lock:
        cached = _token_cache.get(key)
        if cached and cached[1] > time.time():
            return cached[0]
        token, expires_at = _mint_token(shop, client_id, client_secret, timeout=timeout)
        _token_cache[key] = (token, expires_at)
        return token


def invalidate_token(shop):
    """清掉缓存的令牌（收到 401 时调用，下次自动重换）。"""
    try:
        credentials = credentials_for(shop)
    except ShopifyAuthError:
        return
    if not credentials:
        return
    _token_cache.pop(_cache_key(shop, credentials[0]), None)


def api_root(shop, version=API_VERSION):
    """``https://{店铺}/admin/api/{版本}``。"""
    return f"https://{ShopModel.normalize_shop_url(shop.shop_url)}/admin/api/{version}"
