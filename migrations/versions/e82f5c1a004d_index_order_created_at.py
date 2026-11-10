"""Index the default order-list sort and order-statistics time filter.

Revision ID: e82f5c1a004d
Revises: 9b6d24c7e311
"""

from alembic import op

revision = "e82f5c1a004d"
down_revision = "9b6d24c7e311"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index("ix_orders_shop_created_at", "orders", ["shop_id", "created_at"])
    op.create_index("ix_orders_created_at", "orders", ["created_at"])


def downgrade():
    op.drop_index("ix_orders_created_at", table_name="orders")
    op.drop_index("ix_orders_shop_created_at", table_name="orders")
