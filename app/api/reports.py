from flask import request, jsonify
from app.models import Order, Expense, Account, OrderCost
from app import db
from datetime import datetime, timedelta
from sqlalchemy import func, and_
from app.api import bp
from app.services.exchange_rate_service import exchange_rate_service


def _build_order_datetime_range(start_date, end_date):
    """将日期区间转换为订单时间区间：[start_date 00:00:00, end_date+1 00:00:00)。"""
    start_datetime = datetime.combine(start_date, datetime.min.time())
    end_datetime_exclusive = datetime.combine(end_date + timedelta(days=1), datetime.min.time())
    return start_datetime, end_datetime_exclusive


def _get_default_report_range(report_type):
    """获取默认报表日期范围。"""
    today = datetime.now().date()
    if report_type == 'daily':
        # 日报默认当前整月（从月初到月末）
        start_date = today.replace(day=1)
        if today.month == 12:
            end_date = datetime(today.year + 1, 1, 1).date() - timedelta(days=1)
        else:
            end_date = datetime(today.year, today.month + 1, 1).date() - timedelta(days=1)
        return start_date, end_date
    if report_type == 'weekly':
        # 周报默认当前月份（从月初到今天）
        return today.replace(day=1), today
    if report_type == 'monthly':
        # 月报默认近半年（当前月+前5个月）
        start_date = today.replace(day=1)
        for _ in range(5):
            if start_date.month == 1:
                start_date = start_date.replace(year=start_date.year - 1, month=12)
            else:
                start_date = start_date.replace(month=start_date.month - 1)
        return start_date, today
    if report_type in ['quarterly', 'yearly']:
        # 季报/年报默认当年
        return datetime(today.year, 1, 1).date(), today
    return today - timedelta(days=30), today


def _get_quarter_start_date(date_value):
    quarter_start_month = ((date_value.month - 1) // 3) * 3 + 1
    return date_value.replace(month=quarter_start_month, day=1)


def _get_quarter_end_date(date_value):
    quarter_start = _get_quarter_start_date(date_value)
    if quarter_start.month == 10:
        next_quarter_start = datetime(quarter_start.year + 1, 1, 1).date()
    else:
        next_quarter_start = datetime(quarter_start.year, quarter_start.month + 3, 1).date()
    return next_quarter_start - timedelta(days=1)


def _calculate_income_cny(orders):
    total_income = 0.0
    for order in orders:
        if order.financial_status in ['paid', 'partially_paid'] and order.actual_received and order.currency:
            if order.currency == 'CNY':
                total_income += float(order.actual_received)
            else:
                total_income += float(
                    exchange_rate_service.convert_to_cny(float(order.actual_received), order.currency)
                )
    return total_income


def _calculate_expense_cny(start_date, end_date, order_start_dt, order_end_dt_exclusive):
    # 费用记录
    expenses = db.session.query(Expense).filter(
        and_(Expense.expense_date >= start_date, Expense.expense_date <= end_date)
    ).all()

    total_expense = 0.0
    for expense in expenses:
        if expense.amount and expense.currency:
            if expense.currency == 'CNY':
                total_expense += float(expense.amount)
            else:
                total_expense += float(
                    exchange_rate_service.convert_to_cny(float(expense.amount), expense.currency)
                )

    # 订单成本（本身为人民币）
    order_cost_total = db.session.query(
        func.sum(OrderCost.shipping_cost + OrderCost.fangguo_cost + OrderCost.other_cost)
    ).join(Order).filter(
        and_(
            Order.created_at >= order_start_dt,
            Order.created_at < order_end_dt_exclusive,
            OrderCost.status == 'confirmed',
        )
    ).scalar() or 0

    return total_expense + float(order_cost_total)


def _get_period_financial_data(start_date, end_date, label):
    order_start_dt, order_end_dt_exclusive = _build_order_datetime_range(start_date, end_date)

    orders = db.session.query(Order).filter(
        and_(Order.created_at >= order_start_dt, Order.created_at < order_end_dt_exclusive)
    ).all()

    income_cny = _calculate_income_cny(orders)
    expense_cny = _calculate_expense_cny(start_date, end_date, order_start_dt, order_end_dt_exclusive)
    profit_cny = income_cny - expense_cny

    return {
        'date': label,
        'income': round(income_cny, 2),
        'expense': round(expense_cny, 2),
        'profit': round(profit_cny, 2),
    }

@bp.route('/reports/financial-summary', methods=['GET'])
def get_financial_summary():
    """获取财务概览"""
    try:
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        
        # 默认当前月份
        if not start_date or not end_date:
            now = datetime.now()
            # 当前月份的第一天
            start_date = datetime(now.year, now.month, 1).date()
            # 当前月份的最后一天
            if now.month == 12:
                end_date = datetime(now.year + 1, 1, 1).date() - timedelta(days=1)
            else:
                end_date = datetime(now.year, now.month + 1, 1).date() - timedelta(days=1)
        else:
            start_date = datetime.strptime(start_date, '%Y-%m-%d').date()
            end_date = datetime.strptime(end_date, '%Y-%m-%d').date()

        order_start_dt, order_end_dt_exclusive = _build_order_datetime_range(start_date, end_date)
        
        # 计算总收入（订单金额，统一换算为人民币）
        orders = db.session.query(Order).filter(
            and_(Order.created_at >= order_start_dt, Order.created_at < order_end_dt_exclusive)
        ).all()
        
        total_revenue_cny = 0
        for order in orders:
            # 只统计已支付且有实际到账金额的订单
            if (order.financial_status in ['paid', 'partially_paid'] and 
                order.actual_received and order.currency):
                if order.currency == 'CNY':
                    total_revenue_cny += float(order.actual_received)
                else:
                    # 将其他货币换算为人民币
                    total_revenue_cny += float(exchange_rate_service.convert_to_cny(
                        float(order.actual_received), order.currency
                    ))
        
        # 计算总费用（费用记录 + 订单成本，统一换算为人民币）
        expenses = db.session.query(Expense).filter(
            and_(Expense.expense_date >= start_date, Expense.expense_date <= end_date)
        ).all()
        
        expense_total_cny = 0
        for expense in expenses:
            if expense.amount and expense.currency:
                if expense.currency == 'CNY':
                    expense_total_cny += float(expense.amount)
                else:
                    # 将其他货币换算为人民币
                    expense_total_cny += float(exchange_rate_service.convert_to_cny(
                        float(expense.amount), expense.currency
                    ))
        
        # 计算订单费用总额（物流费用 + 方果费用 + 其他费用，已经是人民币）
        order_cost_total = db.session.query(
            func.sum(OrderCost.shipping_cost + OrderCost.fangguo_cost + OrderCost.other_cost)
        ).filter(
            and_(OrderCost.cost_date >= start_date, OrderCost.cost_date <= end_date,
                 OrderCost.status == 'confirmed')
        ).scalar() or 0
        
        total_expenses_cny = expense_total_cny + float(order_cost_total)
        
        # 计算利润（人民币）
        total_profit_cny = total_revenue_cny - total_expenses_cny
        profit_margin = (total_profit_cny / total_revenue_cny * 100) if total_revenue_cny > 0 else 0
        
        # 订单数量
        order_count = db.session.query(func.count(Order.id)).filter(
            and_(Order.created_at >= order_start_dt, Order.created_at < order_end_dt_exclusive)
        ).scalar() or 0
        
        return jsonify({
            'success': True,
            'data': {
                'total_revenue': round(total_revenue_cny, 2),
                'total_expenses': round(total_expenses_cny, 2),
                'total_profit': round(total_profit_cny, 2),
                'profit_margin': round(profit_margin, 2),
                'order_count': order_count,
                'currency': 'CNY',
                'period': {
                    'start_date': start_date.isoformat(),
                    'end_date': end_date.isoformat()
                }
            }
        })        
        
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@bp.route('/reports/financial', methods=['GET'])
def get_financial_report():
    """获取财务报表数据"""
    try:
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        report_type = request.args.get('report_type', 'monthly')
        
        # 默认范围由报表类型决定
        if not start_date or not end_date:
            start_date, end_date = _get_default_report_range(report_type)
        else:
            start_date = datetime.strptime(start_date, '%Y-%m-%d').date()
            end_date = datetime.strptime(end_date, '%Y-%m-%d').date()
        
        # 根据报表类型生成数据
        financial_data = []
        
        if report_type == 'daily':
            # 按日统计
            current_date = start_date
            while current_date <= end_date:
                daily_data = _get_daily_financial_data(current_date)
                financial_data.append(daily_data)
                current_date += timedelta(days=1)
        elif report_type == 'weekly':
            # 按周统计（周一到周日）
            current_date = start_date
            while current_date <= end_date:
                week_end = min(current_date + timedelta(days=(6 - current_date.weekday())), end_date)
                weekly_data = _get_weekly_financial_data(current_date, week_end)
                financial_data.append(weekly_data)
                current_date = week_end + timedelta(days=1)
        elif report_type == 'monthly':
            # 按月统计
            current_date = start_date.replace(day=1)
            while current_date <= end_date:
                monthly_data = _get_monthly_financial_data(current_date.year, current_date.month)
                financial_data.append(monthly_data)
                # 移动到下个月
                if current_date.month == 12:
                    current_date = current_date.replace(year=current_date.year + 1, month=1)
                else:
                    current_date = current_date.replace(month=current_date.month + 1)
        elif report_type == 'quarterly':
            # 按季度统计
            cursor = start_date
            while cursor <= end_date:
                quarter_start = _get_quarter_start_date(cursor)
                quarter_end = _get_quarter_end_date(cursor)
                period_start = max(quarter_start, start_date)
                period_end = min(quarter_end, end_date)
                quarter_index = ((quarter_start.month - 1) // 3) + 1
                financial_data.append(_get_quarterly_financial_data(quarter_start.year, quarter_index, period_start, period_end))
                cursor = quarter_end + timedelta(days=1)
        elif report_type == 'yearly':
            # 按年统计
            year = start_date.year
            while year <= end_date.year:
                year_start = datetime(year, 1, 1).date()
                year_end = datetime(year, 12, 31).date()
                period_start = max(year_start, start_date)
                period_end = min(year_end, end_date)
                financial_data.append(_get_yearly_financial_data(year, period_start, period_end))
                year += 1
        
        # 计算汇总数据
        total_income = float(sum(item['income'] for item in financial_data))
        total_expense = float(sum(item['expense'] for item in financial_data))
        total_profit = total_income - total_expense
        profit_margin = (total_profit / total_income * 100) if total_income > 0 else 0
        
        return jsonify({
            'success': True,
            'data': {
                'financial_data': financial_data,
                'summary': {
                    'total_income': round(total_income, 2),
                    'total_expense': round(total_expense, 2),
                    'total_profit': round(total_profit, 2),
                    'profit_margin': round(profit_margin, 2)
                }
            }
        })
        
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"Error in get_financial_report: {str(e)}")
        print(f"Traceback: {error_details}")
        return jsonify({
            'success': False,
            'message': str(e),
            'traceback': error_details
        }), 500

def _get_daily_financial_data(date):
    """获取指定日期的财务数据"""
    return _get_period_financial_data(date, date, date.isoformat())


def _get_weekly_financial_data(start_date, end_date):
    """获取指定周区间的财务数据"""
    label = f'{start_date.isoformat()} ~ {end_date.isoformat()}'
    return _get_period_financial_data(start_date, end_date, label)

def _get_monthly_financial_data(year, month):
    """获取指定月份的财务数据"""
    # 计算月份的开始和结束日期
    start_date = datetime(year, month, 1).date()
    if month == 12:
        end_date = datetime(year + 1, 1, 1).date() - timedelta(days=1)
    else:
        end_date = datetime(year, month + 1, 1).date() - timedelta(days=1)
    return _get_period_financial_data(start_date, end_date, f'{year}-{month:02d}')


def _get_quarterly_financial_data(year, quarter, start_date, end_date):
    """获取指定季度区间的财务数据"""
    return _get_period_financial_data(start_date, end_date, f'{year}-Q{quarter}')


def _get_yearly_financial_data(year, start_date, end_date):
    """获取指定年份区间的财务数据"""
    return _get_period_financial_data(start_date, end_date, str(year))

@bp.route('/reports/revenue-trend', methods=['GET'])
def get_revenue_trend():
    """获取收入趋势"""
    try:
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        
        if not start_date or not end_date:
            end_date = datetime.now().date()
            start_date = end_date - timedelta(days=30)
        else:
            start_date = datetime.strptime(start_date, '%Y-%m-%d').date()
            end_date = datetime.strptime(end_date, '%Y-%m-%d').date()

        order_start_dt, order_end_dt_exclusive = _build_order_datetime_range(start_date, end_date)
        
        # 按日期分组统计收入（统一换算为人民币）
        revenue_data = db.session.query(
            func.date(Order.created_at).label('date'),
            func.count(Order.id).label('order_count')
        ).filter(
            and_(Order.created_at >= order_start_dt, Order.created_at < order_end_dt_exclusive)
        ).group_by(func.date(Order.created_at)).order_by('date').all()
        
        trend_data = []
        for item in revenue_data:
            # 获取当天的所有订单
            daily_orders = db.session.query(Order).filter(
                func.date(Order.created_at) == item.date
            ).all()
            
            # 计算当天收入（人民币）- 只统计实际到账金额
            daily_revenue_cny = 0
            for order in daily_orders:
                # 只统计已支付且有实际到账金额的订单
                if (order.financial_status in ['paid', 'partially_paid'] and 
                    order.actual_received and order.currency):
                    if order.currency == 'CNY':
                        daily_revenue_cny += float(order.actual_received)
                    else:
                        # 将其他货币换算为人民币
                        daily_revenue_cny += float(exchange_rate_service.convert_to_cny(
                            float(order.actual_received), order.currency
                        ))
            
            trend_data.append({
                'date': str(item.date),
                'revenue': round(daily_revenue_cny, 2),
                'order_count': item.order_count,
                'currency': 'CNY'
            })
        
        return jsonify({
            'success': True,
            'data': trend_data,
            'currency': 'CNY'
        })
        
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@bp.route('/reports/expense-analysis', methods=['GET'])
def get_expense_analysis():
    """获取费用分析 - 按月份统计"""
    try:
        # 获取最近5个月的数据
        end_date = datetime.now().date()
        start_date = end_date - timedelta(days=150)  # 5个月
        
        # 查询日期范围已设置为5个月
        
        # 按月份统计费用（统一换算为人民币）
        monthly_expenses = db.session.query(
            func.strftime('%Y-%m', Expense.expense_date).label('month')
        ).filter(
            Expense.expense_date >= start_date,
            Expense.expense_date <= end_date
        ).group_by(func.strftime('%Y-%m', Expense.expense_date)).all()
        
        # 转换为图表数据格式
        expense_data = []
        for item in monthly_expenses:
            # 获取当月的所有费用
            month_expenses = db.session.query(Expense).filter(
                func.strftime('%Y-%m', Expense.expense_date) == item.month
            ).all()
            
            # 计算当月费用总额（人民币）
            monthly_total_cny = 0
            for expense in month_expenses:
                if expense.amount and expense.currency:
                    if expense.currency == 'CNY':
                        monthly_total_cny += float(expense.amount)
                    else:
                        # 将其他货币换算为人民币
                        monthly_total_cny += float(exchange_rate_service.convert_to_cny(
                            float(expense.amount), expense.currency
                        ))
            
            expense_data.append({
                'month': item.month,
                'value': round(monthly_total_cny, 2),
                'currency': 'CNY'
            })
            print(f"月份: {item.month}, 金额: {monthly_total_cny} CNY")  # 调试信息
        
        return jsonify({
            'success': True,
            'data': expense_data,
            'currency': 'CNY'
        })
        
    except Exception as e:
        print(f"费用分析API错误: {str(e)}")  # 调试信息
        return jsonify({'success': False, 'message': str(e)}), 500

@bp.route('/reports/profit-analysis', methods=['GET'])
def get_profit_analysis():
    """获取利润分析"""
    try:
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        
        if not start_date or not end_date:
            end_date = datetime.now().date()
            start_date = end_date - timedelta(days=30)
        else:
            start_date = datetime.strptime(start_date, '%Y-%m-%d').date()
            end_date = datetime.strptime(end_date, '%Y-%m-%d').date()
        
        # 按日期计算每日利润
        daily_profit = []
        current_date = start_date
        
        while current_date <= end_date:
            # 当日收入（统一换算为人民币）
            daily_orders = db.session.query(Order).filter(
                func.date(Order.created_at) == current_date
            ).all()
            
            daily_revenue_cny = 0
            for order in daily_orders:
                # 只统计已支付且有实际到账金额的订单
                if (order.financial_status in ['paid', 'partially_paid'] and 
                    order.actual_received and order.currency):
                    if order.currency == 'CNY':
                        daily_revenue_cny += float(order.actual_received)
                    else:
                        # 将其他货币换算为人民币
                        daily_revenue_cny += float(exchange_rate_service.convert_to_cny(
                            float(order.actual_received), order.currency
                        ))
            
            # 当日费用（统一换算为人民币）
            daily_expenses = db.session.query(Expense).filter(
                Expense.expense_date == current_date
            ).all()
            
            daily_expense_cny = 0
            for expense in daily_expenses:
                if expense.amount and expense.currency:
                    if expense.currency == 'CNY':
                        daily_expense_cny += float(expense.amount)
                    else:
                        # 将其他货币换算为人民币
                        daily_expense_cny += float(exchange_rate_service.convert_to_cny(
                            float(expense.amount), expense.currency
                        ))
            
            # 当日订单成本（物流+方果+其他，已经是人民币）
            daily_order_cost = db.session.query(
                func.sum(OrderCost.shipping_cost + OrderCost.fangguo_cost + OrderCost.other_cost)
            ).filter(
                and_(OrderCost.cost_date == current_date, OrderCost.status == 'confirmed')
            ).scalar() or 0
            
            daily_total_cost_cny = daily_expense_cny + float(daily_order_cost)
            daily_net_profit_cny = daily_revenue_cny - daily_total_cost_cny
            
            daily_profit.append({
                'date': current_date.isoformat(),
                'revenue': round(daily_revenue_cny, 2),
                'expenses': round(daily_total_cost_cny, 2),
                'profit': round(daily_net_profit_cny, 2),
                'margin': round((daily_net_profit_cny / daily_revenue_cny * 100) if daily_revenue_cny > 0 else 0, 2),
                'currency': 'CNY'
            })
            
            current_date += timedelta(days=1)
        
        return jsonify({
            'success': True,
            'data': daily_profit,
            'currency': 'CNY'
        })
        
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500
