"""Authentication endpoints.

Thin routing layer over app.services.auth_service; failures are rendered by the
LoginError handler in the Ant Design Pro login envelope.
"""

from flask import jsonify, request

from app.api import bp
from app.services import auth_service
from app.utils.responses import ok


def _client_ip():
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


@bp.route("/login/account", methods=["POST"])
def login():
    """Log in and receive a JWT (admin and sub-accounts share this endpoint)."""
    return jsonify(auth_service.login(request.get_json(silent=True), _client_ip()))


@bp.route("/login/outLogin", methods=["POST"])
def logout():
    """Log out (stateless JWT: the client just drops the token)."""
    return ok({}, message="登出成功")


@bp.route("/login/captcha", methods=["POST"])
def get_captcha():
    """SMS captcha placeholder.

    The old implementation returned a hard-coded code, which is no better than
    no check at all, so it stays disabled until a real provider is wired up.
    """
    return jsonify(
        {
            "success": False,
            "message": "短信验证码功能未启用，请使用账号密码登录",
        }
    ), 501
