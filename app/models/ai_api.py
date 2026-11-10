"""AI 接口的 API Key 与调用审计（第一版：客服邮件只读/发送）。"""

from datetime import datetime

from app import db


class AiApiKey(db.Model):
    """发给 AI/自动化工具的 API Key；只保存哈希，明文只在创建时返回一次。"""

    __tablename__ = "ai_api_keys"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    key_prefix = db.Column(db.String(20), nullable=False)
    key_hash = db.Column(db.String(64), nullable=False, unique=True, index=True)
    shop_ids = db.Column(db.JSON, nullable=False, default=list, server_default="[]")
    scopes = db.Column(db.JSON, nullable=False, default=list, server_default="[]")
    # 可选：IP 白名单（为空表示不限）与每日发送上限（防止 AI 群发）
    ip_allowlist = db.Column(db.JSON, nullable=False, default=list, server_default="[]")
    daily_send_limit = db.Column(db.Integer, nullable=False, default=100, server_default="100")
    is_active = db.Column(db.Boolean, nullable=False, default=True, server_default="true")
    created_by = db.Column(db.Integer)
    last_used_at = db.Column(db.DateTime)
    expires_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "key_prefix": self.key_prefix,
            "shop_ids": self.shop_ids or [],
            "scopes": self.scopes or [],
            "ip_allowlist": self.ip_allowlist or [],
            "daily_send_limit": self.daily_send_limit,
            "is_active": self.is_active,
            "created_by": self.created_by,
            "last_used_at": self.last_used_at.isoformat() if self.last_used_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class AiApiAudit(db.Model):
    """AI 接口调用审计：哪个 key、调了什么、结果如何。"""

    __tablename__ = "ai_api_audit_logs"

    id = db.Column(db.Integer, primary_key=True)
    key_id = db.Column(db.Integer, db.ForeignKey("ai_api_keys.id", ondelete="SET NULL"), index=True)
    method = db.Column(db.String(10), nullable=False)
    path = db.Column(db.String(500), nullable=False)
    status_code = db.Column(db.Integer, nullable=False)
    duration_ms = db.Column(db.Integer)
    ip = db.Column(db.String(64))
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
