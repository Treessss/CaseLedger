"""客服邮件（Zoho Mail）相关的表。

设计要点（这版就是为了解决"打开一封邮件很慢、发送很慢"）：

* 每封邮件一行、字段拆列，并按 thread / customer / status / 时间建索引，
  查询单封或单个会话都走索引，不再把整箱邮件读出来在内存里重新分类；
* ``status``（是否已回复）在同步和核查时算好后**存在行上**，
  列表按 status + sent_at 直接分页，打开单封就是一次主键查询；
* 发送记录单独一张表并带主键，天然防重复发送（同一封邮件只能有一条发送记录）。
"""

from datetime import datetime

from app import db


class MailMessage(db.Model):
    __tablename__ = "mail_messages"
    __table_args__ = (
        db.UniqueConstraint("shop_id", "provider_message_id", name="uq_mail_shop_provider"),
        db.Index("ix_mail_shop_inbox", "shop_id", "direction", "is_latest", "status", "sent_at"),
        db.Index("ix_mail_shop_thread", "shop_id", "thread_id", "sent_at"),
        db.Index("ix_mail_shop_customer", "shop_id", "customer", "direction", "sent_at"),
        db.Index("ix_mail_messages_thread", "thread_id", "sent_at"),
        db.Index("ix_mail_messages_customer_time", "customer", "direction", "sent_at"),
        db.Index("ix_mail_messages_status_time", "status", "sent_at"),
        db.Index("ix_mail_messages_inbox", "direction", "is_latest", "sent_at"),
    )

    # Zoho 的 messageId，直接当主键
    id = db.Column(db.String(120), primary_key=True)
    shop_id = db.Column(db.Integer, db.ForeignKey("shops.id"), nullable=False)
    provider_message_id = db.Column(db.String(120), nullable=False)
    ignored_at = db.Column(db.DateTime)
    # thread / customer / status 都靠下面的复合索引，不用再单独建一份
    thread_id = db.Column(db.String(120))
    folder_id = db.Column(db.String(60))
    direction = db.Column(db.String(8), nullable=False)  # in=客户来信 / out=我方发出
    sender = db.Column(db.String(320))
    recipient = db.Column(db.String(320))
    cc = db.Column(db.String(320))
    customer = db.Column(db.String(320))
    recipients = db.Column(db.JSON)
    subject = db.Column(db.String(500))
    body = db.Column(db.Text)
    # epoch 毫秒：Zoho 返回的就是毫秒时间戳，直接存省掉时区换算
    sent_at = db.Column(db.BigInteger, nullable=False, index=True)
    has_attachment = db.Column(db.Boolean, nullable=False, default=False)
    # 自动回复 / 系统通知（这类邮件不算"客户在等回复"）
    is_system = db.Column(db.Boolean, nullable=False, default=False)
    rfc_message_id = db.Column(db.String(320))
    references = db.Column(db.JSON)
    header_checked = db.Column(db.Boolean, nullable=False, default=False)
    header_automatic = db.Column(db.Boolean, nullable=False, default=False)
    # 正文是否已经取回（列表接口只拿 header，打开时才取正文并缓存）
    hydrated = db.Column(db.Boolean, nullable=False, default=False)
    status = db.Column(db.String(20), nullable=False, default="verify")
    evidence = db.Column(db.String(500))
    # 同一个会话里最新的一封客户来信：列表只显示它，避免一个会话刷出一串行
    is_latest = db.Column(db.Boolean, nullable=False, default=False)
    synced_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self, *, with_body=True):
        data = {
            "id": self.id,
            "shop_id": self.shop_id,
            "ignored": self.ignored_at is not None,
            "thread_id": self.thread_id,
            "folder_id": self.folder_id,
            "direction": self.direction,
            "from": self.sender,
            "to": self.recipient,
            "cc": self.cc,
            "customer": self.customer,
            "recipients": self.recipients or [],
            "subject": self.subject,
            "time": self.sent_at,
            "attachment": bool(self.has_attachment),
            "system": bool(self.is_system),
            "rfc": self.rfc_message_id,
            "refs": self.references or [],
            "hydrated": bool(self.hydrated),
            "header_checked": bool(self.header_checked),
            "status": self.status,
            "evidence": self.evidence,
            "latest": bool(self.is_latest),
        }
        if with_body:
            data["text"] = self.body or ""
        return data


class MailDraft(db.Model):
    """一封邮件的回复草稿：人工填的"回复关键点" + DeepSeek 生成的英文正文。"""

    __tablename__ = "mail_drafts"

    message_id = db.Column(
        db.String(120), db.ForeignKey("mail_messages.id", ondelete="CASCADE"), primary_key=True
    )
    guidance = db.Column(db.Text)  # 回复关键点，可用中文
    reply = db.Column(db.Text)  # 英文回复正文
    summary = db.Column(db.Text)
    reason = db.Column(db.Text)
    category = db.Column(db.String(60))
    confidence = db.Column(db.Float)
    safe = db.Column(db.Boolean, nullable=False, default=False)
    edited = db.Column(db.Boolean, nullable=False, default=False)
    analysis = db.Column(db.JSON, nullable=False, default=dict, server_default='{}')
    source_fingerprint = db.Column(db.String(64))
    generated_at = db.Column(db.DateTime)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # 声明关系（而不是只写外键）：这样 ORM 刷库时会先插邮件、再插草稿
    message = db.relationship("MailMessage")

    def to_dict(self):
        return {
            "guidance": self.guidance or "",
            "reply": self.reply or "",
            "summary": self.summary or "",
            "reason": self.reason or "",
            "category": self.category,
            "confidence": self.confidence,
            "safe": bool(self.safe),
            "edited": bool(self.edited),
            "analysis": self.analysis or {},
            "generated_at": self.generated_at.isoformat() if self.generated_at else None,
        }


class MailSend(db.Model):
    """发送记录。主键就是邮件 id，所以同一封邮件不可能被发两次。"""

    __tablename__ = "mail_sends"

    message_id = db.Column(
        db.String(120), db.ForeignKey("mail_messages.id", ondelete="CASCADE"), primary_key=True
    )
    state = db.Column(db.String(20), nullable=False)  # sending / sent / uncertain
    to_address = db.Column(db.String(320))
    reply = db.Column(db.Text)
    automatic = db.Column(db.Boolean, nullable=False, default=False)
    result = db.Column(db.JSON)
    error = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    message = db.relationship("MailMessage")

    def to_dict(self):
        return {
            "id": self.message_id,
            "state": self.state,
            "to": self.to_address,
            "automatic": bool(self.automatic),
            "error": self.error,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class MailSetting(db.Model):
    """邮件模块的键值配置（凭据用 ENCRYPTION_KEY 加密后存 value）。"""

    __tablename__ = "mail_settings"

    key = db.Column(db.String(60), primary_key=True)
    value = db.Column(db.Text)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class MailLock(db.Model):
    """跨进程互斥（刷新 token、同步、发送各用一把锁）。"""

    __tablename__ = "mail_locks"

    name = db.Column(db.String(60), primary_key=True)
    token = db.Column(db.String(80), nullable=False)
    expires_at = db.Column(db.BigInteger, nullable=False)  # epoch 毫秒


class MailDraftJob(db.Model):
    """Durable, shop-isolated draft-only batch; results survive browser/worker restarts."""

    __tablename__ = 'mail_draft_jobs'
    id = db.Column(db.String(36), primary_key=True)
    shop_id = db.Column(db.Integer, db.ForeignKey('shops.id'), nullable=False)
    status = db.Column(db.String(20), nullable=False, server_default='queued')
    message_ids = db.Column(db.JSON, nullable=False, server_default='[]')
    results = db.Column(db.JSON, nullable=False, server_default='{}')
    attempts = db.Column(db.Integer, nullable=False, server_default='0', default=0)
    error = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    heartbeat_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    finished_at = db.Column(db.DateTime)
    __table_args__ = (db.Index('ix_mail_draft_jobs_shop_created', 'shop_id', 'created_at'),)
