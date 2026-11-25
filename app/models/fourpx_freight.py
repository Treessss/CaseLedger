"""4PX 实际出账费用（按订单/4PX 单号保存）。"""

from datetime import datetime

from app import db


class FourPxFreight(db.Model):
    """一张订单在 4PX 的最终费用：计费重、费用明细、总费用。"""

    __tablename__ = "fourpx_freights"

    id = db.Column(db.Integer, primary_key=True)
    shop_id = db.Column(db.Integer, db.ForeignKey("shops.id", ondelete="CASCADE"), nullable=False, index=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, unique=True)
    tracking_number = db.Column(db.String(120), index=True)
    ref_no = db.Column(db.String(120))
    charge_weight_kg = db.Column(db.DECIMAL(10, 4))
    currency = db.Column(db.String(8))
    total_fee = db.Column(db.DECIMAL(12, 2))
    subs = db.Column(db.JSON, nullable=False, default=list, server_default="[]")
    billed_at = db.Column(db.DateTime)
    raw = db.Column(db.JSON, nullable=False, default=dict, server_default="{}")
    synced_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "order_id": self.order_id,
            "shop_id": self.shop_id,
            "tracking_number": self.tracking_number,
            "ref_no": self.ref_no,
            "charge_weight_kg": float(self.charge_weight_kg) if self.charge_weight_kg is not None else None,
            "currency": self.currency,
            "total_fee": float(self.total_fee) if self.total_fee is not None else None,
            "subs": self.subs or [],
            "billed_at": self.billed_at.isoformat() if self.billed_at else None,
            "synced_at": self.synced_at.isoformat() if self.synced_at else None,
        }


class FourPxFreightRetry(db.Model):
    """Exponential backoff for orders whose 4PX fee is not billed yet.

    Kept separate from FourPxFreight because the latter only exists after a
    successful fee fetch; otherwise every 10-minute scheduler run would query
    the same unbilled orders again and again.
    """

    __tablename__ = "fourpx_freight_retries"

    id = db.Column(db.Integer, primary_key=True)
    shop_id = db.Column(db.Integer, db.ForeignKey("shops.id", ondelete="CASCADE"), nullable=False, index=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, unique=True)
    attempts = db.Column(db.Integer, nullable=False, default=0, server_default="0")
    next_retry_at = db.Column(db.DateTime, nullable=False, index=True)
    last_error = db.Column(db.String(500))
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
