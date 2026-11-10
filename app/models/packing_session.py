"""Explicit hard holds for a packing employee; lookups never create these."""
from datetime import datetime
from app import db


class PackingSession(db.Model):
    __tablename__ = "packing_sessions"
    __table_args__ = (
        db.CheckConstraint("(order_id IS NULL) <> (after_sale_id IS NULL)", name="ck_packing_one_target"),
        db.Index("ix_packing_shop_state_expiry", "shop_id", "state", "expires_at"),
    )
    id = db.Column(db.Integer, primary_key=True)
    shop_id = db.Column(db.Integer, db.ForeignKey("shops.id"), nullable=False)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), index=True)
    after_sale_id = db.Column(db.Integer, db.ForeignKey("after_sales.id"), index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    state = db.Column(db.String(20), nullable=False, default="packing", server_default="packing")
    lines = db.Column(db.JSON, nullable=False, default=list, server_default="[]")
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=False)
    ended_at = db.Column(db.DateTime)


class InventoryOutboundLine(db.Model):
    """Append-only physical deductions and returns, independent of soft claims."""
    __tablename__ = "inventory_outbound_lines"
    __table_args__ = (db.Index("ix_outbound_lines_shop_key", "shop_id", "group_key"),)
    id = db.Column(db.Integer, primary_key=True)
    shop_id = db.Column(db.Integer, db.ForeignKey("shops.id"), nullable=False)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False, index=True)
    group_key = db.Column(db.String(500), nullable=False)
    quantity_delta = db.Column(db.Integer, nullable=False)
    action = db.Column(db.String(20), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
