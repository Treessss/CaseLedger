"""网关真实手续费解析（纯函数，不碰数据库）。

背景：
    Shopify 订单交易的顶层 ``fee`` / ``net`` 字段一直是空的（null），
    但 PayPal 支付的交易回执 ``receipt`` 里其实带着完整明细：::

        seller_receivable_breakdown = {
            gross_amount:      {currency_code, value}   # 买家毛额
            paypal_fee:        {currency_code, value}   # 真实手续费
            net_amount:        {currency_code, value}   # 真实净额
            receivable_amount: {currency_code, value}   # 换汇后到账
            exchange_rate:     {source_currency, target_currency, value}
        }

    回执有两种形态：
        - ``capture`` 交易：receipt 本身就是带 breakdown 的对象
        - ``sale`` 交易：receipt 是 PayPal order，breakdown 埋在
          ``purchase_units[].payments.captures[]`` 里

    shopify-python 会把回执包成 Resource 对象（嵌套层同样是 Resource），
    所以先用 :func:`to_plain` 递归还原成普通 dict 再解析。
"""

from decimal import Decimal, InvalidOperation

from app.utils.shopify_resources import to_plain

__all__ = ["to_plain", "extract_paypal_breakdown"]


def _decimal(value):
    """宽松转 Decimal，失败返回 None。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _amount(node):
    """``{currency_code, value}`` -> ``(Decimal | None, str | None)``。"""
    if not isinstance(node, dict):
        return None, None
    return _decimal(node.get("value")), node.get("currency_code")


def extract_paypal_breakdown(receipt):
    """从 PayPal 交易回执里取出真实费用明细。

    返回 ``None`` 表示这笔交易没有费用信息（例如只授权未捕获的 authorization）。
    """
    plain = to_plain(receipt)
    if not isinstance(plain, dict):
        return None

    candidates = []
    if isinstance(plain.get("seller_receivable_breakdown"), dict):
        candidates.append((plain, plain.get("id")))
    for purchase_unit in plain.get("purchase_units") or []:
        if not isinstance(purchase_unit, dict):
            continue
        payments = purchase_unit.get("payments") or {}
        for capture in payments.get("captures") or []:
            if isinstance(capture, dict) and isinstance(capture.get("seller_receivable_breakdown"), dict):
                candidates.append((capture, capture.get("id")))

    for source, capture_id in candidates:
        breakdown = source["seller_receivable_breakdown"]
        gross, gross_currency = _amount(breakdown.get("gross_amount"))
        fee, fee_currency = _amount(breakdown.get("paypal_fee"))
        net, net_currency = _amount(breakdown.get("net_amount"))
        if fee is None:
            continue

        receivable, receivable_currency = _amount(breakdown.get("receivable_amount"))
        rate_node = breakdown.get("exchange_rate")
        rate = _decimal(rate_node.get("value")) if isinstance(rate_node, dict) else None

        reference = plain.get("id")
        return {
            "capture_id": str(capture_id) if capture_id else None,
            "gateway_reference": str(reference) if reference else None,
            "gross": gross,
            "gross_currency": gross_currency,
            "fee": fee,
            "fee_currency": fee_currency or gross_currency,
            "net": net,
            "net_currency": net_currency or fee_currency or gross_currency,
            "settlement_amount": receivable,
            "settlement_currency": receivable_currency,
            "exchange_rate": rate,
        }
    return None
