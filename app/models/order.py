from app import db
from datetime import datetime
from sqlalchemy import func, DECIMAL
from decimal import Decimal


class Order(db.Model):
    """订单模型"""

    __tablename__ = "orders"
    __table_args__ = (
        db.UniqueConstraint("shop_id", "shipping_label_number", name="uq_orders_shop_shipping_label_number"),
        db.Index("ix_orders_shop_order_date", "shop_id", "order_date"),
        db.Index("ix_orders_shop_created_at", "shop_id", "created_at"),
        db.Index("ix_orders_created_at", "created_at"),
        db.Index("ix_orders_shop_order_number", "shop_id", "order_number"),
        db.Index("ix_orders_shop_customer_email", "shop_id", db.text("lower(customer_email)")),
        db.Index("ix_orders_shop_queue", "shop_id", "warehouse_status", "financial_status", "order_date"),
    )

    id = db.Column(db.Integer, primary_key=True)
    shop_id = db.Column(db.Integer, db.ForeignKey("shops.id"), nullable=True, index=True)  # 所属店铺
    shopify_order_id = db.Column(db.String(50), unique=True, nullable=False, index=True)
    order_number = db.Column(db.String(50), nullable=False)

    # 订单基本信息
    customer_email = db.Column(db.String(255))
    customer_name = db.Column(db.String(255))
    customer_phone = db.Column(db.String(50))
    # 收货地址（结构化 JSON：name/phone/address1/address2/city/province/country/zip）
    shipping_address = db.Column(db.JSON)

    # 金额信息
    total_price = db.Column(DECIMAL(10, 2), nullable=False)  # 订单总金额
    subtotal_price = db.Column(DECIMAL(10, 2), nullable=False)  # 商品小计
    total_tax = db.Column(DECIMAL(10, 2), default=0)  # 税费
    shipping_price = db.Column(DECIMAL(10, 2), default=0)  # 运费
    currency = db.Column(db.String(3), default="USD")  # 订单货币
    presentment_currency = db.Column(db.String(3))  # 买家实际结算币种（可能与订单货币不同）

    # 实际收款信息
    actual_received = db.Column(DECIMAL(10, 2))  # 实际到账金额
    settlement_amount = db.Column(DECIMAL(14, 2))  # 网关实际结算到账金额（原币）
    settlement_currency = db.Column(db.String(3))  # 网关实际结算币种（如 HKD）
    payment_method = db.Column(db.String(50))  # 支付方式：paypal, stripe
    payment_fee = db.Column(DECIMAL(10, 2), default=0)  # 支付手续费

    # Shopify 订单补充信息
    note = db.Column(db.Text)  # Shopify 订单备注
    fulfillments = db.Column(db.JSON)  # 履约、承运商、运单号与跟踪链接

    # 成本信息
    product_cost = db.Column(DECIMAL(10, 2), default=0)  # 商品成本
    shipping_cost = db.Column(DECIMAL(10, 2), default=0)  # 物流成本

    # 利润信息
    gross_profit = db.Column(DECIMAL(10, 2))  # 毛利润
    profit_margin = db.Column(DECIMAL(5, 2))  # 利润率（百分比）

    # 订单状态
    financial_status = db.Column(db.String(50))  # 支付状态
    cancelled_at = db.Column(db.DateTime)  # Shopify 取消时间；与支付状态独立
    fulfillment_status = db.Column(db.String(50))  # Shopify 履约状态；不代表仓库实物出库
    shipping_label_number = db.Column(db.String(120))  # 本系统持有的物流标签单号
    shipping_label_source = db.Column(db.String(30))  # manual / 4px
    expected_parcel_count = db.Column(db.Integer, nullable=False, default=1, server_default="1")
    warehouse_status = db.Column(db.String(30), nullable=False, default="pending", server_default="pending", index=True)
    shipped_at = db.Column(db.DateTime)  # UTC，只有扫码确认出库时写入

    # 时间信息
    order_date = db.Column(db.DateTime, nullable=False)
    processed_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    # Successful complete Shopify detail read, UTC; never inferred from updated_at.
    shopify_details_synced_at = db.Column(db.DateTime)
    # Dismisses the current queue occurrence only. Relevant stock adjustments
    # reopen shortages; explicit procurement can always override the dismissal.
    procurement_excluded_at = db.Column(db.DateTime)
    fulfillment_priority = db.Column(db.Integer, nullable=False, default=0, server_default="0")

    # 关联关系
    payments = db.relationship("Payment", backref="order", lazy="dynamic")
    parcels = db.relationship("ShippingParcel", back_populates="order", lazy="selectin", order_by="ShippingParcel.id")
    items = db.relationship(
        "OrderItem",
        backref="order",
        lazy="selectin",
        cascade="all, delete-orphan",
        order_by="OrderItem.id",
    )
    shop = db.relationship("Shop", foreign_keys=[shop_id], backref=db.backref("orders", lazy="dynamic"))

    @property
    def status(self):
        """根据financial_status返回订单状态"""
        if self.cancelled_at:
            return "cancelled"
        if self.financial_status in ("paid", "partially_refunded"):
            return "paid"
        elif self.financial_status == "pending":
            return "pending"
        elif self.financial_status in ["cancelled", "refunded", "voided"]:
            return "cancelled"
        else:
            return "pending"  # 默认状态

    @property
    def order_id(self):
        """返回订单号，兼容模板中的order_id字段"""
        return self.order_number

    @property
    def net_amount(self):
        """返回实际到账金额，兼容模板中的net_amount字段"""
        return self.actual_received

    @property
    def platform_fee(self):
        """返回平台费用，这里返回0或者可以根据需要计算"""
        return 0

    def __repr__(self):
        return f"<Order {self.order_number}>"

    def calculate_profit(self):
        """计算利润"""
        if self.actual_received is not None and self.product_cost is not None and self.shipping_cost is not None:
            # 确保类型一致性，将所有值转换为Decimal
            actual_received = Decimal(str(self.actual_received))
            product_cost = Decimal(str(self.product_cost))
            shipping_cost = Decimal(str(self.shipping_cost))

            self.gross_profit = actual_received - product_cost - shipping_cost
            if actual_received > 0:
                self.profit_margin = (self.gross_profit / actual_received) * 100
            else:
                self.profit_margin = 0

    def calculate_actual_received(self):
        """计算实际到账金额（订单币种口径）。

        有网关真实净额（PayPal / Shopify Payments 回执里的实际入账）时优先采用，
        因为那才是钱真正到账的数字；没有真实数据才退化为「订单金额 - 估算手续费」。
        """
        gateway_net = self.get_gateway_net_amount()
        if gateway_net is not None:
            # Shopify 的退款交易可能仍是 pending，displayFinancialStatus 却继续是 paid。
            # 在网关负向结算流水到来前，先按退款后的订单金额扣除已知手续费，
            # 避免继续把原始收款当成利润；流水到来后取较低的真实净额。
            remaining = self.refund_adjusted_total()
            if remaining is not None:
                fee = Decimal(str(self.payment_fee or 0))
                gateway_net = min(gateway_net, remaining - fee)
            self.actual_received = gateway_net.quantize(Decimal("0.01"))
            return

        remaining = self.refund_adjusted_total()
        if remaining is not None:
            payment_fee = Decimal(str(self.payment_fee)) if self.payment_fee else Decimal("0")
            self.actual_received = (remaining - payment_fee).quantize(Decimal("0.01"))
            return

        if self.total_price is not None:
            # 确保类型一致性，将payment_fee转换为Decimal
            payment_fee = Decimal(str(self.payment_fee)) if self.payment_fee else Decimal("0")
            actual_received = Decimal(str(self.total_price)) - payment_fee
            # 四舍五入保留两位小数
            self.actual_received = actual_received.quantize(Decimal("0.01"))

    def get_gateway_net_amount(self):
        """网关真实净额合计（已折合订单币种）；没有真实数据时返回 None。"""
        try:
            payments = [payment for payment in self._payments_for_calculation() if payment.fee_source == "gateway"]
        except Exception:
            return None

        nets = [Decimal(str(payment.net_amount)) for payment in payments if payment.net_amount is not None]
        if not nets:
            return None
        return sum(nets, Decimal("0"))

    def has_active_refund(self):
        """是否存在 Shopify 已提交、尚未失败的退款交易。"""
        return any(
            payment.payment_method == "shopify_refund"
            and payment.status in ("pending", "success")
            for payment in self._payments_for_calculation()
        )

    def _payments_for_calculation(self):
        """Use a page-level payment snapshot when the order list preloaded one."""
        if hasattr(self, "_list_payments"):
            return self._list_payments
        return self.payments

    def refund_adjusted_total(self):
        """原收款扣除有效退款后的订单币种毛额，不高于 Shopify 当前订单金额。"""
        payments = list(self._payments_for_calculation())
        refunds = [
            item for item in payments
            if item.payment_method == "shopify_refund"
            and item.status in ("pending", "success")
            and item.amount is not None
        ]
        if not refunds:
            return None

        captures = {}
        for item in payments:
            if item.payment_method == "shopify_refund" or item.status != "success" or item.amount is None:
                continue
            buyer_amount = Decimal(str(item.amount))
            if buyer_amount <= 0:
                continue
            order_amount = (
                Decimal(str(item.amount_in_order_currency))
                if item.amount_in_order_currency is not None
                else buyer_amount if item.currency == self.currency else None
            )
            if order_amount is None:
                continue
            bucket = captures.setdefault(item.currency, [Decimal("0"), Decimal("0")])
            bucket[0] += buyer_amount
            bucket[1] += order_amount

        original_gross = sum((bucket[1] for bucket in captures.values()), Decimal("0"))
        if not original_gross:
            return Decimal(str(self.total_price)) if self.total_price is not None else None

        refunded = Decimal("0")
        for item in refunds:
            amount = abs(Decimal(str(item.amount)))
            bucket = captures.get(item.currency)
            if bucket and bucket[0]:
                refunded += amount * bucket[1] / bucket[0]
            elif item.currency == self.currency:
                refunded += amount

        remaining = original_gross - refunded
        if self.total_price is not None:
            remaining = min(remaining, Decimal(str(self.total_price)))
        return remaining

    def is_refund_estimated(self):
        """退款尚未体现在网关净额里，当前到账数为待结算估算值。"""
        remaining = self.refund_adjusted_total()
        if remaining is None:
            return False
        gateway_net = self.get_gateway_net_amount()
        return gateway_net is not None and gateway_net > (
            remaining - Decimal(str(self.payment_fee or 0))
        )

    def actual_received_source(self):
        """``actual_received`` 的口径。

        - ``gateway``：来自网关回执的真实净额（PayPal / Shopify Payments 都算）
        - ``estimated``：网关数据还没拿到，用「订单金额 - 估算手续费」倒推

        注意：PayPal 在 Shopify 侧不产生 Shopify Payments 结算流水，
        所以 ``settlement_amount`` 会是空，但到账金额仍然是真实的。
        """
        if self.is_refund_estimated():
            return "refund_pending"
        return "gateway" if self.get_gateway_net_amount() is not None else "estimated"

    def get_total_shipping_cost_cny(self):
        """获取总物流费用（人民币）"""
        from app.services.exchange_rate_service import exchange_rate_service

        total_cost = Decimal("0")
        if self.order_costs:
            for cost in self.order_costs:
                if cost.shipping_cost:
                    total_cost += Decimal(str(cost.shipping_cost))

        # 如果没有设置费用，返回None表示未设置
        if total_cost == 0:
            return None

        return total_cost  # 物流费用已经是人民币

    def get_total_fangguo_cost_cny(self):
        """获取总方果费用（人民币）"""
        total_cost = Decimal("0")
        if self.order_costs:
            for cost in self.order_costs:
                if cost.fangguo_cost:
                    total_cost += Decimal(str(cost.fangguo_cost))

        # 如果没有设置费用，返回None表示未设置
        if total_cost == 0:
            return None

        return total_cost  # 方果费用已经是人民币

    def calculate_gross_profit_cny(self):
        """动态计算毛利润（人民币）"""
        from app.services.exchange_rate_service import exchange_rate_service

        # 优先按网关真正结算到账的金额/币种换算；尚未结算才使用订单币种口径的旧字段。
        income_amount = self.settlement_amount if self.settlement_amount is not None else self.actual_received
        income_currency = self.settlement_currency if self.settlement_amount is not None else self.currency
        if income_amount and income_currency:
            if income_currency == "CNY":
                actual_received_cny = Decimal(str(income_amount))
            else:
                actual_received_cny = exchange_rate_service.convert_to_cny(
                    float(income_amount), income_currency
                )
        else:
            actual_received_cny = Decimal("0")

        # 获取人民币成本
        shipping_cost_cny = self.get_total_shipping_cost_cny() or Decimal("0")
        fangguo_cost_cny = self.get_total_fangguo_cost_cny() or Decimal("0")

        # 将商品成本转换为人民币（假设商品成本与订单货币一致）
        if self.product_cost and self.currency:
            if self.currency == "CNY":
                product_cost_cny = Decimal(str(self.product_cost))
            else:
                product_cost_cny = exchange_rate_service.convert_to_cny(
                    float(self.product_cost), self.currency
                )
        else:
            product_cost_cny = Decimal("0")

        # 计算毛利润 = 实际到账(CNY) - 商品成本(CNY) - 物流费用(CNY) - 方果费用(CNY)
        # 统一使用Decimal类型进行计算
        after_sale_cost_cny = sum(
            (Decimal(str(cost.amount_cny)) for case in self.after_sales for cost in case.costs),
            Decimal("0"),
        )
        gross_profit_cny = actual_received_cny - product_cost_cny - shipping_cost_cny - fangguo_cost_cny - after_sale_cost_cny

        return float(gross_profit_cny)

    def get_allocated_expenses(self):
        """获取分摊到该订单的费用"""
        from app.models.expense import Expense
        from decimal import Decimal

        allocated_product_cost = Decimal("0")
        allocated_shipping_cost = Decimal("0")

        # 获取所有关联的费用（通过多对多关系）
        for expense in self.expenses:
            if expense.category in ["product_cost", "shipping_cost"]:
                # 计算该费用关联的订单数量
                order_count = len(expense.orders)
                if order_count > 0:
                    # 平均分摊费用
                    allocated_amount = Decimal(str(expense.amount)) / order_count

                    if expense.category == "product_cost":
                        allocated_product_cost += allocated_amount
                    elif expense.category == "shipping_cost":
                        allocated_shipping_cost += allocated_amount

        return {
            "product_cost": float(allocated_product_cost),
            "shipping_cost": float(allocated_shipping_cost),
        }

    def to_dict(self):
        """转换为字典"""
        from app.services.exchange_rate_service import exchange_rate_service

        # 获取RMB费用
        shipping_cost_cny = self.get_total_shipping_cost_cny()
        fangguo_cost_cny = self.get_total_fangguo_cost_cny()
        gross_profit_cny = self.calculate_gross_profit_cny()
        after_sale_cost_cny = sum(
            (Decimal(str(cost.amount_cny)) for case in self.after_sales for cost in case.costs),
            Decimal("0"),
        )

        # 获取分摊的费用
        allocated_expenses = self.get_allocated_expenses()

        # 将订单总价转换为人民币
        if self.total_price and self.currency:
            if self.currency == "CNY":
                total_price_cny = float(self.total_price)
            else:
                total_price_cny = float(
                    exchange_rate_service.convert_to_cny(float(self.total_price), self.currency)
                )
        else:
            total_price_cny = 0

        settlement_amount_cny = None
        settlement_exchange_rate = None
        if self.settlement_amount is not None and self.settlement_currency:
            if self.settlement_currency == "CNY":
                settlement_exchange_rate = Decimal("1")
                settlement_amount_cny = Decimal(str(self.settlement_amount))
            else:
                settlement_exchange_rate = exchange_rate_service.get_exchange_rate(
                    self.settlement_currency, "CNY"
                )
                if settlement_exchange_rate is not None:
                    settlement_amount_cny = (
                        Decimal(str(self.settlement_amount)) * settlement_exchange_rate
                    ).quantize(Decimal("0.01"))

        return {
            "id": self.id,
            "shop_id": self.shop_id,
            "shop_name": self.shop.name if self.shop else None,
            "shopify_order_id": self.shopify_order_id,
            "order_number": self.order_number,
            "customer_email": self.customer_email,
            "customer_name": self.customer_name,
            "customer_phone": self.customer_phone,
            "shipping_address": self.shipping_address,
            "total_price": float(self.total_price) if self.total_price else 0,
            "total_price_cny": total_price_cny,  # 新增：订单总价（人民币）
            "currency": self.currency,
            "presentment_currency": self.presentment_currency,
            "actual_received": float(self.actual_received) if self.actual_received else 0,
            # 到账口径，前端用来区分「真实」和「估算」
            "actual_received_source": self.actual_received_source(),
            "settlement_amount": (
                float(self.settlement_amount) if self.settlement_amount is not None else None
            ),
            "settlement_currency": self.settlement_currency,
            "settlement_amount_cny": (
                float(settlement_amount_cny) if settlement_amount_cny is not None else None
            ),
            "settlement_exchange_rate": (
                float(settlement_exchange_rate) if settlement_exchange_rate is not None else None
            ),
            "payment_method": self.payment_method,
            "payment_fee": float(self.payment_fee) if self.payment_fee else 0,
            "product_cost": float(self.product_cost) if self.product_cost else 0,
            "shipping_cost": float(self.shipping_cost) if self.shipping_cost else 0,
            "gross_profit": float(self.gross_profit) if self.gross_profit else 0,
            "profit_margin": float(self.profit_margin) if self.profit_margin else 0,
            "financial_status": self.financial_status,
            "cancelled_at": self.cancelled_at.isoformat() if self.cancelled_at else None,
            "fulfillment_status": self.fulfillment_status,
            "shipping_label_number": self.shipping_label_number,
            "shipping_label_source": self.shipping_label_source,
            "expected_parcel_count": self.expected_parcel_count,
            "parcels": [parcel.to_dict() for parcel in self.parcels],
            "warehouse_status": self.warehouse_status,
            "shipped_at": self.shipped_at.isoformat() if self.shipped_at else None,
            "note": self.note,
            "fulfillments": self.fulfillments or [],
            "order_date": self.order_date.isoformat() if self.order_date else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "shopify_details_synced_at": (
                self.shopify_details_synced_at.isoformat() if self.shopify_details_synced_at else None
            ),
            # 新增RMB字段
            "shipping_cost_cny": float(shipping_cost_cny) if shipping_cost_cny is not None else None,
            "shipping_cost_source": next(
                (cost.shipping_source for cost in self.order_costs if cost.shipping_cost), None),
            "fangguo_cost_cny": float(fangguo_cost_cny) if fangguo_cost_cny is not None else None,
            "gross_profit_cny": float(gross_profit_cny) if gross_profit_cny else 0,
            "after_sale_cost_cny": float(after_sale_cost_cny),
            "after_sales": [{"id": case.id, "customer_reference": case.customer_reference,
                             "kind": case.kind, "status": case.status} for case in self.after_sales],
            # 新增分摊费用字段
            "allocated_product_cost": allocated_expenses["product_cost"],
            "allocated_shipping_cost": allocated_expenses["shipping_cost"],
        }
