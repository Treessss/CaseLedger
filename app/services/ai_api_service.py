"""AI 接口：API Key 管理、鉴权与调用审计（第一版聚焦客服邮件）。"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from app import db
from app.models import AiApiAudit, AiApiKey, Shop
from app.utils.errors import ForbiddenError, NotFoundError, RateLimitError, ValidationError

_RATE_LIMITS = {}
RATE_LIMIT_PER_MINUTE = 120


SCOPES = {
    "mail_read": "读取客服邮件（列表/详情/附件信息/翻译）",
    "mail_send": "保存/删除草稿、发送回复、标记忽略、同步邮箱、生成草稿",
    "orders_read": "订单列表/详情/时间线（PII 脱敏）",
    "logistics_read": "物流轨迹列表/详情/异常汇总",
    "procurement_read": "采购候选/采购清单",
    "inventory_read": "库存汇总/库存流水",
    "after_sales_read": "售后列表/详情/待补发",
    "after_sales_write": "发起售后补发、修改备注、登记售后费用/补发物流（不能删除/退款）",
    "purchase_draft": "创建采购清单草稿（不能确认下单）",
}
DEFAULT_SCOPES = tuple(SCOPES.keys())


def _client_ip(req):
    forwarded = (req.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    return forwarded or (req.remote_addr or "")


def _rate_limit(key_id):
    """简单内存限流：每个 worker 进程每 Key 每分钟 120 次。"""
    import time
    from collections import deque

    now = time.time()
    window = _RATE_LIMITS.setdefault(key_id, deque())
    while window and now - window[0] > 60:
        window.popleft()
    if len(window) >= RATE_LIMIT_PER_MINUTE:
        raise RateLimitError("调用过于频繁，请稍后重试（每分钟最多 120 次）")
    window.append(now)


def today_sent_count(key_id):
    """今天该 Key 成功/尝试发送的邮件次数（按审计记录统计）。"""
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return AiApiAudit.query.filter(
        AiApiAudit.key_id == key_id,
        AiApiAudit.method == "POST",
        (AiApiAudit.path.like("%/send") | AiApiAudit.path.like("%/send-with-attachments")),
        AiApiAudit.status_code < 400,
        AiApiAudit.created_at >= start.replace(tzinfo=None),
    ).count()


def _aware(value):
    """PostgreSQL timestamptz 读出来是 aware，Python 默认是 naive，统一成 UTC。"""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _hash_key(raw_key):
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def create_key(payload):
    name = str(payload.get("name") or "").strip()
    if not name or len(name) > 100:
        raise ValidationError("请填写密钥名称（1–100 字）")
    raw_shops = payload.get("shop_ids")
    if not isinstance(raw_shops, list) or not raw_shops:
        raise ValidationError("请选择该密钥可访问的店铺")
    shop_ids = []
    for value in raw_shops:
        try:
            shop_id = int(value)
        except (TypeError, ValueError):
            continue
        if shop_id > 0 and shop_id not in shop_ids:
            shop_ids.append(shop_id)
    existing = {row.id for row in Shop.query.filter(Shop.id.in_(shop_ids)).all()} if shop_ids else set()
    if not shop_ids or len(existing) != len(shop_ids):
        raise ValidationError("店铺不存在或已删除")
    raw_scopes = payload.get("scopes")
    if raw_scopes is None:
        scopes = list(DEFAULT_SCOPES)
    elif isinstance(raw_scopes, list):
        scopes = [key for key in raw_scopes if key in SCOPES]
        if not scopes:
            raise ValidationError("请至少选择一个接口范围")
    else:
        raise ValidationError("scopes 必须是数组")
    raw_ips = payload.get("ip_allowlist") or []
    if not isinstance(raw_ips, list):
        raise ValidationError("IP 白名单必须是数组")
    ip_allowlist = []
    for value in raw_ips:
        text = str(value or "").strip()
        if text and text not in ip_allowlist:
            if len(text) > 64 or any(char.isspace() for char in text):
                raise ValidationError("IP 格式不正确")
            ip_allowlist.append(text)
    try:
        daily_send_limit = int(payload.get("daily_send_limit") or 100)
    except (TypeError, ValueError):
        raise ValidationError("每日发送上限必须是整数") from None
    if not 1 <= daily_send_limit <= 10000:
        raise ValidationError("每日发送上限需在 1–10000 之间")
    expires_at = None
    days = payload.get("expires_in_days")
    if days not in (None, ""):
        try:
            days = int(days)
        except (TypeError, ValueError):
            raise ValidationError("有效期必须是 1–365 天") from None
        if not 1 <= days <= 365:
            raise ValidationError("有效期必须是 1–365 天")
        expires_at = datetime.now(timezone.utc) + timedelta(days=days)
    raw_key = "cai_" + secrets.token_hex(24)
    key = AiApiKey(name=name, key_prefix=raw_key[:12], key_hash=_hash_key(raw_key),
                   shop_ids=shop_ids, scopes=scopes, ip_allowlist=ip_allowlist,
                   daily_send_limit=daily_send_limit, created_by=payload.get("created_by"),
                   expires_at=expires_at)
    db.session.add(key)
    db.session.commit()
    return {**key.to_dict(), "api_key": raw_key}


def _usage_map():
    """每个 Key 今日调用次数与发送次数（一次查询，供列表展示）。"""
    from sqlalchemy import func as sa_func, or_

    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    start_naive = start.replace(tzinfo=None)
    calls = dict(db.session.query(AiApiAudit.key_id, sa_func.count(AiApiAudit.id)).filter(
        AiApiAudit.created_at >= start_naive).group_by(AiApiAudit.key_id).all())
    sends = dict(db.session.query(AiApiAudit.key_id, sa_func.count(AiApiAudit.id)).filter(
        AiApiAudit.created_at >= start_naive,
        AiApiAudit.method == "POST",
        or_(AiApiAudit.path.like("%/send"), AiApiAudit.path.like("%/send-with-attachments")),
        AiApiAudit.status_code < 400,
    ).group_by(AiApiAudit.key_id).all())
    return calls, sends


def list_keys():
    rows = AiApiKey.query.order_by(AiApiKey.created_at.desc()).all()
    calls, sends = _usage_map()
    keys = []
    for row in rows:
        data = row.to_dict()
        data["today_calls"] = int(calls.get(row.id, 0))
        data["today_sends"] = int(sends.get(row.id, 0))
        keys.append(data)
    return {"keys": keys, "scopes": SCOPES}


def update_key(key_id, payload):
    key = db.session.get(AiApiKey, key_id)
    if key is None:
        raise NotFoundError("密钥不存在")
    if "name" in payload:
        name = str(payload.get("name") or "").strip()
        if not name or len(name) > 100:
            raise ValidationError("密钥名称需为 1–100 字")
        key.name = name
    if "shop_ids" in payload:
        shop_ids = _normalize_shop_ids(payload.get("shop_ids"))
        key.shop_ids = shop_ids
    if "scopes" in payload:
        raw = payload.get("scopes")
        if not isinstance(raw, list) or not [item for item in raw if item in SCOPES]:
            raise ValidationError("请至少选择一个接口范围")
        key.scopes = [item for item in raw if item in SCOPES]
    if "ip_allowlist" in payload:
        raw_ips = payload.get("ip_allowlist") or []
        if not isinstance(raw_ips, list):
            raise ValidationError("IP 白名单必须是数组")
        key.ip_allowlist = [str(item).strip() for item in raw_ips if str(item).strip()]
    if "daily_send_limit" in payload:
        try:
            limit = int(payload.get("daily_send_limit"))
        except (TypeError, ValueError):
            raise ValidationError("每日发送上限必须是整数") from None
        if not 1 <= limit <= 10000:
            raise ValidationError("每日发送上限需在 1–10000 之间")
        key.daily_send_limit = limit
    if "expires_in_days" in payload:
        days = payload.get("expires_in_days")
        if days in (None, ""):
            key.expires_at = None
        else:
            try:
                days = int(days)
            except (TypeError, ValueError):
                raise ValidationError("有效期必须是 1–365 天") from None
            if not 1 <= days <= 365:
                raise ValidationError("有效期必须是 1–365 天")
            key.expires_at = datetime.now(timezone.utc) + timedelta(days=days)
    db.session.commit()
    return key.to_dict()


def rotate_key(key_id):
    """轮换 Key：旧 Key 立即失效，返回新的明文 Key（只显示一次）。"""
    key = db.session.get(AiApiKey, key_id)
    if key is None:
        raise NotFoundError("密钥不存在")
    raw_key = "cai_" + secrets.token_hex(24)
    key.key_prefix = raw_key[:12]
    key.key_hash = _hash_key(raw_key)
    key.is_active = True
    key.last_used_at = None
    db.session.commit()
    return {**key.to_dict(), "api_key": raw_key}


def _normalize_shop_ids(raw_shops):
    if not isinstance(raw_shops, list) or not raw_shops:
        raise ValidationError("请选择该密钥可访问的店铺")
    shop_ids = []
    for value in raw_shops:
        try:
            shop_id = int(value)
        except (TypeError, ValueError):
            continue
        if shop_id > 0 and shop_id not in shop_ids:
            shop_ids.append(shop_id)
    existing = {row.id for row in Shop.query.filter(Shop.id.in_(shop_ids)).all()} if shop_ids else set()
    if not shop_ids or len(existing) != len(shop_ids):
        raise ValidationError("店铺不存在或已删除")
    return shop_ids


def usage_summary(key):
    calls, sends = _usage_map()
    return {
        "today_calls": int(calls.get(key.id, 0)),
        "today_sends": int(sends.get(key.id, 0)),
        "daily_send_limit": key.daily_send_limit,
        "daily_send_remaining": max(int(key.daily_send_limit or 0) - int(sends.get(key.id, 0)), 0),
        "rate_limit_per_minute": RATE_LIMIT_PER_MINUTE,
    }


def list_audit(args):
    page = max(int(args.get("page") or 1), 1)
    per_page = min(max(int(args.get("per_page") or 20), 1), 100)
    query = AiApiAudit.query
    raw_key = args.get("key_id")
    if raw_key not in (None, ""):
        try:
            query = query.filter(AiApiAudit.key_id == int(raw_key))
        except (TypeError, ValueError):
            pass
    total = query.count()
    rows = query.order_by(AiApiAudit.created_at.desc()).offset((page - 1) * per_page).limit(per_page).all()
    return {
        "items": [
            {"id": row.id, "key_id": row.key_id, "method": row.method, "path": row.path,
             "status_code": row.status_code, "duration_ms": row.duration_ms, "ip": row.ip,
             "created_at": row.created_at.isoformat() if row.created_at else None}
            for row in rows
        ],
        "pagination": {"page": page, "per_page": per_page, "total": total,
                       "pages": (total + per_page - 1) // per_page if per_page else 0},
    }


def revoke_key(key_id):
    key = db.session.get(AiApiKey, key_id)
    if key is None:
        raise NotFoundError("密钥不存在")
    key.is_active = False
    db.session.commit()
    return key.to_dict()


def authenticate_request(req):
    """校验外部请求的 API Key；成功返回 key 行并刷新 last_used_at。"""
    raw = (req.headers.get("X-API-Key") or "").strip()
    if not raw:
        header = req.headers.get("Authorization", "")
        if header.startswith("Bearer cai_"):
            raw = header[7:].strip()
    if not raw.startswith("cai_"):
        return None
    key = AiApiKey.query.filter_by(key_hash=_hash_key(raw)).first()
    if key is None or not key.is_active:
        return None
    if _aware(key.expires_at) and _aware(key.expires_at) < datetime.now(timezone.utc):
        return None
    client_ip = _client_ip(req)
    if key.ip_allowlist and client_ip not in key.ip_allowlist:
        return None
    _rate_limit(key.id)
    key.last_used_at = datetime.now(timezone.utc)
    db.session.commit()
    return key


def record_audit(key_id, method, path, status_code, duration_ms, ip):
    try:
        db.session.add(AiApiAudit(key_id=key_id, method=method, path=path[:500],
                                  status_code=status_code, duration_ms=duration_ms, ip=(ip or "")[:64]))
        db.session.commit()
    except Exception:  # noqa: BLE001 - 审计失败不影响接口响应
        db.session.rollback()
