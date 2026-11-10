from datetime import datetime

from app import db


class FourPxDeclarationJob(db.Model):
    """4PX 真实预申报的异步批次任务（进度可恢复，关页面/刷新不丢）。"""

    __tablename__ = "fourpx_declaration_jobs"

    id = db.Column(db.String(36), primary_key=True)
    shop_id = db.Column(db.Integer, db.ForeignKey("shops.id", ondelete="CASCADE"), nullable=False, index=True)
    status = db.Column(db.String(20), nullable=False, default="queued", server_default="queued")
    order_ids = db.Column(db.JSON, nullable=False, default=list, server_default="[]")
    requested = db.Column(db.Integer, nullable=False, default=0)
    processed = db.Column(db.Integer, nullable=False, default=0)
    created_count = db.Column(db.Integer, nullable=False, default=0)
    failed_count = db.Column(db.Integer, nullable=False, default=0)
    results = db.Column(db.JSON, nullable=False, default=list, server_default="[]")
    error = db.Column(db.String(500))
    created_by = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "shop_id": self.shop_id,
            "status": self.status,
            "requested": self.requested,
            "processed": self.processed,
            "created": self.created_count,
            "failed": self.failed_count,
            "results": self.results or [],
            "error": self.error,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
