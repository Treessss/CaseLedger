from app import db
from datetime import datetime
from sqlalchemy import DECIMAL, UniqueConstraint

class Product(db.Model):
    """商品模型"""
    __tablename__ = 'products'
    
    id = db.Column(db.Integer, primary_key=True)
    shopify_product_id = db.Column(db.String(50), nullable=False)
    shopify_variant_id = db.Column(db.String(50), nullable=False)
    
    # 添加复合唯一约束
    __table_args__ = (UniqueConstraint('shopify_product_id', 'shopify_variant_id', name='uq_product_variant'),)
    
    # 商品基本信息
    title = db.Column(db.String(255), nullable=False)
    sku = db.Column(db.String(100))
    variant_title = db.Column(db.String(255))
    
    # 价格信息
    price = db.Column(DECIMAL(10, 2), nullable=False)  # 销售价格
    cost = db.Column(DECIMAL(10, 2), default=0)  # 成本价格
    
    # 库存信息
    inventory_quantity = db.Column(db.Integer, default=0)
    
    # 商品分类
    product_type = db.Column(db.String(100))  # 商品类型
    vendor = db.Column(db.String(100))  # 供应商
    
    # 状态
    status = db.Column(db.String(50), default='active')  # active, archived
    
    # 时间信息
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f'<Product {self.title}>'
    
    def calculate_profit_margin(self):
        """计算利润率"""
        if self.price and self.cost and self.price > 0:
            profit = self.price - self.cost
            return (profit / self.price) * 100
        return 0
    
    def to_dict(self):
        """转换为字典"""
        return {
            'id': self.id,
            'shopify_product_id': self.shopify_product_id,
            'shopify_variant_id': self.shopify_variant_id,
            'title': self.title,
            'sku': self.sku,
            'variant_title': self.variant_title,
            'price': float(self.price) if self.price else 0,
            'cost': float(self.cost) if self.cost else 0,
            'inventory_quantity': self.inventory_quantity,
            'product_type': self.product_type,
            'vendor': self.vendor,
            'status': self.status,
            'profit_margin': self.calculate_profit_margin(),
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }