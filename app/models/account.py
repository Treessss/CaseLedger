from app import db
from datetime import datetime
from sqlalchemy import func


class Account(db.Model):
    """账户管理表 - 支持多平台多账户管理"""
    __tablename__ = 'accounts'
    
    id = db.Column(db.Integer, primary_key=True)
    platform = db.Column(db.String(50), nullable=False)  # facebook, 4px, fangguo
    account_name = db.Column(db.String(100), nullable=False)  # 账户名称
    account_id = db.Column(db.String(100))  # 平台账户ID
    description = db.Column(db.Text)  # 账户描述
    balance = db.Column(db.Numeric(10, 2), default=0.00)  # 当前余额
    currency = db.Column(db.String(10), default='CNY')  # 货币类型
    status = db.Column(db.String(20), default='active')  # active, inactive, suspended
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # 关联关系
    recharges = db.relationship('Recharge', backref='account', lazy='dynamic', cascade='all, delete-orphan')
    consumptions = db.relationship('Consumption', backref='account', lazy='dynamic', cascade='all, delete-orphan')
    
    def __repr__(self):
        return f'<Account {self.platform}:{self.account_name}>'
    
    def to_dict(self):
        """转换为字典格式"""
        return {
            'id': self.id,
            'platform': self.platform,
            'account_name': self.account_name,
            'account_id': self.account_id,
            'description': self.description,
            'balance': float(self.balance) if self.balance else 0.00,
            'currency': self.currency,
            'status': self.status,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }
    
    def update_balance(self, amount, operation='add'):
        """更新账户余额"""
        from decimal import Decimal
        # 确保 amount 是 Decimal 类型
        if not isinstance(amount, Decimal):
            amount = Decimal(str(amount))
        
        if operation == 'add':
            self.balance = (self.balance or Decimal('0')) + amount
        elif operation == 'subtract':
            self.balance = (self.balance or Decimal('0')) - amount
        self.updated_at = datetime.utcnow()
        db.session.commit()
    
    def get_total_recharge(self):
        """获取总充值金额"""
        result = db.session.query(func.sum(Recharge.amount)).filter(
            Recharge.account_id == self.id,
            Recharge.status == 'completed'
        ).scalar()
        return float(result) if result else 0.00
    
    def get_total_consumption(self):
        """获取总消耗金额"""
        result = db.session.query(func.sum(Consumption.amount)).filter(
            Consumption.account_id == self.id
        ).scalar()
        return float(result) if result else 0.00
    
    @staticmethod
    def get_platforms():
        """获取支持的平台列表"""
        return {
            'facebook': 'Facebook广告',
            '4px': '4PX物流',
            'fangguo': '方果系统',
            'other': '其他平台'
        }
    
    @staticmethod
    def get_by_platform(platform):
        """根据平台获取账户列表"""
        return Account.query.filter_by(platform=platform, status='active').all()


class Recharge(db.Model):
    """预充值记录表"""
    __tablename__ = 'recharges'
    
    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(db.Integer, db.ForeignKey('accounts.id'), nullable=False)
    amount = db.Column(db.Numeric(10, 2), nullable=False)  # 充值金额
    currency = db.Column(db.String(10), default='CNY')  # 货币类型
    recharge_method = db.Column(db.String(50))  # 充值方式：bank_transfer, alipay, wechat, etc.
    transaction_id = db.Column(db.String(100))  # 交易ID
    description = db.Column(db.Text)  # 充值说明
    status = db.Column(db.String(20), default='completed')  # pending, completed, failed, cancelled
    recharge_date = db.Column(db.DateTime, default=datetime.utcnow)  # 充值日期
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f'<Recharge {self.account.account_name}: {self.amount}>'
    
    def to_dict(self):
        """转换为字典格式"""
        return {
            'id': self.id,
            'account_id': self.account_id,
            'account_name': self.account.account_name if self.account else None,
            'platform': self.account.platform if self.account else None,
            'amount': float(self.amount) if self.amount else 0.00,
            'currency': self.currency,
            'recharge_method': self.recharge_method,
            'transaction_id': self.transaction_id,
            'description': self.description,
            'status': self.status,
            'recharge_date': self.recharge_date.isoformat() if self.recharge_date else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }
    
    def confirm_recharge(self):
        """确认充值成功"""
        if self.status == 'pending':
            self.status = 'completed'
            self.account.update_balance(float(self.amount), 'add')
            self.updated_at = datetime.utcnow()
            db.session.commit()
    
    @staticmethod
    def get_recharge_methods():
        """获取充值方式列表"""
        return {
            'bank_transfer': '银行转账',
            'alipay': '支付宝',
            'wechat': '微信支付',
            'cash': '现金',
            'other': '其他方式'
        }


class Consumption(db.Model):
    """消耗记录表"""
    __tablename__ = 'consumptions'
    
    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(db.Integer, db.ForeignKey('accounts.id'), nullable=False)
    amount = db.Column(db.Numeric(10, 2), nullable=False)  # 消耗金额
    currency = db.Column(db.String(10), default='CNY')  # 货币类型
    consumption_type = db.Column(db.String(50))  # 消耗类型：ads, shipping, order_fee, etc.
    description = db.Column(db.Text)  # 消耗说明
    reference_id = db.Column(db.String(100))  # 关联ID（如广告ID、订单ID等）
    consumption_date = db.Column(db.Date, nullable=False)  # 消耗日期（前一天）
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f'<Consumption {self.account.account_name}: {self.amount} on {self.consumption_date}>'
    
    def to_dict(self):
        """转换为字典格式"""
        return {
            'id': self.id,
            'account_id': self.account_id,
            'account_name': self.account.account_name if self.account else None,
            'platform': self.account.platform if self.account else None,
            'amount': float(self.amount) if self.amount else 0.00,
            'currency': self.currency,
            'consumption_type': self.consumption_type,
            'description': self.description,
            'reference_id': self.reference_id,
            'consumption_date': self.consumption_date.isoformat() if self.consumption_date else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }
    
    def process_consumption(self):
        """处理消耗，从账户余额中扣除"""
        if self.account.balance >= self.amount:
            self.account.update_balance(float(self.amount), 'subtract')
            return True
        return False
    
    @staticmethod
    def get_consumption_types():
        """获取消耗类型列表"""
        return {
            'ads': '广告费用',
            'shipping': '物流费用',
            'order_fee': '下单费用',
            'service_fee': '服务费用',
            'other': '其他费用'
        }