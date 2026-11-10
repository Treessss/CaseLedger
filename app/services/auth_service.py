"""Authentication service: credentials, admin bootstrap and login throttling.

The throttle state lives here so the route layer stays free of logic.
"""

import hmac
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta

import jwt
from flask import current_app
from werkzeug.security import check_password_hash

from app import db
from app.models import User
from app.utils.errors import LoginError, ValidationError

# key -> 失败时间戳队列；key -> 解锁时间戳
_login_failures = defaultdict(deque)
_login_lockouts = {}
_login_lock = threading.Lock()


def reset_throttle_state():
    """Clear the in-memory throttle (used by tests and manual unblocking)."""
    with _login_lock:
        _login_failures.clear()
        _login_lockouts.clear()


def lockout_remaining(key):
    """Seconds left before the key may retry (0 when not locked)."""
    now = time.time()
    with _login_lock:
        unlock_at = _login_lockouts.get(key)
        if not unlock_at:
            return 0
        if unlock_at <= now:
            _login_lockouts.pop(key, None)
            return 0
        return int(unlock_at - now)


def record_failure(key):
    now = time.time()
    window = current_app.config.get("LOGIN_WINDOW_SECONDS", 300)
    max_attempts = current_app.config.get("LOGIN_MAX_ATTEMPTS", 5)
    lockout_seconds = current_app.config.get("LOGIN_LOCKOUT_SECONDS", 900)
    with _login_lock:
        attempts = _login_failures[key]
        attempts.append(now)
        while attempts and now - attempts[0] > window:
            attempts.popleft()
        if len(attempts) >= max_attempts:
            _login_lockouts[key] = now + lockout_seconds
            attempts.clear()


def _reset_failures(key):
    with _login_lock:
        _login_failures.pop(key, None)
        _login_lockouts.pop(key, None)


def _env_admin_matches(username, password):
    """Check the bootstrap admin credentials from the environment."""
    admin_username = current_app.config.get("ADMIN_USERNAME") or "admin"
    password_hash = current_app.config.get("ADMIN_PASSWORD_HASH")
    password_plain = current_app.config.get("ADMIN_PASSWORD")

    if not hmac.compare_digest(str(username or ""), str(admin_username)):
        return False
    if password_hash:
        return check_password_hash(password_hash, password or "")
    if password_plain:
        return hmac.compare_digest(str(password or ""), str(password_plain))
    return False


def _load_user(username):
    """Load a user row; returns None when the table is missing (not migrated)."""
    try:
        return User.get_by_username(username)
    except Exception:
        db.session.rollback()
        current_app.logger.warning("读取用户表失败，可能是尚未执行数据库迁移", exc_info=True)
        return None


def _bootstrap_admin(username, password):
    """Persist the env-configured admin so it can be managed like any account."""
    try:
        user = User.get_by_username(username)
        if user is not None:
            return user
        user = User(
            username=username,
            display_name=username,
            role=User.ROLE_ADMIN,
            is_active=True,
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        current_app.logger.info("已根据环境变量创建管理员账号: %s", username)
        return user
    except Exception:
        db.session.rollback()
        current_app.logger.warning("引导管理员落库失败，本次将直接使用环境变量校验", exc_info=True)
        return None


def _authenticate(username, password):
    """Return the identity dict, or raise LoginError."""
    user = _load_user(username)

    if user is not None:
        if not user.is_active:
            raise LoginError("账号已被禁用", code="ACCOUNT_DISABLED")
        if not user.check_password(password):
            raise LoginError("用户名或密码错误")
        return {
            "user_id": user.id,
            "username": user.username,
            "role": user.role,
            "shop_ids": user.shop_ids(),
        }

    # 数据库里没有该账号：只允许环境变量配置的引导管理员
    if not _env_admin_matches(username, password):
        raise LoginError("用户名或密码错误")

    user = _bootstrap_admin(username, password)
    if user is not None:
        return {
            "user_id": user.id,
            "username": user.username,
            "role": user.role,
            "shop_ids": user.shop_ids(),
        }

    # 落库失败（例如还没迁移）时仍允许引导管理员登录
    return {"user_id": None, "username": username, "role": "admin", "shop_ids": None}


def _touch_last_login(identity):
    if not identity.get("user_id"):
        return
    try:
        user = db.session.get(User, identity["user_id"])
        if user is not None:
            user.last_login_at = datetime.utcnow()
            db.session.commit()
    except Exception:
        db.session.rollback()


def login(payload, client_ip="unknown"):
    """Authenticate and issue a JWT. Raises LoginError / ValidationError."""
    if not payload:
        raise ValidationError("请求数据不能为空")

    username = (payload.get("username") or "").strip()
    password = payload.get("password") or ""
    login_type = payload.get("type", "account")

    throttle_key = f"{client_ip}:{username}"
    remaining = lockout_remaining(throttle_key)
    if remaining > 0:
        raise LoginError(
            f"登录失败次数过多，请在 {remaining // 60 + 1} 分钟后重试",
            status_code=429,
            code="TOO_MANY_ATTEMPTS",
            login_type=login_type,
        )

    if not username or not password:
        raise ValidationError("用户名和密码不能为空")

    try:
        identity = _authenticate(username, password)
    except LoginError as exc:
        record_failure(throttle_key)
        current_app.logger.warning("登录失败: 用户名=%s ip=%s", username, client_ip)
        exc.extra["login_type"] = login_type
        raise

    _reset_failures(throttle_key)
    _touch_last_login(identity)

    expires_hours = current_app.config.get("JWT_EXPIRES_HOURS", 12)
    token = jwt.encode(
        {
            "user_id": identity.get("user_id"),
            "username": identity["username"],
            "authority": identity["role"],
            # 令牌内记录店铺便于前端展示；实际鉴权每次请求都会重新读取数据库
            "shop_ids": identity.get("shop_ids"),
            "exp": datetime.utcnow() + timedelta(hours=expires_hours),
            "iat": datetime.utcnow(),
        },
        current_app.config["JWT_SECRET_KEY"],
        algorithm="HS256",
    )

    return {
        "status": "ok",
        "type": login_type,
        "currentAuthority": identity["role"],
        "token": token,
        "message": "登录成功",
    }
