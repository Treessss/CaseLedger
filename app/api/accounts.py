from flask import jsonify, request, current_app
from app.api import bp
from app.models.account import Account, Recharge, Consumption
from app import db
from datetime import datetime, date
from sqlalchemy import func, desc


# ==================== 账户管理 API ====================

@bp.route('/accounts', methods=['GET'])
def get_accounts():
    """获取账户列表"""
    try:
        platform = request.args.get('platform')
        status = request.args.get('status')  # 移除默认值，允许显示所有状态
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 20, type=int), 100)
        
        query = Account.query
        
        # 平台筛选
        if platform:
            query = query.filter_by(platform=platform)
        
        # 状态筛选
        if status:
            query = query.filter_by(status=status)
        
        # 分页
        accounts = query.order_by(Account.created_at.desc()).paginate(
            page=page, per_page=per_page, error_out=False
        )
        
        return jsonify({
            'success': True,
            'data': [account.to_dict() for account in accounts.items],
            'pagination': {
                'page': accounts.page,
                'pages': accounts.pages,
                'per_page': accounts.per_page,
                'total': accounts.total,
                'has_next': accounts.has_next,
                'has_prev': accounts.has_prev
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"Get accounts error: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取账户列表失败: {str(e)}'
        }), 500


@bp.route('/accounts', methods=['POST'])
def create_account():
    """创建新账户"""
    try:
        data = request.get_json()
        
        # 验证必填字段
        required_fields = ['platform', 'account_name']
        for field in required_fields:
            if not data.get(field):
                return jsonify({
                    'success': False,
                    'message': f'缺少必填字段: {field}'
                }), 400
        
        # 检查账户名是否重复
        existing_account = Account.query.filter_by(
            platform=data['platform'],
            account_name=data['account_name']
        ).first()
        
        if existing_account:
            return jsonify({
                'success': False,
                'message': '该平台下已存在同名账户'
            }), 400
        
        # 创建账户
        account = Account(
            platform=data['platform'],
            account_name=data['account_name'],
            account_id=data.get('account_id'),
            description=data.get('description'),
            balance=data.get('balance', 0.00),
            currency=data.get('currency', 'CNY'),
            status=data.get('status', 'active')
        )
        
        db.session.add(account)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': '账户创建成功',
            'account': account.to_dict()
        }), 201
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Create account error: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'创建账户失败: {str(e)}'
        }), 500


@bp.route('/accounts/<int:account_id>', methods=['GET'])
def get_account(account_id):
    """获取单个账户详情"""
    try:
        account = Account.query.get_or_404(account_id)
        
        # 获取账户统计信息
        account_data = account.to_dict()
        account_data['total_recharge'] = account.get_total_recharge()
        account_data['total_consumption'] = account.get_total_consumption()
        
        return jsonify({
            'success': True,
            'account': account_data
        })
        
    except Exception as e:
        current_app.logger.error(f"Get account error: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取账户详情失败: {str(e)}'
        }), 500


@bp.route('/accounts/<int:account_id>', methods=['PUT'])
def update_account(account_id):
    """更新账户信息"""
    try:
        account = Account.query.get_or_404(account_id)
        data = request.get_json()
        
        # 更新字段
        if 'account_name' in data:
            # 检查账户名是否重复
            existing_account = Account.query.filter(
                Account.platform == account.platform,
                Account.account_name == data['account_name'],
                Account.id != account_id
            ).first()
            
            if existing_account:
                return jsonify({
                    'success': False,
                    'message': '该平台下已存在同名账户'
                }), 400
            
            account.account_name = data['account_name']
        
        if 'account_id' in data:
            account.account_id = data['account_id']
        if 'description' in data:
            account.description = data['description']
        if 'currency' in data:
            account.currency = data['currency']
        if 'status' in data:
            account.status = data['status']
        
        account.updated_at = datetime.utcnow()
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': '账户更新成功',
            'account': account.to_dict()
        })
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Update account error: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'更新账户失败: {str(e)}'
        }), 500


@bp.route('/accounts/<int:account_id>', methods=['DELETE'])
def delete_account(account_id):
    """删除账户"""
    try:
        account = Account.query.get_or_404(account_id)
        force_delete = request.args.get('force', 'false').lower() == 'true'
        
        # 检查是否有充值或消耗记录
        recharge_count = Recharge.query.filter_by(account_id=account_id).count()
        consumption_count = Consumption.query.filter_by(account_id=account_id).count()
        
        if (recharge_count > 0 or consumption_count > 0) and not force_delete:
            return jsonify({
                'success': False,
                'message': '该账户有充值或消耗记录，无法删除。如需强制删除，请确认操作。',
                'has_records': True,
                'recharge_count': recharge_count,
                'consumption_count': consumption_count
            }), 400
        
        # 如果强制删除，先删除相关记录
        if force_delete:
            # 删除充值记录
            Recharge.query.filter_by(account_id=account_id).delete()
            # 删除消耗记录
            Consumption.query.filter_by(account_id=account_id).delete()
        
        db.session.delete(account)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': '账户删除成功'
        })
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Delete account error: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'删除账户失败: {str(e)}'
        }), 500


# ==================== 预充值管理 API ====================

@bp.route('/accounts/<int:account_id>/recharges', methods=['GET'])
def get_account_recharges(account_id):
    """获取账户充值记录"""
    try:
        account = Account.query.get_or_404(account_id)
        
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 20, type=int), 100)
        status = request.args.get('status')
        
        query = Recharge.query.filter_by(account_id=account_id)
        
        if status:
            query = query.filter_by(status=status)
        
        recharges = query.order_by(desc(Recharge.recharge_date)).paginate(
            page=page, per_page=per_page, error_out=False
        )
        
        return jsonify({
            'success': True,
            'recharges': [recharge.to_dict() for recharge in recharges.items],
            'pagination': {
                'page': recharges.page,
                'pages': recharges.pages,
                'per_page': recharges.per_page,
                'total': recharges.total,
                'has_next': recharges.has_next,
                'has_prev': recharges.has_prev
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"Get account recharges error: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取充值记录失败: {str(e)}'
        }), 500


@bp.route('/accounts/<int:account_id>/recharges', methods=['POST'])
def create_recharge(account_id):
    """创建充值记录"""
    try:
        account = Account.query.get_or_404(account_id)
        data = request.get_json()
        
        # 验证必填字段
        if not data.get('amount') or float(data['amount']) <= 0:
            return jsonify({
                'success': False,
                'message': '充值金额必须大于0'
            }), 400
        
        # 创建充值记录，默认状态为已完成
        recharge = Recharge(
            account_id=account_id,
            amount=float(data['amount']),
            currency=data.get('currency', account.currency),
            recharge_method=data.get('recharge_method'),
            transaction_id=data.get('transaction_id'),
            description=data.get('description'),
            status=data.get('status', 'completed'),
            recharge_date=datetime.strptime(data['recharge_date'], '%Y-%m-%d') if data.get('recharge_date') else datetime.utcnow()
        )
        
        db.session.add(recharge)
        
        # 直接更新账户余额
        from decimal import Decimal
        account.update_balance(Decimal(str(data['amount'])), 'add')
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': '充值记录创建成功',
            'recharge': recharge.to_dict()
        }), 201
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Create recharge error: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'创建充值记录失败: {str(e)}'
        }), 500


@bp.route('/recharges/<int:recharge_id>/confirm', methods=['POST'])
def confirm_recharge(recharge_id):
    """确认充值"""
    try:
        recharge = Recharge.query.get_or_404(recharge_id)
        
        if recharge.status != 'pending':
            return jsonify({
                'success': False,
                'message': '只能确认待处理状态的充值记录'
            }), 400
        
        recharge.confirm_recharge()
        
        return jsonify({
            'success': True,
            'message': '充值确认成功',
            'recharge': recharge.to_dict()
        })
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Confirm recharge error: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'确认充值失败: {str(e)}'
        }), 500


# ==================== 消耗记录管理 API ====================

@bp.route('/accounts/<int:account_id>/consumptions', methods=['GET'])
def get_account_consumptions(account_id):
    """获取账户消耗记录"""
    try:
        account = Account.query.get_or_404(account_id)
        
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 20, type=int), 100)
        consumption_type = request.args.get('consumption_type')
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        
        query = Consumption.query.filter_by(account_id=account_id)
        
        if consumption_type:
            query = query.filter_by(consumption_type=consumption_type)
        
        if start_date:
            query = query.filter(Consumption.consumption_date >= datetime.strptime(start_date, '%Y-%m-%d').date())
        
        if end_date:
            query = query.filter(Consumption.consumption_date <= datetime.strptime(end_date, '%Y-%m-%d').date())
        
        consumptions = query.order_by(desc(Consumption.consumption_date)).paginate(
            page=page, per_page=per_page, error_out=False
        )
        
        return jsonify({
            'success': True,
            'consumptions': [consumption.to_dict() for consumption in consumptions.items],
            'pagination': {
                'page': consumptions.page,
                'pages': consumptions.pages,
                'per_page': consumptions.per_page,
                'total': consumptions.total,
                'has_next': consumptions.has_next,
                'has_prev': consumptions.has_prev
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"Get account consumptions error: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取消耗记录失败: {str(e)}'
        }), 500


@bp.route('/accounts/<int:account_id>/consumptions', methods=['POST'])
def create_consumption(account_id):
    """创建消耗记录"""
    try:
        account = Account.query.get_or_404(account_id)
        data = request.get_json()
        
        # 验证必填字段
        if not data.get('amount') or float(data['amount']) <= 0:
            return jsonify({
                'success': False,
                'message': '消耗金额必须大于0'
            }), 400
        
        if not data.get('consumption_date'):
            return jsonify({
                'success': False,
                'message': '消耗日期不能为空'
            }), 400
        
        # 检查账户余额
        amount = float(data['amount'])
        if account.balance < amount:
            return jsonify({
                'success': False,
                'message': f'账户余额不足：需要{amount}，余额{account.balance}'
            }), 400
        
        # 创建消耗记录
        consumption = Consumption(
            account_id=account_id,
            amount=amount,
            currency=data.get('currency', account.currency),
            consumption_type=data.get('consumption_type'),
            description=data.get('description'),
            reference_id=data.get('reference_id'),
            consumption_date=datetime.strptime(data['consumption_date'], '%Y-%m-%d').date()
        )
        
        db.session.add(consumption)
        
        # 从账户余额扣除
        if consumption.process_consumption():
            db.session.commit()
            return jsonify({
                'success': True,
                'message': '消耗记录创建成功',
                'consumption': consumption.to_dict()
            }), 201
        else:
            db.session.rollback()
            return jsonify({
                'success': False,
                'message': '账户余额不足，无法创建消耗记录'
            }), 400
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Create consumption error: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'创建消耗记录失败: {str(e)}'
        }), 500


# ==================== 统计和查询 API ====================

@bp.route('/accounts/platforms', methods=['GET'])
def get_platforms():
    """获取支持的平台列表"""
    try:
        platforms = Account.get_platforms()
        return jsonify({
            'success': True,
            'platforms': platforms
        })
    except Exception as e:
        current_app.logger.error(f"Get platforms error: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取平台列表失败: {str(e)}'
        }), 500


@bp.route('/accounts/summary', methods=['GET'])
def get_accounts_summary():
    """获取账户汇总信息"""
    try:
        platform = request.args.get('platform')
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        
        query = Account.query.filter_by(status='active')
        if platform:
            query = query.filter_by(platform=platform)
        
        accounts = query.all()
        
        # 计算汇总数据
        total_balance = sum(float(account.balance or 0) for account in accounts)
        total_accounts = len(accounts)
        
        # 计算充值和消耗（支持日期范围过滤）
        from datetime import datetime
        
        # 构建充值查询
        recharge_query = db.session.query(func.sum(Recharge.amount)).filter(
            Recharge.status == 'completed'
        )
        
        # 构建消耗查询
        consumption_query = db.session.query(func.sum(Consumption.amount))
        
        if start_date and end_date:
            try:
                start_dt = datetime.strptime(start_date, '%Y-%m-%d')
                end_dt = datetime.strptime(end_date, '%Y-%m-%d')
                
                recharge_query = recharge_query.filter(
                    func.date(Recharge.recharge_date) >= start_dt.date(),
                    func.date(Recharge.recharge_date) <= end_dt.date()
                )
                
                consumption_query = consumption_query.filter(
                    Consumption.consumption_date >= start_dt.date(),
                    Consumption.consumption_date <= end_dt.date()
                )
            except ValueError:
                return jsonify({
                    'success': False,
                    'message': '日期格式错误，请使用 YYYY-MM-DD 格式'
                }), 400
        else:
            # 默认使用当前月份
            current_month = datetime.now().month
            current_year = datetime.now().year
            
            recharge_query = recharge_query.filter(
                func.extract('month', Recharge.recharge_date) == current_month,
                func.extract('year', Recharge.recharge_date) == current_year
            )
            
            consumption_query = consumption_query.filter(
                func.extract('month', Consumption.consumption_date) == current_month,
                func.extract('year', Consumption.consumption_date) == current_year
            )
        
        month_recharge = recharge_query.scalar() or 0
        month_consumption = consumption_query.scalar() or 0
        
        # 按平台分组统计
        platform_summary = {}
        for account in accounts:
            platform_name = account.platform
            if platform_name not in platform_summary:
                platform_summary[platform_name] = {
                    'count': 0,
                    'total_balance': 0.00,
                    'accounts': []
                }
            
            platform_summary[platform_name]['count'] += 1
            platform_summary[platform_name]['total_balance'] += float(account.balance or 0)
            platform_summary[platform_name]['accounts'].append({
                'id': account.id,
                'account_name': account.account_name,
                'balance': float(account.balance or 0)
            })
        
        return jsonify({
            'success': True,
            'data': {
                'total_accounts': total_accounts,
                'total_balance': total_balance,
                'month_recharge': float(month_recharge),
                'month_consumption': float(month_consumption),
                'platform_summary': platform_summary
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"Get accounts summary error: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取账户汇总失败: {str(e)}'
        }), 500


@bp.route('/recharges/methods', methods=['GET'])
def get_recharge_methods():
    """获取充值方式列表"""
    try:
        methods = Recharge.get_recharge_methods()
        return jsonify({
            'success': True,
            'methods': methods
        })
    except Exception as e:
        current_app.logger.error(f"Get recharge methods error: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取充值方式失败: {str(e)}'
        }), 500


@bp.route('/accounts/recharge', methods=['POST', 'OPTIONS'])
def create_account_recharge():
    """通用充值接口"""
    if request.method == 'OPTIONS':
        # 处理预检请求
        response = jsonify({'success': True})
        response.headers.add('Access-Control-Allow-Origin', '*')
        response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
        response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
        return response
        
    try:
        data = request.get_json()
        account_id = data.get('account_id')
        
        if not account_id:
            return jsonify({
                'success': False,
                'message': '账户ID不能为空'
            }), 400
            
        account = Account.query.get_or_404(account_id)
        
        # 验证必填字段
        if not data.get('amount') or float(data['amount']) <= 0:
            return jsonify({
                'success': False,
                'message': '充值金额必须大于0'
            }), 400
        
        # 创建充值记录
        recharge = Recharge(
            account_id=account_id,
            amount=float(data['amount']),
            currency=data.get('currency', account.currency),
            recharge_method=data.get('recharge_method'),
            transaction_id=data.get('transaction_id'),
            description=data.get('description'),
            status=data.get('status', 'completed'),
            recharge_date=datetime.strptime(data['recharge_date'], '%Y-%m-%d') if data.get('recharge_date') else datetime.utcnow()
        )
        
        db.session.add(recharge)
        
        # 如果状态为已完成，直接更新账户余额
        if recharge.status == 'completed':
            from decimal import Decimal
            account.update_balance(Decimal(str(data['amount'])), 'add')
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': '充值记录创建成功',
            'recharge': recharge.to_dict()
        }), 201
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Create recharge error: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'创建充值记录失败: {str(e)}'
        }), 500


@bp.route('/consumptions/types', methods=['GET'])
def get_consumption_types():
    """获取消耗类型列表"""
    try:
        types = Consumption.get_consumption_types()
        return jsonify({
            'success': True,
            'types': types
        })
    except Exception as e:
        current_app.logger.error(f"Get consumption types error: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取消耗类型失败: {str(e)}'
        }), 500