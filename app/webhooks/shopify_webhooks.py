import json
import logging
from flask import Blueprint, request, jsonify, current_app
from app import db
from app.models.order import Order
from app.models.product import Product
from app.models.payment import Payment
from app.services.shopify_service import ShopifyService
from app.utils.helpers import validate_shopify_webhook_signature
from datetime import datetime

# 创建webhook蓝图
webhook_bp = Blueprint('webhooks', __name__)
logger = logging.getLogger(__name__)


@webhook_bp.route('/shopify/orders/create', methods=['POST'])
def handle_order_create():
    """处理Shopify订单创建webhook"""
    try:
        # 验证webhook签名
        if not _verify_webhook_signature():
            return jsonify({'error': 'Invalid signature'}), 401
        
        # 获取订单数据
        order_data = request.get_json()
        
        if not order_data:
            return jsonify({'error': 'No data received'}), 400
        
        # 处理订单
        shopify_service = ShopifyService()
        result = shopify_service._process_order(order_data)
        
        logger.info(f"Order created via webhook: {order_data.get('order_number')}")
        
        return jsonify({
            'status': 'success',
            'message': 'Order processed successfully',
            'order_id': result.get('order_id')
        }), 200
        
    except Exception as e:
        logger.error(f"Error processing order create webhook: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500


@webhook_bp.route('/shopify/orders/update', methods=['POST'])
def handle_order_update():
    """处理Shopify订单更新webhook"""
    try:
        # 验证webhook签名
        if not _verify_webhook_signature():
            return jsonify({'error': 'Invalid signature'}), 401
        
        # 获取订单数据
        order_data = request.get_json()
        
        if not order_data:
            return jsonify({'error': 'No data received'}), 400
        
        # 查找现有订单
        shopify_order_id = str(order_data.get('id'))
        order = Order.query.filter_by(shopify_order_id=shopify_order_id).first()
        
        if order:
            # 更新订单信息
            shopify_service = ShopifyService()
            result = shopify_service._process_order(order_data, update_existing=True)
            
            logger.info(f"Order updated via webhook: {order_data.get('order_number')}")
            
            return jsonify({
                'status': 'success',
                'message': 'Order updated successfully',
                'order_id': result.get('order_id')
            }), 200
        else:
            # 如果订单不存在，创建新订单
            shopify_service = ShopifyService()
            result = shopify_service._process_order(order_data)
            
            logger.info(f"New order created via update webhook: {order_data.get('order_number')}")
            
            return jsonify({
                'status': 'success',
                'message': 'Order created successfully',
                'order_id': result.get('order_id')
            }), 200
        
    except Exception as e:
        logger.error(f"Error processing order update webhook: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500


@webhook_bp.route('/shopify/orders/paid', methods=['POST'])
def handle_order_paid():
    """处理Shopify订单支付webhook"""
    try:
        # 验证webhook签名
        if not _verify_webhook_signature():
            return jsonify({'error': 'Invalid signature'}), 401
        
        # 获取订单数据
        order_data = request.get_json()
        
        if not order_data:
            return jsonify({'error': 'No data received'}), 400
        
        # 查找订单并更新支付状态
        shopify_order_id = str(order_data.get('id'))
        order = Order.query.filter_by(shopify_order_id=shopify_order_id).first()
        
        if order:
            order.financial_status = order_data.get('financial_status', 'paid')
            order.updated_at = datetime.utcnow()
            
            # 处理支付信息
            shopify_service = ShopifyService()
            for transaction in order_data.get('transactions', []):
                if transaction.get('status') == 'success':
                    shopify_service._process_payment(transaction, order.id)
            
            db.session.commit()
            
            logger.info(f"Order payment processed via webhook: {order_data.get('order_number')}")
            
            return jsonify({
                'status': 'success',
                'message': 'Payment processed successfully'
            }), 200
        else:
            logger.warning(f"Order not found for payment webhook: {shopify_order_id}")
            return jsonify({'error': 'Order not found'}), 404
        
    except Exception as e:
        logger.error(f"Error processing order paid webhook: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500


@webhook_bp.route('/shopify/orders/cancelled', methods=['POST'])
def handle_order_cancelled():
    """处理Shopify订单取消webhook"""
    try:
        # 验证webhook签名
        if not _verify_webhook_signature():
            return jsonify({'error': 'Invalid signature'}), 401
        
        # 获取订单数据
        order_data = request.get_json()
        
        if not order_data:
            return jsonify({'error': 'No data received'}), 400
        
        # 查找订单并更新状态
        shopify_order_id = str(order_data.get('id'))
        order = Order.query.filter_by(shopify_order_id=shopify_order_id).first()
        
        if order:
            order.financial_status = 'cancelled'
            order.fulfillment_status = 'cancelled'
            order.cancelled_at = datetime.fromisoformat(
                order_data.get('cancelled_at', datetime.utcnow().isoformat()).replace('Z', '+00:00')
            )
            order.updated_at = datetime.utcnow()
            
            db.session.commit()
            
            logger.info(f"Order cancelled via webhook: {order_data.get('order_number')}")
            
            return jsonify({
                'status': 'success',
                'message': 'Order cancellation processed successfully'
            }), 200
        else:
            logger.warning(f"Order not found for cancellation webhook: {shopify_order_id}")
            return jsonify({'error': 'Order not found'}), 404
        
    except Exception as e:
        logger.error(f"Error processing order cancelled webhook: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500


@webhook_bp.route('/shopify/products/create', methods=['POST'])
def handle_product_create():
    """处理Shopify产品创建webhook"""
    try:
        # 验证webhook签名
        if not _verify_webhook_signature():
            return jsonify({'error': 'Invalid signature'}), 401
        
        # 获取产品数据
        product_data = request.get_json()
        
        if not product_data:
            return jsonify({'error': 'No data received'}), 400
        
        # 处理产品
        shopify_service = ShopifyService()
        result = shopify_service._sync_product(product_data)
        
        logger.info(f"Product created via webhook: {product_data.get('title')}")
        
        return jsonify({
            'status': 'success',
            'message': 'Product processed successfully',
            'product_id': result.get('product_id')
        }), 200
        
    except Exception as e:
        logger.error(f"Error processing product create webhook: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500


@webhook_bp.route('/shopify/products/update', methods=['POST'])
def handle_product_update():
    """处理Shopify产品更新webhook"""
    try:
        # 验证webhook签名
        if not _verify_webhook_signature():
            return jsonify({'error': 'Invalid signature'}), 401
        
        # 获取产品数据
        product_data = request.get_json()
        
        if not product_data:
            return jsonify({'error': 'No data received'}), 400
        
        # 处理产品更新
        shopify_service = ShopifyService()
        result = shopify_service._sync_product(product_data)
        
        logger.info(f"Product updated via webhook: {product_data.get('title')}")
        
        return jsonify({
            'status': 'success',
            'message': 'Product updated successfully',
            'product_id': result.get('product_id')
        }), 200
        
    except Exception as e:
        logger.error(f"Error processing product update webhook: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500


@webhook_bp.route('/shopify/products/delete', methods=['POST'])
def handle_product_delete():
    """处理Shopify产品删除webhook"""
    try:
        # 验证webhook签名
        if not _verify_webhook_signature():
            return jsonify({'error': 'Invalid signature'}), 401
        
        # 获取产品数据
        product_data = request.get_json()
        
        if not product_data:
            return jsonify({'error': 'No data received'}), 400
        
        # 查找并删除产品
        shopify_product_id = str(product_data.get('id'))
        product = Product.query.filter_by(shopify_product_id=shopify_product_id).first()
        
        if product:
            db.session.delete(product)
            db.session.commit()
            
            logger.info(f"Product deleted via webhook: {product_data.get('title')}")
            
            return jsonify({
                'status': 'success',
                'message': 'Product deleted successfully'
            }), 200
        else:
            logger.warning(f"Product not found for deletion webhook: {shopify_product_id}")
            return jsonify({'error': 'Product not found'}), 404
        
    except Exception as e:
        logger.error(f"Error processing product delete webhook: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500


def _verify_webhook_signature():
    """验证Shopify webhook签名"""
    try:
        # 获取签名
        signature = request.headers.get('X-Shopify-Hmac-Sha256')
        if not signature:
            logger.warning("Missing webhook signature")
            return False
        
        # 获取webhook密钥
        webhook_secret = current_app.config.get('SHOPIFY_WEBHOOK_SECRET')
        if not webhook_secret:
            logger.warning("Shopify webhook secret not configured")
            return True  # 如果没有配置密钥，跳过验证
        
        # 验证签名
        return validate_shopify_webhook_signature(
            request.get_data(),
            signature,
            webhook_secret
        )
        
    except Exception as e:
        logger.error(f"Error verifying webhook signature: {str(e)}")
        return False


@webhook_bp.route('/health', methods=['GET'])
def webhook_health():
    """Webhook健康检查"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.utcnow().isoformat(),
        'service': 'shopify-webhooks'
    }), 200