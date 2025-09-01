from flask import jsonify, request, current_app
from app.api import bp
from app.models.order import Order
from app.models.payment import Payment
from app import db
from datetime import datetime, timedelta
from sqlalchemy import desc, asc, and_, or_


@bp.route('/orders', methods=['GET'])
def get_orders():
    """获取订单列表"""
    try:
        # 获取查询参数
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 20, type=int), 100)
        
        # 筛选参数
        status = request.args.get('status')
        financial_status = request.args.get('financial_status')
        fulfillment_status = request.args.get('fulfillment_status')
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        search = request.args.get('search')
        sort_by = request.args.get('sort_by', 'created_at')
        sort_order = request.args.get('sort_order', 'desc')
        
        # 构建查询
        query = Order.query
        
        # 应用筛选条件
        if financial_status:
            query = query.filter(Order.financial_status == financial_status)
        
        if fulfillment_status:
            query = query.filter(Order.fulfillment_status == fulfillment_status)
        
        if start_date:
            try:
                # 移除可能的引号并解析日期
                clean_start_date = start_date.strip('"')
                start_dt = datetime.fromisoformat(clean_start_date.replace('Z', '+00:00'))
                query = query.filter(Order.created_at >= start_dt)
            except ValueError:
                return jsonify({
                    'success': False,
                    'message': '开始日期格式错误'
                }), 400
        
        if end_date:
            try:
                # 移除可能的引号并解析日期
                clean_end_date = end_date.strip('"')
                end_dt = datetime.fromisoformat(clean_end_date.replace('Z', '+00:00'))
                query = query.filter(Order.created_at <= end_dt)
            except ValueError:
                return jsonify({
                    'success': False,
                    'message': '结束日期格式错误'
                }), 400
        
        if search:
            search_term = f"%{search}%"
            query = query.filter(
                or_(
                    Order.order_number.ilike(search_term),
                    Order.customer_email.ilike(search_term),
                    Order.customer_name.ilike(search_term)
                )
            )
        
        # 应用排序
        if hasattr(Order, sort_by):
            order_column = getattr(Order, sort_by)
            if sort_order == 'desc':
                query = query.order_by(desc(order_column))
            else:
                query = query.order_by(asc(order_column))
        else:
            query = query.order_by(desc(Order.created_at))
        
        # 分页
        pagination = query.paginate(
            page=page,
            per_page=per_page,
            error_out=False
        )
        
        orders = pagination.items
        
        # 转换订单数据（费用分摊已在to_dict方法中处理）
        orders_data = [order.to_dict() for order in orders]
        
        return jsonify({
            'success': True,
            'data': {
                'orders': orders_data,
                'pagination': {
                    'page': page,
                    'per_page': per_page,
                    'total': pagination.total,
                    'pages': pagination.pages,
                    'has_prev': pagination.has_prev,
                    'has_next': pagination.has_next
                }
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"Get orders error: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取订单列表失败: {str(e)}'
        }), 500


@bp.route('/orders/<int:order_id>', methods=['GET'])
def get_order(order_id):
    """获取单个订单详情"""
    try:
        order = Order.query.get_or_404(order_id)
        
        # 获取支付记录
        payments = Payment.query.filter_by(order_id=order_id).all()
        
        order_data = order.to_dict()
        order_data['payments'] = [payment.to_dict() for payment in payments]
        
        return jsonify({
            'success': True,
            'data': order_data
        })
        
    except Exception as e:
        current_app.logger.error(f"Get order error: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取订单详情失败: {str(e)}'
        }), 500


@bp.route('/orders/<int:order_id>', methods=['PUT'])
def update_order(order_id):
    """更新订单信息"""
    try:
        order = Order.query.get_or_404(order_id)
        data = request.get_json()
        
        # 可更新的字段
        updatable_fields = [
            'total_cost', 'shipping_cost', 'other_fees', 
            'notes', 'tags'
        ]
        
        for field in updatable_fields:
            if field in data:
                setattr(order, field, data[field])
        
        # 重新计算利润
        order.calculate_profit()
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': '订单更新成功',
            'data': order.to_dict()
        })
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Update order error: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'更新订单失败: {str(e)}'
        }), 500


@bp.route('/orders/recent', methods=['GET'])
def get_recent_orders():
    """获取最近订单"""
    try:
        limit = min(request.args.get('limit', 10, type=int), 50)
        
        orders = Order.query.order_by(desc(Order.created_at)).limit(limit).all()
        
        return jsonify({
            'success': True,
            'data': [order.to_dict() for order in orders]
        })
        
    except Exception as e:
        current_app.logger.error(f"Get recent orders error: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取最近订单失败: {str(e)}'
        }), 500


@bp.route('/orders/stats', methods=['GET'])
def get_order_stats():
    """获取订单统计信息"""
    try:
        # 获取日期范围
        days = request.args.get('days', 30, type=int)
        start_date = datetime.now() - timedelta(days=days)
        
        # 基础统计
        total_orders = Order.query.filter(Order.created_at >= start_date).count()
        total_revenue = db.session.query(db.func.sum(Order.total_price)).filter(
            Order.created_at >= start_date
        ).scalar() or 0
        total_profit = db.session.query(db.func.sum(Order.profit)).filter(
            Order.created_at >= start_date
        ).scalar() or 0
        
        # 今日统计
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        today_orders = Order.query.filter(Order.created_at >= today_start).count()
        today_revenue = db.session.query(db.func.sum(Order.total_price)).filter(
            Order.created_at >= today_start
        ).scalar() or 0
        today_profit = db.session.query(db.func.sum(Order.profit)).filter(
            Order.created_at >= today_start
        ).scalar() or 0
        
        # 状态统计
        status_stats = db.session.query(
            Order.financial_status,
            db.func.count(Order.id)
        ).filter(
            Order.created_at >= start_date
        ).group_by(Order.financial_status).all()
        
        # 履行状态统计
        fulfillment_stats = db.session.query(
            Order.fulfillment_status,
            db.func.count(Order.id)
        ).filter(
            Order.created_at >= start_date
        ).group_by(Order.fulfillment_status).all()
        
        return jsonify({
            'success': True,
            'data': {
                'period': {
                    'days': days,
                    'orders': total_orders,
                    'revenue': float(total_revenue),
                    'profit': float(total_profit),
                    'profit_margin': float(total_profit / total_revenue * 100) if total_revenue > 0 else 0
                },
                'today': {
                    'orders': today_orders,
                    'revenue': float(today_revenue),
                    'profit': float(today_profit),
                    'profit_margin': float(today_profit / today_revenue * 100) if today_revenue > 0 else 0
                },
                'financial_status': dict(status_stats),
                'fulfillment_status': dict(fulfillment_stats)
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"Get order stats error: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取订单统计失败: {str(e)}'
        }), 500