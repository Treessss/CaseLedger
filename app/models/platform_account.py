from app import db
from datetime import datetime
from sqlalchemy import func
from cryptography.fernet import Fernet
import os


class PlatformAccount(db.Model):
    """平台账户密码管理表"""

    __tablename__ = "platform_accounts"

    id = db.Column(db.Integer, primary_key=True)
    platform = db.Column(db.String(100), nullable=False)  # 平台名称
    username = db.Column(db.String(200), nullable=False)  # 账号
    encrypted_password = db.Column(db.Text, nullable=False)  # 加密后的密码
    notes = db.Column(db.Text)  # 备注信息
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<PlatformAccount {self.platform}:{self.username}>"

    @staticmethod
    def _get_cipher():
        """获取加密密钥。

        密钥必须来自环境变量 ENCRYPTION_KEY（或应用配置），不再提供硬编码兜底值，
        避免源码泄露导致数据库中的密码可被直接解密。
        """
        key = os.environ.get("ENCRYPTION_KEY")
        if not key:
            try:
                from flask import current_app

                key = current_app.config.get("ENCRYPTION_KEY")
            except RuntimeError:
                # 不在应用上下文中（例如迁移脚本）
                key = None
        if not key:
            raise RuntimeError("缺少 ENCRYPTION_KEY 配置，无法加解密平台账号密码。请在 .env 中配置后重试。")
        if isinstance(key, str):
            key = key.encode()
        return Fernet(key)

    def set_password(self, password):
        """设置加密密码"""
        cipher = self._get_cipher()
        self.encrypted_password = cipher.encrypt(password.encode()).decode()

    def get_password(self):
        """获取解密密码"""
        cipher = self._get_cipher()
        return cipher.decrypt(self.encrypted_password.encode()).decode()

    def to_dict(self, include_password=False):
        """转换为字典格式"""
        result = {
            "id": self.id,
            "platform": self.platform,
            "username": self.username,
            "notes": self.notes,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

        if include_password:
            result["password"] = self.get_password()

        return result

    @staticmethod
    def search(query):
        """搜索平台账户"""
        if not query:
            return PlatformAccount.query.all()

        search_pattern = f"%{query}%"
        return PlatformAccount.query.filter(
            db.or_(
                PlatformAccount.platform.ilike(search_pattern),
                PlatformAccount.username.ilike(search_pattern),
                PlatformAccount.notes.ilike(search_pattern),
            )
        ).all()

    @staticmethod
    def get_platforms():
        """获取所有平台列表"""
        platforms = db.session.query(PlatformAccount.platform).distinct().all()
        return [platform[0] for platform in platforms]
