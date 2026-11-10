"""Shopify Payments GraphQL balance transaction mapping."""

from datetime import datetime

from app import db
from app.models import Order, Payment, Shop
from app.services import gateway_fee_service, shopify_graphql


def test_graphql_balance_transaction_updates_settlement(app, monkeypatch):
    calls = []

    def fake_execute(shop, query, variables):
        calls.append((shop.id, variables))
        return {
            "shopifyPaymentsAccount": {
                "balanceTransactions": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": [{
                        "id": "gid://shopify/ShopifyPaymentsBalanceTransaction/99",
                        "type": "CHARGE", "transactionDate": "2026-01-02T00:00:00Z",
                        "associatedOrder": {"id": "gid://shopify/Order/909"},
                        "amount": {"amount": "100.00", "currencyCode": "HKD"},
                        "fee": {"amount": "5.00", "currencyCode": "HKD"},
                        "net": {"amount": "95.00", "currencyCode": "HKD"},
                    }],
                }
            }
        }

    monkeypatch.setattr(shopify_graphql, "_execute", fake_execute)
    with app.app_context():
        shop = Shop.query.filter_by(shop_url="shop-a.myshopify.com").one()
        order = Order(
            shop_id=shop.id, shopify_order_id="909", order_number="#909", total_price=100,
            subtotal_price=100, currency="HKD", financial_status="paid",
            order_date=datetime(2026, 1, 2),
        )
        db.session.add(order)
        db.session.flush()
        payment = Payment(
            shop_id=shop.id, order_id=order.id, payment_method="shopify_payment",
            transaction_id="txn-909", amount=100, currency="HKD", payment_date=datetime(2026, 1, 2),
            fee_source="estimated",
        )
        db.session.add(payment)
        db.session.commit()

        result = gateway_fee_service.sync_shopify_payments_charges(
            shop, since_date=datetime(2026, 1, 1).date()
        )
        assert result["updated"] == 1
        assert order.settlement_amount == 95
        assert order.settlement_currency == "HKD"
        assert payment.real_fee == 5
        assert calls[0][1]["first"] == 250


def test_recent_refund_recomputes_full_order_balance(app, monkeypatch):
    rows = [
        {"id": "refund", "type": "REFUND", "transactionDate": "2026-02-02T00:00:00Z",
         "associatedOrder": {"id": "gid://shopify/Order/910"},
         "amount": {"amount": "-40", "currencyCode": "HKD"},
         "fee": {"amount": "0", "currencyCode": "HKD"},
         "net": {"amount": "-40", "currencyCode": "HKD"}},
        {"id": "charge", "type": "CHARGE", "transactionDate": "2026-01-01T00:00:00Z",
         "associatedOrder": {"id": "gid://shopify/Order/910"},
         "amount": {"amount": "100", "currencyCode": "HKD"},
         "fee": {"amount": "5", "currencyCode": "HKD"},
         "net": {"amount": "95", "currencyCode": "HKD"}},
    ]
    calls = []

    def fake_execute(shop, query, variables):
        calls.append(variables["after"])
        index = 1 if variables["after"] else 0
        return {"shopifyPaymentsAccount": {"balanceTransactions": {
            "pageInfo": {"hasNextPage": index == 0, "endCursor": "older" if index == 0 else None},
            "nodes": [rows[index]],
        }}}

    monkeypatch.setattr(shopify_graphql, "_execute", fake_execute)
    with app.app_context():
        shop = Shop.query.filter_by(shop_url="shop-a.myshopify.com").one()
        order = Order(shop_id=shop.id, shopify_order_id="910", order_number="#910",
                      total_price=100, subtotal_price=100, currency="HKD",
                      financial_status="partially_refunded", order_date=datetime(2026, 1, 1))
        db.session.add(order)
        db.session.flush()
        payment = Payment(shop_id=shop.id, order_id=order.id, payment_method="shopify_payment",
                          transaction_id="txn-910", amount=100, currency="HKD",
                          payment_date=datetime(2026, 1, 1), fee_source="gateway",
                          real_fee=5, real_net=95, net_amount=95)
        db.session.add(payment)
        db.session.commit()

        result = gateway_fee_service.sync_shopify_payments_charges(
            shop, since_date=datetime(2026, 2, 1).date()
        )

        assert calls == [None, "older", None, "older"]
        assert result["updated"] == 1
        assert order.settlement_amount == 55
        assert order.actual_received == 55
        assert order.gross_profit == 55
