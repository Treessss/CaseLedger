"""Shop service: CRUD plus per-shop activity counters."""

from sqlalchemy import func

from app import db
from app.models import Account, Expense, Order, Payment, Shop, User, WebhookDelivery
from app.utils.errors import ForbiddenError, NotFoundError, ValidationError
from app.utils.security import allowed_shop_ids, can_access_shop


def _activity_counts():
    """Order/expense counts per shop in two grouped queries."""
    order_counts = dict(db.session.query(Order.shop_id, func.count(Order.id)).group_by(Order.shop_id).all())
    expense_counts = dict(
        db.session.query(Expense.shop_id, func.count(Expense.id)).group_by(Expense.shop_id).all()
    )
    pending_fee_counts = dict(
        db.session.query(Payment.shop_id, func.count(Payment.id))
        .filter(Payment.fee_source == "estimated")
        .group_by(Payment.shop_id)
        .all()
    )
    return order_counts, expense_counts, pending_fee_counts


def _with_activity(shop, order_counts, expense_counts, pending_fee_counts):
    data = shop.to_dict()
    data["order_count"] = order_counts.get(shop.id, 0)
    data["expense_count"] = expense_counts.get(shop.id, 0)
    data["pending_fee_count"] = pending_fee_counts.get(shop.id, 0)
    return data


def list_shops(args):
    include_inactive = (args.get("include_inactive") or "false").lower() == "true"

    query = Shop.query
    if not include_inactive:
        query = query.filter(Shop.is_active.is_(True))

    allowed = allowed_shop_ids()
    if allowed is not None:
        if not allowed:
            return []
        query = query.filter(Shop.id.in_(allowed))

    shops = query.order_by(Shop.name).all()
    counts = _activity_counts()
    return [_with_activity(shop, *counts) for shop in shops]


def get_shop(shop_id):
    shop = db.session.get(Shop, shop_id)
    if shop is None:
        raise NotFoundError("店铺不存在")
    if not can_access_shop(shop_id):
        raise ForbiddenError("无权访问该店铺")

    return _with_activity(shop, *_activity_counts())


def create_shop(payload):
    payload = payload or {}
    name = (payload.get("name") or "").strip()
    shop_url = Shop.normalize_shop_url(payload.get("shop_url"))

    if not name:
        raise ValidationError("店铺名称不能为空")
    if not shop_url:
        raise ValidationError("店铺域名不能为空")
    if Shop.query.filter_by(shop_url=shop_url).first():
        raise ValidationError("该店铺域名已存在")

    shop = Shop(
        name=name,
        shop_url=shop_url,
        currency=payload.get("currency") or "USD",
        notes=payload.get("notes"),
        is_active=True if payload.get("is_active") is None else bool(payload.get("is_active")),
    )
    shop.oauth_client_id = (payload.get("oauth_client_id") or "").strip() or None
    _apply_oauth_secret(shop, payload.get("oauth_client_secret"))

    if not shop.has_oauth_credentials():
        raise ValidationError(
            "店铺域名、Client ID、Client Secret 都必须填写，否则无法从 Shopify 读取数据"
        )

    db.session.add(shop)
    db.session.commit()
    return shop.to_dict()


def _apply_oauth_secret(shop, value):
    """写入 client_secret：空值忽略，掩码值忽略，其余加密保存。"""
    if not value:
        return
    text = str(value).strip()
    if not text or "*" in text:
        return
    shop.set_oauth_client_secret(text)


def update_shop(shop_id, payload):
    """Update a shop. Blank or masked secrets are left untouched."""
    shop = db.session.get(Shop, shop_id)
    if shop is None:
        raise NotFoundError("店铺不存在")

    payload = payload or {}
    if payload.get("name"):
        shop.name = payload["name"].strip()

    if payload.get("shop_url"):
        normalized = Shop.normalize_shop_url(payload["shop_url"])
        if Shop.query.filter(Shop.shop_url == normalized, Shop.id != shop.id).first():
            raise ValidationError("该店铺域名已存在")
        shop.shop_url = normalized

    if "oauth_client_id" in payload:
        shop.oauth_client_id = (payload.get("oauth_client_id") or "").strip() or None
    _apply_oauth_secret(shop, payload.get("oauth_client_secret"))

    if "fourpx_app_name" in payload:
        shop.fourpx_app_name = (payload.get("fourpx_app_name") or "").strip() or None
    if "fourpx_resource_code" in payload:
        shop.fourpx_resource_code = (payload.get("fourpx_resource_code") or "").strip() or None
    if "fourpx_environment" in payload:
        if payload["fourpx_environment"] not in ("test", "production"):
            raise ValidationError("4PX 环境必须为测试或正式环境")
        shop.fourpx_environment = payload["fourpx_environment"]
    fourpx_key = str(payload.get("fourpx_app_key") or "").strip()
    fourpx_secret = str(payload.get("fourpx_app_secret") or "").strip()
    if fourpx_key or fourpx_secret:
        if not fourpx_key or not fourpx_secret:
            raise ValidationError("更新 4PX 凭据时须同时填写 AppKey 和 AppSecret")
        if len(fourpx_key) > 255 or len(fourpx_secret) > 255:
            raise ValidationError("4PX 凭据长度不合法")
        shop.set_fourpx_credentials(fourpx_key, fourpx_secret)

    if not shop.has_oauth_credentials():
        raise ValidationError(
            "店铺域名、Client ID、Client Secret 必须齐全，否则该店铺无法连接 Shopify"
        )

    if payload.get("currency"):
        shop.currency = payload["currency"]
    if "notes" in payload:
        shop.notes = payload.get("notes")
    if "is_active" in payload:
        shop.is_active = bool(payload.get("is_active"))

    db.session.commit()
    return shop.to_dict()


def delete_shop(shop_id, force=False):
    """Delete a shop; shops with business data need an explicit force."""
    shop = db.session.get(Shop, shop_id)
    if shop is None:
        raise NotFoundError("店铺不存在")

    order_count = Order.query.filter_by(shop_id=shop_id).count()
    expense_count = Expense.query.filter_by(shop_id=shop_id).count()
    account_count = Account.query.filter_by(shop_id=shop_id).count()
    delivery_count = WebhookDelivery.query.filter_by(shop_id=shop_id).count()
    from app.models import OutboundScanFailure
    scan_failure_count = OutboundScanFailure.query.filter_by(shop_id=shop_id).count()

    if account_count:
        raise ValidationError(
            "该店铺有财务账户和余额，请先处理账户后再删除；不能强制删除财务账目。",
            extra={"has_data": True, "account_count": account_count},
        )
    if delivery_count:
        raise ValidationError(
            "该店铺已有 Shopify 回调审计记录，请停用店铺而不是删除。",
            extra={"has_data": True, "webhook_count": delivery_count},
        )

    if (order_count or expense_count or scan_failure_count) and not force:
        raise ValidationError(
            "该店铺已有业务数据，无法直接删除。可改为「停用」，或确认强制删除。",
            extra={
                "has_data": True,
                "order_count": order_count,
                "expense_count": expense_count,
                "scan_failure_count": scan_failure_count,
            },
        )

    for user in list(shop.users):
        user.shops.remove(shop)

    # Explicit shop deletion includes this shop's scan audit; remove child rows
    # in the same transaction before ORM cascades remove their parent orders.
    OutboundScanFailure.query.filter_by(shop_id=shop_id).delete(synchronize_session=False)
    db.session.delete(shop)
    db.session.commit()
    return {"id": shop_id}
