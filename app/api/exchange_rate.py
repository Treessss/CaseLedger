from flask import Blueprint, request, jsonify
from app.services.exchange_rate_service import exchange_rate_service
from decimal import Decimal
import logging

# 创建蓝图
exchange_rate_bp = Blueprint('exchange_rate', __name__)

@exchange_rate_bp.route('/api/exchange/convert', methods=['POST'])
def convert_currency():
    """货币转换API"""
    try:
        data = request.get_json()
        
        # 验证必需参数
        if not data:
            return jsonify({
                'success': False,
                'message': '请求数据不能为空'
            }), 400
        
        amount = data.get('amount')
        from_currency = data.get('from_currency')
        to_currency = data.get('to_currency', 'CNY')
        
        if amount is None:
            return jsonify({
                'success': False,
                'message': '金额不能为空'
            }), 400
        
        if not from_currency:
            return jsonify({
                'success': False,
                'message': '源货币不能为空'
            }), 400
        
        # 验证金额
        try:
            amount = float(amount)
            if amount < 0:
                return jsonify({
                    'success': False,
                    'message': '金额不能为负数'
                }), 400
        except (ValueError, TypeError):
            return jsonify({
                'success': False,
                'message': '金额格式不正确'
            }), 400
        
        # 验证货币代码
        from_currency = from_currency.upper()
        to_currency = to_currency.upper()
        
        if not exchange_rate_service.is_currency_supported(from_currency):
            return jsonify({
                'success': False,
                'message': f'不支持的源货币: {from_currency}'
            }), 400
        
        if not exchange_rate_service.is_currency_supported(to_currency):
            return jsonify({
                'success': False,
                'message': f'不支持的目标货币: {to_currency}'
            }), 400
        
        # 执行货币转换
        converted_amount = exchange_rate_service.convert_currency(amount, from_currency, to_currency)
        exchange_rate = exchange_rate_service.get_exchange_rate(from_currency, to_currency)
        
        if exchange_rate is None:
            return jsonify({
                'success': False,
                'message': f'无法获取汇率 {from_currency} -> {to_currency}'
            }), 500
        
        return jsonify({
            'success': True,
            'data': {
                'original_amount': amount,
                'original_currency': from_currency,
                'converted_amount': float(converted_amount),
                'target_currency': to_currency,
                'exchange_rate': float(exchange_rate)
            },
            'message': '转换成功'
        })
        
    except Exception as e:
        logging.error(f'货币转换失败: {str(e)}')
        return jsonify({
            'success': False,
            'message': f'转换失败: {str(e)}'
        }), 500

@exchange_rate_bp.route('/api/exchange/rates', methods=['GET'])
def get_exchange_rates():
    """获取支持的货币列表和当前汇率"""
    try:
        from_currency = request.args.get('from_currency', 'USD')
        to_currency = request.args.get('to_currency', 'CNY')
        
        from_currency = from_currency.upper()
        to_currency = to_currency.upper()
        
        # 获取支持的货币列表
        supported_currencies = exchange_rate_service.get_supported_currencies()
        
        # 获取汇率
        exchange_rate = exchange_rate_service.get_exchange_rate(from_currency, to_currency)
        
        return jsonify({
            'success': True,
            'data': {
                'supported_currencies': supported_currencies,
                'from_currency': from_currency,
                'to_currency': to_currency,
                'exchange_rate': float(exchange_rate) if exchange_rate else None
            },
            'message': '获取成功'
        })
        
    except Exception as e:
        logging.error(f'获取汇率失败: {str(e)}')
        return jsonify({
            'success': False,
            'message': f'获取失败: {str(e)}'
        }), 500

@exchange_rate_bp.route('/api/exchange/supported-currencies', methods=['GET'])
def get_supported_currencies():
    """获取支持的货币列表"""
    try:
        currencies = exchange_rate_service.get_supported_currencies()
        return jsonify({
            'success': True,
            'data': currencies,
            'message': '获取成功'
        })
    except Exception as e:
        logging.error(f'获取支持货币列表失败: {str(e)}')
        return jsonify({
            'success': False,
            'message': f'获取失败: {str(e)}'
        }), 500