from flask import Blueprint

bp = Blueprint("api", __name__)

from . import (
    shops,
    users,
    orders,
    sync,
    accounts,
    order_costs,
    expenses,
    reports,
    settings,
    auth,
    platform_accounts,
    purchases,
    inventory,
    shipping,
    after_sales,
    mail,
    products,
    logistics,
    ai,
)
