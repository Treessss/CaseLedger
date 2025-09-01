from app import db
from datetime import datetime
from sqlalchemy import func


class OrderCost(db.Model):
    """订单费用表 - 记录每个订单的物流费用和方果下单费用"""
    __tablename__ = 'order_costs'
    
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=False)  # 关联订单
    order_number = db.Column(db.String(100), nullable=False)  # 订单号（用于用户识别）
    
    # 物流费用相关
    shipping_cost = db.Column(db.Numeric(10, 2), default=0.00)  # 物流费用
    shipping_account_id = db.Column(db.Integer, db.ForeignKey('accounts.id'))  # 关联4PX账户
    shipping_reference = db.Column(db.String(100))  # 物流单号或参考号
    shipping_notes = db.Column(db.Text)  # 物流费用备注
    
    # 方果下单费用相关
    fangguo_cost = db.Column(db.Numeric(10, 2), default=0.00)  # 方果下单费用
    fangguo_account_id = db.Column(db.Integer, db.ForeignKey('accounts.id'))  # 关联方果账户
    fangguo_reference = db.Column(db.String(100))  # 方果订单号或参考号
    fangguo_notes = db.Column(db.Text)  # 方果费用备注
    
    # 其他费用
    other_cost = db.Column(db.Numeric(10, 2), default=0.00)  # 其他费用
    other_description = db.Column(db.Text)  # 其他费用说明
    
    # 费用录入信息
    cost_date = db.Column(db.Date, nullable=False)  # 费用发生日期
    entry_date = db.Column(db.Date, default=datetime.utcnow().date)  # 费用录入日期
    entry_user = db.Column(db.String(100))  # 录入人员
    status = db.Column(db.String(20), default='pending')  # pending, confirmed, cancelled
    
    # 批量操作相关
    batch_id = db.Column(db.String(50))  # 批量操作ID（用于标识同一批录入的费用）
    batch_notes = db.Column(db.Text)  # 批量操作备注
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # 关联关系
    order = db.relationship('Order', backref='order_costs')
    shipping_account = db.relationship('Account', foreign_keys=[shipping_account_id], backref='shipping_costs')
    fangguo_account = db.relationship('Account', foreign_keys=[fangguo_account_id], backref='fangguo_costs')
    
    def __repr__(self):
        return f'<OrderCost {self.order_number}: shipping={self.shipping_cost}, fangguo={self.fangguo_cost}>'
    
    def to_dict(self):
        """转换为字典格式"""
        return {
            'id': self.id,
            'order_id': self.order_id,
            'order_number': self.order_number,
            'shipping_cost': float(self.shipping_cost) if self.shipping_cost else 0.00,
            'shipping_account_id': self.shipping_account_id,
            'shipping_account_name': self.shipping_account.account_name if self.shipping_account else None,
            'shipping_reference': self.shipping_reference,
            'shipping_notes': self.shipping_notes,
            'fangguo_cost': float(self.fangguo_cost) if self.fangguo_cost else 0.00,
            'fangguo_account_id': self.fangguo_account_id,
            'fangguo_account_name': self.fangguo_account.account_name if self.fangguo_account else None,
            'fangguo_reference': self.fangguo_reference,
            'fangguo_notes': self.fangguo_notes,
            'other_cost': float(self.other_cost) if self.other_cost else 0.00,
            'other_description': self.other_description,
            'total_cost': self.get_total_cost(),
            'cost_date': self.cost_date.isoformat() if self.cost_date else None,
            'entry_date': self.entry_date.isoformat() if self.entry_date else None,
            'entry_user': self.entry_user,
            'status': self.status,
            'batch_id': self.batch_id,
            'batch_notes': self.batch_notes,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }
    
    def get_total_cost(self):
        """计算总费用"""
        shipping = float(self.shipping_cost) if self.shipping_cost else 0.00
        fangguo = float(self.fangguo_cost) if self.fangguo_cost else 0.00
        other = float(self.other_cost) if self.other_cost else 0.00
        return shipping + fangguo + other
    
    def confirm_costs(self):
        """确认费用并从相关账户扣除余额"""
        if self.status != 'pending':
            return False
        
        try:
            # 从物流账户扣除费用
            if self.shipping_cost and self.shipping_cost > 0 and self.shipping_account:
                if self.shipping_account.balance < self.shipping_cost:
                    return False, f"物流账户余额不足：需要{self.shipping_cost}，余额{self.shipping_account.balance}"
                self.shipping_account.update_balance(float(self.shipping_cost), 'subtract')
                
                # 创建消耗记录
                from .account import Consumption
                consumption = Consumption(
                    account_id=self.shipping_account_id,
                    amount=self.shipping_cost,
                    consumption_type='shipping',
                    description=f"订单{self.order_number}物流费用",
                    reference_id=self.order_number,
                    consumption_date=self.cost_date
                )
                db.session.add(consumption)
            
            # 从方果账户扣除费用
            if self.fangguo_cost and self.fangguo_cost > 0 and self.fangguo_account:
                if self.fangguo_account.balance < self.fangguo_cost:
                    return False, f"方果账户余额不足：需要{self.fangguo_cost}，余额{self.fangguo_account.balance}"
                self.fangguo_account.update_balance(float(self.fangguo_cost), 'subtract')
                
                # 创建消耗记录
                from .account import Consumption
                consumption = Consumption(
                    account_id=self.fangguo_account_id,
                    amount=self.fangguo_cost,
                    consumption_type='order_fee',
                    description=f"订单{self.order_number}方果下单费用",
                    reference_id=self.order_number,
                    consumption_date=self.cost_date
                )
                db.session.add(consumption)
            
            self.status = 'confirmed'
            self.updated_at = datetime.utcnow()
            db.session.commit()
            return True, "费用确认成功"
            
        except Exception as e:
            db.session.rollback()
            return False, f"费用确认失败：{str(e)}"
    
    @staticmethod
    def create_batch_costs(order_data_list, batch_notes=None):
        """批量创建订单费用记录"""
        import uuid
        batch_id = str(uuid.uuid4())[:8]  # 生成8位批次ID
        
        created_costs = []
        try:
            for order_data in order_data_list:
                order_cost = OrderCost(
                    order_id=order_data.get('order_id'),
                    order_number=order_data.get('order_number'),
                    shipping_cost=order_data.get('shipping_cost', 0),
                    shipping_account_id=order_data.get('shipping_account_id'),
                    shipping_reference=order_data.get('shipping_reference'),
                    shipping_notes=order_data.get('shipping_notes'),
                    fangguo_cost=order_data.get('fangguo_cost', 0),
                    fangguo_account_id=order_data.get('fangguo_account_id'),
                    fangguo_reference=order_data.get('fangguo_reference'),
                    fangguo_notes=order_data.get('fangguo_notes'),
                    other_cost=order_data.get('other_cost', 0),
                    other_description=order_data.get('other_description'),
                    cost_date=order_data.get('cost_date'),
                    entry_user=order_data.get('entry_user'),
                    batch_id=batch_id,
                    batch_notes=batch_notes
                )
                db.session.add(order_cost)
                created_costs.append(order_cost)
            
            db.session.commit()
            return True, created_costs, batch_id
            
        except Exception as e:
            db.session.rollback()
            return False, str(e), None
    
    @staticmethod
    def get_by_batch(batch_id):
        """根据批次ID获取费用记录"""
        return OrderCost.query.filter_by(batch_id=batch_id).all()
    
    @staticmethod
    def get_by_order_numbers(order_numbers):
        """根据订单号列表获取费用记录"""
        return OrderCost.query.filter(OrderCost.order_number.in_(order_numbers)).all()
    
    @staticmethod
    def get_cost_summary_by_date_range(start_date, end_date):
        """获取日期范围内的费用汇总"""
        result = db.session.query(
            func.sum(OrderCost.shipping_cost).label('total_shipping'),
            func.sum(OrderCost.fangguo_cost).label('total_fangguo'),
            func.sum(OrderCost.other_cost).label('total_other'),
            func.count(OrderCost.id).label('order_count')
        ).filter(
            OrderCost.cost_date >= start_date,
            OrderCost.cost_date <= end_date,
            OrderCost.status == 'confirmed'
        ).first()
        
        return {
            'total_shipping': float(result.total_shipping) if result.total_shipping else 0.00,
            'total_fangguo': float(result.total_fangguo) if result.total_fangguo else 0.00,
            'total_other': float(result.total_other) if result.total_other else 0.00,
            'total_cost': float((result.total_shipping or 0) + (result.total_fangguo or 0) + (result.total_other or 0)),
            'order_count': result.order_count or 0
        }
    
    @staticmethod
    def get_pending_costs():
        """获取待确认的费用记录"""
        return OrderCost.query.filter_by(status='pending').order_by(OrderCost.created_at.desc()).all()


class OrderCostBatch(db.Model):
    """订单费用批次表 - 记录批量操作的元信息"""
    __tablename__ = 'order_cost_batches'
    
    id = db.Column(db.Integer, primary_key=True)
    batch_id = db.Column(db.String(50), unique=True, nullable=False)  # 批次ID
    batch_name = db.Column(db.String(200))  # 批次名称
    description = db.Column(db.Text)  # 批次描述
    order_count = db.Column(db.Integer, default=0)  # 订单数量
    total_shipping_cost = db.Column(db.Numeric(10, 2), default=0.00)  # 总物流费用
    total_fangguo_cost = db.Column(db.Numeric(10, 2), default=0.00)  # 总方果费用
    total_other_cost = db.Column(db.Numeric(10, 2), default=0.00)  # 总其他费用
    status = db.Column(db.String(20), default='pending')  # pending, confirmed, cancelled
    created_by = db.Column(db.String(100))  # 创建人
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f'<OrderCostBatch {self.batch_id}: {self.order_count} orders>'
    
    def to_dict(self):
        """转换为字典格式"""
        return {
            'id': self.id,
            'batch_id': self.batch_id,
            'batch_name': self.batch_name,
            'description': self.description,
            'order_count': self.order_count,
            'total_shipping_cost': float(self.total_shipping_cost) if self.total_shipping_cost else 0.00,
            'total_fangguo_cost': float(self.total_fangguo_cost) if self.total_fangguo_cost else 0.00,
            'total_other_cost': float(self.total_other_cost) if self.total_other_cost else 0.00,
            'total_cost': self.get_total_cost(),
            'status': self.status,
            'created_by': self.created_by,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }
    
    def get_total_cost(self):
        """计算总费用"""
        shipping = float(self.total_shipping_cost) if self.total_shipping_cost else 0.00
        fangguo = float(self.total_fangguo_cost) if self.total_fangguo_cost else 0.00
        other = float(self.total_other_cost) if self.total_other_cost else 0.00
        return shipping + fangguo + other
    
    def update_totals(self):
        """更新批次汇总数据"""
        costs = OrderCost.get_by_batch(self.batch_id)
        self.order_count = len(costs)
        self.total_shipping_cost = sum(float(cost.shipping_cost or 0) for cost in costs)
        self.total_fangguo_cost = sum(float(cost.fangguo_cost or 0) for cost in costs)
        self.total_other_cost = sum(float(cost.other_cost or 0) for cost in costs)
        self.updated_at = datetime.utcnow()
        db.session.commit()