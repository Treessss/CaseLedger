from datetime import datetime

from app import db


class ProductNaming(db.Model):
    """Merchant names attached to a shop-scoped immutable Shopify product ID."""
    __tablename__ = 'product_namings'
    __table_args__ = (db.UniqueConstraint('shop_id', 'shopify_product_id', name='uq_product_naming_identity'),)
    id = db.Column(db.Integer, primary_key=True)
    shop_id = db.Column(db.Integer, db.ForeignKey('shops.id'), nullable=False, index=True)
    shopify_product_id = db.Column(db.String(50), nullable=False)
    current_title = db.Column(db.String(255), nullable=False)
    aliases = db.Column(db.JSON, nullable=False, default=list, server_default='[]')
    chinese_name = db.Column(db.String(255), nullable=False, default='', server_default='')
    color_names = db.Column(db.JSON, nullable=False, default=dict, server_default='{}')
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
