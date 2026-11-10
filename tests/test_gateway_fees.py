"""网关真实手续费的解析与折算。"""

from datetime import datetime
from decimal import Decimal

import pytest

from app import db
from app.models import Order, Payment
from app.services.exchange_rate_service import ExchangeRateService
from app.services.gateway_fee_service import apply_estimated_fee, apply_gateway_fee
from app.utils.gateway_fees import extract_paypal_breakdown


CAPTURE_RECEIPT = {
    "status": "COMPLETED",
    "id": "92D062395F209463P",
    "seller_receivable_breakdown": {
        "gross_amount": {"currency_code": "USD", "value": "22.50"},
        "paypal_fee": {"currency_code": "USD", "value": "1.29"},
        "net_amount": {"currency_code": "USD", "value": "21.21"},
    },
}

SALE_RECEIPT = {
    "id": "0EG53675M39490103",
    "purchase_units": [
        {
            "payments": {
                "captures": [
                    {
                        "id": "4LB47763M21214149",
                        "seller_receivable_breakdown": {
                            "gross_amount": {"currency_code": "CAD", "value": "33.03"},
                            "paypal_fee": {"currency_code": "CAD", "value": "1.75"},
                            "net_amount": {"currency_code": "CAD", "value": "31.28"},
                            "receivable_amount": {"currency_code": "USD", "value": "21.65"},
                            "exchange_rate": {
                                "source_currency": "CAD",
                                "target_currency": "USD",
                                "value": "0.692132406704101",
                            },
                        },
                    }
                ]
            }
        }
    ],
}


class _Resource:
    """模拟 shopify-python 把回执包装成的 Resource 对象。"""

    def __init__(self, attributes):
        self.attributes = attributes


def test_extract_capture_style_receipt():
    breakdown = extract_paypal_breakdown(CAPTURE_RECEIPT)
    assert breakdown["fee"] == Decimal("1.29")
    assert breakdown["fee_currency"] == "USD"
    assert breakdown["net"] == Decimal("21.21")
    assert breakdown["capture_id"] == "92D062395F209463P"


def test_extract_sale_style_receipt():
    breakdown = extract_paypal_breakdown(SALE_RECEIPT)
    assert breakdown["fee"] == Decimal("1.75")
    assert breakdown["fee_currency"] == "CAD"
    assert breakdown["capture_id"] == "4LB47763M21214149"
    assert breakdown["settlement_amount"] == Decimal("21.65")
    assert breakdown["exchange_rate"] == Decimal("0.692132406704101")
    assert breakdown["gateway_reference"] == "0EG53675M39490103"


def test_extract_wraps_shopify_resource_objects():
    """shopify-python 会返回 Resource 对象（嵌套层也是），必须能解析。"""
    wrapped = _Resource(
        {
            "id": "0EG53675M39490103",
            "purchase_units": [
                _Resource(
                    {
                        "payments": _Resource(
                            {
                                "captures": [
                                    _Resource(SALE_RECEIPT["purchase_units"][0]["payments"]["captures"][0])
                                ]
                            }
                        )
                    }
                )
            ],
        }
    )
    breakdown = extract_paypal_breakdown(wrapped)
    assert breakdown is not None
    assert breakdown["fee"] == Decimal("1.75")


def test_extract_returns_none_without_breakdown():
    """只授权未捕获的交易没有费用明细。"""
    assert extract_paypal_breakdown({"id": "2TA10841GF4049833", "status": "COMPLETED"}) is None
    assert extract_paypal_breakdown(None) is None
    assert extract_paypal_breakdown("not-a-dict") is None


@pytest.fixture()
def paypal_order(app):
    order = Order(
        shop_id=1,
        shopify_order_id="GATEWAY-1",
        order_number="#G1",
        total_price=23.48,
        subtotal_price=23.48,
        currency="USD",
        financial_status="paid",
        order_date=datetime.utcnow(),
    )
    db.session.add(order)
    db.session.commit()
    return order


def test_apply_gateway_fee_converts_to_order_currency(app, paypal_order):
    """手续费按扣款币种记账，再用回执里的换汇价折合到订单币种。"""
    payment = Payment(
        order_id=paypal_order.id,
        payment_method="paypal",
        transaction_id="tx-1",
        amount=33.03,
        currency="CAD",
        status="success",
        payment_date=datetime.utcnow(),
    )
    db.session.add(payment)
    db.session.commit()

    applied = apply_gateway_fee(
        payment,
        paypal_order,
        fee=Decimal("1.75"),
        fee_currency="CAD",
        net=Decimal("31.28"),
        settlement_amount=Decimal("21.65"),
        settlement_currency="USD",
        exchange_rate=Decimal("0.692132406704101"),
        capture_id="4LB47763M21214149",
    )
    assert applied is True
    db.session.commit()

    assert payment.real_fee == Decimal("1.7500")
    assert payment.fee_source == "gateway"
    # 1.75 CAD × 0.6921 ≈ 1.21 USD
    assert payment.total_fee == Decimal("1.21")
    assert payment.net_amount == Decimal("21.65")
    assert paypal_order.payment_fee == Decimal("1.21")
    assert paypal_order.settlement_amount == Decimal("21.65")
    assert paypal_order.settlement_currency == "USD"
    # 有真实净额时，实际到账直接用网关数字
    assert paypal_order.actual_received == Decimal("21.65")
    assert paypal_order.gross_profit == Decimal("21.65")


def test_gateway_settlement_keeps_native_hkd_on_order(app, paypal_order):
    """订单级到账必须保存网关原始结算币种，不能先换成 USD。"""
    payment = Payment(
        order_id=paypal_order.id,
        payment_method="shopify_payment",
        transaction_id="tx-hkd",
        amount=23.48,
        currency="USD",
        status="success",
        payment_date=datetime.utcnow(),
    )
    db.session.add(payment)

    assert apply_gateway_fee(
        payment,
        paypal_order,
        fee=Decimal("8.20"),
        fee_currency="HKD",
        net=Decimal("175.30"),
        settlement_amount=Decimal("175.30"),
        settlement_currency="HKD",
        gross=Decimal("183.50"),
        gross_in_order=Decimal("23.48"),
    )
    assert paypal_order.settlement_amount == Decimal("175.30")
    assert paypal_order.settlement_currency == "HKD"


def test_estimated_fee_uses_shopify_transaction_rate(app, paypal_order, monkeypatch):
    """买家币种与店铺币种不同时，优先使用 Shopify 同笔交易的金额比例。"""
    payment = Payment(
        order_id=paypal_order.id,
        payment_method="shopify_payment",
        transaction_id="tx-aud",
        amount=Decimal("30.00"),
        amount_in_order_currency=Decimal("20.00"),
        currency="AUD",
        status="success",
        payment_date=datetime.utcnow(),
    )
    monkeypatch.setattr(
        "app.services.gateway_fee_service._resolve_rate",
        lambda *_: pytest.fail("Shopify transaction amount should avoid external FX lookup"),
    )

    assert apply_estimated_fee(payment, paypal_order, Decimal("3.00"), "AUD")
    assert payment.total_fee == Decimal("2.00")


def test_estimated_fee_refuses_identity_rate_for_other_currency(app, paypal_order, monkeypatch):
    payment = Payment(
        order_id=paypal_order.id,
        payment_method="shopify_payment",
        transaction_id="tx-no-rate",
        amount=Decimal("30.00"),
        currency="AUD",
        status="success",
        payment_date=datetime.utcnow(),
    )
    monkeypatch.setattr("app.services.gateway_fee_service._resolve_rate", lambda *_: None)

    assert not apply_estimated_fee(payment, paypal_order, Decimal("3.00"), "AUD")
    assert payment.total_fee is None


def test_zero_receipt_recalculates_profit_for_refunded_order(app, paypal_order):
    paypal_order.total_price = Decimal("0")
    paypal_order.payment_fee = Decimal("0")
    paypal_order.actual_received = Decimal("99")
    paypal_order.calculate_actual_received()
    assert paypal_order.actual_received == Decimal("0")

    paypal_order.actual_received = Decimal("0")
    paypal_order.product_cost = Decimal("8")
    paypal_order.shipping_cost = Decimal("2")
    paypal_order.gross_profit = Decimal("99")

    paypal_order.calculate_profit()

    assert paypal_order.gross_profit == Decimal("-10")
    assert paypal_order.profit_margin == 0


def test_measured_fee_estimate_marks_source(app, paypal_order):
    payment = Payment(
        order_id=paypal_order.id,
        payment_method="paypal",
        transaction_id="tx-2",
        amount=23.48,
        currency="USD",
        status="success",
        payment_date=datetime.utcnow(),
    )
    db.session.add(payment)
    db.session.commit()

    apply_estimated_fee(payment, paypal_order, Decimal("1.33"), "USD")
    assert payment.fee_source == "estimated"
    assert payment.total_fee == Decimal("1.33")
    assert payment.net_amount == Decimal("22.15")
    assert paypal_order.actual_received == Decimal("22.15")


def test_actual_received_falls_back_without_gateway_data(app, paypal_order):
    """没有真实净额时退回「订单金额 - 手续费」。"""
    paypal_order.total_price = Decimal("20.00")
    paypal_order.payment_fee = Decimal("1.00")
    paypal_order.calculate_actual_received()
    assert paypal_order.actual_received == Decimal("19.00")
    assert paypal_order.actual_received_source() == "estimated"


def test_actual_received_stays_real_without_settlement_record(app, paypal_order):
    """回归：PayPal 订单没有 Shopify 结算流水时，到账口径仍然是真实值。

    真实场景（订单 10471）：回执里没有 receivable_amount（买家币种与结算币种相同），
    所以 settlement_amount 为空；但净额来自网关回执，不能被前端标成「估算」。
    """
    payment = Payment(
        order_id=paypal_order.id,
        payment_method="paypal",
        transaction_id="tx-no-settlement",
        amount=27.99,
        currency="USD",
        status="success",
        payment_date=datetime.utcnow(),
    )
    db.session.add(payment)
    db.session.commit()

    applied = apply_gateway_fee(
        payment,
        paypal_order,
        fee=Decimal("1.53"),
        fee_currency="USD",
        net=Decimal("26.46"),
        settlement_amount=None,
        settlement_currency=None,
    )
    assert applied is True
    assert payment.fee_source == "gateway"
    assert payment.net_amount == Decimal("26.46")
    # 没有结算流水
    assert paypal_order.settlement_amount is None
    # 但到账金额是真实的
    assert paypal_order.actual_received == Decimal("26.46")
    assert paypal_order.actual_received_source() == "gateway"


def test_to_dict_exposes_actual_received_source(app, paypal_order):
    """列表/详情要靠这个字段区分「真实」和「估算」。"""
    paypal_order.payment_fee = Decimal("1.00")
    paypal_order.calculate_actual_received()
    assert paypal_order.to_dict()["actual_received_source"] == "estimated"


def test_hkd_cross_rate_uses_verified_quotes_not_identity(app, monkeypatch, tmp_path):
    """在线汇率不可用时，可用最近成功的人民币报价推导港币/美元。"""
    app.config["EXCHANGE_RATE_CACHE_PATH"] = str(tmp_path / "rates.json")
    first = ExchangeRateService()
    quotes = {("HKD", "CNY"): Decimal("0.856"), ("USD", "CNY"): Decimal("6.72")}
    monkeypatch.setattr(
        first, "_fetch_rate_from_apis", lambda source, target: (quotes[source, target], "test")
    )

    with app.app_context():
        first.get_exchange_rate("HKD", "CNY")
        first.get_exchange_rate("USD", "CNY")
        second = ExchangeRateService()
        monkeypatch.setattr(second, "_fetch_rate_from_apis", lambda *_: None)
        assert second.get_exchange_rate("HKD", "USD") == Decimal("0.856") / Decimal("6.72")
        assert second.get_exchange_rate("USD", "HKD") == Decimal("6.72") / Decimal("0.856")


def test_order_dict_exposes_presentment_currency(app, paypal_order):
    paypal_order.presentment_currency = "AUD"
    db.session.commit()
    assert paypal_order.to_dict()["presentment_currency"] == "AUD"


def test_gateway_fee_sync_rejects_unknown_shop(client, admin_headers):
    """同步接口只做鉴权与参数校验，不做网络请求。"""
    response = client.post("/api/sync/gateway-fees", json={"shop_id": 999999}, headers=admin_headers)
    assert response.status_code == 404
