"""Saved procurement snapshots generated from selected orders."""

from datetime import datetime

from app import db


class PurchaseListEdit(db.Model):
    """Append-only snapshots for manual procurement changes, never stock movements."""
    __tablename__ = "purchase_list_edits"

    id = db.Column(db.Integer, primary_key=True)
    purchase_id = db.Column(db.Integer, db.ForeignKey("purchase_lists.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    before = db.Column(db.JSON, nullable=False)
    after = db.Column(db.JSON, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class PurchaseList(db.Model):
    __tablename__ = "purchase_lists"

    id = db.Column(db.Integer, primary_key=True)
    shop_id = db.Column(db.Integer, db.ForeignKey("shops.id"), nullable=False, index=True)
    after_sale_id = db.Column(db.Integer, db.ForeignKey("after_sales.id"), nullable=True, index=True)
    name = db.Column(db.String(120), nullable=False)
    order_ids = db.Column(db.JSON, nullable=False)
    items = db.Column(db.JSON, nullable=False)
    order_count = db.Column(db.Integer, nullable=False, default=0)
    total_quantity = db.Column(db.Integer, nullable=False, default=0)
    status = db.Column(db.String(20), nullable=False, default="draft", server_default="draft")
    confirmed_at = db.Column(db.DateTime)
    received_at = db.Column(db.DateTime)
    allocations = db.Column(db.JSON, nullable=False, default=list, server_default="[]")
    receipt = db.Column(db.JSON, nullable=False, default=list, server_default="[]")
    receipt_batches = db.Column(db.JSON, nullable=False, default=list, server_default="[]")
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    shop = db.relationship("Shop")
    after_sale = db.relationship("AfterSale", back_populates="purchase_lists")
    edit_history = db.relationship("PurchaseListEdit", cascade="all, delete-orphan")

    def to_dict(self, include_items=False):
        data = {
            "id": self.id,
            "shop_id": self.shop_id,
            "after_sale_id": self.after_sale_id,
            "shop_name": self.shop.name if self.shop else None,
            "name": self.name,
            "order_count": self.order_count,
            "item_count": len(self.items or []),
            "total_quantity": self.total_quantity,
            "status": self.status,
            "confirmed_at": self.confirmed_at.isoformat() if self.confirmed_at else None,
            "received_at": self.received_at.isoformat() if self.received_at else None,
            "receipt": self.receipt or [],
            "receipt_batches": self.receipt_batches or [],
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        if include_items:
            data["order_ids"] = self.order_ids or []
            data["items"] = self.items or []
            data["allocations"] = self.allocations or []
        return data
