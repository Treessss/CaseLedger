from flask import jsonify, request, current_app
from app.api import bp
from app.models.platform_account import PlatformAccount
from app import db
from datetime import datetime


# ==================== 平台账户密码管理 API ====================

@bp.route('/platform-accounts', methods=['GET'])
def get_platform_accounts():
    """获取平台账户列表"""
    try:
        platform = request.args.get('platform')
        search = request.args.get('search')
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 20, type=int), 100)
        
        query = PlatformAccount.query
        
        # 平台筛选
        if platform:
            query = query.filter_by(platform=platform)
        
        # 搜索功能
        if search:
            search_pattern = f'%{search}%'
            query = query.filter(
                db.or_(
                    PlatformAccount.platform.ilike(search_pattern),
                    PlatformAccount.username.ilike(search_pattern),
                    PlatformAccount.notes.ilike(search_pattern)
                )
            )
        
        # 分页
        accounts = query.order_by(PlatformAccount.created_at.desc()).paginate(
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
        current_app.logger.error(f"Get platform accounts error: {str(e)}")
        return jsonify({
            'success': False,
            'message': '获取平台账户列表失败',
            'error': str(e)
        }), 500


@bp.route('/platform-accounts', methods=['POST'])
def create_platform_account():
    """创建平台账户"""
    try:
        data = request.get_json()
        
        # 验证必填字段
        required_fields = ['platform', 'username', 'password']
        for field in required_fields:
            if not data.get(field):
                return jsonify({
                    'success': False,
                    'message': f'缺少必填字段: {field}'
                }), 400
        
        # 检查是否已存在相同平台和用户名的账户
        existing_account = PlatformAccount.query.filter_by(
            platform=data['platform'],
            username=data['username']
        ).first()
        
        if existing_account:
            return jsonify({
                'success': False,
                'message': '该平台下已存在相同用户名的账户'
            }), 400
        
        # 创建新账户
        account = PlatformAccount(
            platform=data['platform'],
            username=data['username'],
            notes=data.get('notes', '')
        )
        account.set_password(data['password'])
        
        db.session.add(account)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': '平台账户创建成功',
            'data': account.to_dict()
        }), 201
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Create platform account error: {str(e)}")
        return jsonify({
            'success': False,
            'message': '创建平台账户失败',
            'error': str(e)
        }), 500


@bp.route('/platform-accounts/<int:account_id>', methods=['GET'])
def get_platform_account(account_id):
    """获取单个平台账户详情"""
    try:
        include_password = request.args.get('include_password', 'false').lower() == 'true'
        
        account = PlatformAccount.query.get_or_404(account_id)
        
        return jsonify({
            'success': True,
            'data': account.to_dict(include_password=include_password)
        })
        
    except Exception as e:
        current_app.logger.error(f"Get platform account error: {str(e)}")
        return jsonify({
            'success': False,
            'message': '获取平台账户详情失败',
            'error': str(e)
        }), 500


@bp.route('/platform-accounts/<int:account_id>', methods=['PUT'])
def update_platform_account(account_id):
    """更新平台账户"""
    try:
        account = PlatformAccount.query.get_or_404(account_id)
        data = request.get_json()
        
        # 检查是否更新用户名时与其他账户冲突
        if 'username' in data and data['username'] != account.username:
            existing_account = PlatformAccount.query.filter_by(
                platform=data.get('platform', account.platform),
                username=data['username']
            ).filter(PlatformAccount.id != account_id).first()
            
            if existing_account:
                return jsonify({
                    'success': False,
                    'message': '该平台下已存在相同用户名的账户'
                }), 400
        
        # 更新字段
        if 'platform' in data:
            account.platform = data['platform']
        if 'username' in data:
            account.username = data['username']
        if 'password' in data:
            account.set_password(data['password'])
        if 'notes' in data:
            account.notes = data['notes']
        
        account.updated_at = datetime.utcnow()
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': '平台账户更新成功',
            'data': account.to_dict()
        })
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Update platform account error: {str(e)}")
        return jsonify({
            'success': False,
            'message': '更新平台账户失败',
            'error': str(e)
        }), 500


@bp.route('/platform-accounts/<int:account_id>', methods=['DELETE'])
def delete_platform_account(account_id):
    """删除平台账户"""
    try:
        account = PlatformAccount.query.get_or_404(account_id)
        
        db.session.delete(account)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': '平台账户删除成功'
        })
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Delete platform account error: {str(e)}")
        return jsonify({
            'success': False,
            'message': '删除平台账户失败',
            'error': str(e)
        }), 500


@bp.route('/platform-accounts/platforms', methods=['GET'])
def get_platform_account_platforms():
    """获取所有平台列表"""
    try:
        platforms = PlatformAccount.get_platforms()
        
        return jsonify({
            'success': True,
            'data': platforms
        })
        
    except Exception as e:
        current_app.logger.error(f"Get platform account platforms error: {str(e)}")
        return jsonify({
            'success': False,
            'message': '获取平台列表失败',
            'error': str(e)
        }), 500


@bp.route('/platform-accounts/<int:account_id>/password', methods=['GET'])
def get_platform_account_password(account_id):
    """获取平台账户密码"""
    try:
        account = PlatformAccount.query.get_or_404(account_id)
        
        return jsonify({
            'success': True,
            'data': {
                'password': account.get_password()
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"Get platform account password error: {str(e)}")
        return jsonify({
            'success': False,
            'message': '获取密码失败',
            'error': str(e)
        }), 500


@bp.route('/platform-accounts/search', methods=['GET'])
def search_platform_accounts():
    """搜索平台账户"""
    try:
        query = request.args.get('q', '')
        
        accounts = PlatformAccount.search(query)
        
        return jsonify({
            'success': True,
            'data': [account.to_dict() for account in accounts]
        })
        
    except Exception as e:
        current_app.logger.error(f"Search platform accounts error: {str(e)}")
        return jsonify({
            'success': False,
            'message': '搜索平台账户失败',
            'error': str(e)
        }), 500