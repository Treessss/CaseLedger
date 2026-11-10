"""Settings service: default shop credentials and fee configuration."""

from datetime import datetime

from flask import current_app

from app import db
from app.models import FeeConfig, Shop
from app.services.shopify_service import ShopifyService
from app.utils.errors import NotFoundError, ValidationError

FEE_METHODS_REQUIRING_PERCENTAGE = ("percentage", "percentage_plus_fixed")
FEE_METHODS_REQUIRING_FIXED = ("fixed", "percentage_plus_fixed")


def default_shop():
    """The settings page maintains the first (default) shop."""
    return Shop.query.order_by(Shop.id).first()


def _shop_service(shop):
    service = ShopifyService()
    service.init_shop(shop, current_app)
    return service


def get_shopify_config():
    shop = default_shop()
    if shop is None:
        return {
            "shop_id": None,
            "shop_url": "",
            "oauth_client_id": "",
            "oauth_client_secret": "",
            "is_active": False,
            "last_sync": None,
        }

    data = shop.to_dict()
    return {
        "shop_id": shop.id,
        "shop_url": shop.shop_url,
        "oauth_client_id": data.get("oauth_client_id", ""),
        # 回显为打码值，前端原样回传时后端会忽略
        "oauth_client_secret": data.get("oauth_client_secret", ""),
        "is_active": shop.is_active,
        "last_sync": shop.last_sync.isoformat() if shop.last_sync else None,
    }


def save_shopify_config(payload):
    """Create or update the default shop credentials.

    Masked values (containing '*') are ignored so the UI can post back the
    masked string without wiping the stored secret.
    """
    payload = payload or {}
    shop = default_shop()

    if shop is None:
        for field in ("shop_url", "oauth_client_id", "oauth_client_secret"):
            if not payload.get(field):
                raise ValidationError(f"{field} 不能为空")
        shop = Shop(
            name=payload.get("name") or Shop.normalize_shop_url(payload["shop_url"]).split(".")[0],
            shop_url=Shop.normalize_shop_url(payload["shop_url"]),
        )
        db.session.add(shop)

    if payload.get("shop_url"):
        shop.shop_url = Shop.normalize_shop_url(payload["shop_url"])

    if "oauth_client_id" in payload:
        shop.oauth_client_id = (payload.get("oauth_client_id") or "").strip() or None
    secret = payload.get("oauth_client_secret")
    if secret and "*" not in str(secret):
        shop.set_oauth_client_secret(str(secret).strip())

    if not shop.has_oauth_credentials():
        raise ValidationError("店铺域名、Client ID、Client Secret 必须齐全")

    shop.is_active = True
    db.session.commit()
    return {"shop_id": shop.id, "shop_url": shop.shop_url}


def test_shopify_connection():
    shop = default_shop()
    if shop is None:
        raise ValidationError("请先在「店铺管理」中配置店铺")
    if not _shop_service(shop).test_connection():
        raise ValidationError("连接测试失败，请检查配置信息")
    return {"shop_url": shop.shop_url, "shop_id": shop.id}


def sync_shopify_orders(days_back=30, limit=250):
    shop = default_shop()
    if shop is None:
        raise ValidationError("请先在「店铺管理」中配置店铺")

    result = _shop_service(shop).sync_orders(days_back=days_back, limit=limit)
    shop.last_sync = datetime.utcnow()
    db.session.commit()
    return result


def _validate_fee_payload(payload, partial=False):
    payload = payload or {}
    if not partial:
        if not payload.get("fee_type"):
            raise ValidationError("费用类型不能为空")
        if not payload.get("fee_name"):
            raise ValidationError("费用名称不能为空")
        if not payload.get("calculation_method"):
            raise ValidationError("计算方式不能为空")

    method = payload.get("calculation_method")
    if method in FEE_METHODS_REQUIRING_PERCENTAGE and not payload.get("percentage_rate"):
        raise ValidationError("百分比费率不能为空")
    if method in FEE_METHODS_REQUIRING_FIXED and not payload.get("fixed_amount"):
        raise ValidationError("固定金额不能为空")
    return payload


def list_fees():
    configs = FeeConfig.query.filter_by(is_active=True).all()
    return [fee_to_dict(config) for config in configs]


def fee_to_dict(config):
    return {
        "id": config.id,
        "fee_type": config.fee_type,
        "fee_name": config.fee_name,
        "description": config.description,
        "calculation_method": config.calculation_method,
        "percentage_rate": float(config.percentage_rate) if config.percentage_rate else None,
        "fixed_amount": float(config.fixed_amount) if config.fixed_amount else None,
        "currency": config.currency,
        "is_active": config.is_active,
        "created_at": config.created_at.isoformat() if config.created_at else None,
    }


def create_fee(payload):
    payload = _validate_fee_payload(payload)

    config = FeeConfig(
        fee_type=payload["fee_type"],
        fee_name=payload["fee_name"],
        description=payload.get("description", ""),
        calculation_method=payload["calculation_method"],
        percentage_rate=payload.get("percentage_rate"),
        fixed_amount=payload.get("fixed_amount"),
        currency=payload.get("currency", "USD"),
        is_active=True,
    )
    db.session.add(config)
    db.session.commit()
    return fee_to_dict(config)


def update_fee(fee_id, payload):
    config = db.session.get(FeeConfig, fee_id)
    if config is None:
        raise NotFoundError("费用配置不存在")

    payload = _validate_fee_payload(payload)
    config.fee_type = payload["fee_type"]
    config.fee_name = payload["fee_name"]
    config.description = payload.get("description", "")
    config.calculation_method = payload["calculation_method"]
    config.percentage_rate = payload.get("percentage_rate")
    config.fixed_amount = payload.get("fixed_amount")
    config.currency = payload.get("currency", config.currency)
    config.updated_at = datetime.utcnow()
    db.session.commit()
    return fee_to_dict(config)


def delete_fee(fee_id):
    """Soft delete: keep the row, flip is_active off."""
    config = db.session.get(FeeConfig, fee_id)
    if config is None:
        raise NotFoundError("费用配置不存在")

    config.is_active = False
    config.updated_at = datetime.utcnow()
    db.session.commit()
    return {"id": fee_id}
