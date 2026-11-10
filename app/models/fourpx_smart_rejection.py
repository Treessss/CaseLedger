"""4PX 智选产品被拒的邮编缓存（用于下次直接回退轻小件）。"""

from datetime import datetime

from app import db


class FourPxSmartRejection(db.Model):
    __tablename__ = "fourpx_smart_rejections"
    __table_args__ = (db.UniqueConstraint("shop_id", "country", "postal_code", "product_code",
                                          name="uq_fourpx_smart_rejection"),)

    id = db.Column(db.Integer, primary_key=True)
    shop_id = db.Column(db.Integer, db.ForeignKey("shops.id", ondelete="CASCADE"), nullable=False, index=True)
    country = db.Column(db.String(8), nullable=False)
    postal_code = db.Column(db.String(20), nullable=False)
    product_code = db.Column(db.String(20), nullable=False)
    error_code = db.Column(db.String(20))
    error_message = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
