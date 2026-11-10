"""Zoho Mail 的接入层：配置、凭据、并发锁、HTTP 调用。

只负责"怎么跟 Zoho 说话"，业务判断都在 mail_service 里。

性能相关的两点：
* access token 缓存在库里（带过期时间），常规请求不会每次都去换 token；
* 刷新 token / 同步 / 发送各用一把跨进程锁，避免并发时重复刷新或重复同步。
"""

import time
import uuid
import hashlib

import requests
from sqlalchemy.exc import IntegrityError

from app import db
from app.models import MailLock, MailSetting
from app.services.mail_scope import shop_id
from app.utils.errors import ConflictError, ValidationError
from app.utils.secrets import open_sealed, seal

# Zoho 数据中心 → 域名后缀
DC_DOMAINS = {
    "com": "com",
    "eu": "eu",
    "in": "in",
    "comau": "com.au",
    "jp": "jp",
    "ca": "ca",
}

HTTP_TIMEOUT = 25

# GET 请求遇到网络抖动自动重试几次。同步要翻几百页，Zoho 偶尔会掐连接。
# POST（发信、换 token）不重试：结果不明时重发可能造成重复发送。
GET_RETRY_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 1.5

# 敏感配置：读的时候自动解密，写的时候自动加密
SECRET_KEYS = {"refresh", "access", "deepseek", "zoho_client_secret"}


def now_ms():
    return int(time.time() * 1000)


def get_setting(key, default=""):
    row = db.session.get(MailSetting, f"s{shop_id()}:{key}")
    if row is None or row.value is None:
        return default
    if key in SECRET_KEYS:
        return open_sealed(row.value)
    return row.value


def put_setting(key, value):
    stored = seal(value) if key in SECRET_KEYS else (value or "")
    scoped_key = f"s{shop_id()}:{key}"
    row = db.session.get(MailSetting, scoped_key)
    if row is None:
        db.session.add(MailSetting(key=scoped_key, value=stored))
    else:
        row.value = stored
    db.session.commit()


def acquire_lock(name, ttl_ms=180_000):
    """抢一把锁；抢不到返回 None。

    锁是"过期即失效"的：进程被中断后不会永久卡住。
    这里在请求最开始调用，所以抢失败时的回滚不会丢掉已有改动。
    """
    name = _lock_name(name)
    token = uuid.uuid4().hex
    deadline = now_ms() + ttl_ms
    row = db.session.get(MailLock, name)
    if row is None:
        db.session.add(MailLock(name=name, token=token, expires_at=deadline))
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            return None
        return token
    if row.expires_at > now_ms():
        return None
    row.token = token
    row.expires_at = deadline
    db.session.commit()
    return token


def release_lock(name, token):
    if not token:
        return
    name = _lock_name(name)
    db.session.query(MailLock).filter(
        MailLock.name == name, MailLock.token == token
    ).delete(synchronize_session=False)
    db.session.commit()


def _lock_name(name):
    return f"s{shop_id()}:" + hashlib.sha256(name.encode()).hexdigest()[:40]


def busy_error(message="另一项邮件操作正在处理，请稍后重试"):
    return ConflictError(message)


def domain():
    return DC_DOMAINS.get(get_setting("dc", "com"), "com")


def client_credentials():
    """Zoho OAuth 客户端凭据：优先用 .env，其次用设置里加密保存的值。

    两种都支持，是为了不必为了填一次 Client Secret 就去改 .env 重启服务。
    """
    import os

    from flask import current_app

    client_id = (
        os.environ.get("ZOHO_CLIENT_ID")
        or current_app.config.get("ZOHO_CLIENT_ID")
        or get_setting("zoho_client_id")
    )
    client_secret = (
        os.environ.get("ZOHO_CLIENT_SECRET")
        or current_app.config.get("ZOHO_CLIENT_SECRET")
        or get_setting("zoho_client_secret")
    )
    return client_id, client_secret


def _refresh_access_token():
    client_id, client_secret = client_credentials()
    if not client_id or not client_secret:
        raise ValidationError(
            "还没有配置 Zoho Client ID / Client Secret，请在「连接与设置」里填写"
        )
    refresh = get_setting("refresh")
    if not refresh:
        raise ValidationError("请先在设置里用授权码连接 Zoho 邮箱")
    response = requests.post(
        f"https://accounts.zoho.{domain()}/oauth/v2/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh,
            "client_id": client_id,
            "client_secret": client_secret,
        },
        timeout=HTTP_TIMEOUT,
    )
    payload = response.json()
    if "access_token" not in payload:
        error = str(payload.get("error") or "")
        if error in ("invalid_code", "invalid_client", "invalid_grant"):
            raise ValidationError(
                "Zoho 授权已失效（Refresh Token 无效或被撤销），请重新连接邮箱"
            )
        raise ValidationError(f"Zoho 换取访问令牌失败：{error or '未知错误'}")
    put_setting("access", payload["access_token"])
    put_setting("access_expires", str(now_ms() + int(payload.get("expires_in", 3600)) * 1000))
    return payload["access_token"]


def access_token():
    """拿一个可用的 access token；没到期就直接用缓存。"""
    cached = get_setting("access")
    expires = int(get_setting("access_expires", "0") or 0)
    # 留 60 秒余量，避免边界上刚好过期
    if cached and expires - 60_000 > now_ms():
        return cached
    token = acquire_lock("oauth-refresh", ttl_ms=60_000)
    if token is None:
        # 别人正在刷新：短暂等待后直接用新缓存
        time.sleep(1.5)
        fresh = get_setting("access")
        if fresh and int(get_setting("access_expires", "0") or 0) - 60_000 > now_ms():
            return fresh
        return _refresh_access_token()
    try:
        return _refresh_access_token()
    finally:
        release_lock("oauth-refresh", token)


def connect_account(payload):
    """用授权码换 refresh token 并绑定邮箱账户。"""
    dc = str(payload.get("dc") or "com")
    if dc not in DC_DOMAINS:
        raise ValidationError("请选择正确的 Zoho 区域")
    client_id, client_secret = client_credentials()
    if not client_id or not client_secret:
        raise ValidationError(
            "还没有配置 Zoho Client ID / Client Secret，请在「连接与设置」里填写"
        )

    refresh = str(payload.get("refresh") or "").strip()
    if not refresh:
        code = str(payload.get("code") or "").strip()
        if not code:
            raise ValidationError("请填写授权码或 Refresh Token")
        body = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
        }
        if payload.get("redirect_uri"):
            body["redirect_uri"] = str(payload["redirect_uri"]).strip()
        response = requests.post(
            f"https://accounts.zoho.{DC_DOMAINS[dc]}/oauth/v2/token",
            data=body,
            timeout=HTTP_TIMEOUT,
        )
        result = response.json()
        if not result.get("refresh_token"):
            raise ValidationError(
                "授权码无效、已过期或 Client 类型不匹配。请重新生成授权码再试。"
            )
        refresh = result["refresh_token"]

    put_setting("dc", dc)
    put_setting("refresh", refresh)
    put_setting("access", "")
    put_setting("access_expires", "0")

    accounts = api("accounts")
    email = str(payload.get("email") or "").strip().lower()
    account = None
    for item in accounts:
        addresses = [str(a.get("mailId", "")).lower() for a in item.get("emailAddress", [])]
        primary = str(item.get("primaryEmailAddress", "")).lower()
        if email and (email in addresses or email == primary):
            account = item
            break
        if not email and addresses:
            account = item
            break
    if account is None:
        raise ValidationError("授权账户中未找到指定邮箱，请核对邮箱地址")

    other = MailSetting.query.filter(
        MailSetting.key.like("s%:account"),
        MailSetting.key != f"s{shop_id()}:account",
        MailSetting.value == str(account["accountId"]),
    ).first()
    if other:
        raise ValidationError("该邮箱账户已经绑定其他店铺，不能重复绑定")

    bound = get_setting("account")
    if bound and bound != str(account["accountId"]):
        raise ValidationError("这个工作台已经绑定另一个邮箱账户，不能混用邮件记录")
    put_setting("account", str(account["accountId"]))
    put_setting("email", (email or str(account.get("primaryEmailAddress", "")).lower()))
    put_setting(
        "own",
        ",".join(
            str(a.get("mailId", "")).lower() for a in account.get("emailAddress", [])
        ),
    )
    return status()


def api(path, method="GET", body=None, params=None, timeout=HTTP_TIMEOUT):
    """调用 Zoho Mail API，统一把错误转成可读提示。"""
    url = f"https://mail.zoho.{domain()}/api/{path}"
    attempts = GET_RETRY_ATTEMPTS if method.upper() == "GET" else 1
    response = None
    for attempt in range(attempts):
        try:
            response = requests.request(
                method,
                url,
                headers={
                    "Authorization": f"Zoho-oauthtoken {access_token()}",
                    "Content-Type": "application/json",
                },
                json=body,
                params=params,
                timeout=timeout,
            )
        except requests.RequestException as exc:
            if attempt + 1 < attempts:
                time.sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))
                continue
            raise ValidationError(
                f"连接 Zoho 失败（{exc.__class__.__name__}），网络可能不稳定，请稍后重试"
            ) from exc
        # 5xx 通常是服务端瞬时故障，重试一次往往就好了
        if response.status_code >= 500 and attempt + 1 < attempts:
            time.sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))
            continue
        break
    try:
        payload = response.json()
    except ValueError:
        raise ValidationError(f"Zoho 返回了非 JSON 响应（HTTP {response.status_code}）")
    code = (payload.get("status") or {}).get("code")
    if response.status_code >= 400 or (code is not None and int(code) >= 400):
        description = (payload.get("status") or {}).get("description")
        raise ValidationError(
            f"Zoho 请求失败（HTTP {response.status_code}/{code or 'unknown'}）："
            f"{description or '请检查权限或稍后重试'}"
        )
    return payload.get("data")


def upload_attachment(filename, content, mimetype):
    """Upload one attachment to Zoho and return the metadata needed to send it.

    Zoho wants multipart field ``attach``; the send API then references the
    returned ``attachmentName`` / ``attachmentPath`` / ``storeName``.
    """
    account = get_setting("account")
    if not account:
        raise ValidationError("邮箱还没有连接，无法上传附件")
    url = (f"https://mail.zoho.{domain()}/api/accounts/{account}"
           "/messages/attachments?uploadType=multipart")
    try:
        response = requests.post(
            url,
            headers={"Authorization": f"Zoho-oauthtoken {access_token()}"},
            files=[("attach", (filename, content, mimetype))],
            timeout=60,
        )
    except requests.RequestException as exc:
        raise ValidationError(
            f"上传附件到 Zoho 失败（{exc.__class__.__name__}），请稍后重试"
        ) from exc
    try:
        payload = response.json()
    except ValueError as exc:
        raise ValidationError(f"Zoho 附件上传返回了非 JSON 响应（HTTP {response.status_code}）") from exc
    code = (payload.get("status") or {}).get("code")
    if response.status_code >= 400 or (code is not None and int(code) >= 400):
        description = (payload.get("status") or {}).get("description")
        raise ValidationError(
            f"Zoho 附件上传失败（HTTP {response.status_code}/{code or 'unknown'}）："
            f"{description or '请检查附件大小或稍后重试'}"
        )
    rows = payload.get("data")
    if isinstance(rows, dict):
        rows = [rows]
    if not isinstance(rows, list) or not rows:
        raise ValidationError("Zoho 附件上传没有返回附件信息，请稍后重试")
    row = rows[0]
    if not row.get("attachmentName") or not row.get("attachmentPath") or not row.get("storeName"):
        raise ValidationError("Zoho 附件上传返回的信息不完整，请稍后重试")
    return {
        "attachmentName": row["attachmentName"],
        "attachmentPath": row["attachmentPath"],
        "storeName": str(row["storeName"]),
    }


def status():
    """给前端用的配置/同步状态。"""
    from app.models import MailMessage

    account = get_setting("account")
    client_id, client_secret = client_credentials()
    return {
        "shop_id": shop_id(),
        "connected": bool(account),
        "credentials": bool(client_id and client_secret),
        # 不回传密文，只告诉前端"配过了"
        "client_id": client_id or "",
        "deepseek": bool(get_setting("deepseek")),
        "account": account,
        "email": get_setting("email"),
        "dc": get_setting("dc", "com"),
        "model": get_setting("model", "deepseek-flash"),
        "consent": get_setting("consent") == "true",
        "complete": get_setting("complete") == "true",
        "last_sync": int(get_setting("last_sync", "0") or 0),
        "job_error": get_setting("job_error"),
        "message_count": db.session.query(MailMessage).filter_by(shop_id=shop_id()).count(),
    }
