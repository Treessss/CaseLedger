"""Security helpers: safe error messages and role checks.

safe_error logs the full exception server-side and returns a generic message to clients.
require_admin / require_roles add fine-grained authorization for sensitive endpoints.
"""

import logging
from functools import wraps

from flask import current_app, g, jsonify
from sqlalchemy import false

logger = logging.getLogger(__name__)

ROLE_ADMIN = "admin"
ROLE_USER = "user"


def safe_error(exc, default_message: str = "操作失败"):
    """Convert an internal exception into a client-safe message.

    The real exception (including traceback) is written to the server log. Details are
    only returned to the caller when EXPOSE_ERROR_DETAILS is explicitly enabled, so that
    database structure and file paths do not leak.
    """
    logger.exception("接口处理失败: %s", exc)
    try:
        expose = bool(current_app.config.get("EXPOSE_ERROR_DETAILS"))
    except RuntimeError:
        # Not running inside an application context (e.g. a standalone script)
        expose = False
    if expose:
        return f"{default_message}: {exc}"
    return default_message


def current_user():
    """Current logged-in user payload (written by before_request)."""
    return getattr(g, "current_user", None)


def current_role():
    user = current_user() or {}
    return user.get("authority") or user.get("role") or ROLE_USER


def has_role(*roles):
    return current_role() in roles


def is_admin():
    return current_role() == ROLE_ADMIN


def allowed_shop_ids():
    """当前用户可见的店铺 id 列表。

    管理员返回 ``None``（表示不做店铺过滤）；子账户返回被分配的店铺 id 列表，
    未分配任何店铺时返回空列表（即看不到任何店铺数据）。
    """
    user = current_user() or {}
    if user.get("authority") == ROLE_ADMIN:
        return None
    return list(user.get("shop_ids") or [])


def requested_shop_id(default=None):
    """从请求的查询参数或 JSON 体中读取 shop_id（'all'/空 视为未指定）。"""
    raw = None
    try:
        from flask import request

        raw = request.args.get("shop_id")
        if raw in (None, "", "all") and request.is_json:
            body = request.get_json(silent=True) or {}
            raw = body.get("shop_id")
    except RuntimeError:
        raw = None

    if raw in (None, "", "all"):
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def can_access_shop(shop_id):
    allowed = allowed_shop_ids()
    if allowed is None:
        return True
    return shop_id in allowed


def apply_shop_scope(query, model, shop_id=None):
    """给查询加上店铺过滤条件。

    - 管理员：默认不过滤；显式传入 shop_id 时按该店铺过滤
    - 子账户：只能看到被分配店铺的数据，显式传入非授权店铺时返回空结果
    """
    target = shop_id if shop_id is not None else requested_shop_id()
    allowed = allowed_shop_ids()

    if allowed is None:
        if target is not None:
            return query.filter(model.shop_id == target)
        return query

    if not allowed:
        return query.filter(false())

    if target is not None:
        if target not in allowed:
            return query.filter(false())
        return query.filter(model.shop_id == target)

    return query.filter(model.shop_id.in_(allowed))


def resolve_write_shop_id(payload_shop_id):
    """确定写入数据时归属的店铺。

    返回 ``(shop_id, error_message)``：
    - 管理员：按传入值（允许为空，表示公司级）
    - 子账户：只能写入已分配店铺；只分配了一个店铺时可省略 shop_id
    """
    allowed = allowed_shop_ids()
    if allowed is None:
        return payload_shop_id, None

    if payload_shop_id is None:
        if len(allowed) == 1:
            return allowed[0], None
        return None, "请指定店铺（shop_id）"

    if payload_shop_id not in allowed:
        return None, "无权操作该店铺"

    return payload_shop_id, None


def require_roles(*roles):
    """Decorator: caller must hold one of the given roles."""

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if not has_role(*roles):
                return jsonify({"success": False, "errorCode": "403", "errorMessage": "权限不足"}), 403
            return func(*args, **kwargs)

        return wrapper

    return decorator


def require_admin(func):
    """Decorator: admin-only endpoint."""
    return require_roles(ROLE_ADMIN)(func)
