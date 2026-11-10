from datetime import datetime

from sqlalchemy import DECIMAL, UniqueConstraint

from app import db


class OrderItem(db.Model):
    """订单商品明细（一个订单可以有多个商品行）。"""

    __tablename__ = "order_items"
    __table_args__ = (UniqueConstraint("order_id", "shopify_line_item_id", name="uq_order_line_item"),)

    id = db.Column(db.Integer, primary_key=True)
    shop_id = db.Column(db.Integer, db.ForeignKey("shops.id"), nullable=True, index=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False, index=True)

    shopify_line_item_id = db.Column(db.String(50), nullable=False)
    shopify_product_id = db.Column(db.String(50))
    shopify_variant_id = db.Column(db.String(50))

    title = db.Column(db.String(255))
    variant_title = db.Column(db.String(255))
    sku = db.Column(db.String(120))
    vendor = db.Column(db.String(120))
    image_url = db.Column(db.String(2048))  # 变体图；没有变体图时保存商品主图

    quantity = db.Column(db.Integer, default=1)
    price = db.Column(DECIMAL(12, 2), default=0)  # 单价（订单币种）
    total_discount = db.Column(DECIMAL(12, 2), default=0)  # 该行折扣合计

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<OrderItem {self.title} x{self.quantity}>"

    @property
    def subtotal(self):
        """小计（未扣折扣），用于页面展示。"""
        from decimal import Decimal

        return (Decimal(str(self.price or 0)) * Decimal(str(self.quantity or 0))).quantize(Decimal("0.01"))

    def to_dict(self):
        return {
            "id": self.id,
            "order_id": self.order_id,
            "shopify_line_item_id": self.shopify_line_item_id,
            "shopify_product_id": self.shopify_product_id,
            "shopify_variant_id": self.shopify_variant_id,
            "title": self.title,
            "variant_title": self.variant_title,
            "sku": self.sku,
            "vendor": self.vendor,
            "image_url": self.image_url,
            "quantity": self.quantity or 0,
            "price": float(self.price) if self.price is not None else 0,
            "total_discount": float(self.total_discount) if self.total_discount is not None else 0,
            "subtotal": float(self.subtotal),
        }
