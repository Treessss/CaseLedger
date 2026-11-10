"""Report endpoints.

Thin routing layer over app.services.report_service; every report is scoped to
the shops the caller may access.
"""

from flask import request

from app.api import bp
from app.services import report_service
from app.utils.responses import ok


@bp.route("/reports/financial-summary", methods=["GET"])
def get_financial_summary():
    """Revenue / expense / profit overview for a date range."""
    return ok(report_service.financial_summary(request.args))


@bp.route("/reports/financial", methods=["GET"])
def get_financial_report():
    """Per-period financial report (daily/weekly/monthly/quarterly/yearly)."""
    return ok(report_service.financial_report(request.args))


@bp.route("/reports/revenue-trend", methods=["GET"])
def get_revenue_trend():
    """Daily revenue trend (orders plus extra income)."""
    return ok(report_service.revenue_trend(request.args), currency="CNY")


@bp.route("/reports/expense-analysis", methods=["GET"])
def get_expense_analysis():
    """Monthly expense totals for the last 150 days."""
    return ok(report_service.expense_analysis(request.args), currency="CNY")


@bp.route("/reports/profit-analysis", methods=["GET"])
def get_profit_analysis():
    """Daily revenue / cost / profit breakdown."""
    return ok(report_service.profit_analysis(request.args), currency="CNY")
