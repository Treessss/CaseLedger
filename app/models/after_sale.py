"""Independent reshipment workflow; never mutates the original fulfillment."""

from datetime import datetime

from app import db


class AfterSale(db.Model):
    __tablename__ = "after_sales"
    source_mail_message_id = db.Column(db.String(120), db.ForeignKey("mail_messages.id"))
    __table_args__ = (
        db.Index("uq_after_sales_active_reference", "shop_id", "customer_reference", unique=True,
                 postgresql_where=db.text("deleted_at IS NULL"), sqlite_where=db.text("deleted_at IS NULL")),
        db.Index("ix_after_sales_shop_status_created", "shop_id", "status", "created_at"),
    )

    id = db.Column(db.Integer, primary_key=True)
    shop_id = db.Column(db.Integer, db.ForeignKey("shops.id"), nullable=False, index=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False, index=True)
    customer_reference = db.Column(db.String(80), nullable=False)
    kind = db.Column(db.String(20), nullable=False)
    reason = db.Column(db.Text, nullable=False)
    note = db.Column(db.Text)
    status = db.Column(db.String(30), nullable=False, default="pending_purchase")
    shipping_address = db.Column(db.JSON, nullable=False)
    tracking_number = db.Column(db.String(120), unique=True)
    label_barcode = db.Column(db.String(120), unique=True)
    purchased_at = db.Column(db.DateTime)
    shipped_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    deleted_at = db.Column(db.DateTime)
    deleted_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    order = db.relationship("Order", backref=db.backref("after_sales", lazy="selectin"))
    shop = db.relationship("Shop")
    items = db.relationship("AfterSaleItem", back_populates="after_sale", cascade="all, delete-orphan", order_by="AfterSaleItem.id")
    costs = db.relationship("AfterSaleCost", back_populates="after_sale", cascade="all, delete-orphan", order_by="AfterSaleCost.id")
    purchase_lists = db.relationship("PurchaseList", back_populates="after_sale", lazy="selectin", order_by="PurchaseList.id")

    def to_dict(self, *, mixed_purchases=None):
        from app.models.purchase_list import PurchaseList

        linked = [purchase for purchase in self.purchase_lists if purchase.status != "cancelled"]
        mixed = mixed_purchases
        if mixed is None:
            mixed = PurchaseList.query.filter(
                PurchaseList.shop_id == self.shop_id,
                PurchaseList.after_sale_id.is_(None),
                PurchaseList.status != "cancelled",
            ).all()
        linked.extend(purchase for purchase in mixed if any(
            int(row.get("after_sale_id") or 0) == self.id for row in purchase.allocations or []
        ))
        return {
            "id": self.id, "shop_id": self.shop_id, "order_id": self.order_id,
            "deleted_at": self.deleted_at.isoformat() if self.deleted_at else None,
            "order_number": self.order.order_number, "customer_reference": self.customer_reference,
            "kind": self.kind, "reason": self.reason, "note": self.note, "status": self.status,
            "shipping_address": self.shipping_address, "tracking_number": self.tracking_number,
            "source_mail_message_id": self.source_mail_message_id,
            "label_barcode": self.label_barcode,
            "purchased_at": self.purchased_at.isoformat() if self.purchased_at else None,
            "shipped_at": self.shipped_at.isoformat() if self.shipped_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "items": [item.to_dict() for item in self.items],
            "costs": [cost.to_dict() for cost in self.costs],
            "purchase_lists": [{"id": purchase.id, "status": purchase.status,
                                "name": purchase.name} for purchase in linked],
            "total_cost_cny": float(sum((cost.amount_cny for cost in self.costs), 0)),
        }


class AfterSaleItem(db.Model):
    __tablename__ = "after_sale_items"

    id = db.Column(db.Integer, primary_key=True)
    after_sale_id = db.Column(db.Integer, db.ForeignKey("after_sales.id"), nullable=False, index=True)
    source_order_item_id = db.Column(db.Integer, db.ForeignKey("order_items.id"), nullable=True)
    catalog_order_item_id = db.Column(db.Integer, db.ForeignKey("order_items.id"), nullable=True)
    catalog_product_id = db.Column(db.Integer, nullable=True)
    shopify_product_id = db.Column(db.String(50))
    shopify_variant_id = db.Column(db.String(50))
    vendor = db.Column(db.String(120))
    group_key = db.Column(db.String(500), nullable=False)
    title = db.Column(db.String(255), nullable=False)
    variant_title = db.Column(db.String(255))
    sku = db.Column(db.String(120))
    image_url = db.Column(db.String(2048))
    quantity = db.Column(db.Integer, nullable=False)
    reserved_quantity = db.Column(db.Integer, nullable=False, default=0)
    received_quantity = db.Column(db.Integer, nullable=False, default=0)
    damaged_quantity = db.Column(db.Integer, nullable=False, default=0)

    after_sale = db.relationship("AfterSale", back_populates="items")

    def to_dict(self):
        return {
            "id": self.id, "source_order_item_id": self.source_order_item_id,
            "catalog_order_item_id": self.catalog_order_item_id,
            "catalog_product_id": self.catalog_product_id,
            "shopify_product_id": self.shopify_product_id,
            "shopify_variant_id": self.shopify_variant_id,
            "vendor": self.vendor,
            "group_key": self.group_key,
            "title": self.title, "variant_title": self.variant_title,
            "sku": self.sku, "image_url": self.image_url,
            "quantity": self.quantity, "reserved_quantity": self.reserved_quantity,
            "received_quantity": self.received_quantity, "damaged_quantity": self.damaged_quantity,
            "needed_quantity": max(self.quantity - self.reserved_quantity, 0),
        }


class AfterSaleCost(db.Model):
    __tablename__ = "after_sale_costs"

    id = db.Column(db.Integer, primary_key=True)
    after_sale_id = db.Column(db.Integer, db.ForeignKey("after_sales.id"), nullable=False, index=True)
    shop_id = db.Column(db.Integer, db.ForeignKey("shops.id"), nullable=False, index=True)
    cost_type = db.Column(db.String(20), nullable=False)
    amount_cny = db.Column(db.Numeric(12, 2), nullable=False)
    note = db.Column(db.String(255))
    incurred_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    # manual=人工登记, 4px=4PX 接口真实补发运费
    source = db.Column(db.String(20), nullable=False, default="manual", server_default="manual")
    fourpx_tracking_no = db.Column(db.String(120))
    fourpx_synced_at = db.Column(db.DateTime)

    after_sale = db.relationship("AfterSale", back_populates="costs")

    def to_dict(self):
        return {"id": self.id, "cost_type": self.cost_type, "amount_cny": float(self.amount_cny),
                "note": self.note, "incurred_at": self.incurred_at.isoformat() if self.incurred_at else None,
                "source": self.source or "manual", "fourpx_tracking_no": self.fourpx_tracking_no,
                "fourpx_synced_at": self.fourpx_synced_at.isoformat() if self.fourpx_synced_at else None}
