from datetime import datetime

from app import db


class WebhookDelivery(db.Model):
    """Successfully processed Shopify delivery; unique key makes retries harmless."""

    __tablename__ = "webhook_deliveries"
    id = db.Column(db.Integer, primary_key=True)
    shop_id = db.Column(db.Integer, db.ForeignKey("shops.id"), nullable=False, index=True)
    delivery_id = db.Column(db.String(128), nullable=False)
    topic = db.Column(db.String(100), nullable=False)
    processed_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint("shop_id", "delivery_id", name="uq_webhook_shop_delivery"),)
