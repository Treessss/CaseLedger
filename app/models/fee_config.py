from app import db
from datetime import datetime
from sqlalchemy import DECIMAL

class FeeConfig(db.Model):
    """手续费配置模型"""
    __tablename__ = 'fee_configs'
    
    id = db.Column(db.Integer, primary_key=True)
    
    # 配置基本信息
    fee_type = db.Column(db.String(50), nullable=False)  # payment, shipping, platform, other
    fee_name = db.Column(db.String(100), nullable=False)  # 费用名称
    description = db.Column(db.Text)  # 费用描述
    
    # 计算方式配置
    calculation_method = db.Column(db.String(20), nullable=False)  # percentage, fixed, percentage_plus_fixed
    percentage_rate = db.Column(DECIMAL(5, 2), default=0)  # 百分比费率
    fixed_amount = db.Column(DECIMAL(10, 2), default=0)  # 固定金额
    currency = db.Column(db.String(3), default='USD')  # 货币单位，默认USD
    
    # 配置状态
    is_active = db.Column(db.Boolean, default=True)  # 是否启用
    
    # 时间信息
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f'<FeeConfig {self.fee_type}: {self.fee_name}>'
    
    def calculate_fee(self, amount, order_currency=None):
        """计算手续费
        
        Args:
            amount: 订单金额
            order_currency: 订单货币，如果与费用配置货币不同，会进行汇率转换
        
        Returns:
            计算后的手续费（以费用配置的货币为单位）
        """
        if not self.is_active:
            return 0
        
        # 如果订单货币与费用配置货币不同，需要进行汇率转换
        converted_amount = amount
        if order_currency and order_currency != self.currency:
            from app.services.exchange_rate_service import ExchangeRateService
            exchange_service = ExchangeRateService()
            
            # 将订单金额转换为费用配置的货币
            try:
                rate = exchange_service.get_exchange_rate(order_currency, self.currency)
                if rate is not None:
                    converted_amount = float(amount) * float(rate)
                else:
                    print(f"无法获取汇率 {order_currency} -> {self.currency}，使用原始金额计算费用")
                    converted_amount = amount
            except Exception as e:
                # 如果汇率转换失败，记录错误并使用原始金额
                print(f"汇率转换失败: {e}，使用原始金额计算费用")
                converted_amount = amount
        
        # 确保所有计算都使用float类型
        from decimal import Decimal
        converted_amount = float(converted_amount) if isinstance(converted_amount, Decimal) else converted_amount
        
        fee = 0
        if self.calculation_method == 'percentage':
            fee = float(converted_amount) * (float(self.percentage_rate) / 100)
        elif self.calculation_method == 'fixed':
            fee = float(self.fixed_amount)
        elif self.calculation_method == 'percentage_plus_fixed':
            # 百分比 + 固定金额
            percentage_fee = float(converted_amount) * (float(self.percentage_rate) / 100)
            fee = percentage_fee + float(self.fixed_amount)
        
        # 四舍五入保留两位小数
        return round(fee, 2)
    
    def to_dict(self):
        """转换为字典"""
        return {
            'id': self.id,
            'fee_type': self.fee_type,
            'fee_name': self.fee_name,
            'description': self.description,
            'calculation_method': self.calculation_method,
            'percentage_rate': float(self.percentage_rate) if self.percentage_rate else 0,
            'fixed_amount': float(self.fixed_amount) if self.fixed_amount else 0,
            'currency': self.currency,
            'is_active': self.is_active,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }
    
    @staticmethod
    def get_default_configs():
        """获取默认配置"""
        return [
            {
                'fee_type': 'payment',
                'fee_name': 'PayPal手续费',
                'description': 'PayPal支付手续费',
                'calculation_method': 'percentage_plus_fixed',
                'percentage_rate': 4.4,
                'fixed_amount': 0.30,
                'currency': 'USD',
                'is_active': True
            },
            {
                'fee_type': 'payment',
                'fee_name': 'Stripe手续费',
                'description': 'Stripe支付手续费',
                'calculation_method': 'percentage',
                'percentage_rate': 2.9,
                'fixed_amount': 0.30,
                'currency': 'USD',
                'is_active': True
            },
            {
                'fee_type': 'platform',
                'fee_name': 'Shopify平台费',
                'description': 'Shopify平台交易费',
                'calculation_method': 'percentage',
                'percentage_rate': 2.0,
                'fixed_amount': 0,
                'currency': 'USD',
                'is_active': True
            }
        ]
    
    @classmethod
    def init_default_configs(cls):
        """初始化默认配置"""
        for config_data in cls.get_default_configs():
            existing = cls.query.filter_by(
                fee_type=config_data['fee_type'],
                fee_name=config_data['fee_name']
            ).first()
            
            if not existing:
                config = cls(**config_data)
                db.session.add(config)
        
        db.session.commit()