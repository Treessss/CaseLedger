from flask import jsonify, request, current_app
from app.api import bp


@bp.route('/currentUser', methods=['GET'])
def get_current_user():
    """获取当前用户信息"""
    try:
        # 验证token参数
        token = request.args.get('token')
        if not token:
            return jsonify({
                'success': False,
                'errorCode': '401',
                'errorMessage': '缺少token参数'
            }), 401
        
        # 简单的token验证（这里使用固定token "123"）
        if token != '123':
            return jsonify({
                'success': False,
                'errorCode': '401',
                'errorMessage': '无效的token'
            }), 401
        
        # 返回模拟用户数据
        user_data = {
            'name': 'CaseLedger User',
            'avatar': '/logo.svg',
            'userid': '1',
            'email': 'user@caseledger.com',
            'signature': '案例账本管理员',
            'title': '系统管理员',
            'group': 'CaseLedger团队',
            'tags': [
                {'key': '0', 'label': '管理员'},
                {'key': '1', 'label': '财务'},
            ],
            'notifyCount': 0,
            'unreadCount': 0,
            'country': 'China',
            'access': 'admin',
            'geographic': {
                'province': {'label': '广东省', 'key': '440000'},
                'city': {'label': '深圳市', 'key': '440300'}
            },
            'address': '深圳市南山区',
            'phone': '138-0000-0000'
        }
        
        return jsonify({
            'success': True,
            'data': user_data
        })
        
    except Exception as e:
        current_app.logger.error(f"Get current user error: {str(e)}")
        return jsonify({
            'success': False,
            'errorCode': '500',
            'errorMessage': f'获取用户信息失败: {str(e)}'
        }), 500