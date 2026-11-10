import os
from datetime import timedelta
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name) or default)
    except (TypeError, ValueError):
        return default


def _normalize_database_url(url: str) -> str:
    """统一数据库连接串的驱动前缀。

    - postgres:// 与 postgresql:// 统一成 postgresql+psycopg2://
    - mysql:// 统一成 mysql+pymysql://
    - sqlite:// 原样保留
    """
    if not url:
        return url
    if url.startswith("postgres://"):
        return "postgresql+psycopg2://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg2://" + url[len("postgresql://") :]
    if url.startswith("mysql://"):
        return "mysql+pymysql://" + url[len("mysql://") :]
    return url


def _build_engine_options(uri: str) -> dict:
    """按数据库类型生成引擎参数。

    注意：check_same_thread / timeout 是 SQLite 专用参数，
    如果无条件传给 PostgreSQL/MySQL 会直接报错，因此必须区分。
    """
    if uri.startswith("sqlite"):
        return {
            "pool_pre_ping": True,
            "connect_args": {
                "check_same_thread": False,
                "timeout": 30,
            },
        }

    return {
        "pool_pre_ping": True,
        "pool_recycle": _env_int("DB_POOL_RECYCLE", 300),
        "pool_size": _env_int("DB_POOL_SIZE", 10),
        "max_overflow": _env_int("DB_MAX_OVERFLOW", 20),
        "pool_timeout": _env_int("DB_POOL_TIMEOUT", 30),
    }


class Config:
    # ==================== 安全密钥 ====================
    # 不再提供硬编码兜底值：缺少配置时应用会在启动阶段直接报错，避免“默认密钥”上线
    SECRET_KEY = os.environ.get("SECRET_KEY")
    JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY") or os.environ.get("SECRET_KEY")
    JWT_EXPIRES_HOURS = _env_int("JWT_EXPIRES_HOURS", 12)
    # 物流已有实际轨迹、但仓库仍未出库时，自动补记出库；设为 0 可关闭。
    TRACKING_AUTO_OUTBOUND = _env_bool("TRACKING_AUTO_OUTBOUND", True)
    # 4PX 真实物流费自动归集：切换日按订单日期起算，自动写 pending 费用 + 每日账单
    LOGISTICS_COST_AUTO = _env_bool("LOGISTICS_COST_AUTO", True)
    LOGISTICS_COST_CUTOVER = os.environ.get("LOGISTICS_COST_CUTOVER", "2026-11-01")
    LOGISTICS_COST_BATCH = int(os.environ.get("LOGISTICS_COST_BATCH", "100"))
    # 4PX 出账有延迟：出库不满 N 小时的订单先不查，避免反复请求未出账订单
    LOGISTICS_COST_MIN_AGE_HOURS = int(os.environ.get("LOGISTICS_COST_MIN_AGE_HOURS", "24"))

    # 平台账号密码加密密钥（Fernet key），迁移历史数据时必须保持一致
    ENCRYPTION_KEY = os.environ.get("ENCRYPTION_KEY")

    # ==================== 管理员账号 ====================
    # 优先使用带盐哈希 ADMIN_PASSWORD_HASH，其次使用明文 ADMIN_PASSWORD
    ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME") or "admin"
    ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD")
    ADMIN_PASSWORD_HASH = os.environ.get("ADMIN_PASSWORD_HASH")

    # ==================== 登录保护 ====================
    LOGIN_MAX_ATTEMPTS = _env_int("LOGIN_MAX_ATTEMPTS", 5)
    LOGIN_WINDOW_SECONDS = _env_int("LOGIN_WINDOW_SECONDS", 300)
    LOGIN_LOCKOUT_SECONDS = _env_int("LOGIN_LOCKOUT_SECONDS", 900)

    # 是否把异常细节返回给客户端（生产环境必须为 False）
    EXPOSE_ERROR_DETAILS = _env_bool("EXPOSE_ERROR_DETAILS", False)

    # CORS 白名单，逗号分隔
    CORS_ORIGINS = [
        origin.strip()
        for origin in (os.environ.get("CORS_ORIGINS") or "http://localhost:8002,http://127.0.0.1:8002").split(
            ","
        )
        if origin.strip()
    ]

    # 数据库配置
    DATABASE_URL = _normalize_database_url(os.environ.get("DATABASE_URL"))
    if DATABASE_URL:
        SQLALCHEMY_DATABASE_URI = DATABASE_URL
    else:
        # 未配置时使用本地 SQLite（开发环境默认）
        basedir = os.path.abspath(os.path.dirname(__file__))
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{os.path.join(basedir, 'case_ledger.db')}"

    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # 连接池/连接参数按数据库类型区分
    SQLALCHEMY_ENGINE_OPTIONS = _build_engine_options(SQLALCHEMY_DATABASE_URI)

    # SQLite 并发优化（WAL）+ 外键约束；PostgreSQL/MySQL 使用原生行为
    SQLITE_JOURNAL_MODE = os.environ.get("SQLITE_JOURNAL_MODE") or "WAL"
    SQLITE_BUSY_TIMEOUT_MS = _env_int("SQLITE_BUSY_TIMEOUT_MS", 30000)
    SQLITE_ENABLE_WAL = _env_bool("SQLITE_ENABLE_WAL", True)

    # Shopify API配置
    SHOPIFY_API_KEY = os.environ.get("SHOPIFY_API_KEY")
    SHOPIFY_API_SECRET = os.environ.get("SHOPIFY_API_SECRET")
    SHOPIFY_ACCESS_TOKEN = os.environ.get("SHOPIFY_ACCESS_TOKEN")
    SHOPIFY_SHOP_URL = os.environ.get("SHOPIFY_SHOP_URL")
    SHOPIFY_WEBHOOK_SECRET = os.environ.get("SHOPIFY_WEBHOOK_SECRET")
    # Partner 应用（走 client_credentials 换票）的凭据。
    # 店铺后台「自建应用」签发的是永久 shpat_ 令牌，拿不到客户隐私数据（PII）；
    # 客户姓名/收货地址/电话必须用已获批 PII 权限的 Partner 应用换取临时令牌。
    SHOPIFY_OAUTH_CLIENT_ID = os.environ.get("SHOPIFY_OAUTH_CLIENT_ID")
    SHOPIFY_OAUTH_CLIENT_SECRET = os.environ.get("SHOPIFY_OAUTH_CLIENT_SECRET")
    SHOPIFY_API_TIMEOUT = int(os.environ.get("SHOPIFY_API_TIMEOUT") or 30)

    # Zoho Mail（客服邮件模块）OAuth 客户端凭据。
    # refresh token 与 DeepSeek Key 加密存在数据库里，这里只放应用级 client 凭据。
    ZOHO_CLIENT_ID = os.environ.get("ZOHO_CLIENT_ID")
    ZOHO_CLIENT_SECRET = os.environ.get("ZOHO_CLIENT_SECRET")

    # 汇率源网络偶尔需要 5-10 秒；成功报价保存到本项目 instance/ 供短时故障兜底。
    EXCHANGE_RATE_REQUEST_TIMEOUT = _env_int("EXCHANGE_RATE_REQUEST_TIMEOUT", 10)
    EXCHANGE_RATE_MAX_STALE_DAYS = _env_int("EXCHANGE_RATE_MAX_STALE_DAYS", 7)
    EXCHANGE_RATE_CACHE_PATH = os.environ.get("EXCHANGE_RATE_CACHE_PATH")

    # PayPal API配置
    PAYPAL_CLIENT_ID = os.environ.get("PAYPAL_CLIENT_ID")
    PAYPAL_CLIENT_SECRET = os.environ.get("PAYPAL_CLIENT_SECRET")
    PAYPAL_MODE = os.environ.get("PAYPAL_MODE") or "sandbox"  # sandbox 或 live

    # Stripe API配置
    STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY")
    STRIPE_PUBLISHABLE_KEY = os.environ.get("STRIPE_PUBLISHABLE_KEY")

    # Redis配置（用于Celery）
    REDIS_URL = os.environ.get("REDIS_URL") or "redis://localhost:6379/0"

    # Celery配置
    CELERY_BROKER_URL = REDIS_URL
    CELERY_RESULT_BACKEND = REDIS_URL
    CELERY_TASK_SERIALIZER = "json"
    CELERY_RESULT_SERIALIZER = "json"
    CELERY_ACCEPT_CONTENT = ["json"]
    CELERY_TIMEZONE = "UTC"
    CELERY_ENABLE_UTC = True

    # 调度器配置
    SCHEDULER_API_ENABLED = True


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False


config = {"development": DevelopmentConfig, "production": ProductionConfig, "default": DevelopmentConfig}
