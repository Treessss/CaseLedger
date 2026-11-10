import os
import re
import time
import sqlite3

from flask import Flask, current_app, g, jsonify, request
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_cors import CORS
from dotenv import load_dotenv
from sqlalchemy import event
from sqlalchemy.engine import Engine
from werkzeug.exceptions import HTTPException
from config import Config
from app.utils.errors import ApiError, LoginError

# 加载环境变量
load_dotenv()

db = SQLAlchemy()
migrate = Migrate()
cors = CORS()


@event.listens_for(Engine, "connect")
def _apply_sqlite_pragmas(dbapi_connection, connection_record):
    """SQLite 专用连接设置（对 PostgreSQL/MySQL 自动跳过）。

    - foreign_keys=ON：让外键约束真正生效（默认是关闭的）
    - journal_mode=WAL：写入不再阻塞读取，显著缓解「database is locked」
    - busy_timeout：遇到写锁时等待而不是立刻失败
    """
    if not isinstance(dbapi_connection, sqlite3.Connection):
        return

    journal_mode = os.environ.get("SQLITE_JOURNAL_MODE") or "WAL"
    busy_timeout = os.environ.get("SQLITE_BUSY_TIMEOUT_MS") or "30000"

    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute(f"PRAGMA busy_timeout={int(busy_timeout)}")
        cursor.execute(f"PRAGMA journal_mode={journal_mode}")
        cursor.execute("PRAGMA synchronous=NORMAL")
    finally:
        cursor.close()


# 登录相关接口无需鉴权
PUBLIC_API_ENDPOINTS = {
    "/api/login/account",
    "/api/login/outLogin",
    "/api/login/captcha",
}

# /webhooks/* 使用 HMAC 签名校验，静态资源无需登录
PUBLIC_PREFIXES = ("/webhooks/", "/static/")

# 仅管理员可访问的接口：(允许的 HTTP 方法集合, 路径正则)
ADMIN_ONLY_RULES = (
    # 平台账号凭据保险箱：整体仅管理员可见可改
    ({"GET"}, re.compile(r"^/api/platform-accounts(/\d+)?(/password)?$")),
    ({"POST", "PUT", "DELETE"}, re.compile(r"^/api/platform-accounts")),
    # 店铺的新增/修改/删除仅管理员；列表所有登录用户可见（子账户只看到被分配的店铺）
    ({"POST", "PUT", "DELETE"}, re.compile(r"^/api/shops")),
    # 子账户管理仅管理员；/api/users/me/password 允许本人修改密码
    ({"GET", "POST", "PUT", "DELETE", "PATCH"}, re.compile(r"^/api/users(?!/(me)\b)")),
    # 功能权限清单仅管理员
    ({"GET"}, re.compile(r"^/api/permissions$")),
    # AI 接口 Key 管理仅管理员
    ({"GET", "POST", "DELETE"}, re.compile(r"^/api/ai-keys")),
    ({"POST", "PUT", "DELETE"}, re.compile(r"^/api/settings/")),
    ({"POST"}, re.compile(r"^/api/sync/")),
    ({"DELETE"}, re.compile(r"^/api/accounts/\d+$")),
    ({"POST", "PUT", "DELETE"}, re.compile(r"^/api/order-costs/")),
)


def _validate_security_config(app):
    """启动即校验安全相关配置，避免用默认/缺失密钥上线。"""
    missing = [
        name for name in ("SECRET_KEY", "JWT_SECRET_KEY", "ENCRYPTION_KEY") if not app.config.get(name)
    ]
    if missing:
        raise RuntimeError(
            "缺少必要的安全配置: {}。请在 .env 中配置（可参考 .env.example）后重新启动。".format(
                ", ".join(missing)
            )
        )
    if not app.config.get("ADMIN_PASSWORD_HASH") and not app.config.get("ADMIN_PASSWORD"):
        raise RuntimeError("缺少管理员密码配置：请设置 ADMIN_PASSWORD_HASH 或 ADMIN_PASSWORD。")


def _extract_token():
    """只从 Authorization 请求头读取 Bearer token。

    不再支持 URL 查询参数传 token（?token=...），避免令牌被写入访问日志、浏览器历史与 Referer。
    """
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        return header[7:].strip()
    return None


def _requires_admin(method, path):
    for methods, pattern in ADMIN_ONLY_RULES:
        if method in methods and pattern.match(path):
            return True
    return False


def _build_principal(payload):
    """把 JWT 内容解析成当前用户身份（角色 + 店铺 + 功能权限）。

    每次请求都从数据库读取一次账号信息，这样禁用账号、调整店铺授权或功能权限
    都能立即生效，不必等旧令牌过期。
    """
    from app.models import User

    username = payload.get("username")
    user_id = payload.get("user_id")

    try:
        user = db.session.get(User, user_id) if user_id else None
        if user is None and username:
            user = User.get_by_username(username)
    except Exception:
        # users 表还不存在（尚未执行迁移）时，回退到令牌内自带的信息
        db.session.rollback()
        current_app.logger.warning("读取用户信息失败，暂用令牌内信息", exc_info=True)
        return {
            "user_id": user_id,
            "username": username,
            "authority": payload.get("authority") or "user",
            "shop_ids": payload.get("shop_ids") or [],
            "permissions": payload.get("permissions") or [],
        }

    if user is None:
        # 仅环境变量里配置的引导管理员允许在没有用户记录时登录
        if (
            username
            and username == current_app.config.get("ADMIN_USERNAME")
            and payload.get("authority") == "admin"
        ):
            from app.utils.permissions import ALL_KEYS

            return {
                "user_id": None,
                "username": username,
                "authority": "admin",
                "shop_ids": None,
                "permissions": list(ALL_KEYS),
            }
        return None

    if not user.is_active:
        return None

    return {
        "user_id": user.id,
        "username": user.username,
        "authority": user.role,
        "shop_ids": user.shop_ids(),
        "permissions": user.permission_keys(),
    }


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    _validate_security_config(app)

    # 初始化扩展
    db.init_app(app)
    from app.services.logistics_event_service import register as register_tracking_events
    register_tracking_events()
    from app.utils.api_performance import init_api_performance
    with app.app_context():
        init_api_performance(app, db.engine)
    migrate.init_app(app, db)
    cors.init_app(
        app,
        resources={
            r"/api/*": {
                "origins": app.config.get("CORS_ORIGINS"),
                "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
                "allow_headers": ["Content-Type", "Authorization", "X-API-Key"],
            }
        },
    )

    # JWT token 验证与权限中间件
    @app.before_request
    def verify_token():
        import jwt

        path = request.path

        # 浏览器 CORS 预检请求不携带令牌，交给 flask-cors 处理
        if request.method == "OPTIONS":
            return

        # 登录接口与 webhook 各自有校验逻辑
        if path in PUBLIC_API_ENDPOINTS or path.startswith(PUBLIC_PREFIXES):
            return

        # 非 API 路由（前端静态资源、健康检查）不在此处鉴权
        if not path.startswith("/api/"):
            return

        # AI 接口文档（OpenAPI）公开只读，便于工具导入
        if path == "/api/ai/openapi.json":
            return

        # AI 接口独立鉴权：X-API-Key（或 Authorization: Bearer cai_...），不走登录令牌
        if path.startswith("/api/ai/"):
            from app.services.ai_api_service import authenticate_request

            key = authenticate_request(request)
            if key is None:
                return jsonify({"success": False, "code": "INVALID_API_KEY",
                                "message": "缺少或无效的 API Key"}), 401
            g.ai_key = key
            g._ai_started = time.monotonic()
            # 只读接口内部复用店铺范围校验，按 key 的店铺范围伪装成受限账号
            g.current_user = {
                "authority": "user", "user_id": key.created_by or 0,
                "username": f"ai-key:{key.name}", "shop_ids": list(key.shop_ids or []),
                "permissions": [], "role": "user",
            }
            return

        token = _extract_token()
        if not token:
            return jsonify({"error": "Missing token", "code": "MISSING_TOKEN"}), 401

        try:
            payload = jwt.decode(token, current_app.config["JWT_SECRET_KEY"], algorithms=["HS256"])
        except jwt.ExpiredSignatureError:
            return jsonify({"error": "Token已过期", "code": "TOKEN_EXPIRED"}), 401
        except jwt.InvalidTokenError:
            return jsonify({"error": "Invalid token", "code": "INVALID_TOKEN"}), 401

        # 令牌有效：解析账号状态（角色、可访问店铺），禁用账号会立即失效
        principal = _build_principal(payload)
        if principal is None:
            return jsonify({"error": "账号不存在或已被禁用", "code": "ACCOUNT_DISABLED"}), 401
        g.current_user = principal
        if g.get('_api_perf') is not None:
            g._api_perf['authenticated'] = True

        if _requires_admin(request.method, path):
            from app.utils.security import has_role, ROLE_ADMIN

            if not has_role(ROLE_ADMIN):
                return jsonify({"error": "权限不足", "code": "FORBIDDEN"}), 403

        # 功能权限校验：子账户只能访问被授予的模块（管理员放行）
        from app.utils.permissions import label_of, required_permission, required_action_permission

        required_keys = (required_permission(path), required_action_permission(request.method, path))
        for required in required_keys if principal.get("authority") != "admin" else ():
            if not required:
                continue
            if required not in (principal.get("permissions") or []):
                return (
                    jsonify(
                        {
                            "success": False,
                            "message": f"无权访问「{label_of(required)}」，请联系管理员开通",
                            "error": f"无权访问「{label_of(required)}」，请联系管理员开通",
                            "code": "FORBIDDEN_FEATURE",
                            "permission": required,
                        }
                    ),
                    403,
                )

        return

    @app.after_request
    def audit_ai_request(response):
        key = getattr(g, "ai_key", None)
        if key is not None and request.path.startswith("/api/ai/"):
            started = getattr(g, "_ai_started", None)
            duration = int((time.monotonic() - started) * 1000) if started else None
            from app.services.ai_api_service import record_audit

            record_audit(key.id, request.method, request.path, response.status_code,
                         duration, request.headers.get("X-Forwarded-For", request.remote_addr))
        return response

    # 业务异常：统一 JSON 信封（{success, code, message}）
    @app.errorhandler(ApiError)
    def handle_api_error(error):
        payload = {
            "success": False,
            "code": error.code,
            "message": error.message,
        }
        payload.update(error.extra)
        if isinstance(error, LoginError):
            # 登录失败保持 Ant Design Pro 登录页约定的字段
            payload.update(
                {
                    "status": "error",
                    "type": payload.pop("login_type", "account"),
                    "currentAuthority": "guest",
                }
            )
        return jsonify(payload), error.status_code

    # HTTP 异常（404/405 等）也返回 JSON，避免前端拿到 HTML 报错页
    @app.errorhandler(HTTPException)
    def handle_http_error(error):
        return jsonify(
            {
                "success": False,
                "code": (error.name or "ERROR").upper().replace(" ", "_"),
                "message": error.description,
            }
        ), error.code

    # 兜底异常处理：内部细节只进日志，不返回给客户端
    @app.errorhandler(Exception)
    def handle_unexpected_error(error):
        try:
            db.session.rollback()
        except Exception:
            pass
        current_app.logger.exception("未处理的服务端异常")
        return jsonify({"success": False, "message": "服务器内部错误"}), 500

    # 初始化服务
    from app.services.shopify_service import shopify_service

    shopify_service.init_app(app)

    # 注册蓝图
    from app.main import bp as main_bp

    app.register_blueprint(main_bp)

    from app.api import bp as api_bp

    app.register_blueprint(api_bp, url_prefix="/api")

    from app.webhooks.shopify_webhooks import webhook_bp

    app.register_blueprint(webhook_bp, url_prefix="/webhooks")

    from app.api.exchange_rate import exchange_rate_bp

    app.register_blueprint(exchange_rate_bp)

    return app


# 导入模型以确保它们被注册
from app import models
