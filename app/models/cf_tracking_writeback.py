"""Audit CF tracking replacements without changing physical warehouse history."""

from datetime import datetime

from app import db


class CfTrackingWriteback(db.Model):
    __tablename__ = "cf_tracking_writebacks"

    id = db.Column(db.Integer, primary_key=True)
    shop_id = db.Column(db.Integer, db.ForeignKey("shops.id"), nullable=False, index=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False, index=True)
    after_sale_id = db.Column(db.Integer, db.ForeignKey("after_sales.id"))
    customer_reference = db.Column(db.String(80), nullable=False)
    tracking_number = db.Column(db.String(120), nullable=False)
    fulfillment_id = db.Column(db.String(120), nullable=False)
    previous_tracking_numbers = db.Column(db.JSON, nullable=False)
    state = db.Column(db.String(20), nullable=False)
    notification_requested = db.Column(db.Boolean, nullable=False, default=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
