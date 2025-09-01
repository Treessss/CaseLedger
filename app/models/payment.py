from app import db
from datetime import datetime
from sqlalchemy import DECIMAL

class Payment(db.Model):
    """支付记录模型"""
    __tablename__ = 'payments'
    
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=False)
    
    # 支付信息
    payment_method = db.Column(db.String(50), nullable=False)  # paypal, stripe
    transaction_id = db.Column(db.String(255))  # 交易ID
    
    # 金额信息
    amount = db.Column(DECIMAL(10, 2), nullable=False)  # 支付金额
    currency = db.Column(db.String(3), default='USD')  # 货币单位，默认USD
    fee_fixed = db.Column(DECIMAL(10, 2), default=0)  # 固定手续费
    fee_percentage = db.Column(DECIMAL(5, 4), default=0)  # 百分比手续费
    total_fee = db.Column(DECIMAL(10, 2), default=0)  # 总手续费
    net_amount = db.Column(DECIMAL(10, 2))  # 净收入
    
    # 状态
    status = db.Column(db.String(50), default='pending')  # pending, completed, failed
    
    # 时间信息
    payment_date = db.Column(db.DateTime, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f'<Payment {self.transaction_id}>'
    
    def calculate_fee(self):
        """计算手续费"""
        from decimal import Decimal
        
        if self.amount is None:
            self.amount = Decimal('0')
        if self.fee_percentage is None:
            self.fee_percentage = Decimal('0')
        if self.fee_fixed is None:
            self.fee_fixed = Decimal('0')
            
        # 确保所有值都是Decimal类型
        amount = Decimal(str(self.amount))
        fee_percentage = Decimal(str(self.fee_percentage))
        fee_fixed = Decimal(str(self.fee_fixed))
            
        percentage_fee = amount * (fee_percentage / Decimal('100'))
        self.total_fee = (fee_fixed + percentage_fee).quantize(Decimal('0.01'))
        self.net_amount = amount - self.total_fee
    
    def to_dict(self):
        """转换为字典"""
        return {
            'id': self.id,
            'order_id': self.order_id,
            'payment_method': self.payment_method,
            'transaction_id': self.transaction_id,
            'amount': float(self.amount) if self.amount else 0,
            'currency': self.currency,
            'fee_fixed': float(self.fee_fixed) if self.fee_fixed else 0,
            'fee_percentage': float(self.fee_percentage) if self.fee_percentage else 0,
            'total_fee': float(self.total_fee) if self.total_fee else 0,
            'net_amount': float(self.net_amount) if self.net_amount else 0,
            'status': self.status,
            'payment_date': self.payment_date.isoformat() if self.payment_date else None,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }