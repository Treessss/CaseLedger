import os
from datetime import datetime

from app import db


class ShopCredentialError(RuntimeError):
    """店铺凭据加解密失败。"""


class Shop(db.Model):
    """店铺模型。

    每个店铺对应一个独立的 Shopify 店铺（含独立 API 凭据），
    订单、商品、支付、费用、订单成本都通过 shop_id 归属到具体店铺。
    """

    __tablename__ = "shops"

    id = db.Column(db.Integer, primary_key=True)

    # 店铺基本信息
    name = db.Column(db.String(100), nullable=False)  # 店铺显示名，如「美国主店」
    shop_url = db.Column(db.String(255), nullable=False, unique=True)  # xxx.myshopify.com

    # 独立 API 凭据
    api_key = db.Column(db.String(255))
    api_secret = db.Column(db.String(255))
    access_token = db.Column(db.String(255))
    webhook_secret = db.Column(db.String(255))

    # 客户隐私数据（姓名/收货地址/电话）专用：Partner 应用的 client_id / client_secret。
    # 店铺后台自建应用发的是永久 shpat_ 令牌，Shopify 不给它 PII；
    # 必须用已获批 PII 权限的 Partner 应用走 client_credentials 换临时令牌。
    # client_secret 用 Fernet 加密后落库（与平台账号密码同一套密钥）。
    oauth_client_id = db.Column(db.String(255))
    oauth_client_secret = db.Column(db.Text)

    # 4PX credentials are shop-scoped and encrypted; integration is disabled
    # until product/channel and declaration settings are configured separately.
    fourpx_app_name = db.Column(db.String(100))
    fourpx_app_key_encrypted = db.Column(db.Text)
    fourpx_app_secret_encrypted = db.Column(db.Text)
    fourpx_resource_code = db.Column(db.String(100))
    fourpx_environment = db.Column(db.String(20), nullable=False, default="test", server_default="test")

    # 业务配置
    currency = db.Column(db.String(10), default="USD")  # 结算货币
    is_active = db.Column(db.Boolean, default=True)  # 是否启用
    notes = db.Column(db.Text)
    last_sync = db.Column(db.DateTime)
    last_sync_attempt_at = db.Column(db.DateTime)
    last_sync_error = db.Column(db.Text)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<Shop {self.name}:{self.shop_url}>"

    @staticmethod
    def normalize_shop_url(raw_url):
        """把用户输入统一成 xxx.myshopify.com 形式。"""
        if not raw_url:
            return ""
        value = str(raw_url).strip()
        for prefix in ("https://", "http://"):
            if value.lower().startswith(prefix):
                value = value[len(prefix) :]
        value = value.rstrip("/")
        value = value.split("/")[0]
        if "." not in value:
            value = f"{value}.myshopify.com"
        return value

    def to_dict(self, include_secrets=False):
        """店铺信息。

        绑定方式统一为「域名 + Client ID + Client Secret」，旧的
        api_key / api_secret / access_token 已不再使用，也不再对外暴露。
        """
        bound = self.has_oauth_credentials()
        data = {
            "id": self.id,
            "name": self.name,
            "shop_url": self.shop_url,
            "currency": self.currency,
            "is_active": self.is_active,
            "notes": self.notes,
            "last_sync": self.last_sync.isoformat() if self.last_sync else None,
            "last_sync_attempt_at": self.last_sync_attempt_at.isoformat() if self.last_sync_attempt_at else None,
            "last_sync_error": self.last_sync_error,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            # 域名 + 应用凭据齐全才算「已绑定」
            "has_credentials": bound,
            "has_oauth_credentials": bound,
            "oauth_client_id": self.oauth_client_id,
            "oauth_client_secret": (
                self.get_oauth_client_secret() if include_secrets else self.masked_oauth_client_secret()
            ),
            "fourpx_app_name": self.fourpx_app_name,
            "fourpx_resource_code": self.fourpx_resource_code,
            "fourpx_environment": self.fourpx_environment,
            "has_fourpx_credentials": bool(self.fourpx_app_key_encrypted and self.fourpx_app_secret_encrypted),
        }
        return data

    # ---- Partner 应用凭据（PII 专用） -------------------------------------

    @staticmethod
    def _cipher():
        """与平台账号密码共用 ENCRYPTION_KEY。"""
        key = os.environ.get("ENCRYPTION_KEY")
        if not key:
            try:
                from flask import current_app

                key = current_app.config.get("ENCRYPTION_KEY")
            except RuntimeError:
                key = None
        if not key:
            raise ShopCredentialError(
                "缺少 ENCRYPTION_KEY 配置，无法加解密店铺密钥。请在 .env 中配置后重试。"
            )
        if isinstance(key, str):
            key = key.encode()
        from cryptography.fernet import Fernet

        return Fernet(key)

    def set_oauth_client_secret(self, secret):
        """加密保存 client_secret；传空值表示清除。"""
        if not secret:
            self.oauth_client_secret = None
            return
        self.oauth_client_secret = self._cipher().encrypt(str(secret).encode()).decode()

    def set_fourpx_credentials(self, app_key, app_secret):
        """Replace both 4PX credential values atomically with Fernet ciphertext."""
        cipher = self._cipher()
        self.fourpx_app_key_encrypted = cipher.encrypt(str(app_key).encode()).decode()
        self.fourpx_app_secret_encrypted = cipher.encrypt(str(app_secret).encode()).decode()

    def get_fourpx_credentials(self):
        if not self.fourpx_app_key_encrypted or not self.fourpx_app_secret_encrypted:
            raise ShopCredentialError("此店铺未配置完整的 4PX 凭据")
        try:
            cipher = self._cipher()
            return (
                cipher.decrypt(self.fourpx_app_key_encrypted.encode()).decode(),
                cipher.decrypt(self.fourpx_app_secret_encrypted.encode()).decode(),
            )
        except Exception as exc:
            raise ShopCredentialError("4PX 凭据解密失败，请检查 ENCRYPTION_KEY") from exc

    def get_oauth_client_secret(self):
        """取出明文 client_secret（仅在真正调用 Shopify 时使用）。"""
        if not self.oauth_client_secret:
            return None
        try:
            return self._cipher().decrypt(self.oauth_client_secret.encode()).decode()
        except Exception as exc:  # noqa: BLE001 - 解密失败要给出可执行的提示
            raise ShopCredentialError(
                f"店铺「{self.name}」的 client_secret 解密失败，"
                "通常是因为 ENCRYPTION_KEY 被更换过，请重新填写该密钥。"
            ) from exc

    def has_oauth_credentials(self):
        """域名 + Client ID + Client Secret 齐全才算绑定完成。"""
        return bool(self.shop_url and self.oauth_client_id and self.oauth_client_secret)

    def masked_oauth_client_secret(self):
        """给前端回显用；解不开就退回通用掩码，保证列表接口不报错。"""
        if not self.oauth_client_secret:
            return ""
        try:
            return self._mask(self.get_oauth_client_secret())
        except ShopCredentialError:
            return "****"

    @staticmethod
    def _mask(value):
        if not value:
            return ""
        if len(value) <= 4:
            return "*" * len(value)
        return value[:4] + "*" * (len(value) - 4)
