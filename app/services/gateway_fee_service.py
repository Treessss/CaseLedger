"""网关真实手续费服务。

把「网关实际扣了多少、到账多少」写回 payments 表，让利润不再依赖估算费率。

两条链路来源不同：
    - PayPal：费用藏在订单交易回执里（``extract_paypal_breakdown``）
    - Shopify Payments：费用在 Admin GraphQL ``balanceTransactions`` 里

统一口径（重要）：
    - ``real_fee`` / ``real_net``    网关原始数字，币种是 ``fee_currency``
    - ``total_fee`` / ``net_amount`` 已折合订单币种，供订单利润直接相减
    - ``fee_source``                 ``gateway`` 表示真实值，``estimated`` 表示费率表估算
"""

from decimal import Decimal, ROUND_HALF_UP

import requests

from app import db
from app.models import Order, Payment
from app.models.shop import Shop as ShopModel
from app.services.exchange_rate_service import exchange_rate_service
from app.services import shopify_graphql
from app.services.shopify_auth import API_VERSION, get_access_token
from app.utils.gateway_fees import extract_paypal_breakdown

PAGE_LIMIT = 250
SETTLEMENT_TYPES = ("charge", "refund", "adjustment", "dispute")

# Validated against Shopify Admin GraphQL 2026-07 and checked on a connected shop.
BALANCE_TRANSACTIONS_QUERY = """
query BalanceTransactions($first: Int!, $after: String) {
  shopifyPaymentsAccount {
    balanceTransactions(first: $first, after: $after, reverse: true) {
      pageInfo { hasNextPage endCursor }
      nodes {
        id type transactionDate sourceOrderTransactionId
        associatedOrder { id }
        amount { amount currencyCode }
        fee { amount currencyCode }
        net { amount currencyCode }
      }
    }
  }
}
"""


def _money(value, places="0.01"):
    """转 Decimal 并量化，None 原样返回。"""
    if value is None:
        return None
    return Decimal(str(value)).quantize(Decimal(places), rounding=ROUND_HALF_UP)


def _resolve_rate(
    fee_currency, order, *, rate_hint=None, settlement_currency=None, gross=None, gross_in_order=None
):
    """求「手续费币种 -> 订单币种」的汇率，取不到返回 None。

    优先级：同币种 → 回执里的换汇价 → 同一笔交易的毛额隐含汇率 → 汇率服务。
    完全退款时毛额可能为 0，严格校验后才使用隐含汇率。
    """
    order_currency = order.currency
    if not fee_currency or not order_currency:
        return None
    if fee_currency == order_currency:
        return Decimal("1")

    if rate_hint and settlement_currency == order_currency:
        return Decimal(str(rate_hint))

    # 结算流水与订单毛额是同一笔钱，两者相除就是隐含汇率（无需外部接口）
    if gross and gross_in_order and Decimal(str(gross)) > 0:
        implied = Decimal(str(gross_in_order)) / Decimal(str(gross))
        if Decimal("0.0001") < implied < Decimal("1000"):
            return implied

    if fee_currency.upper() in exchange_rate_service.get_supported_currencies():
        from app.services.exchange_rate_service import ExchangeRateUnavailableError

        try:
            return exchange_rate_service.get_exchange_rate(fee_currency, order_currency)
        except ExchangeRateUnavailableError:
            return None

    return None


def apply_gateway_fee(
    payment,
    order,
    *,
    fee,
    fee_currency,
    net=None,
    settlement_amount=None,
    settlement_currency=None,
    exchange_rate=None,
    capture_id=None,
    gateway_reference=None,
    amount_in_order_currency=None,
    gross=None,
    gross_in_order=None,
):
    """把网关真实费用写入支付记录，并折算出订单币种口径的手续费与净额。"""
    if fee is None:
        return False

    rate = _resolve_rate(
        fee_currency,
        order,
        rate_hint=exchange_rate,
        settlement_currency=settlement_currency,
        gross=gross,
        gross_in_order=gross_in_order,
    )
    if rate is None:
        return False

    fee_in_order_currency = _money(Decimal(str(fee)) * rate)
    payment.real_fee = _money(fee, "0.0001")
    payment.fee_currency = fee_currency
    payment.total_fee = fee_in_order_currency
    payment.fee_source = "gateway"
    if net is not None:
        payment.real_net = _money(net, "0.0001")
        payment.net_amount = _money(Decimal(str(net)) * rate)
    elif payment.amount_in_order_currency:
        payment.net_amount = _money(Decimal(str(payment.amount_in_order_currency)) - fee_in_order_currency)

    if amount_in_order_currency is not None:
        payment.amount_in_order_currency = _money(amount_in_order_currency)
    elif payment.amount is not None:
        payment.amount_in_order_currency = _amount_in_order_currency(payment, order, rate)
    if settlement_amount is not None:
        payment.settlement_amount = _money(settlement_amount)
    payment.settlement_currency = settlement_currency
    payment.exchange_rate = Decimal(str(exchange_rate)) if exchange_rate is not None else None
    if capture_id:
        payment.capture_id = str(capture_id)
    if gateway_reference:
        payment.gateway_reference = str(gateway_reference)

    order.payment_fee = fee_in_order_currency
    _refresh_order_settlement(order, payment)
    order.calculate_actual_received()
    order.calculate_profit()
    return True


def _refresh_order_settlement(order, current_payment=None):
    """把支付表中的真实到账汇总到订单表，供列表直接读取。

    同一订单只有在所有真实结算记录币种一致时才合计，避免把不同币种直接相加。
    ``current_payment`` 可能尚未 flush，因此显式并入集合。
    """
    try:
        payments = list(order.payments)
    except Exception:
        payments = []
    if current_payment is not None and current_payment not in payments:
        payments.append(current_payment)

    settlements = [
        item
        for item in payments
        if item.fee_source == "gateway" and item.settlement_amount is not None and item.settlement_currency
    ]
    currencies = {item.settlement_currency for item in settlements}
    if not settlements or len(currencies) != 1:
        return

    currency = currencies.pop()
    settlement = _money(sum((Decimal(str(item.settlement_amount)) for item in settlements), Decimal("0")))
    remaining = order.refund_adjusted_total()
    if remaining is not None:
        gateway_net = order.get_gateway_net_amount()
        order_fee = Decimal(str(order.payment_fee or 0))
        expected_net = remaining - order_fee
        # 只有网关净额尚未反映退款时才估算；已入账的负向流水不能再扣一次。
        if gateway_net is not None and gateway_net > expected_net:
            captures = [
                item
                for item in payments
                if item.fee_source == "gateway"
                and item.payment_method != "shopify_refund"
                and item.amount is not None
            ]
            original_gross = sum(
                (
                    Decimal(str(item.amount_in_order_currency))
                    if item.amount_in_order_currency is not None
                    else Decimal(str(item.amount))
                    for item in captures
                    if item.amount_in_order_currency is not None or item.currency == order.currency
                ),
                Decimal("0"),
            )
            refund_due = max(original_gross - remaining, Decimal("0"))
            if original_gross > 0 and refund_due > 0:
                # 网关原币中原始毛额 = 原始净入账 + 手续费。
                # 用同一笔收款的隐含汇率估算尚未结算的退款，避免混用 USD/HKD。
                original_settlement_gross = settlement + sum(
                    (Decimal(str(item.real_fee or 0)) for item in captures if item.fee_currency == currency),
                    Decimal("0"),
                )
                refund_settlement = refund_due * original_settlement_gross / original_gross
                settlement = _money(settlement - refund_settlement)

    order.settlement_currency = currency
    order.settlement_amount = settlement


def _amount_in_order_currency(payment, order, fee_rate):
    """把「买家实付金额」折合订单币种。

    优先用订单自身的折算比例（Shopify 记账用的官方汇率），
    这样「实付折合」和订单总额口径一致，不会冒出第三个汇率源；
    取不到才退回外部汇率。
    """
    amount = Decimal(str(payment.amount))
    if payment.currency and order.currency and payment.currency == order.currency:
        return _money(amount)

    try:
        siblings = [item for item in order.payments if item.currency == payment.currency]
        sibling_total = sum(
            (Decimal(str(item.amount)) for item in siblings if item.amount is not None), Decimal("0")
        )
    except Exception:
        sibling_total = Decimal("0")
    if sibling_total > 0 and order.total_price:
        return _money(Decimal(str(order.total_price)) * amount / sibling_total)

    return _money(amount * fee_rate)


def _shop_api_root(shop):
    return f"https://{ShopModel.normalize_shop_url(shop.shop_url)}/admin/api/{API_VERSION}"


def apply_estimated_fee(payment, order, fee, fee_currency):
    """按订单币种写入估算手续费；不同币种绝不按 1:1 默算。"""
    if fee is None:
        return False

    rate = None
    if fee_currency == order.currency:
        rate = Decimal("1")
    elif (
        fee_currency == payment.currency
        and payment.amount_in_order_currency is not None
        and payment.amount is not None
        and Decimal(str(payment.amount)) > 0
    ):
        # Shopify 同一笔交易同时给出买家扣款和店铺币种金额，比市场参考价更贴近实际。
        implied = Decimal(str(payment.amount_in_order_currency)) / Decimal(str(payment.amount))
        if Decimal("0.0001") < implied < Decimal("1000"):
            rate = implied
    if rate is None:
        rate = _resolve_rate(fee_currency, order)
    if rate is None:
        return False

    fee = Decimal(str(fee))
    payment.total_fee = _money(fee * rate)
    payment.fee_source = "estimated"
    payment.fee_currency = fee_currency or payment.fee_currency
    if payment.amount is not None:
        # Shopify 交易给出的 shopMoney 是原始店铺币种金额；退款后订单总额
        # 可能变化，不要用当前订单总额或外部汇率覆盖这个原始数。
        if payment.amount_in_order_currency is None:
            payment.amount_in_order_currency = _money(Decimal(str(payment.amount)) * rate)
        payment.net_amount = _money(payment.amount_in_order_currency - payment.total_fee)

    order.payment_fee = payment.total_fee
    order.calculate_actual_received()
    order.calculate_profit()
    return True


def _paginate(url, headers, params, limit=None, stop_when=None):
    """按 Link 头翻页，逐页 yield 记录列表（带重试，Shopify 偶发断连）。

    ``stop_when(page)`` 返回 True 时提前结束翻页。结算流水接口是按
    ``processed_at`` 倒序返回的，所以「整页都比同步窗口旧」就可以停，
    不必把 8000+ 条历史流水全拉一遍。
    """
    import time

    fetched = 0
    while url:
        response = None
        last_error = None
        for attempt in range(4):
            try:
                response = requests.get(url, headers=headers, params=params, timeout=60)
            except requests.RequestException as exc:
                last_error = exc
                time.sleep(1.5 * (attempt + 1))
                continue
            if response.status_code in (429, 500, 502, 503, 504):
                last_error = RuntimeError(f"HTTP {response.status_code}")
                time.sleep(1.5 * (attempt + 1))
                continue
            break
        if response is None:
            raise RuntimeError(f"请求失败: {last_error}")
        if response.status_code != 200:
            raise RuntimeError(f"HTTP {response.status_code}: {response.text[:200]}")
        page = response.json().get("transactions")
        if page is None:
            page = response.json().get("orders", [])
        if not page:
            return
        yield page
        fetched += len(page)
        if stop_when is not None and stop_when(page):
            return
        if limit and fetched >= limit:
            return

        next_url = None
        for part in response.headers.get("Link", "").split(","):
            if 'rel="next"' in part:
                next_url = part.split(";")[0].strip().strip("<>")
                break
        if not next_url:
            return
        url, _, query = next_url.partition("?")
        params = dict(item.split("=", 1) for item in query.split("&") if "=" in item)


def sync_shopify_payments_charges(shop, since_date=None, limit=None):
    """拉取 Shopify Payments 结算流水，把真实手续费/净额写回支付记录。

    ``since_date`` 传 ``date`` 或 ``datetime``，按交易日期过滤（用于增量同步）。
    """
    stats = {
        "fetched": 0,
        "matched": 0,
        "updated": 0,
        "unmatched": 0,
        "types": {},
        "currency": None,
    }

    # GraphQL reverse:true 按交易日期倒序返回，整页都比窗口旧即可停下。
    since_key = str(since_date)[:10] if since_date else None

    # 同一个订单可能有多条流水（收款 + 退款 + 争议），先按订单聚合
    aggregated = {}
    recent_non_charge = False
    cursor = None
    while True:
        remaining = limit - stats["fetched"] if limit else PAGE_LIMIT
        if remaining <= 0:
            break
        data = shopify_graphql._execute(
            shop, BALANCE_TRANSACTIONS_QUERY, {"first": min(PAGE_LIMIT, remaining), "after": cursor}
        )
        account = data.get("shopifyPaymentsAccount") or {}
        connection = account.get("balanceTransactions") or {}
        page = connection.get("nodes") or []
        if not page:
            break
        for item in page:
            stats["fetched"] += 1
            item_type = str(item.get("type") or "").lower()
            stats["types"][item_type] = stats["types"].get(item_type, 0) + 1
            money = item.get("amount") or {}
            currency = money.get("currencyCode")
            stats["currency"] = currency or stats["currency"]

            associated_order = item.get("associatedOrder") or {}
            order_gid = associated_order.get("id")
            order_id = str(order_gid).rsplit("/", 1)[-1] if order_gid else None
            if not order_id or item_type not in SETTLEMENT_TYPES:
                continue
            processed_at = item.get("transactionDate") or ""
            if since_date and processed_at[:10] < str(since_date)[:10]:
                continue
            if since_date and item_type in ("refund", "adjustment", "dispute"):
                recent_non_charge = True

            bucket = aggregated.setdefault(
                str(order_id),
                {
                    "amount": Decimal("0"),
                    "fee": Decimal("0"),
                    "net": Decimal("0"),
                    "currency": currency,
                },
            )
            for key in ("amount", "fee", "net"):
                value = (item.get(key) or {}).get("amount")
                if value is not None:
                    bucket[key] += Decimal(str(value))

        if since_key and all(
            item.get("transactionDate") and item["transactionDate"][:10] < since_key for item in page
        ):
            break
        page_info = connection.get("pageInfo") or {}
        cursor = page_info.get("endCursor")
        if not page_info.get("hasNextPage") or not cursor:
            break

    # A recent refund of an old order must be combined with its original charge.
    # Applying only the recent negative entry would overwrite the true cumulative net.
    if recent_non_charge:
        return sync_shopify_payments_charges(shop, since_date=None, limit=None)

    applied_since_commit = 0
    for order_id, bucket in aggregated.items():
        order = Order.query.filter_by(shop_id=shop.id, shopify_order_id=order_id).first()
        if order is None:
            stats["unmatched"] += 1
            continue
        stats["matched"] += 1

        payment = Payment.query.filter_by(order_id=order.id, payment_method="shopify_payment").first()
        if payment is None:
            stats["unmatched"] += 1
            continue

        applied = apply_gateway_fee(
            payment,
            order,
            fee=bucket["fee"],
            fee_currency=bucket["currency"],
            net=bucket["net"],
            gross=bucket["amount"],
            gross_in_order=order.total_price,
            settlement_amount=bucket["net"],
            settlement_currency=bucket["currency"],
        )
        if applied:
            stats["updated"] += 1
            applied_since_commit += 1
            # A full-history pass can touch thousands of orders. Commit in
            # batches so the shop/payment key-share locks never stay open for
            # minutes and block after-sale creation or warehouse writes.
            if applied_since_commit >= 100:
                db.session.commit()
                applied_since_commit = 0

    db.session.commit()
    return stats


def refresh_paypal_payments(shop, orders=None, limit=None, pause=0.35, progress=None, only_missing=False):
    """重新拉取 PayPal 订单的交易回执，写回真实手续费。

    已同步过的老订单不会带上回执费用，靠这个函数补数据。
    ``only_missing=True`` 时只处理还没有真实费用的订单（补漏用，快很多）。
    """
    import time

    headers = {"X-Shopify-Access-Token": get_access_token(shop)}
    api_root = _shop_api_root(shop)

    query = Order.query.filter(Order.payment_method == "paypal")
    if shop.id:
        query = query.filter(Order.shop_id == shop.id)
    if only_missing and orders is None:
        # 让「只补缺失」在数据库层生效：否则 limit 先截断、再过滤，
        # 最近 N 单都已经有真实费用时会一单都补不到，漏掉的旧单永远轮不上。
        query = query.filter(Order.payments.any(Payment.fee_source != "gateway"))
    query = query.order_by(Order.created_at.desc())
    if limit:
        query = query.limit(limit)
    targets = orders if orders is not None else query.all()
    if only_missing:
        targets = [
            order for order in targets if any(payment.fee_source != "gateway" for payment in order.payments)
        ]

    stats = {"orders": 0, "transactions": 0, "updated": 0, "skipped": 0, "failed": 0}
    for index, order in enumerate(targets, 1):
        stats["orders"] += 1
        try:
            response = requests.get(
                f"{api_root}/orders/{order.shopify_order_id}/transactions.json", headers=headers, timeout=30
            )
        except Exception:
            stats["failed"] += 1
            continue
        if response.status_code != 200:
            stats["failed"] += 1
            continue

        for transaction in response.json().get("transactions", []) or []:
            if "paypal" not in str(transaction.get("gateway", "")).lower():
                continue
            if transaction.get("kind") not in ("sale", "capture"):
                continue
            stats["transactions"] += 1
            breakdown = extract_paypal_breakdown(transaction.get("receipt"))
            if not breakdown:
                stats["skipped"] += 1
                continue

            payment = Payment.query.filter_by(
                order_id=order.id, transaction_id=str(transaction.get("id"))
            ).first()
            if payment is None:
                stats["skipped"] += 1
                continue

            applied = apply_gateway_fee(
                payment,
                order,
                fee=breakdown["fee"],
                fee_currency=breakdown["fee_currency"],
                net=breakdown["net"],
                settlement_amount=breakdown["settlement_amount"],
                settlement_currency=breakdown["settlement_currency"],
                exchange_rate=breakdown["exchange_rate"],
                capture_id=breakdown["capture_id"],
                gateway_reference=breakdown["gateway_reference"],
            )
            stats["updated"] += 1 if applied else 0
            stats["skipped"] += 0 if applied else 1

        if index % 25 == 0:
            db.session.commit()
            if progress:
                progress(index, len(targets))
        time.sleep(pause)

    db.session.commit()
    return stats
