from flask import render_template, request, jsonify, redirect, url_for
from app.main import bp
from app.models import Order, Expense, FeeConfig, Account, Recharge, Consumption
from app import db
from datetime import datetime, timedelta
from sqlalchemy import func, and_

@bp.route('/')
def index():
    """首页 - 重定向到仪表板"""
    return redirect(url_for('main.dashboard'))

@bp.route('/dashboard')
def dashboard():
    """仪表板"""
    # 获取今日数据
    today = datetime.now().date()
    
    # 今日订单统计
    today_orders = Order.query.filter(
        func.date(Order.order_date) == today
    ).all()
    
    today_revenue = sum([order.total_price or 0 for order in today_orders])
    today_profit = sum([order.gross_profit or 0 for order in today_orders])
    
    # 本月统计
    month_start = today.replace(day=1)
    month_orders = Order.query.filter(
        Order.order_date >= month_start
    ).all()
    
    month_revenue = sum([order.total_price or 0 for order in month_orders])
    month_profit = sum([order.gross_profit or 0 for order in month_orders])
    
    # 本月费用
    month_expenses = Expense.query.filter(
        Expense.expense_date >= month_start
    ).all()
    
    month_expense_total = sum([expense.amount or 0 for expense in month_expenses])
    
    stats = {
        'today': {
            'orders': len(today_orders),
            'revenue': float(today_revenue),
            'profit': float(today_profit)
        },
        'month': {
            'orders': len(month_orders),
            'revenue': float(month_revenue),
            'profit': float(month_profit),
            'expenses': float(month_expense_total)
        }
    }
    
    return render_template('dashboard.html', stats=stats)

@bp.route('/orders')
def orders():
    """订单列表"""
    page = request.args.get('page', 1, type=int)
    per_page = 20
    
    # 获取搜索参数
    search_order = request.args.get('order_id', '').strip()
    search_email = request.args.get('customer_email', '').strip()
    status = request.args.get('status', '').strip()
    start_date = request.args.get('start_date', '').strip()
    end_date = request.args.get('end_date', '').strip()
    
    # 如果没有提供日期，默认设置为当前月
    if not start_date or not end_date:
        today = datetime.now().date()
        if not start_date:
            start_date = today.replace(day=1).strftime('%Y-%m-%d')
        if not end_date:
            # 计算当月最后一天
            if today.month == 12:
                next_month = today.replace(year=today.year + 1, month=1, day=1)
            else:
                next_month = today.replace(month=today.month + 1, day=1)
            end_date = (next_month - timedelta(days=1)).strftime('%Y-%m-%d')
    
    # 构建查询
    query = Order.query
    
    # 应用搜索条件
    if search_order:
        query = query.filter(Order.order_number.like(f'%{search_order}%'))
    
    if search_email:
        query = query.filter(Order.customer_email.like(f'%{search_email}%'))
    
    if status:
        query = query.filter(Order.financial_status == status)
    
    # 应用日期筛选
    if start_date:
        try:
            start_date_obj = datetime.strptime(start_date, '%Y-%m-%d').date()
            query = query.filter(func.date(Order.order_date) >= start_date_obj)
        except ValueError:
            pass
    
    if end_date:
        try:
            end_date_obj = datetime.strptime(end_date, '%Y-%m-%d').date()
            query = query.filter(func.date(Order.order_date) <= end_date_obj)
        except ValueError:
            pass
    
    # 分页查询
    orders = query.order_by(Order.order_date.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )
    
    return render_template('orders.html', 
                         orders=orders, 
                         search_order=search_order, 
                         search_email=search_email,
                         status=status,
                         start_date=start_date,
                         end_date=end_date)

@bp.route('/expenses')
def expenses():
    """费用列表"""
    page = request.args.get('page', 1, type=int)
    per_page = 20
    
    expenses = Expense.query.order_by(Expense.expense_date.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )
    
    return render_template('expenses.html', expenses=expenses)

@bp.route('/reports')
def reports():
    """报表页面"""
    return render_template('reports.html')

@bp.route('/accounts')
def accounts():
    """账户管理"""
    return render_template('accounts.html')

@bp.route('/settings')
def settings():
    """系统设置"""
    fee_configs = FeeConfig.query.all()
    return render_template('settings.html', fee_configs=fee_configs)