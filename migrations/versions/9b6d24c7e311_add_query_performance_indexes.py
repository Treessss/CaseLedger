"""Add indexes for shop-scoped lists, business dates and work queues.

Revision ID: 9b6d24c7e311
Revises: 7f2c5e4a9b10
"""

from alembic import op

revision = "9b6d24c7e311"
down_revision = "7f2c5e4a9b10"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index("ix_orders_shop_order_date", "orders", ["shop_id", "order_date"])
    op.create_index("ix_orders_shop_order_number", "orders", ["shop_id", "order_number"])
    op.create_index("ix_orders_shop_queue", "orders", ["shop_id", "warehouse_status", "financial_status", "order_date"])
    op.create_index("ix_expenses_shop_date", "expenses", ["shop_id", "expense_date"])
    op.create_index("ix_after_sales_shop_status_created", "after_sales", ["shop_id", "status", "created_at"])


def downgrade():
    op.drop_index("ix_after_sales_shop_status_created", table_name="after_sales")
    op.drop_index("ix_expenses_shop_date", table_name="expenses")
    op.drop_index("ix_orders_shop_queue", table_name="orders")
    op.drop_index("ix_orders_shop_order_number", table_name="orders")
    op.drop_index("ix_orders_shop_order_date", table_name="orders")
