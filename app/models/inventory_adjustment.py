"""Auditable manual inventory corrections, separate from purchase receipts."""

from datetime import datetime

from app import db


class InventoryAdjustment(db.Model):
    __tablename__ = "inventory_adjustments"

    id = db.Column(db.Integer, primary_key=True)
    shop_id = db.Column(db.Integer, db.ForeignKey("shops.id"), nullable=False, index=True)
    group_key = db.Column(db.String(500), nullable=False, index=True)
    quantity_delta = db.Column(db.Integer, nullable=False)
    reason = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    shop = db.relationship("Shop")

    def to_dict(self):
        return {
            "id": self.id,
            "shop_id": self.shop_id,
            "group_key": self.group_key,
            "quantity_delta": self.quantity_delta,
            "reason": self.reason,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
