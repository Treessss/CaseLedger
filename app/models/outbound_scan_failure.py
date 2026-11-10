"""Daily, shop-scoped scan rejections. Repeated outbound is deliberately omitted."""
from datetime import datetime

from app import db


class OutboundScanFailure(db.Model):
    __tablename__ = "outbound_scan_failures"
    __table_args__ = (
        db.UniqueConstraint("shop_id", "business_date", "scan_code", "category", name="uq_scan_failure_daily"),
        db.Index("ix_scan_failure_shop_day_last", "shop_id", "business_date", "last_seen_at"),
    )

    id = db.Column(db.Integer, primary_key=True)
    shop_id = db.Column(db.Integer, db.ForeignKey("shops.id"), nullable=False)
    business_date = db.Column(db.Date, nullable=False)
    scan_code = db.Column(db.String(256), nullable=False)
    category = db.Column(db.String(32), nullable=False)
    reason = db.Column(db.String(1000), nullable=False)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"))
    after_sale_id = db.Column(db.Integer, db.ForeignKey("after_sales.id"))
    order_number = db.Column(db.String(100))
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    attempts = db.Column(db.Integer, nullable=False, default=1, server_default="1")
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    last_seen_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def to_dict(self):
        return {key: getattr(self, key) for key in (
            "id", "shop_id", "scan_code", "category", "reason", "order_id", "after_sale_id",
            "order_number", "user_id", "attempts",
        )} | {
            "business_date": self.business_date.isoformat(),
            "created_at": self.created_at.isoformat(),
            "last_seen_at": self.last_seen_at.isoformat(),
        }
