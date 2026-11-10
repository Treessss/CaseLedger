"""User (admin / sub-account) service."""

from app import db
from app.models import Shop, User
from app.utils.errors import NotFoundError, ValidationError
from app.utils.permissions import ADMIN_ONLY_KEYS, GRANTABLE_KEYS, catalog, is_grantable
from app.utils.security import current_user

MIN_PASSWORD_LENGTH = 8


def _resolve_shops(shop_ids):
    """Turn a list of shop ids into Shop rows, raising on unknown ids."""
    if shop_ids is None:
        return []
    if not isinstance(shop_ids, (list, tuple)):
        raise ValidationError("shop_ids 必须是数组")

    normalized = []
    for raw in shop_ids:
        try:
            normalized.append(int(raw))
        except (TypeError, ValueError) as exc:
            raise ValidationError("shop_ids 只能是数字") from exc

    shops = Shop.query.filter(Shop.id.in_(normalized)).all() if normalized else []
    missing = set(normalized) - {shop.id for shop in shops}
    if missing:
        raise ValidationError("店铺不存在: {}".format(", ".join(str(item) for item in sorted(missing))))
    return shops


def _validate_password(password, field="密码"):
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise ValidationError(f"{field}长度至少 {MIN_PASSWORD_LENGTH} 位")


def _validate_permissions(keys):
    """只接受可授予子账户的功能权限 key。"""
    if keys is None:
        return []
    if not isinstance(keys, (list, tuple)):
        raise ValidationError("permissions 必须是数组")

    normalized = []
    for key in keys:
        if not is_grantable(key):
            raise ValidationError(f"权限不可授予子账户: {key}")
        if key not in normalized:
            normalized.append(key)
    return normalized


def permission_catalog():
    """供管理员在界面上勾选的权限清单。"""
    return {
        "permissions": catalog(include_admin_only=True),
        "grantable_keys": list(GRANTABLE_KEYS),
        "admin_only_keys": list(ADMIN_ONLY_KEYS),
    }


def _get_user(user_id):
    user = db.session.get(User, user_id)
    if user is None:
        raise NotFoundError("账号不存在")
    return user


def current_user_payload():
    """Profile of the caller, including role and visible shops."""
    principal = current_user() or {}
    user_id = principal.get("user_id")
    user = db.session.get(User, user_id) if user_id else None
    role = principal.get("authority") or "user"

    if role == "admin":
        shops = Shop.query.order_by(Shop.name).all()
    else:
        shops = list(user.shops) if user else []

    shop_ids = None if role == "admin" else [shop.id for shop in shops]

    return {
        "name": (user.display_name if user and user.display_name else principal.get("username"))
        or "CaseLedger User",
        "avatar": "/logo.svg",
        "userid": str(user_id) if user_id else "1",
        "email": user.email if user else "",
        "signature": user.display_name if user and user.display_name else "",
        "title": "系统管理员" if role == "admin" else "子账户",
        "group": "CaseLedger团队",
        "tags": [{"key": "0", "label": "管理员" if role == "admin" else "子账户"}],
        "notifyCount": 0,
        "unreadCount": 0,
        "country": "China",
        "access": role,
        "shop_ids": shop_ids,
        "shops": [shop.to_dict() for shop in shops],
        # 功能权限：管理员返回全部 key，子账户返回被授予的 key（前端据此显示菜单）
        "permissions": principal.get("permissions") or [],
        "geographic": {
            "province": {"label": "广东省", "key": "440000"},
            "city": {"label": "深圳市", "key": "440300"},
        },
        "address": "",
        "phone": "",
    }


def list_users(args):
    query = User.query
    role = args.get("role")
    if role:
        query = query.filter(User.role == role)
    is_active = args.get("is_active")
    if is_active is not None and is_active != "":
        query = query.filter(User.is_active == (str(is_active).lower() == "true"))
    keyword = (args.get("keyword") or "").strip()
    if keyword:
        query = query.filter(User.username.ilike(f"%{keyword}%"))

    return [user.to_dict() for user in query.order_by(User.created_at.desc()).all()]


def get_user(user_id):
    return _get_user(user_id).to_dict()


def create_user(payload):
    payload = payload or {}
    username = (payload.get("username") or "").strip()
    password = payload.get("password") or ""

    if not username:
        raise ValidationError("用户名不能为空")
    _validate_password(password)
    if User.get_by_username(username):
        raise ValidationError("用户名已存在")

    shops = _resolve_shops(payload.get("shop_ids"))
    user = User(
        username=username,
        display_name=(payload.get("display_name") or username).strip(),
        email=payload.get("email"),
        role=User.ROLE_USER,
        is_active=True if payload.get("is_active") is None else bool(payload.get("is_active")),
    )
    user.set_password(password)
    user.shops = shops
    if "permissions" in payload:
        user.set_permissions(_validate_permissions(payload.get("permissions")))
    db.session.add(user)
    db.session.commit()
    return user.to_dict()


def update_user(user_id, payload):
    user = _get_user(user_id)
    payload = payload or {}

    if "display_name" in payload:
        user.display_name = (payload.get("display_name") or "").strip() or user.username
    if "email" in payload:
        user.email = payload.get("email")
    if "is_active" in payload:
        if user.is_admin and not payload.get("is_active"):
            raise ValidationError("不能禁用管理员账号")
        user.is_active = bool(payload.get("is_active"))
    if payload.get("password"):
        _validate_password(payload["password"])
        user.set_password(payload["password"])
    if "shop_ids" in payload:
        user.shops = _resolve_shops(payload.get("shop_ids"))
    if "permissions" in payload:
        user.set_permissions(_validate_permissions(payload.get("permissions")))

    db.session.commit()
    return user.to_dict()


def assign_shops(user_id, shop_ids):
    user = _get_user(user_id)
    user.shops = _resolve_shops(shop_ids)
    db.session.commit()
    return user.to_dict()


def assign_permissions(user_id, permissions):
    """设置子账户可访问的功能模块（覆盖式）。"""
    user = _get_user(user_id)
    user.set_permissions(_validate_permissions(permissions))
    db.session.commit()
    return user.to_dict()


def delete_user(user_id):
    user = _get_user(user_id)
    if user.is_admin:
        raise ValidationError("不能删除管理员账号")
    from app.models import OutboundScanFailure
    # Keep historical rejection records without blocking deletion of an employee.
    OutboundScanFailure.query.filter_by(user_id=user.id).update({"user_id": None}, synchronize_session=False)
    db.session.delete(user)
    db.session.commit()
    return {"id": user_id}


def change_own_password(old_password, new_password):
    """Rotate the caller's own password."""
    principal = current_user() or {}
    user_id = principal.get("user_id")
    user = db.session.get(User, user_id) if user_id else None
    if user is None:
        raise ValidationError("当前账号不支持修改密码（请在 .env 中配置）")

    _validate_password(new_password, "新密码")
    if not user.check_password(old_password or ""):
        raise ValidationError("原密码不正确")

    user.set_password(new_password)
    db.session.commit()
    return {"id": user.id}
