"""Platform account (credential vault) service.

Passwords are stored encrypted with Fernet; the plaintext is only returned by
the explicit password endpoints, and every endpoint is admin-only.
"""

from datetime import datetime

from app import db
from app.models import PlatformAccount
from app.utils.errors import NotFoundError, ValidationError

DEFAULT_PER_PAGE = 20
MAX_PER_PAGE = 100


def _pagination(page_obj):
    return {
        "page": page_obj.page,
        "pages": page_obj.pages,
        "per_page": page_obj.per_page,
        "total": page_obj.total,
        "has_next": page_obj.has_next,
        "has_prev": page_obj.has_prev,
    }


def _get_account(account_id):
    account = db.session.get(PlatformAccount, account_id)
    if account is None:
        raise NotFoundError("平台账户不存在")
    return account


def list_accounts(args):
    page = args.get("page", 1, type=int) or 1
    per_page = min(args.get("per_page", DEFAULT_PER_PAGE, type=int) or DEFAULT_PER_PAGE, MAX_PER_PAGE)

    query = PlatformAccount.query
    platform = args.get("platform")
    if platform:
        query = query.filter_by(platform=platform)

    keyword = args.get("search")
    if keyword:
        pattern = f"%{keyword}%"
        query = query.filter(
            db.or_(
                PlatformAccount.platform.ilike(pattern),
                PlatformAccount.username.ilike(pattern),
                PlatformAccount.notes.ilike(pattern),
            )
        )

    pagination = query.order_by(PlatformAccount.created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )
    return {
        "accounts": [account.to_dict() for account in pagination.items],
        "pagination": _pagination(pagination),
    }


def search(keyword):
    return [account.to_dict() for account in PlatformAccount.search(keyword or "")]


def platforms():
    return PlatformAccount.get_platforms()


def create_account(payload):
    payload = payload or {}
    for field in ("platform", "username", "password"):
        if not payload.get(field):
            raise ValidationError(f"缺少必填字段: {field}")

    exists = PlatformAccount.query.filter_by(
        platform=payload["platform"], username=payload["username"]
    ).first()
    if exists:
        raise ValidationError("该平台下已存在相同用户名的账户")

    account = PlatformAccount(
        platform=payload["platform"],
        username=payload["username"],
        notes=payload.get("notes", ""),
    )
    account.set_password(payload["password"])
    db.session.add(account)
    db.session.commit()
    return account.to_dict()


def get_account(account_id, include_password=False):
    return _get_account(account_id).to_dict(include_password=include_password)


def reveal_password(account_id):
    return _get_account(account_id).get_password()


def update_account(account_id, payload):
    account = _get_account(account_id)
    payload = payload or {}

    if "username" in payload and payload["username"] != account.username:
        duplicate = (
            PlatformAccount.query.filter_by(
                platform=payload.get("platform", account.platform),
                username=payload["username"],
            )
            .filter(PlatformAccount.id != account_id)
            .first()
        )
        if duplicate:
            raise ValidationError("该平台下已存在相同用户名的账户")

    if "platform" in payload:
        account.platform = payload["platform"]
    if "username" in payload:
        account.username = payload["username"]
    if payload.get("password"):
        account.set_password(payload["password"])
    if "notes" in payload:
        account.notes = payload["notes"]

    account.updated_at = datetime.utcnow()
    db.session.commit()
    return account.to_dict()


def delete_account(account_id):
    account = _get_account(account_id)
    db.session.delete(account)
    db.session.commit()
    return {"id": account_id}
