from datetime import datetime
from decimal import Decimal

from app import db
from app.models import Order, Payment, Shop
from app.services.gateway_fee_service import _refresh_order_settlement
from app.services.report_service import _income_cny
from app.services.shopify_service import ShopifyService, graphql_order_to_rest
from app.utils.shopify_resources import WebhookResource


def _refund_order():
    shop = Shop.query.filter_by(name="Shop A").first()
    order = Order(
        shop_id=shop.id,
        shopify_order_id="refund-order-10530",
        order_number="10530",
        total_price=Decimal("0"),
        subtotal_price=Decimal("0"),
        currency="USD",
        financial_status="paid",
        order_date=datetime.utcnow(),
        payment_fee=Decimal("1.61"),
    )
    db.session.add(order)
    db.session.flush()
    charge = Payment(
        shop_id=shop.id,
        order_id=order.id,
        payment_method="shopify_payment",
        transaction_id="capture-1",
        amount=Decimal("22.50"),
        currency="USD",
        total_fee=Decimal("1.61"),
        net_amount=Decimal("20.80"),
        real_fee=Decimal("12.70"),
        real_net=Decimal("163.76"),
        fee_currency="HKD",
        settlement_amount=Decimal("163.76"),
        settlement_currency="HKD",
        fee_source="gateway",
        status="success",
        payment_date=datetime.utcnow(),
    )
    db.session.add(charge)
    db.session.flush()
    return shop, order, charge


def test_pending_full_refund_does_not_keep_original_profit(app, monkeypatch):
    shop, order, charge = _refund_order()
    service = ShopifyService()
    service.shop_id = shop.id
    payload = WebhookResource(
        {
            "id": order.shopify_order_id,
            "order_number": order.order_number,
            "created_at": "2026-09-25T09:15:13Z",
            "updated_at": "2026-09-26T09:15:13Z",
            "total_price": "0.00",
            "subtotal_price": "0.00",
            "currency": "USD",
            "financial_status": "paid",
            "fulfillment_status": "unfulfilled",
            "line_items": [],
            "transactions": [
                {
                    "id": charge.transaction_id,
                    "kind": "capture",
                    "status": "success",
                    "gateway": "shopify_payments",
                    "amount": "22.50",
                    "currency": "USD",
                    "processed_at": "2026-09-25T09:15:13Z",
                },
                {
                    "id": "refund-1",
                    "kind": "refund",
                    "status": "pending",
                    "gateway": "shopify_payments",
                    "amount": "22.50",
                    "currency": "USD",
                    "processed_at": "2026-09-26T09:15:13Z",
                },
            ],
        }
    )

    service._process_order(payload)
    assert order.has_active_refund()
    assert order.actual_received == Decimal("-1.61")
    assert order.settlement_amount == Decimal("-12.70")
    assert order.settlement_currency == "HKD"
    assert order.actual_received_source() == "refund_pending"

    monkeypatch.setattr(
        "app.services.report_service.exchange_rate_service.convert_to_cny",
        lambda amount, currency: Decimal(str(amount)) * Decimal("0.92"),
    )
    assert _income_cny(order) == -11.684

    # 网关随后入账退款：真实负向净额应取代估算值，不能再扣一次退款。
    charge.net_amount = Decimal("-1.61")
    charge.settlement_amount = Decimal("-12.70")
    _refresh_order_settlement(order)
    order.calculate_actual_received()
    assert order.actual_received == Decimal("-1.61")
    assert order.settlement_amount == Decimal("-12.70")
    assert order.actual_received_source() == "gateway"


def test_refunded_financial_status_keeps_unreimbursed_fee_in_report(app, monkeypatch):
    _, order, _ = _refund_order()
    order.financial_status = "refunded"
    order.settlement_amount = Decimal("-12.70")
    order.settlement_currency = "HKD"
    monkeypatch.setattr(
        "app.services.report_service.exchange_rate_service.convert_to_cny",
        lambda amount, currency: Decimal(str(amount)) * Decimal("0.92"),
    )
    assert _income_cny(order) == -11.684


def test_partial_refund_is_not_deducted_twice_after_gateway_settlement(app):
    shop, order, charge = _refund_order()
    order.total_price = Decimal("11.25")
    order.financial_status = "partially_refunded"
    db.session.add(
        Payment(
            shop_id=shop.id,
            order_id=order.id,
            payment_method="shopify_refund",
            transaction_id="partial-refund-1",
            amount=Decimal("-11.25"),
            currency="USD",
            status="pending",
            payment_date=datetime.utcnow(),
        )
    )
    db.session.flush()

    _refresh_order_settlement(order)
    order.calculate_actual_received()
    assert order.actual_received == Decimal("9.64")
    assert order.settlement_amount == Decimal("75.53")

    charge.net_amount = Decimal("9.64")
    charge.settlement_amount = Decimal("75.53")
    _refresh_order_settlement(order)
    order.calculate_actual_received()
    assert order.actual_received == Decimal("9.64")
    assert order.settlement_amount == Decimal("75.53")


def test_refund_transactions_take_precedence_over_stale_current_total(app):
    shop, order, _ = _refund_order()
    order.total_price = Decimal("9.00")  # Shopify may leave shipping on a fully refunded order.
    db.session.add(
        Payment(
            shop_id=shop.id,
            order_id=order.id,
            payment_method="shopify_refund",
            transaction_id="full-refund-1",
            amount=Decimal("-22.50"),
            currency="USD",
            status="success",
            payment_date=datetime.utcnow(),
        )
    )
    db.session.flush()
    assert order.refund_adjusted_total() == Decimal("0.00")
    _refresh_order_settlement(order)
    order.calculate_actual_received()
    assert order.actual_received == Decimal("-1.61")
    assert order.settlement_amount == Decimal("-12.70")

    order.total_price = Decimal("22.50")  # Partial refunds need not change this field.
    refund = Payment.query.filter_by(transaction_id="full-refund-1").first()
    refund.amount = Decimal("-7.80")
    assert order.refund_adjusted_total() == Decimal("14.70")
    _refresh_order_settlement(order)
    order.calculate_actual_received()
    assert order.actual_received == Decimal("13.09")


def test_refund_uses_original_shop_currency_amount_for_cross_currency_payment(app):
    shop, order, charge = _refund_order()
    order.total_price = Decimal("20.48")
    charge.amount = Decimal("71.11")
    charge.currency = "NZD"
    charge.amount_in_order_currency = Decimal("41.82")
    db.session.add(
        Payment(
            shop_id=shop.id,
            order_id=order.id,
            payment_method="shopify_refund",
            transaction_id="nzd-refund-1",
            amount=Decimal("-36.28"),
            currency="NZD",
            status="success",
            payment_date=datetime.utcnow(),
        )
    )
    db.session.flush()
    assert order.refund_adjusted_total() == Decimal("20.48")

    node = {
        "transactions": [{
            "id": "gid://shopify/OrderTransaction/capture-1",
            "kind": "CAPTURE",
            "status": "SUCCESS",
            "amountSet": {
                "shopMoney": {"amount": "41.82", "currencyCode": "USD"},
                "presentmentMoney": {"amount": "71.11", "currencyCode": "NZD"},
            },
        }],
    }
    normalized = graphql_order_to_rest(node)
    assert normalized["transactions"][0]["shop_amount"] == "41.82"
    assert normalized["transactions"][0]["shop_currency"] == "USD"
