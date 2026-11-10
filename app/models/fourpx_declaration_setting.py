from datetime import datetime

from app import db


class FourPxDeclarationSetting(db.Model):
    """4PX 预申报的店铺级开关（建单后是否自动回填 Shopify）。

    没有独立设置表的历史包袱，先用一张小表按 shop_id 存布尔开关；
    真实回填仍走 shopify_fulfillment_service.writeback_tracking 的同一套校验。
    """

    __tablename__ = "fourpx_declaration_settings"

    shop_id = db.Column(db.Integer, db.ForeignKey("shops.id", ondelete="CASCADE"), primary_key=True)
    auto_writeback = db.Column(db.Boolean, nullable=False, default=False, server_default="false")
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "shop_id": self.shop_id,
            "auto_writeback": bool(self.auto_writeback),
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
