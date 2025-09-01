from app import db
from datetime import datetime

class ExpenseOrder(db.Model):
    """费用-订单关联表"""
    __tablename__ = 'expense_orders'
    
    id = db.Column(db.Integer, primary_key=True)
    expense_id = db.Column(db.Integer, db.ForeignKey('expenses.id'), nullable=False)
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # 添加唯一约束，防止重复关联
    __table_args__ = (db.UniqueConstraint('expense_id', 'order_id', name='unique_expense_order'),)
    
    def __repr__(self):
        return f'<ExpenseOrder expense_id={self.expense_id}, order_id={self.order_id}>'
    
    def to_dict(self):
        return {
            'id': self.id,
            'expense_id': self.expense_id,
            'order_id': self.order_id,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }