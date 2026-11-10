from app import db
from datetime import datetime
from sqlalchemy import DECIMAL


class Payment(db.Model):
    """支付记录模型"""

    __tablename__ = "payments"

    id = db.Column(db.Integer, primary_key=True)
    shop_id = db.Column(db.Integer, db.ForeignKey("shops.id"), nullable=True, index=True)  # 所属店铺
    # 订单详情、手续费回填都按 order_id 查，必须带索引（否则每次全表扫）
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False, index=True)

    # 支付信息
    payment_method = db.Column(db.String(50), nullable=False)  # paypal, stripe
    transaction_id = db.Column(db.String(255))  # 交易ID

    # 金额信息
    amount = db.Column(DECIMAL(10, 2), nullable=False)  # 支付金额（买家实际扣款币种）
    currency = db.Column(db.String(3), default="USD")  # 买家实际扣款币种
    fee_fixed = db.Column(DECIMAL(10, 2), default=0)  # 固定手续费
    fee_percentage = db.Column(DECIMAL(5, 4), default=0)  # 百分比手续费
    total_fee = db.Column(DECIMAL(10, 2), default=0)  # 总手续费（已折合订单币种）
    net_amount = db.Column(DECIMAL(10, 2))  # 净收入（已折合订单币种）

    # 网关真实费用（来自交易回执/结算流水，区别于 fee_configs 的估算值）
    real_fee = db.Column(DECIMAL(14, 4))  # 网关实际手续费（fee_currency 计）
    real_net = db.Column(DECIMAL(14, 4))  # 网关实际净额（fee_currency 计）
    fee_currency = db.Column(db.String(3))  # 手续费币种
    amount_in_order_currency = db.Column(DECIMAL(12, 2))  # 支付金额折合订单币种
    settlement_currency = db.Column(db.String(3))  # 到账币种
    settlement_amount = db.Column(DECIMAL(14, 2))  # 到账金额（结算币种计）
    exchange_rate = db.Column(DECIMAL(20, 10))  # 扣款币种 -> 到账币种
    fee_source = db.Column(db.String(16), default="estimated")  # gateway=真实 / estimated=估算
    capture_id = db.Column(db.String(64), index=True)  # 网关捕获号
    gateway_reference = db.Column(db.String(64))  # 网关单号（PayPal order id 等）

    # 状态
    status = db.Column(db.String(50), default="pending")  # pending, completed, failed

    # 时间信息
    payment_date = db.Column(db.DateTime, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<Payment {self.transaction_id}>"

    def calculate_fee(self):
        """计算手续费"""
        from decimal import Decimal

        if self.amount is None:
            self.amount = Decimal("0")
        if self.fee_percentage is None:
            self.fee_percentage = Decimal("0")
        if self.fee_fixed is None:
            self.fee_fixed = Decimal("0")

        # 确保所有值都是Decimal类型
        amount = Decimal(str(self.amount))
        fee_percentage = Decimal(str(self.fee_percentage))
        fee_fixed = Decimal(str(self.fee_fixed))

        percentage_fee = amount * (fee_percentage / Decimal("100"))
        self.total_fee = (fee_fixed + percentage_fee).quantize(Decimal("0.01"))
        self.net_amount = amount - self.total_fee

    def to_dict(self):
        """转换为字典"""
        return {
            "id": self.id,
            "shop_id": self.shop_id,
            "order_id": self.order_id,
            "payment_method": self.payment_method,
            "transaction_id": self.transaction_id,
            "amount": float(self.amount) if self.amount else 0,
            "currency": self.currency,
            "fee_fixed": float(self.fee_fixed) if self.fee_fixed else 0,
            "fee_percentage": float(self.fee_percentage) if self.fee_percentage else 0,
            "total_fee": float(self.total_fee) if self.total_fee else 0,
            "net_amount": float(self.net_amount) if self.net_amount else 0,
            "real_fee": float(self.real_fee) if self.real_fee is not None else None,
            "real_net": float(self.real_net) if self.real_net is not None else None,
            "fee_currency": self.fee_currency,
            "amount_in_order_currency": (
                float(self.amount_in_order_currency) if self.amount_in_order_currency is not None else None
            ),
            "settlement_currency": self.settlement_currency,
            "settlement_amount": float(self.settlement_amount)
            if self.settlement_amount is not None
            else None,
            "exchange_rate": float(self.exchange_rate) if self.exchange_rate is not None else None,
            "fee_source": self.fee_source or "estimated",
            "capture_id": self.capture_id,
            "gateway_reference": self.gateway_reference,
            "status": self.status,
            "payment_date": self.payment_date.isoformat() if self.payment_date else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
