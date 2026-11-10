from app import db
from datetime import datetime
from sqlalchemy import DECIMAL, Table

# 费用和订单的多对多关系表
expense_order_association = Table(
    "expense_order_association",
    db.Model.metadata,
    db.Column("expense_id", db.Integer, db.ForeignKey("expenses.id"), primary_key=True),
    db.Column("order_id", db.Integer, db.ForeignKey("orders.id"), primary_key=True),
)


class Expense(db.Model):
    """费用/收入流水模型。

    record_type 区分方向：expense（支出，默认） / income（额外收入）。
    两者共用同一张表与同一套列表/统计接口，便于在「费用」页面统一查看。
    """

    __tablename__ = "expenses"
    __table_args__ = (db.Index("ix_expenses_shop_date", "shop_id", "expense_date"),)

    TYPE_EXPENSE = "expense"
    TYPE_INCOME = "income"
    RECORD_TYPES = (TYPE_EXPENSE, TYPE_INCOME)

    id = db.Column(db.Integer, primary_key=True)
    shop_id = db.Column(
        db.Integer, db.ForeignKey("shops.id"), nullable=False, index=True
    )  # 每条费用或收入必须归属一个店铺

    # 流水方向：支出 / 收入
    record_type = db.Column(db.String(20), nullable=False, default=TYPE_EXPENSE, index=True)

    # 费用基本信息
    category = db.Column(db.String(50), nullable=False)  # facebook_ads, product_cost, shipping_cost
    description = db.Column(db.Text)  # 费用描述

    # 金额信息
    amount = db.Column(DECIMAL(10, 2), nullable=False)  # 费用金额（转换后的人民币金额）
    currency = db.Column(db.String(10), default="CNY")  # 货币类型（统一为人民币）

    # 多币种支持
    original_amount = db.Column(DECIMAL(10, 2))  # 原始金额
    original_currency = db.Column(db.String(10))  # 原始货币类型
    exchange_rate = db.Column(DECIMAL(10, 6), default=1.0)  # 汇率

    # 关联信息
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=True)  # 主要关联订单（向后兼容）
    account_id = db.Column(db.Integer, db.ForeignKey("accounts.id"), nullable=True)  # 关联账户（可选）

    # 多对多关系：一个费用可以关联多个订单
    orders = db.relationship("Order", secondary=expense_order_association, backref="expenses")
    shop = db.relationship("Shop", foreign_keys=[shop_id], backref=db.backref("expenses", lazy="dynamic"))
    reference_id = db.Column(db.String(255))  # 外部参考ID（如广告账单ID）

    # 供应商信息
    vendor = db.Column(db.String(255))  # 供应商名称（如：方果工厂、4PX、Facebook）
    submitter = db.Column(db.String(255))  # 提交人

    # 时间信息
    expense_date = db.Column(db.Date, nullable=False)  # 费用发生日期
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # 状态
    status = db.Column(db.String(50), default="confirmed")  # confirmed, pending, cancelled

    def __repr__(self):
        return f"<Expense {self.category}: {self.amount}>"

    def to_dict(self):
        """转换为字典"""
        return {
            "id": self.id,
            "shop_id": self.shop_id,
            "record_type": self.record_type or self.TYPE_EXPENSE,
            "category": self.category,
            "description": self.description,
            "amount": float(self.amount) if self.amount else 0,
            "currency": self.currency,
            "original_amount": float(self.original_amount) if self.original_amount else None,
            "original_currency": self.original_currency,
            "exchange_rate": float(self.exchange_rate) if self.exchange_rate else 1.0,
            "order_id": self.order_id,
            "account_id": self.account_id,
            "reference_id": self.reference_id,
            "vendor": self.vendor,
            "submitter": self.submitter,
            "expense_date": self.expense_date.isoformat() if self.expense_date else None,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    @staticmethod
    def get_categories():
        """获取费用类别"""
        return {
            "facebook_ads": "Facebook广告费",
            "product_cost": "商品成本费",
            "shipping_cost": "物流费用",
            "other": "其他费用",
        }

    @staticmethod
    def get_income_categories():
        """获取收入类别（额外收入）"""
        return {
            "platform_subsidy": "平台补贴",
            "refund": "退款返还",
            "interest": "利息收入",
            "compensation": "赔偿/理赔",
            "other_income": "其他收入",
        }

    @staticmethod
    def categories_for(record_type):
        """按流水方向返回可选类别。"""
        if record_type == Expense.TYPE_INCOME:
            return Expense.get_income_categories()
        return Expense.get_categories()

    @staticmethod
    def get_vendors():
        """获取供应商列表"""
        return {"facebook": "Facebook", "fangguo": "方果工厂", "4px": "4PX物流", "other": "其他供应商"}
