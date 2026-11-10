from flask import jsonify
from sqlalchemy import text

from app import db
from app.main import bp

# 说明：原 /dashboard、/orders、/expenses、/reports、/accounts、/settings 页面路由
# 既不在 /api/ 前缀下（完全绕过鉴权），依赖的 templates 也从未存在（访问必然 500，
# 在调试模式下还会把 Werkzeug 调试器暴露出来）。前端已经是 Umi SPA，这些路由属于
# 失效的历史代码，因此统一替换为不带业务数据的健康检查入口。


@bp.route("/")
def index():
    """根路径：返回服务基本信息，不暴露任何业务数据。"""
    return jsonify({"service": "CaseLedger API", "status": "ok", "api_prefix": "/api"})


@bp.route("/health")
def health():
    """健康检查（含数据库连通性），不暴露业务数据。"""
    try:
        db.session.execute(text("SELECT 1"))
        database_status = "up"
    except Exception:
        database_status = "down"

    healthy = database_status == "up"
    return jsonify({"status": "ok" if healthy else "degraded", "database": database_status}), (
        200 if healthy else 503
    )
