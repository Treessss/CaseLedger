from app import db
from datetime import datetime
from sqlalchemy import func, DECIMAL
from decimal import Decimal

class Order(db.Model):
    """订单模型"""
    __tablename__ = 'orders'
    
    id = db.Column(db.Integer, primary_key=True)
    shopify_order_id = db.Column(db.String(50), unique=True, nullable=False, index=True)
    order_number = db.Column(db.String(50), nullable=False)
    
    # 订单基本信息
    customer_email = db.Column(db.String(255))
    customer_name = db.Column(db.String(255))
    
    # 金额信息
    total_price = db.Column(DECIMAL(10, 2), nullable=False)  # 订单总金额
    subtotal_price = db.Column(DECIMAL(10, 2), nullable=False)  # 商品小计
    total_tax = db.Column(DECIMAL(10, 2), default=0)  # 税费
    shipping_price = db.Column(DECIMAL(10, 2), default=0)  # 运费
    currency = db.Column(db.String(3), default='USD')  # 订单货币
    
    # 实际收款信息
    actual_received = db.Column(DECIMAL(10, 2))  # 实际到账金额
    payment_method = db.Column(db.String(50))  # 支付方式：paypal, stripe
    payment_fee = db.Column(DECIMAL(10, 2), default=0)  # 支付手续费
    
    # 成本信息
    product_cost = db.Column(DECIMAL(10, 2), default=0)  # 商品成本
    shipping_cost = db.Column(DECIMAL(10, 2), default=0)  # 物流成本
    
    # 利润信息
    gross_profit = db.Column(DECIMAL(10, 2))  # 毛利润
    profit_margin = db.Column(DECIMAL(5, 2))  # 利润率（百分比）
    
    # 订单状态
    financial_status = db.Column(db.String(50))  # 支付状态
    fulfillment_status = db.Column(db.String(50))  # 发货状态
    
    # 时间信息
    order_date = db.Column(db.DateTime, nullable=False)
    processed_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # 关联关系
    payments = db.relationship('Payment', backref='order', lazy='dynamic')
    
    @property
    def status(self):
        """根据financial_status返回订单状态"""
        if self.financial_status == 'paid':
            return 'paid'
        elif self.financial_status == 'pending':
            return 'pending'
        elif self.financial_status in ['cancelled', 'refunded', 'voided']:
            return 'cancelled'
        else:
            return 'pending'  # 默认状态
    
    @property
    def order_id(self):
        """返回订单号，兼容模板中的order_id字段"""
        return self.order_number
    
    @property
    def net_amount(self):
        """返回实际到账金额，兼容模板中的net_amount字段"""
        return self.actual_received
    
    @property
    def platform_fee(self):
        """返回平台费用，这里返回0或者可以根据需要计算"""
        return 0

    def __repr__(self):
        return f'<Order {self.order_number}>'
    
    def calculate_profit(self):
        """计算利润"""
        if self.actual_received and self.product_cost is not None and self.shipping_cost is not None:
            # 确保类型一致性，将所有值转换为Decimal
            actual_received = Decimal(str(self.actual_received))
            product_cost = Decimal(str(self.product_cost))
            shipping_cost = Decimal(str(self.shipping_cost))
            
            self.gross_profit = actual_received - product_cost - shipping_cost
            if actual_received > 0:
                self.profit_margin = (self.gross_profit / actual_received) * 100
            else:
                self.profit_margin = 0
        
    def calculate_actual_received(self):
        """计算实际到账金额"""
        if self.total_price:
            # 确保类型一致性，将payment_fee转换为Decimal
            payment_fee = Decimal(str(self.payment_fee)) if self.payment_fee else Decimal('0')
            actual_received = Decimal(str(self.total_price)) - payment_fee
            # 四舍五入保留两位小数
            self.actual_received = actual_received.quantize(Decimal('0.01'))
    
    def get_total_shipping_cost_cny(self):
        """获取总物流费用（人民币）"""
        from app.services.exchange_rate_service import exchange_rate_service
        
        total_cost = Decimal('0')
        if self.order_costs:
            for cost in self.order_costs:
                if cost.shipping_cost:
                    total_cost += Decimal(str(cost.shipping_cost))
        
        # 如果没有设置费用，返回None表示未设置
        if total_cost == 0:
            return None
        
        return total_cost  # 物流费用已经是人民币
    
    def get_total_fangguo_cost_cny(self):
        """获取总方果费用（人民币）"""
        total_cost = Decimal('0')
        if self.order_costs:
            for cost in self.order_costs:
                if cost.fangguo_cost:
                    total_cost += Decimal(str(cost.fangguo_cost))
        
        # 如果没有设置费用，返回None表示未设置
        if total_cost == 0:
            return None
        
        return total_cost  # 方果费用已经是人民币
    
    def calculate_gross_profit_cny(self):
        """动态计算毛利润（人民币）"""
        from app.services.exchange_rate_service import exchange_rate_service
        
        # 将订单收入转换为人民币
        if self.actual_received and self.currency:
            if self.currency == 'CNY':
                actual_received_cny = Decimal(str(self.actual_received))
            else:
                actual_received_cny = exchange_rate_service.convert_to_cny(
                    float(self.actual_received), self.currency
                )
        else:
            actual_received_cny = Decimal('0')
        
        # 获取人民币成本
        shipping_cost_cny = self.get_total_shipping_cost_cny() or Decimal('0')
        fangguo_cost_cny = self.get_total_fangguo_cost_cny() or Decimal('0')
        
        # 将商品成本转换为人民币（假设商品成本与订单货币一致）
        if self.product_cost and self.currency:
            if self.currency == 'CNY':
                product_cost_cny = Decimal(str(self.product_cost))
            else:
                product_cost_cny = exchange_rate_service.convert_to_cny(
                    float(self.product_cost), self.currency
                )
        else:
            product_cost_cny = Decimal('0')
        
        # 计算毛利润 = 实际到账(CNY) - 商品成本(CNY) - 物流费用(CNY) - 方果费用(CNY)
        # 统一使用Decimal类型进行计算
        gross_profit_cny = actual_received_cny - product_cost_cny - shipping_cost_cny - fangguo_cost_cny
        
        return float(gross_profit_cny)
    
    def get_allocated_expenses(self):
        """获取分摊到该订单的费用"""
        from app.models.expense import Expense
        from decimal import Decimal
        
        allocated_product_cost = Decimal('0')
        allocated_shipping_cost = Decimal('0')
        
        # 获取所有关联的费用（通过多对多关系）
        for expense in self.expenses:
            if expense.category in ['product_cost', 'shipping_cost']:
                # 计算该费用关联的订单数量
                order_count = len(expense.orders)
                if order_count > 0:
                    # 平均分摊费用
                    allocated_amount = Decimal(str(expense.amount)) / order_count
                    
                    if expense.category == 'product_cost':
                        allocated_product_cost += allocated_amount
                    elif expense.category == 'shipping_cost':
                        allocated_shipping_cost += allocated_amount
        
        return {
            'product_cost': float(allocated_product_cost),
            'shipping_cost': float(allocated_shipping_cost)
        }
    
    def to_dict(self):
        """转换为字典"""
        from app.services.exchange_rate_service import exchange_rate_service
        
        # 获取RMB费用
        shipping_cost_cny = self.get_total_shipping_cost_cny()
        fangguo_cost_cny = self.get_total_fangguo_cost_cny()
        gross_profit_cny = self.calculate_gross_profit_cny()
        
        # 获取分摊的费用
        allocated_expenses = self.get_allocated_expenses()
        
        # 将订单总价转换为人民币
        if self.total_price and self.currency:
            if self.currency == 'CNY':
                total_price_cny = float(self.total_price)
            else:
                total_price_cny = float(exchange_rate_service.convert_to_cny(
                    float(self.total_price), self.currency
                ))
        else:
            total_price_cny = 0
        
        return {
            'id': self.id,
            'shopify_order_id': self.shopify_order_id,
            'order_number': self.order_number,
            'customer_email': self.customer_email,
            'customer_name': self.customer_name,
            'total_price': float(self.total_price) if self.total_price else 0,
            'total_price_cny': total_price_cny,  # 新增：订单总价（人民币）
            'currency': self.currency,
            'actual_received': float(self.actual_received) if self.actual_received else 0,
            'payment_method': self.payment_method,
            'payment_fee': float(self.payment_fee) if self.payment_fee else 0,
            'product_cost': float(self.product_cost) if self.product_cost else 0,
            'shipping_cost': float(self.shipping_cost) if self.shipping_cost else 0,
            'gross_profit': float(self.gross_profit) if self.gross_profit else 0,
            'profit_margin': float(self.profit_margin) if self.profit_margin else 0,
            'financial_status': self.financial_status,
            'fulfillment_status': self.fulfillment_status,
            'order_date': self.order_date.isoformat() if self.order_date else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            # 新增RMB字段
            'shipping_cost_cny': float(shipping_cost_cny) if shipping_cost_cny is not None else None,
            'fangguo_cost_cny': float(fangguo_cost_cny) if fangguo_cost_cny is not None else None,
            'gross_profit_cny': float(gross_profit_cny) if gross_profit_cny else 0,
            # 新增分摊费用字段
            'allocated_product_cost': allocated_expenses['product_cost'],
            'allocated_shipping_cost': allocated_expenses['shipping_cost']
        }