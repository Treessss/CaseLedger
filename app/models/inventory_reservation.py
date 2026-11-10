"""Explicitly assign available warehouse stock to an order line."""

from datetime import datetime

from app import db


class InventoryReservation(db.Model):
    __tablename__ = "inventory_reservations"
    __table_args__ = (db.UniqueConstraint("shop_id", "order_item_id", name="uq_inventory_reservation_order_item"),)

    id = db.Column(db.Integer, primary_key=True)
    shop_id = db.Column(db.Integer, db.ForeignKey("shops.id"), nullable=False, index=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False, index=True)
    order_item_id = db.Column(db.Integer, nullable=False)
    group_key = db.Column(db.String(500), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
