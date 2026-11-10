"""Reporting service.

Replaces the previous "query once per day / per month" pattern with a single
range query plus in-memory grouping, so a 30-day report costs a handful of
queries instead of a hundred.
"""

from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import and_, func

from app import db
from app.models import AfterSaleCost, Expense, Order, OrderCost
from app.services.exchange_rate_service import exchange_rate_service
from app.utils.errors import ValidationError
from app.utils.security import apply_shop_scope
from app.utils.timezone import beijing_date_from_utc, beijing_day_bounds_utc

DEFAULT_TREND_DAYS = 30
EXPENSE_ANALYSIS_DAYS = 150
BEIJING_OFFSET = timedelta(hours=8)

# 报表只用到订单的这几个字段。订单表里有 fulfillments / shipping_address
# 这类 JSON 大字段，整个实体查出来会白白多读很多数据。
_ORDER_REPORT_COLUMNS = (
    Order.id,
    Order.order_date,
    Order.financial_status,
    Order.settlement_amount,
    Order.settlement_currency,
    Order.actual_received,
    Order.currency,
)


def _beijing_today():
    return (datetime.utcnow() + BEIJING_OFFSET).date()


def _parse_range(args, default_days=DEFAULT_TREND_DAYS):
    """Read start_date/end_date from query params, defaulting to the last N days."""
    start_raw = args.get("start_date")
    end_raw = args.get("end_date")

    if not start_raw or not end_raw:
        end_date = _beijing_today()
        start_date = end_date - timedelta(days=default_days)
        return start_date, end_date

    try:
        start_date = datetime.strptime(start_raw, "%Y-%m-%d").date()
        end_date = datetime.strptime(end_raw, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValidationError("日期格式错误，应为 YYYY-MM-DD") from exc

    return start_date, end_date


def _order_datetime_range(start_date, end_date):
    return beijing_day_bounds_utc(start_date, end_date)


def _income_cny(order):
    """Recognised income in CNY for one order."""
    if order.financial_status not in ("paid", "partially_paid", "partially_refunded", "refunded"):
        return 0.0
    amount = order.settlement_amount if order.settlement_amount is not None else order.actual_received
    currency = order.settlement_currency if order.settlement_amount is not None else order.currency
    if amount is None or not currency:
        return 0.0
    # 尚未补齐退款流水的旧数据不能把退款前的正向收款算进报表；
    # 网关已确认的负向净额（不可退手续费）则要保留为亏损。
    if order.financial_status == "refunded" and amount > 0:
        return 0.0
    if currency == "CNY":
        return float(amount)
    return float(exchange_rate_service.convert_to_cny(float(amount), currency))


def _expense_cny(expense):
    """Expense amount converted to CNY."""
    if not expense.amount or not expense.currency:
        return 0.0
    if expense.currency == "CNY":
        return float(expense.amount)
    return float(exchange_rate_service.convert_to_cny(float(expense.amount), expense.currency))


def _orders_by_date(start_dt, end_dt):
    """All orders in range, grouped by Beijing business date (single query)."""
    orders = apply_shop_scope(
        db.session.query(*_ORDER_REPORT_COLUMNS)
        .filter(and_(Order.order_date >= start_dt, Order.order_date < end_dt)),
        Order,
    ).all()

    grouped = defaultdict(list)
    for order in orders:
        if order.order_date:
            grouped[beijing_date_from_utc(order.order_date)].append(order)
    return grouped


def _expenses_by_date(start_date, end_date):
    expenses = apply_shop_scope(
        db.session.query(Expense).filter(
            and_(
                Expense.expense_date >= start_date,
                Expense.expense_date <= end_date,
                Expense.record_type == Expense.TYPE_EXPENSE,
            )
        ),
        Expense,
    ).all()

    grouped = defaultdict(list)
    for expense in expenses:
        if expense.expense_date:
            grouped[expense.expense_date].append(expense)
    return grouped


def _income_records_by_date(start_date, end_date):
    """Extra income records (record_type='income') grouped by date."""
    records = apply_shop_scope(
        db.session.query(Expense).filter(
            and_(
                Expense.expense_date >= start_date,
                Expense.expense_date <= end_date,
                Expense.record_type == Expense.TYPE_INCOME,
            )
        ),
        Expense,
    ).all()

    grouped = defaultdict(list)
    for record in records:
        if record.expense_date:
            grouped[record.expense_date].append(record)
    return grouped


def _confirmed_order_cost_by_date(start_date, end_date):
    """Confirmed order costs (already CNY) grouped by cost date (single query)."""
    rows = (
        apply_shop_scope(
            db.session.query(
                OrderCost.cost_date,
                func.sum(OrderCost.shipping_cost + OrderCost.fangguo_cost + OrderCost.other_cost),
            ).filter(
                and_(
                    OrderCost.cost_date >= start_date,
                    OrderCost.cost_date <= end_date,
                    OrderCost.status == "confirmed",
                )
            ),
            OrderCost,
        )
        .group_by(OrderCost.cost_date)
        .all()
    )

    by_day = {cost_date: float(total or 0) for cost_date, total in rows}
    start_dt, end_dt = _order_datetime_range(start_date, end_date)
    after_sale_costs = apply_shop_scope(
        db.session.query(AfterSaleCost).filter(
            AfterSaleCost.incurred_at >= start_dt, AfterSaleCost.incurred_at < end_dt,
        ), AfterSaleCost,
    ).all()
    for cost in after_sale_costs:
        day = beijing_date_from_utc(cost.incurred_at)
        by_day[day] = by_day.get(day, 0.0) + float(cost.amount_cny)
    return by_day


def revenue_trend(args):
    """Daily revenue (CNY) and order count for the selected range."""
    start_date, end_date = _parse_range(args)
    start_dt, end_dt = _order_datetime_range(start_date, end_date)
    grouped = _orders_by_date(start_dt, end_dt)
    extra_income = _income_records_by_date(start_date, end_date)

    trend = []
    for day in sorted(set(grouped) | set(extra_income)):
        orders = grouped.get(day, [])
        revenue = sum(_income_cny(order) for order in orders)
        revenue += sum(_expense_cny(record) for record in extra_income.get(day, []))
        trend.append(
            {
                "date": str(day),
                "revenue": round(revenue, 2),
                "order_count": len(orders),
                "extra_income": round(sum(_expense_cny(r) for r in extra_income.get(day, [])), 2),
                "currency": "CNY",
            }
        )
    return trend


def expense_analysis(args=None):
    """Monthly expense totals (CNY) for the last 150 days."""
    args = args or {}
    start_raw = args.get("start_date")
    end_raw = args.get("end_date")

    if start_raw and end_raw:
        start_date, end_date = _parse_range(args, EXPENSE_ANALYSIS_DAYS)
    else:
        end_date = _beijing_today()
        start_date = end_date - timedelta(days=EXPENSE_ANALYSIS_DAYS)

    expenses = apply_shop_scope(
        db.session.query(Expense).filter(
            and_(
                Expense.expense_date >= start_date,
                Expense.expense_date <= end_date,
                Expense.record_type == Expense.TYPE_EXPENSE,
            )
        ),
        Expense,
    ).all()

    monthly = defaultdict(float)
    for expense in expenses:
        if not expense.expense_date:
            continue
        monthly[expense.expense_date.strftime("%Y-%m")] += _expense_cny(expense)

    return [
        {"month": month, "value": round(total, 2), "currency": "CNY"}
        for month, total in sorted(monthly.items())
    ]


def profit_analysis(args):
    """Daily revenue / cost / profit breakdown for the selected range."""
    start_date, end_date = _parse_range(args)
    start_dt, end_dt = _order_datetime_range(start_date, end_date)

    orders_by_day = _orders_by_date(start_dt, end_dt)
    expenses_by_day = _expenses_by_date(start_date, end_date)
    income_records_by_day = _income_records_by_date(start_date, end_date)
    order_costs_by_day = _confirmed_order_cost_by_date(start_date, end_date)

    daily_profit = []
    current_date = start_date
    while current_date <= end_date:
        revenue = sum(_income_cny(order) for order in orders_by_day.get(current_date, []))
        revenue += sum(_expense_cny(r) for r in income_records_by_day.get(current_date, []))
        expense = sum(_expense_cny(item) for item in expenses_by_day.get(current_date, []))
        total_cost = expense + order_costs_by_day.get(current_date, 0.0)
        profit = revenue - total_cost

        daily_profit.append(
            {
                "date": current_date.isoformat(),
                "revenue": round(revenue, 2),
                "expenses": round(total_cost, 2),
                "profit": round(profit, 2),
                "margin": round((profit / revenue * 100) if revenue > 0 else 0, 2),
                "currency": "CNY",
            }
        )
        current_date += timedelta(days=1)

    return daily_profit


def daily_financial_series(start_date, end_date):
    """Daily income / expense / profit rows using prefetched data.

    Same numbers as the old per-day implementation, but with one range query
    per data source instead of one query set per day.
    """
    start_dt, end_dt = _order_datetime_range(start_date, end_date)
    orders_by_day = _orders_by_date(start_dt, end_dt)
    expenses_by_day = _expenses_by_date(start_date, end_date)
    income_records_by_day = _income_records_by_date(start_date, end_date)
    order_costs_by_day = _confirmed_order_cost_by_date(start_date, end_date)

    rows = []
    current_date = start_date
    while current_date <= end_date:
        income = sum(_income_cny(order) for order in orders_by_day.get(current_date, []))
        income += sum(_expense_cny(r) for r in income_records_by_day.get(current_date, []))
        expense = sum(_expense_cny(item) for item in expenses_by_day.get(current_date, []))
        expense += order_costs_by_day.get(current_date, 0.0)

        rows.append(
            {
                "date": current_date.isoformat(),
                "income": round(income, 2),
                "expense": round(expense, 2),
                "profit": round(income - expense, 2),
            }
        )
        current_date += timedelta(days=1)

    return rows


# ---------------------------------------------------------------------------
# 财务报表：概览 + 多周期（日/周/月/季/年）
# ---------------------------------------------------------------------------

REPORT_TYPES = ("daily", "weekly", "monthly", "quarterly", "yearly")


def _month_start(day):
    return day.replace(day=1)


def _month_end(day):
    if day.month == 12:
        return datetime(day.year + 1, 1, 1).date() - timedelta(days=1)
    return datetime(day.year, day.month + 1, 1).date() - timedelta(days=1)


def _default_report_range(report_type):
    """Default date range per report type (same rules as before)."""
    today = _beijing_today()
    if report_type == "daily":
        return _month_start(today), today
    if report_type == "weekly":
        return _month_start(today), today
    if report_type == "monthly":
        start = _month_start(today)
        for _ in range(5):
            start = (start - timedelta(days=1)).replace(day=1)
        return start, today
    if report_type in ("quarterly", "yearly"):
        return datetime(today.year, 1, 1).date(), today
    return today - timedelta(days=30), today


def _quarter_start(day):
    return day.replace(month=((day.month - 1) // 3) * 3 + 1, day=1)


def _quarter_end(day):
    start = _quarter_start(day)
    if start.month == 10:
        next_start = datetime(start.year + 1, 1, 1).date()
    else:
        next_start = datetime(start.year, start.month + 3, 1).date()
    return next_start - timedelta(days=1)


def _periods(report_type, start_date, end_date):
    """[(label, period_start, period_end)] covering the requested range."""
    periods = []
    if report_type == "daily":
        cursor = start_date
        while cursor <= end_date:
            periods.append((cursor.isoformat(), cursor, cursor))
            cursor += timedelta(days=1)
    elif report_type == "weekly":
        cursor = start_date
        while cursor <= end_date:
            week_end = min(cursor + timedelta(days=6 - cursor.weekday()), end_date)
            periods.append((f"{cursor.isoformat()} ~ {week_end.isoformat()}", cursor, week_end))
            cursor = week_end + timedelta(days=1)
    elif report_type == "monthly":
        cursor = _month_start(start_date)
        while cursor <= end_date:
            period_start = max(cursor, start_date)
            period_end = min(_month_end(cursor), end_date)
            periods.append((f"{cursor.year}-{cursor.month:02d}", period_start, period_end))
            cursor = _month_end(cursor) + timedelta(days=1)
    elif report_type == "quarterly":
        cursor = start_date
        while cursor <= end_date:
            q_start, q_end = _quarter_start(cursor), _quarter_end(cursor)
            periods.append(
                (
                    f"{q_start.year}Q{((q_start.month - 1) // 3) + 1}",
                    max(q_start, start_date),
                    min(q_end, end_date),
                )
            )
            cursor = q_end + timedelta(days=1)
    else:  # yearly
        year = start_date.year
        while year <= end_date.year:
            periods.append(
                (
                    str(year),
                    max(datetime(year, 1, 1).date(), start_date),
                    min(datetime(year, 12, 31).date(), end_date),
                )
            )
            year += 1
    return periods


def _sum_range(by_day, period_start, period_end, converter):
    total = 0.0
    for day, items in by_day.items():
        if period_start <= day <= period_end:
            total += sum(converter(item) for item in items)
    return total


def _financial_series(report_type, start_date, end_date):
    """Income/expense/profit rows per period, from a single prefetch each."""
    start_dt, end_dt = _order_datetime_range(start_date, end_date)
    orders_by_day = _orders_by_date(start_dt, end_dt)
    expenses_by_day = _expenses_by_date(start_date, end_date)
    income_by_day = _income_records_by_date(start_date, end_date)
    costs_by_day = _confirmed_order_cost_by_date(start_date, end_date)

    rows = []
    for label, period_start, period_end in _periods(report_type, start_date, end_date):
        income = _sum_range(orders_by_day, period_start, period_end, _income_cny)
        income += _sum_range(income_by_day, period_start, period_end, _expense_cny)
        expense = _sum_range(expenses_by_day, period_start, period_end, _expense_cny)
        expense += sum(amount for day, amount in costs_by_day.items() if period_start <= day <= period_end)
        rows.append(
            {
                "date": label,
                "income": round(income, 2),
                "expense": round(expense, 2),
                "profit": round(income - expense, 2),
            }
        )
    return rows


def financial_summary(args):
    """Overview for the selected range (defaults to the current month)."""
    start_raw = args.get("start_date")
    end_raw = args.get("end_date")

    if not start_raw or not end_raw:
        today = _beijing_today()
        start_date, end_date = _month_start(today), _month_end(today)
    else:
        start_date = _parse_date_arg(start_raw, "开始日期")
        end_date = _parse_date_arg(end_raw, "结束日期")

    start_dt, end_dt = _order_datetime_range(start_date, end_date)
    orders_by_day = _orders_by_date(start_dt, end_dt)
    income_by_day = _income_records_by_date(start_date, end_date)
    expenses_by_day = _expenses_by_date(start_date, end_date)
    costs_by_day = _confirmed_order_cost_by_date(start_date, end_date)

    order_income = _sum_range(orders_by_day, start_date, end_date, _income_cny)
    extra_income = _sum_range(income_by_day, start_date, end_date, _expense_cny)
    total_revenue = order_income + extra_income

    expenses = _sum_range(expenses_by_day, start_date, end_date, _expense_cny)
    expenses += sum(costs_by_day.values())

    profit = total_revenue - expenses
    order_count = sum(len(items) for items in orders_by_day.values())

    return {
        "total_revenue": round(total_revenue, 2),
        "order_income": round(order_income, 2),
        "extra_income": round(extra_income, 2),
        "total_expenses": round(expenses, 2),
        "total_profit": round(profit, 2),
        "profit_margin": round(profit / total_revenue * 100, 2) if total_revenue else 0,
        "order_count": order_count,
        "currency": "CNY",
        "period": {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        },
    }


def financial_report(args):
    """Per-period (daily/weekly/monthly/quarterly/yearly) financial rows."""
    report_type = args.get("report_type", "daily")
    if report_type not in REPORT_TYPES:
        raise ValidationError("报表类型不支持，可选 daily/weekly/monthly/quarterly/yearly")

    start_raw = args.get("start_date")
    end_raw = args.get("end_date")
    if not start_raw or not end_raw:
        start_date, end_date = _default_report_range(report_type)
    else:
        start_date = _parse_date_arg(start_raw, "开始日期")
        end_date = _parse_date_arg(end_raw, "结束日期")

    rows = _financial_series(report_type, start_date, end_date)
    total_income = sum(row["income"] for row in rows)
    total_expense = sum(row["expense"] for row in rows)
    total_profit = total_income - total_expense

    return {
        "report_type": report_type,
        "financial_data": rows,
        "summary": {
            "total_income": round(total_income, 2),
            "total_expense": round(total_expense, 2),
            "total_profit": round(total_profit, 2),
            "profit_margin": round(total_profit / total_income * 100, 2) if total_income else 0,
        },
    }


def _parse_date_arg(value, field):
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValidationError(f"{field}格式错误，应为 YYYY-MM-DD") from exc
