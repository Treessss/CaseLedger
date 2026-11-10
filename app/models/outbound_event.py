"""扫码出库与撤回的操作流水：谁、什么时候、扫的哪个码。

撤回会直接改订单的出库状态（库存是从状态算出来的），属于可追溯的业务动作，
所以每一次出库和每一次撤回都在这里留一条记录，事后能查明是谁操作的。
"""

from datetime import datetime

from app import db

# 出库：扫码确认发货；撤回：把订单退回待出库
ACTION_SCAN = "scan"
ACTION_UNDO = "undo"


class OutboundEvent(db.Model):
    __tablename__ = "outbound_events"

    id = db.Column(db.Integer, primary_key=True)
    shop_id = db.Column(db.Integer, db.ForeignKey("shops.id"), nullable=False, index=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False, index=True)
    parcel_id = db.Column(db.Integer, db.ForeignKey("shipping_parcels.id"))
    after_sale_id = db.Column(db.Integer, db.ForeignKey("after_sales.id"))
    order_number = db.Column(db.String(100), nullable=False)
    action = db.Column(db.String(20), nullable=False)
    # 扫到的编号（撤回时记的是被撤掉的那张面单）
    code = db.Column(db.String(120))
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    reason = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "shop_id": self.shop_id,
            "order_id": self.order_id,
            "parcel_id": self.parcel_id,
            "after_sale_id": self.after_sale_id,
            "order_number": self.order_number,
            "action": self.action,
            "code": self.code,
            "user_id": self.user_id,
            "reason": self.reason,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
