from flask import jsonify, request, current_app
from app.api import bp
from app.services.shopify_service import shopify_service
from app.models.order import Order
from app.models.product import Product
from app import db
from datetime import datetime, timedelta


@bp.route('/sync/test', methods=['GET'])
def test_sync_connection():
    """测试Shopify API连接"""
    try:
        is_connected = shopify_service.test_connection()
        if is_connected:
            shop_info = shopify_service.get_shop_info()
            return jsonify({
                'success': True,
                'message': 'Shopify连接成功',
                'shop_info': shop_info
            })
        else:
            return jsonify({
                'success': False,
                'message': 'Shopify连接失败'
            }), 400
    except Exception as e:
        current_app.logger.error(f"Shopify connection test error: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'连接测试失败: {str(e)}'
        }), 500


@bp.route('/sync/orders', methods=['POST'])
def sync_orders():
    """同步Shopify订单"""
    try:
        data = request.get_json() or {}
        days_back = data.get('days_back', 30)
        limit = data.get('limit', 250)
        
        # 验证参数
        if days_back < 1 or days_back > 365:
            return jsonify({
                'success': False,
                'message': '同步天数必须在1-365之间'
            }), 400
        
        if limit < 1 or limit > 250:
            return jsonify({
                'success': False,
                'message': '每次同步数量必须在1-250之间'
            }), 400
        
        # 执行同步
        stats = shopify_service.sync_orders(days_back=days_back, limit=limit)
        
        return jsonify({
            'success': True,
            'message': '订单同步完成',
            'stats': stats
        })
        
    except Exception as e:
        current_app.logger.error(f"Order sync error: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'订单同步失败: {str(e)}'
        }), 500


@bp.route('/sync/products', methods=['POST'])
def sync_products():
    """同步Shopify商品"""
    try:
        data = request.get_json() or {}
        limit = data.get('limit', 250)
        
        # 验证参数
        if limit < 1 or limit > 250:
            return jsonify({
                'success': False,
                'message': '每次同步数量必须在1-250之间'
            }), 400
        
        # 执行同步
        stats = shopify_service.sync_products(limit=limit)
        
        return jsonify({
            'success': True,
            'message': '商品同步完成',
            'stats': stats
        })
        
    except Exception as e:
        current_app.logger.error(f"Product sync error: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'商品同步失败: {str(e)}'
        }), 500


@bp.route('/sync/recent', methods=['POST'])
def sync_recent_orders():
    """同步最近订单（用于定时任务）"""
    try:
        data = request.get_json() or {}
        hours = data.get('hours', 24)
        
        # 验证参数
        if hours < 1 or hours > 168:  # 最多一周
            return jsonify({
                'success': False,
                'message': '同步小时数必须在1-168之间'
            }), 400
        
        # 执行同步
        stats = shopify_service.sync_recent_orders(hours=hours)
        
        return jsonify({
            'success': True,
            'message': '最近订单同步完成',
            'stats': stats
        })
        
    except Exception as e:
        current_app.logger.error(f"Recent orders sync error: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'最近订单同步失败: {str(e)}'
        }), 500


@bp.route('/sync/status', methods=['GET'])
def get_sync_status():
    """获取同步状态统计"""
    try:
        # 获取订单统计
        total_orders = Order.query.count()
        recent_orders = Order.query.filter(
            Order.created_at >= datetime.now() - timedelta(days=7)
        ).count()
        
        # 获取商品统计
        total_products = Product.query.count()
        active_products = Product.query.filter_by(status='active').count()
        
        # 获取最后同步时间
        last_order = Order.query.order_by(Order.updated_at.desc()).first()
        last_sync_time = last_order.updated_at.isoformat() if last_order else None
        
        return jsonify({
            'success': True,
            'data': {
                'orders': {
                    'total': total_orders,
                    'recent_7_days': recent_orders
                },
                'products': {
                    'total': total_products,
                    'active': active_products
                },
                'last_sync_time': last_sync_time
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"Get sync status error: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取同步状态失败: {str(e)}'
        }), 500


@bp.route('/sync/shop-info', methods=['GET'])
def get_shop_info():
    """获取店铺信息"""
    try:
        shop_info = shopify_service.get_shop_info()
        
        if shop_info:
            return jsonify({
                'success': True,
                'data': shop_info
            })
        else:
            return jsonify({
                'success': False,
                'message': '无法获取店铺信息'
            }), 400
            
    except Exception as e:
        current_app.logger.error(f"Get shop info error: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取店铺信息失败: {str(e)}'
        }), 500