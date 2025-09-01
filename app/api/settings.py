from flask import request, jsonify
from app.api import bp
from app.models import db, ShopifyConfig, FeeConfig
from app.services.shopify_service import ShopifyService
from datetime import datetime

@bp.route('/settings/shopify', methods=['GET'])
def get_shopify_config():
    """获取Shopify配置"""
    try:
        config = ShopifyConfig.query.first()
        if config:
            return jsonify({
                'success': True,
                'data': {
                    'shop_url': config.shop_url,
                    'api_key': config.api_key,
                    'api_secret': config.api_secret[:4] + '*' * (len(config.api_secret) - 4) if config.api_secret else '',
                    'access_token': config.access_token[:4] + '*' * (len(config.access_token) - 4) if config.access_token else '',
                    'is_active': config.is_active,
                    'last_sync': config.last_sync.isoformat() if config.last_sync else None
                }
            })
        else:
            return jsonify({
                'success': True,
                'data': {
                    'shop_url': '',
                    'api_key': '',
                    'api_secret': '',
                    'access_token': '',
                    'is_active': False,
                    'last_sync': None
                }
            })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@bp.route('/settings/shopify/sync', methods=['POST'])
def sync_shopify_orders():
    """同步Shopify订单"""
    try:
        data = request.get_json() or {}
        days_back = data.get('days_back', 30)
        limit = data.get('limit', 250)
        
        # 获取当前保存的Shopify配置
        config = ShopifyConfig.query.first()
        if not config:
            return jsonify({'success': False, 'message': '请先保存Shopify配置'}), 400
        
        # 创建Shopify服务实例
        from flask import current_app
        
        # 临时设置配置
        original_config = {
            'SHOPIFY_API_KEY': current_app.config.get('SHOPIFY_API_KEY'),
            'SHOPIFY_API_SECRET': current_app.config.get('SHOPIFY_API_SECRET'),
            'SHOPIFY_SHOP_URL': current_app.config.get('SHOPIFY_SHOP_URL'),
            'SHOPIFY_ACCESS_TOKEN': current_app.config.get('SHOPIFY_ACCESS_TOKEN')
        }
        
        current_app.config['SHOPIFY_API_KEY'] = config.api_key
        current_app.config['SHOPIFY_API_SECRET'] = config.api_secret
        current_app.config['SHOPIFY_SHOP_URL'] = config.shop_url
        current_app.config['SHOPIFY_ACCESS_TOKEN'] = config.access_token
        
        shopify_service = ShopifyService()
        shopify_service.init_app(current_app)
        
        # 同步订单
        result = shopify_service.sync_orders(days_back=days_back, limit=limit)
        
        # 恢复原始配置
        for key, value in original_config.items():
            if value is not None:
                current_app.config[key] = value
            else:
                current_app.config.pop(key, None)
        
        return jsonify({
            'success': True,
            'message': '订单同步完成',
            'data': result
        })
        
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@bp.route('/settings/shopify', methods=['POST'])
def save_shopify_config():
    """保存Shopify配置"""
    try:
        data = request.get_json()
        
        # 验证必填字段
        required_fields = ['shop_url', 'api_key', 'api_secret', 'access_token']
        for field in required_fields:
            if not data.get(field):
                return jsonify({'success': False, 'message': f'{field} 不能为空'}), 400
        
        # 查找或创建配置
        config = ShopifyConfig.query.first()
        if not config:
            config = ShopifyConfig()
        
        # 更新配置
        config.shop_url = data['shop_url']
        config.api_key = data['api_key']
        config.api_secret = data['api_secret']
        config.access_token = data['access_token']
        config.is_active = True
        config.updated_at = datetime.utcnow()
        
        if not config.id:
            db.session.add(config)
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Shopify配置保存成功'
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500

@bp.route('/settings/shopify/test', methods=['POST'])
def test_shopify_connection():
    """测试Shopify连接"""
    try:
        # 获取当前保存的Shopify配置
        config = ShopifyConfig.query.first()
        if not config:
            return jsonify({'success': False, 'message': '请先保存Shopify配置'}), 400
        
        # 创建临时的Shopify服务实例
        from flask import current_app
        import tempfile
        
        # 临时设置配置
        original_config = {
            'SHOPIFY_API_KEY': current_app.config.get('SHOPIFY_API_KEY'),
            'SHOPIFY_API_SECRET': current_app.config.get('SHOPIFY_API_SECRET'),
            'SHOPIFY_SHOP_URL': current_app.config.get('SHOPIFY_SHOP_URL'),
            'SHOPIFY_ACCESS_TOKEN': current_app.config.get('SHOPIFY_ACCESS_TOKEN')
        }
        
        current_app.config['SHOPIFY_API_KEY'] = config.api_key
        current_app.config['SHOPIFY_API_SECRET'] = config.api_secret
        current_app.config['SHOPIFY_SHOP_URL'] = config.shop_url
        current_app.config['SHOPIFY_ACCESS_TOKEN'] = config.access_token
        
        shopify_service = ShopifyService()
        shopify_service.init_app(current_app)
        
        # 测试连接
        result = shopify_service.test_connection()
        
        # 恢复原始配置
        for key, value in original_config.items():
            if value is not None:
                current_app.config[key] = value
            else:
                current_app.config.pop(key, None)
        
        if result:
            return jsonify({
                'success': True,
                'message': '连接测试成功',
                'data': {'shop_url': config.shop_url}
            })
        else:
            return jsonify({
                'success': False,
                'message': '连接测试失败，请检查配置信息'
            }), 400
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@bp.route('/settings/fees', methods=['GET'])
def get_fee_configs():
    """获取费用配置列表"""
    try:
        configs = FeeConfig.query.filter_by(is_active=True).all()
        
        return jsonify({
            'success': True,
            'data': [{
                'id': config.id,
                'fee_type': config.fee_type,
                'fee_name': config.fee_name,
                'description': config.description,
                'calculation_method': config.calculation_method,
                'percentage_rate': float(config.percentage_rate) if config.percentage_rate else None,
                'fixed_amount': float(config.fixed_amount) if config.fixed_amount else None,
                'currency': config.currency,
                'is_active': config.is_active,
                'created_at': config.created_at.isoformat()
            } for config in configs]
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@bp.route('/settings/fees', methods=['POST'])
def create_fee_config():
    """创建费用配置"""
    try:
        data = request.get_json()
        
        # 验证必填字段
        if not data.get('fee_type'):
            return jsonify({'success': False, 'message': '费用类型不能为空'}), 400
            
        if not data.get('fee_name'):
            return jsonify({'success': False, 'message': '费用名称不能为空'}), 400
        
        if not data.get('calculation_method'):
            return jsonify({'success': False, 'message': '计算方式不能为空'}), 400
        
        # 验证计算方式和对应的值
        if data['calculation_method'] == 'percentage':
            if not data.get('percentage_rate'):
                return jsonify({'success': False, 'message': '百分比费率不能为空'}), 400
        elif data['calculation_method'] == 'fixed':
            if not data.get('fixed_amount'):
                return jsonify({'success': False, 'message': '固定金额不能为空'}), 400
        elif data['calculation_method'] == 'percentage_plus_fixed':
            if not data.get('percentage_rate'):
                return jsonify({'success': False, 'message': '百分比费率不能为空'}), 400
            if not data.get('fixed_amount'):
                return jsonify({'success': False, 'message': '固定金额不能为空'}), 400
        elif data['calculation_method'] == 'percentage_plus_fixed':
            if not data.get('percentage_rate'):
                return jsonify({'success': False, 'message': '百分比费率不能为空'}), 400
            if not data.get('fixed_amount'):
                return jsonify({'success': False, 'message': '固定金额不能为空'}), 400
        elif data['calculation_method'] == 'percentage_plus_fixed':
            if not data.get('percentage_rate'):
                return jsonify({'success': False, 'message': '百分比费率不能为空'}), 400
            if not data.get('fixed_amount'):
                return jsonify({'success': False, 'message': '固定金额不能为空'}), 400
        elif data['calculation_method'] == 'percentage_plus_fixed':
            if not data.get('percentage_rate'):
                return jsonify({'success': False, 'message': '百分比费率不能为空'}), 400
            if not data.get('fixed_amount'):
                return jsonify({'success': False, 'message': '固定金额不能为空'}), 400
        
        # 创建费用配置
        config = FeeConfig(
            fee_type=data['fee_type'],
            fee_name=data['fee_name'],
            description=data.get('description', ''),
            calculation_method=data['calculation_method'],
            percentage_rate=data.get('percentage_rate'),
            fixed_amount=data.get('fixed_amount'),
            currency=data.get('currency', 'USD'),
            is_active=True
        )
        
        db.session.add(config)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': '费用配置创建成功',
            'data': {
                'id': config.id,
                'fee_type': config.fee_type,
                'fee_name': config.fee_name,
                'description': config.description,
                'calculation_method': config.calculation_method,
                'percentage_rate': float(config.percentage_rate) if config.percentage_rate else None,
                'fixed_amount': float(config.fixed_amount) if config.fixed_amount else None,
                'currency': config.currency,
                'is_active': config.is_active,
                'created_at': config.created_at.isoformat()
            }
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500

@bp.route('/settings/fees/<int:fee_id>', methods=['PUT'])
def update_fee_config(fee_id):
    """更新费用配置"""
    try:
        config = FeeConfig.query.get_or_404(fee_id)
        data = request.get_json()
        
        # 验证必填字段
        if not data.get('fee_type'):
            return jsonify({'success': False, 'message': '费用类型不能为空'}), 400
            
        if not data.get('fee_name'):
            return jsonify({'success': False, 'message': '费用名称不能为空'}), 400
        
        if not data.get('calculation_method'):
            return jsonify({'success': False, 'message': '计算方式不能为空'}), 400
        
        # 验证计算方式和对应的值
        if data['calculation_method'] == 'percentage':
            if not data.get('percentage_rate'):
                return jsonify({'success': False, 'message': '百分比费率不能为空'}), 400
        elif data['calculation_method'] == 'fixed':
            if not data.get('fixed_amount'):
                return jsonify({'success': False, 'message': '固定金额不能为空'}), 400
        elif data['calculation_method'] == 'percentage_plus_fixed':
            if not data.get('percentage_rate'):
                return jsonify({'success': False, 'message': '百分比费率不能为空'}), 400
            if not data.get('fixed_amount'):
                return jsonify({'success': False, 'message': '固定金额不能为空'}), 400
        
        # 更新配置
        config.fee_type = data['fee_type']
        config.fee_name = data['fee_name']
        config.description = data.get('description', '')
        config.calculation_method = data['calculation_method']
        config.percentage_rate = data.get('percentage_rate')
        config.fixed_amount = data.get('fixed_amount')
        config.currency = data.get('currency', config.currency)
        config.updated_at = datetime.utcnow()
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': '费用配置更新成功'
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500

@bp.route('/settings/fees/<int:fee_id>', methods=['DELETE'])
def delete_fee_config(fee_id):
    """删除费用配置"""
    try:
        config = FeeConfig.query.get_or_404(fee_id)
        
        # 软删除
        config.is_active = False
        config.updated_at = datetime.utcnow()
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': '费用配置删除成功'
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500