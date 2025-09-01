from flask import jsonify, request, current_app
from app.api import bp
import jwt
from datetime import datetime, timedelta


@bp.route('/login/account', methods=['POST'])
def login():
    """用户登录接口"""
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({
                'status': 'error',
                'type': 'account',
                'currentAuthority': 'guest',
                'message': '请求数据不能为空'
            }), 400
        
        username = data.get('username')
        password = data.get('password')
        login_type = data.get('type', 'account')
        
        # 验证必填字段
        if not username or not password:
            return jsonify({
                'status': 'error',
                'type': login_type,
                'currentAuthority': 'guest',
                'message': '用户名和密码不能为空'
            }), 400
        
        # 验证用户名和密码
        if username == 'admin' and password == 'qweasdzxc':
            # 生成JWT token
            token_payload = {
                'username': username,
                'authority': 'admin',
                'exp': datetime.utcnow() + timedelta(hours=24),
                'iat': datetime.utcnow()
            }
            
            # 使用简单的密钥生成token
            token = jwt.encode(token_payload, 'caseledger-secret-key', algorithm='HS256')
            
            return jsonify({
                'status': 'ok',
                'type': login_type,
                'currentAuthority': 'admin',
                'token': token,
                'message': '登录成功'
            })
        else:
            return jsonify({
                'status': 'error',
                'type': login_type,
                'currentAuthority': 'guest',
                'message': '用户名或密码错误'
            }), 401
            
    except Exception as e:
        current_app.logger.error(f"Login error: {str(e)}")
        return jsonify({
            'status': 'error',
            'type': 'account',
            'currentAuthority': 'guest',
            'message': f'登录失败: {str(e)}'
        }), 500


@bp.route('/login/outLogin', methods=['POST'])
def logout():
    """用户登出接口"""
    try:
        return jsonify({
            'data': {},
            'success': True,
            'message': '登出成功'
        })
        
    except Exception as e:
        current_app.logger.error(f"Logout error: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'登出失败: {str(e)}'
        }), 500


@bp.route('/login/captcha', methods=['POST'])
def get_captcha():
    """获取验证码接口（模拟）"""
    try:
        phone = request.args.get('phone')
        
        if not phone:
            return jsonify({
                'success': False,
                'message': '手机号不能为空'
            }), 400
        
        # 模拟发送验证码
        return jsonify({
            'success': True,
            'message': '验证码发送成功',
            'captcha': '1234'  # 模拟验证码
        })
        
    except Exception as e:
        current_app.logger.error(f"Get captcha error: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取验证码失败: {str(e)}'
        }), 500