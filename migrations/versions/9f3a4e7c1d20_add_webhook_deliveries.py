"""Remember processed Shopify webhook deliveries for retry idempotency."""

from alembic import op
import sqlalchemy as sa


revision = "9f3a4e7c1d20"
down_revision = "8d4f9c2b0a71"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("shop_id", sa.Integer(), sa.ForeignKey("shops.id"), nullable=False),
        sa.Column("delivery_id", sa.String(length=128), nullable=False),
        sa.Column("topic", sa.String(length=100), nullable=False),
        sa.Column("processed_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("shop_id", "delivery_id", name="uq_webhook_shop_delivery"),
    )
    op.create_index("ix_webhook_deliveries_shop_id", "webhook_deliveries", ["shop_id"])


def downgrade():
    op.drop_index("ix_webhook_deliveries_shop_id", table_name="webhook_deliveries")
    op.drop_table("webhook_deliveries")
