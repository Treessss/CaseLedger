from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_cors import CORS
from dotenv import load_dotenv
from config import Config

# 加载环境变量
load_dotenv()

db = SQLAlchemy()
migrate = Migrate()
cors = CORS()


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)
    
    # 初始化扩展
    db.init_app(app)
    migrate.init_app(app, db)
    cors.init_app(app, resources={
        r"/api/*": {
            "origins": ["http://localhost:8002", "http://127.0.0.1:8002", "http://192.168.1.11:8002"],
            "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
            "allow_headers": ["Content-Type", "Authorization"]
        }
    })
    
    # 添加JWT token验证中间件
    @app.before_request
    def verify_token():
        from flask import request, jsonify
        import jwt
        
        # 跳过登录相关的接口
        skip_paths = ['/api/login/account', '/api/login/outLogin', '/api/login/captcha']
        if request.path in skip_paths:
            return
        
        # 只对API路由进行token验证
        if request.path.startswith('/api/'):
            # 从请求头或查询参数获取token
            token = request.headers.get('Authorization')
            if token and token.startswith('Bearer '):
                token = token[7:]  # 移除 'Bearer ' 前缀
            else:
                token = request.args.get('token')
            
            # 兼容旧的简单token验证
            if token == '123':
                return
            
            # JWT token验证
            if token:
                try:
                    payload = jwt.decode(token, 'caseledger-secret-key', algorithms=['HS256'])
                    # token有效，继续处理请求
                    return
                except jwt.ExpiredSignatureError:
                    return jsonify({'error': 'Token已过期', 'code': 'TOKEN_EXPIRED'}), 401
                except jwt.InvalidTokenError:
                    return jsonify({'error': 'Invalid token', 'code': 'INVALID_TOKEN'}), 401
            
            return jsonify({'error': 'Missing or invalid token', 'code': 'MISSING_TOKEN'}), 401
    
    # 初始化服务
    from app.services.shopify_service import shopify_service
    shopify_service.init_app(app)
    
    # 注册蓝图
    from app.main import bp as main_bp
    app.register_blueprint(main_bp)
    
    from app.api import bp as api_bp
    app.register_blueprint(api_bp, url_prefix='/api')
    
    from app.webhooks.shopify_webhooks import webhook_bp
    app.register_blueprint(webhook_bp, url_prefix='/webhooks')
    
    from app.api.exchange_rate import exchange_rate_bp
    app.register_blueprint(exchange_rate_bp)
    
    return app


# 导入模型以确保它们被注册
from app import models