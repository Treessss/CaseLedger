from datetime import datetime

from app import db


class ProductTranslation(db.Model):
    """Merchant-owned display names. Never overwrite Shopify catalog snapshots."""

    __tablename__ = "product_translations"
    __table_args__ = (db.UniqueConstraint("shop_id", "original_title", "original_color",
                                          name="uq_product_translation_name"),)
    id = db.Column(db.Integer, primary_key=True)
    shop_id = db.Column(db.Integer, db.ForeignKey("shops.id"), nullable=False, index=True)
    original_title = db.Column(db.String(255), nullable=False)
    original_color = db.Column(db.String(255), nullable=False, default="", server_default="")
    chinese_name = db.Column(db.String(255), nullable=False)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
