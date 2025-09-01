from flask import jsonify, request, current_app
from app.api import bp
from app.models.order_cost import OrderCost, OrderCostBatch
from app.models.account import Account
from app.models import Order
from app import db
from datetime import datetime, date
from sqlalchemy import func, desc, and_


# ==================== 订单费用管理 API ====================

@bp.route('/order-costs', methods=['GET'])
def get_order_costs():
    """获取订单费用列表"""
    try:
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 20, type=int), 100)
        order_number = request.args.get('order_number')
        status = request.args.get('status')
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        batch_id = request.args.get('batch_id')
        
        query = OrderCost.query
        
        # 订单号筛选
        if order_number:
            query = query.filter(OrderCost.order_number.like(f'%{order_number}%'))
        
        # 状态筛选
        if status:
            query = query.filter_by(status=status)
        
        # 批次筛选
        if batch_id:
            query = query.filter_by(batch_id=batch_id)
        
        # 日期筛选
        if start_date:
            query = query.filter(OrderCost.cost_date >= datetime.strptime(start_date, '%Y-%m-%d').date())
        
        if end_date:
            query = query.filter(OrderCost.cost_date <= datetime.strptime(end_date, '%Y-%m-%d').date())
        
        # 分页
        order_costs = query.order_by(desc(OrderCost.created_at)).paginate(
            page=page, per_page=per_page, error_out=False
        )
        
        return jsonify({
            'success': True,
            'order_costs': [cost.to_dict() for cost in order_costs.items],
            'pagination': {
                'page': order_costs.page,
                'pages': order_costs.pages,
                'per_page': order_costs.per_page,
                'total': order_costs.total,
                'has_next': order_costs.has_next,
                'has_prev': order_costs.has_prev
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"Get order costs error: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取订单费用列表失败: {str(e)}'
        }), 500


@bp.route('/order-costs', methods=['POST'])
def create_order_cost():
    """创建单个订单费用记录"""
    try:
        data = request.get_json()
        
        # 验证必填字段
        required_fields = ['order_number', 'cost_date']
        for field in required_fields:
            if not data.get(field):
                return jsonify({
                    'success': False,
                    'message': f'缺少必填字段: {field}'
                }), 400
        
        # 验证至少有一种费用
        if not any([data.get('shipping_cost'), data.get('fangguo_cost'), data.get('other_cost')]):
            return jsonify({
                'success': False,
                'message': '至少需要填写一种费用'
            }), 400
        
        # 检查订单是否已存在费用记录
        existing_cost = OrderCost.query.filter_by(
            order_number=data['order_number']
        ).first()
        
        if existing_cost:
            return jsonify({
                'success': False,
                'message': f'订单 {data["order_number"]} 已存在费用记录'
            }), 400
        
        # 创建订单费用记录
        order_cost = OrderCost(
            order_number=data['order_number'],
            shipping_cost=float(data.get('shipping_cost', 0)),
            fangguo_cost=float(data.get('fangguo_cost', 0)),
            other_cost=float(data.get('other_cost', 0)),
            shipping_account_id=data.get('shipping_account_id'),
            fangguo_account_id=data.get('fangguo_account_id'),
            other_account_id=data.get('other_account_id'),
            shipping_reference=data.get('shipping_reference'),
            fangguo_reference=data.get('fangguo_reference'),
            other_reference=data.get('other_reference'),
            notes=data.get('notes'),
            cost_date=datetime.strptime(data['cost_date'], '%Y-%m-%d').date(),
            created_by=data.get('created_by', 'system'),
            status=data.get('status', 'pending')
        )
        
        db.session.add(order_cost)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': '订单费用记录创建成功',
            'order_cost': order_cost.to_dict()
        }), 201
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Create order cost error: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'创建订单费用记录失败: {str(e)}'
        }), 500


@bp.route('/order-costs/batch', methods=['POST'])
def create_batch_order_costs():
    """批量创建订单费用记录"""
    try:
        data = request.get_json()
        
        # 验证必填字段
        if not data.get('order_numbers') or not isinstance(data['order_numbers'], list):
            return jsonify({
                'success': False,
                'message': '订单号列表不能为空'
            }), 400
        
        if not data.get('cost_date'):
            return jsonify({
                'success': False,
                'message': '费用日期不能为空'
            }), 400
        
        # 验证至少有一种费用
        if not any([data.get('shipping_cost'), data.get('fangguo_cost'), data.get('other_cost')]):
            return jsonify({
                'success': False,
                'message': '至少需要填写一种费用'
            }), 400
        
        # 检查是否有重复的订单费用记录
        existing_orders = OrderCost.query.filter(
            OrderCost.order_number.in_(data['order_numbers'])
        ).all()
        
        if existing_orders:
            existing_order_numbers = [cost.order_number for cost in existing_orders]
            return jsonify({
                'success': False,
                'message': f'以下订单已存在费用记录: {", ".join(existing_order_numbers)}'
            }), 400
        
        # 批量创建订单费用记录
        order_costs = OrderCost.create_batch(
            order_numbers=data['order_numbers'],
            shipping_cost=float(data.get('shipping_cost', 0)),
            fangguo_cost=float(data.get('fangguo_cost', 0)),
            other_cost=float(data.get('other_cost', 0)),
            shipping_account_id=data.get('shipping_account_id'),
            fangguo_account_id=data.get('fangguo_account_id'),
            other_account_id=data.get('other_account_id'),
            shipping_reference=data.get('shipping_reference'),
            fangguo_reference=data.get('fangguo_reference'),
            other_reference=data.get('other_reference'),
            notes=data.get('notes'),
            cost_date=datetime.strptime(data['cost_date'], '%Y-%m-%d').date(),
            created_by=data.get('created_by', 'system')
        )
        
        return jsonify({
            'success': True,
            'message': f'成功创建 {len(order_costs)} 条订单费用记录',
            'order_costs': [cost.to_dict() for cost in order_costs],
            'batch_id': order_costs[0].batch_id if order_costs else None
        }), 201
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Create batch order costs error: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'批量创建订单费用记录失败: {str(e)}'
        }), 500


@bp.route('/order-costs/<int:cost_id>', methods=['GET'])
def get_order_cost(cost_id):
    """获取单个订单费用详情"""
    try:
        order_cost = OrderCost.query.get_or_404(cost_id)
        
        return jsonify({
            'success': True,
            'order_cost': order_cost.to_dict()
        })
        
    except Exception as e:
        current_app.logger.error(f"Get order cost error: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取订单费用详情失败: {str(e)}'
        }), 500


@bp.route('/order-costs/<int:cost_id>', methods=['PUT'])
def update_order_cost(cost_id):
    """更新订单费用记录"""
    try:
        order_cost = OrderCost.query.get_or_404(cost_id)
        data = request.get_json()
        
        # 如果已确认，不允许修改
        if order_cost.status == 'confirmed':
            return jsonify({
                'success': False,
                'message': '已确认的费用记录不允许修改'
            }), 400
        
        # 更新字段
        if 'shipping_cost' in data:
            order_cost.shipping_cost = float(data['shipping_cost'])
        if 'fangguo_cost' in data:
            order_cost.fangguo_cost = float(data['fangguo_cost'])
        if 'other_cost' in data:
            order_cost.other_cost = float(data['other_cost'])
        if 'shipping_account_id' in data:
            order_cost.shipping_account_id = data['shipping_account_id']
        if 'fangguo_account_id' in data:
            order_cost.fangguo_account_id = data['fangguo_account_id']
        if 'other_account_id' in data:
            order_cost.other_account_id = data['other_account_id']
        if 'shipping_reference' in data:
            order_cost.shipping_reference = data['shipping_reference']
        if 'fangguo_reference' in data:
            order_cost.fangguo_reference = data['fangguo_reference']
        if 'other_reference' in data:
            order_cost.other_reference = data['other_reference']
        if 'notes' in data:
            order_cost.notes = data['notes']
        if 'cost_date' in data:
            order_cost.cost_date = datetime.strptime(data['cost_date'], '%Y-%m-%d').date()
        
        order_cost.updated_at = datetime.utcnow()
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': '订单费用记录更新成功',
            'order_cost': order_cost.to_dict()
        })
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Update order cost error: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'更新订单费用记录失败: {str(e)}'
        }), 500


@bp.route('/order-costs/<int:cost_id>', methods=['DELETE'])
def delete_order_cost(cost_id):
    """删除订单费用记录"""
    try:
        order_cost = OrderCost.query.get_or_404(cost_id)
        
        # 如果已确认，不允许删除
        if order_cost.status == 'confirmed':
            return jsonify({
                'success': False,
                'message': '已确认的费用记录不允许删除'
            }), 400
        
        db.session.delete(order_cost)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': '订单费用记录删除成功'
        })
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Delete order cost error: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'删除订单费用记录失败: {str(e)}'
        }), 500


@bp.route('/order-costs/<int:cost_id>/confirm', methods=['POST'])
def confirm_order_cost(cost_id):
    """确认订单费用并扣费"""
    try:
        order_cost = OrderCost.query.get_or_404(cost_id)
        
        if order_cost.status != 'pending':
            return jsonify({
                'success': False,
                'message': '只能确认待处理状态的费用记录'
            }), 400
        
        # 确认费用（自动扣费）
        success, message = order_cost.confirm_costs()
        
        if success:
            return jsonify({
                'success': True,
                'message': '费用确认成功，已自动扣费',
                'order_cost': order_cost.to_dict()
            })
        else:
            return jsonify({
                'success': False,
                'message': f'费用确认失败: {message}'
            }), 400
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Confirm order cost error: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'确认费用失败: {str(e)}'
        }), 500


@bp.route('/order-costs/batch/<batch_id>/confirm', methods=['POST'])
def confirm_batch_order_costs(batch_id):
    """批量确认订单费用并扣费"""
    try:
        order_costs = OrderCost.get_by_batch_id(batch_id)
        
        if not order_costs:
            return jsonify({
                'success': False,
                'message': '未找到指定批次的费用记录'
            }), 404
        
        # 检查是否都是待处理状态
        pending_costs = [cost for cost in order_costs if cost.status == 'pending']
        if len(pending_costs) != len(order_costs):
            return jsonify({
                'success': False,
                'message': '批次中存在非待处理状态的费用记录'
            }), 400
        
        success_count = 0
        failed_orders = []
        
        for order_cost in pending_costs:
            success, message = order_cost.confirm_costs()
            if success:
                success_count += 1
            else:
                failed_orders.append({
                    'order_number': order_cost.order_number,
                    'error': message
                })
        
        if failed_orders:
            return jsonify({
                'success': False,
                'message': f'批量确认部分失败：成功 {success_count} 条，失败 {len(failed_orders)} 条',
                'failed_orders': failed_orders
            }), 400
        else:
            return jsonify({
                'success': True,
                'message': f'批量确认成功：共确认 {success_count} 条费用记录'
            })
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Confirm batch order costs error: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'批量确认费用失败: {str(e)}'
        }), 500


# ==================== 批次管理 API ====================

@bp.route('/order-cost-batches', methods=['GET'])
def get_order_cost_batches():
    """获取费用批次列表"""
    try:
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 20, type=int), 100)
        
        batches = OrderCostBatch.query.order_by(desc(OrderCostBatch.created_at)).paginate(
            page=page, per_page=per_page, error_out=False
        )
        
        return jsonify({
            'success': True,
            'batches': [batch.to_dict() for batch in batches.items],
            'pagination': {
                'page': batches.page,
                'pages': batches.pages,
                'per_page': batches.per_page,
                'total': batches.total,
                'has_next': batches.has_next,
                'has_prev': batches.has_prev
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"Get order cost batches error: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取费用批次列表失败: {str(e)}'
        }), 500


@bp.route('/order-cost-batches/<batch_id>', methods=['GET'])
def get_order_cost_batch(batch_id):
    """获取批次详情及其包含的费用记录"""
    try:
        batch = OrderCostBatch.query.filter_by(batch_id=batch_id).first_or_404()
        order_costs = OrderCost.get_by_batch_id(batch_id)
        
        batch_data = batch.to_dict()
        batch_data['order_costs'] = [cost.to_dict() for cost in order_costs]
        
        return jsonify({
            'success': True,
            'batch': batch_data
        })
        
    except Exception as e:
        current_app.logger.error(f"Get order cost batch error: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取批次详情失败: {str(e)}'
        }), 500


# ==================== 统计和查询 API ====================

@bp.route('/order-costs/summary', methods=['GET'])
def get_order_costs_summary():
    """获取订单费用汇总统计"""
    try:
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        status = request.args.get('status')
        
        # 构建查询
        query = OrderCost.query
        
        if start_date:
            query = query.filter(OrderCost.cost_date >= datetime.strptime(start_date, '%Y-%m-%d').date())
        
        if end_date:
            query = query.filter(OrderCost.cost_date <= datetime.strptime(end_date, '%Y-%m-%d').date())
        
        if status:
            query = query.filter_by(status=status)
        
        # 获取汇总数据
        summary = OrderCost.get_cost_summary(
            start_date=datetime.strptime(start_date, '%Y-%m-%d').date() if start_date else None,
            end_date=datetime.strptime(end_date, '%Y-%m-%d').date() if end_date else None
        )
        
        return jsonify({
            'success': True,
            'summary': summary
        })
        
    except Exception as e:
        current_app.logger.error(f"Get order costs summary error: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取费用汇总失败: {str(e)}'
        }), 500


@bp.route('/order-costs/by-orders', methods=['POST'])
def get_order_costs_by_orders():
    """根据订单号列表获取费用记录"""
    try:
        data = request.get_json()
        
        if not data.get('order_numbers') or not isinstance(data['order_numbers'], list):
            return jsonify({
                'success': False,
                'message': '订单号列表不能为空'
            }), 400
        
        order_costs = OrderCost.get_by_order_numbers(data['order_numbers'])
        
        return jsonify({
            'success': True,
            'order_costs': [cost.to_dict() for cost in order_costs]
        })
        
    except Exception as e:
        current_app.logger.error(f"Get order costs by orders error: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取订单费用失败: {str(e)}'
        }), 500