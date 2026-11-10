"""Domain errors.

Routes raise these instead of returning raw error tuples, so every endpoint
produces the same JSON envelope and the correct HTTP status code.
"""


class ApiError(Exception):
    """Base class for expected business errors."""

    def __init__(self, message, status_code=400, code="BAD_REQUEST", extra=None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code
        # Extra fields merged into the JSON error payload (e.g. record counts).
        self.extra = extra or {}


class ValidationError(ApiError):
    def __init__(self, message="参数不合法", extra=None):
        super().__init__(message, 400, "VALIDATION_ERROR", extra)


class UnauthorizedError(ApiError):
    def __init__(self, message="未登录或登录已失效"):
        super().__init__(message, 401, "UNAUTHORIZED")


class ForbiddenError(ApiError):
    def __init__(self, message="无权访问"):
        super().__init__(message, 403, "FORBIDDEN")


class NotFoundError(ApiError):
    def __init__(self, message="资源不存在"):
        super().__init__(message, 404, "NOT_FOUND")


class RateLimitError(ApiError):
    def __init__(self, message="调用过于频繁，请稍后重试", retry_after=60):
        super().__init__(message, 429, "RATE_LIMITED", {"retry_after": retry_after})


class ConflictError(ApiError):
    def __init__(self, message="数据冲突"):
        super().__init__(message, 409, "CONFLICT")


class LoginError(ApiError):
    """Login failure.

    Rendered in the Ant Design Pro login envelope (status/type/currentAuthority)
    that the frontend login page expects, while still carrying code/message.
    """

    def __init__(
        self, message="用户名或密码错误", status_code=401, code="LOGIN_FAILED", login_type="account"
    ):
        super().__init__(message, status_code, code, extra={"login_type": login_type})
