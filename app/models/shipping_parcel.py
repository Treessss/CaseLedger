"""One physical shipping label and scan record per parcel."""

from datetime import datetime

from app import db


class ShippingParcel(db.Model):
    __tablename__ = "shipping_parcels"
    __table_args__ = (
        db.UniqueConstraint("shop_id", "label_number", name="uq_shipping_parcels_shop_label"),
        db.UniqueConstraint("shop_id", "label_barcode", name="uq_shipping_parcels_shop_barcode"),
    )

    id = db.Column(db.Integer, primary_key=True)
    shop_id = db.Column(db.Integer, db.ForeignKey("shops.id"), nullable=False, index=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False, index=True)
    label_number = db.Column(db.String(120), nullable=False)
    label_barcode = db.Column(db.String(120))  # 4PX PDF physical barcode; may differ from tracking number
    source = db.Column(db.String(30), nullable=False, default="manual", server_default="manual")
    scanned_at = db.Column(db.DateTime)
    scanned_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    order = db.relationship("Order", back_populates="parcels")

    def to_dict(self):
        return {
            "id": self.id,
            "label_number": self.label_number,
            "label_barcode": self.label_barcode,
            "source": self.source,
            "scanned_at": self.scanned_at.isoformat() if self.scanned_at else None,
            "scanned_by_user_id": self.scanned_by_user_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
