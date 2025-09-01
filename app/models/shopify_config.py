from app import db
from datetime import datetime

class ShopifyConfig(db.Model):
    """Shopify配置模型"""
    __tablename__ = 'shopify_configs'
    
    id = db.Column(db.Integer, primary_key=True)
    
    # Shopify配置信息
    shop_url = db.Column(db.String(255), nullable=False)  # 店铺URL
    api_key = db.Column(db.String(255), nullable=False)  # API Key
    api_secret = db.Column(db.String(255), nullable=False)  # API Secret
    access_token = db.Column(db.String(255), nullable=False)  # Access Token
    
    # 配置状态
    is_active = db.Column(db.Boolean, default=True)  # 是否启用
    last_sync = db.Column(db.DateTime)  # 最后同步时间
    
    # 时间信息
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f'<ShopifyConfig {self.shop_url}>'
    
    def to_dict(self):
        return {
            'id': self.id,
            'shop_url': self.shop_url,
            'api_key': self.api_key,
            'api_secret': self.api_secret,
            'access_token': self.access_token,
            'is_active': self.is_active,
            'last_sync': self.last_sync.isoformat() if self.last_sync else None,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat()
        }