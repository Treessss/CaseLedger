import time
from flask import request, jsonify, current_app
from app.models import Expense, Account, Consumption, Order
from app import db
from datetime import datetime
from sqlalchemy import desc
from sqlalchemy.exc import OperationalError
from app.api import bp

@bp.route('/expenses', methods=['GET'])
def get_expenses():
    """获取费用列表"""
    try:
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 10, type=int)
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        
        # 构建查询
        expenses_query = Expense.query
        
        # 添加日期过滤
        if start_date:
            try:
                # 移除可能的引号并解析日期
                clean_start_date = start_date.strip('"')
                start_dt = datetime.fromisoformat(clean_start_date.replace('Z', '+00:00'))
                start_date_obj = start_dt.date()
                expenses_query = expenses_query.filter(Expense.expense_date >= start_date_obj)
            except ValueError:
                return jsonify({'success': False, 'message': '开始日期格式错误'}), 400
        
        if end_date:
            try:
                # 移除可能的引号并解析日期
                clean_end_date = end_date.strip('"')
                end_dt = datetime.fromisoformat(clean_end_date.replace('Z', '+00:00'))
                end_date_obj = end_dt.date()
                expenses_query = expenses_query.filter(Expense.expense_date <= end_date_obj)
            except ValueError:
                return jsonify({'success': False, 'message': '结束日期格式错误'}), 400
        
        # 分页查询费用
        expenses_query = expenses_query.order_by(desc(Expense.expense_date))
        expenses_pagination = expenses_query.paginate(
            page=page, per_page=per_page, error_out=False
        )
        
        expenses_data = []
        for expense in expenses_pagination.items:
            # 获取关联账户名称
            account_name = None
            if expense.account_id:
                account = Account.query.get(expense.account_id)
                if account:
                    account_name = account.account_name
            
            # 获取关联订单信息（支持多个订单）
            order_info = None
            orders_info = []
            
            # 获取多对多关联的所有订单
            if expense.orders:
                for order in expense.orders:
                    orders_info.append({
                        'id': order.id,
                        'order_number': order.order_number,
                        'customer_name': order.customer_name,
                        'total_price': float(order.total_price) if order.total_price else 0
                    })
            
            # 向后兼容：如果没有多对多关联但有主要订单，则使用主要订单
            elif expense.order_id:
                from app.models.order import Order
                order = Order.query.get(expense.order_id)
                if order:
                    order_info = {
                        'id': order.id,
                        'order_number': order.order_number,
                        'customer_name': order.customer_name,
                        'total_price': float(order.total_price) if order.total_price else 0
                    }
                    orders_info = [order_info]
            
            # 为了向后兼容，保留order_info字段（显示第一个订单）
            if orders_info:
                order_info = orders_info[0]
            
            expenses_data.append({
                'id': expense.id,
                'date': expense.expense_date.strftime('%Y-%m-%d') if expense.expense_date else None,
                'category': expense.category,
                'amount': float(expense.amount) if expense.amount else 0,
                'description': expense.description,
                'status': expense.status if hasattr(expense, 'status') else 'confirmed',
                'submitter': expense.submitter if hasattr(expense, 'submitter') and expense.submitter else 'Admin',
                'account_id': expense.account_id,
                'account_name': account_name,
                'order_id': expense.order_id,
                'order_info': order_info,
                'orders_info': orders_info,  # 所有关联订单信息
                'created_at': expense.created_at.strftime('%Y-%m-%d %H:%M:%S') if expense.created_at else None,
                'updated_at': expense.updated_at.strftime('%Y-%m-%d %H:%M:%S') if expense.updated_at else None
            })
        
        return jsonify({
            'success': True,
            'data': {
                'expenses': expenses_data,
                'pagination': {
                    'page': page,
                    'per_page': per_page,
                    'total': expenses_pagination.total,
                    'pages': expenses_pagination.pages,
                    'has_prev': expenses_pagination.has_prev,
                    'has_next': expenses_pagination.has_next
                }
            }
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@bp.route('/expenses/categories', methods=['GET'])
def get_expense_categories():
    """获取费用分类列表"""
    try:
        categories = Expense.get_categories()
        data = [{'key': key, 'label': value} for key, value in categories.items()]
        return jsonify({
            'success': True,
            'data': data
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@bp.route('/expenses', methods=['POST'])
def create_expense():
    """创建新费用"""
    try:
        data = request.get_json() or {}
        
        # 验证必需字段
        required_fields = ['date', 'category', 'amount', 'description']
        for field in required_fields:
            if field not in data or not data[field]:
                return jsonify({
                    'success': False, 
                    'message': f'缺少必需字段: {field}'
                }), 400

        from decimal import Decimal

        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                account_id = data.get('account_id')
                amount_decimal = Decimal(str(data['amount']))
                account = None

                if account_id:
                    account = Account.query.get(account_id)
                    if not account:
                        return jsonify({
                            'success': False,
                            'message': f'账户不存在: {account_id}'
                        }), 400

                    if account.balance < amount_decimal:
                        return jsonify({
                            'success': False,
                            'message': f'账户余额不足，当前余额: {account.balance}，需要扣除: {data["amount"]}'
                        }), 400

                expense = Expense(
                    expense_date=datetime.strptime(data['date'], '%Y-%m-%d').date(),
                    category=data['category'],
                    amount=float(data['amount']),
                    currency='CNY',
                    original_amount=data.get('original_amount'),
                    original_currency=data.get('original_currency'),
                    exchange_rate=data.get('exchange_rate', 1.0),
                    description=data['description'],
                    vendor=data.get('vendor', ''),
                    reference_id=data.get('reference_id', ''),
                    submitter=data.get('submitter', 'Admin'),
                    account_id=account_id,
                    order_id=data.get('order_id'),
                    status=data.get('status', 'completed'),
                    created_at=datetime.now()
                )

                # 处理关联订单（如果有）
                order_ids = data.get('order_ids', [])
                if order_ids:
                    expense.order_id = order_ids[0]
                    for order_id in order_ids:
                        order = Order.query.get(order_id)
                        if order:
                            expense.orders.append(order)

                if account:
                    account.balance -= amount_decimal
                    account.updated_at = datetime.now()

                    consumption = Consumption(
                        account_id=account_id,
                        amount=amount_decimal,
                        description=f"费用支出: {data['description']}",
                        consumption_date=datetime.now().date(),
                        created_at=datetime.now()
                    )
                    db.session.add(consumption)

                db.session.add(expense)
                db.session.commit()

                return jsonify({
                    'success': True,
                    'message': '费用添加成功',
                    'data': {
                        'id': expense.id,
                        'date': expense.expense_date.strftime('%Y-%m-%d'),
                        'category': expense.category,
                        'amount': float(expense.amount),
                        'currency': expense.currency,
                        'original_amount': float(expense.original_amount) if expense.original_amount else None,
                        'original_currency': expense.original_currency,
                        'exchange_rate': float(expense.exchange_rate) if expense.exchange_rate else 1.0,
                        'description': expense.description,
                        'vendor': expense.vendor,
                        'reference_id': expense.reference_id,
                        'submitter': expense.submitter,
                        'status': expense.status
                    }
                })
            except OperationalError as e:
                db.session.rollback()
                if "database is locked" in str(e).lower() and attempt < max_retries:
                    current_app.logger.warning(
                        f"Create expense failed by database lock, retrying (attempt {attempt})"
                    )
                    time.sleep(0.2 * attempt)
                    continue
                current_app.logger.exception("Create expense failed")
                return jsonify({'success': False, 'message': str(e)}), 500
            except ValueError:
                db.session.rollback()
                return jsonify({'success': False, 'message': '日期格式错误'}), 400
            except Exception as e:
                db.session.rollback()
                current_app.logger.exception("Create expense failed")
                return jsonify({'success': False, 'message': str(e)}), 500
    except ValueError as e:
        return jsonify({'success': False, 'message': '日期格式错误'}), 400
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("Create expense request failed")
        return jsonify({'success': False, 'message': str(e)}), 500

@bp.route('/expenses/<int:expense_id>', methods=['PUT'])
def update_expense(expense_id):
    """更新费用"""
    try:
        expense = Expense.query.get_or_404(expense_id)
        data = request.get_json()
        
        # 更新字段
        if 'date' in data:
            expense.expense_date = datetime.strptime(data['date'], '%Y-%m-%d').date()
        if 'expense_date' in data:
            expense.expense_date = datetime.strptime(data['expense_date'], '%Y-%m-%d').date()
        if 'category' in data:
            expense.category = data['category']
        if 'amount' in data:
            expense.amount = float(data['amount'])
        if 'description' in data:
            expense.description = data['description']
        if 'submitter' in data:
            expense.submitter = data['submitter']
        if 'status' in data:
            expense.status = data['status']
        if 'vendor' in data:
            expense.vendor = data['vendor']
        if 'reference_id' in data:
            expense.reference_id = data['reference_id']
        if 'account_id' in data:
            expense.account_id = data['account_id']
        
        # 处理关联订单
        if 'order_ids' in data:
            # 清除现有关联
            expense.orders.clear()
            # 添加新关联
            if data['order_ids']:
                orders = Order.query.filter(Order.id.in_(data['order_ids'])).all()
                expense.orders.extend(orders)
        
        expense.updated_at = datetime.utcnow()
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': '费用更新成功',
            'data': {
                'id': expense.id,
                'date': expense.expense_date.strftime('%Y-%m-%d'),
                'category': expense.category,
                'amount': float(expense.amount),
                'description': expense.description,
                'submitter': expense.submitter,
                'status': expense.status,
                'vendor': expense.vendor,
                'reference_id': expense.reference_id,
                'account_id': expense.account_id
            }
        })
    except ValueError as e:
        return jsonify({'success': False, 'message': '日期格式错误'}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500

@bp.route('/expenses/<int:expense_id>', methods=['DELETE'])
def delete_expense(expense_id):
    """删除费用"""
    try:
        expense = Expense.query.get_or_404(expense_id)
        
        # 如果费用关联了账户，需要处理消费记录和恢复余额
        if expense.account_id:
            account = Account.query.get(expense.account_id)
            if account:
                # 查找关联的消费记录（通过费用描述匹配）
                consumption = Consumption.query.filter(
                    Consumption.account_id == expense.account_id,
                    Consumption.description.like(f"%费用支出: {expense.description}%")
                ).first()
                
                if consumption:
                    # 恢复账户余额
                    from decimal import Decimal
                    account.balance += consumption.amount
                    account.updated_at = datetime.now()
                    
                    # 删除消费记录
                    db.session.delete(consumption)
        
        db.session.delete(expense)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': '费用删除成功'
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500


@bp.route('/expenses/batch-delete', methods=['POST'])
def batch_delete_expenses():
    """批量删除费用"""
    try:
        data = request.get_json()
        expense_ids = data.get('expense_ids', [])
        
        if not expense_ids:
            return jsonify({
                'success': False,
                'message': '请选择要删除的费用记录'
            }), 400
        
        # 查找要删除的费用记录
        expenses = Expense.query.filter(Expense.id.in_(expense_ids)).all()
        
        if not expenses:
            return jsonify({
                'success': False,
                'message': '未找到要删除的费用记录'
            }), 404
        
        deleted_count = len(expenses)
        
        # 批量删除
        for expense in expenses:
            # 如果费用关联了账户，需要处理消费记录和恢复余额
            if expense.account_id:
                account = Account.query.get(expense.account_id)
                if account:
                    # 查找关联的消费记录（通过费用描述匹配）
                    consumption = Consumption.query.filter(
                        Consumption.account_id == expense.account_id,
                        Consumption.description.like(f"%费用支出: {expense.description}%")
                    ).first()
                    
                    if consumption:
                        # 恢复账户余额
                        from decimal import Decimal
                        account.balance += consumption.amount
                        account.updated_at = datetime.now()
                        
                        # 删除消费记录
                        db.session.delete(consumption)
            
            db.session.delete(expense)
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f'成功删除 {deleted_count} 条费用记录'
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500

@bp.route('/expenses/summary', methods=['GET'])
def get_expenses_summary():
    """获取费用统计"""
    try:
        # 按类型统计
        type_summary = db.session.query(
            Expense.category,
            db.func.sum(Expense.amount).label('total_amount'),
            db.func.count(Expense.id).label('count')
        ).group_by(Expense.category).all()
        
        # 总计
        total_amount = db.session.query(db.func.sum(Expense.amount)).scalar() or 0
        total_count = Expense.query.count()
        
        type_data = []
        for item in type_summary:
            type_data.append({
                'category': item.category,
                'total_amount': float(item.total_amount),
                'count': item.count
            })
        
        return jsonify({
            'success': True,
            'data': {
                'total_amount': float(total_amount),
                'total_count': total_count,
                'type_summary': type_data
            }
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500
