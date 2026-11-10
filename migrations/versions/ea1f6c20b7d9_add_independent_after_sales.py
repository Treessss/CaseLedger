"""Add independent after-sale reshipment records and CNY cost ledger.

Revision ID: ea1f6c20b7d9
Revises: ce4a0d72f631
"""

from alembic import op
import sqlalchemy as sa

revision = "ea1f6c20b7d9"
down_revision = "ce4a0d72f631"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "after_sales",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("shop_id", sa.Integer(), sa.ForeignKey("shops.id"), nullable=False),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("customer_reference", sa.String(80), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="pending_purchase"),
        sa.Column("shipping_address", sa.JSON(), nullable=False),
        sa.Column("tracking_number", sa.String(120), unique=True),
        sa.Column("label_barcode", sa.String(120), unique=True),
        sa.Column("purchased_at", sa.DateTime()),
        sa.Column("shipped_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("shop_id", "customer_reference", name="uq_after_sales_reference"),
    )
    op.create_index("ix_after_sales_shop_id", "after_sales", ["shop_id"])
    op.create_index("ix_after_sales_order_id", "after_sales", ["order_id"])
    op.create_table(
        "after_sale_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("after_sale_id", sa.Integer(), sa.ForeignKey("after_sales.id"), nullable=False),
        sa.Column("source_order_item_id", sa.Integer(), sa.ForeignKey("order_items.id")),
        sa.Column("catalog_order_item_id", sa.Integer(), sa.ForeignKey("order_items.id"), nullable=False),
        sa.Column("group_key", sa.String(500), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("variant_title", sa.String(255)),
        sa.Column("sku", sa.String(120)),
        sa.Column("image_url", sa.String(2048)),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("reserved_quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("received_quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("damaged_quantity", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_after_sale_items_after_sale_id", "after_sale_items", ["after_sale_id"])
    op.create_table(
        "after_sale_costs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("after_sale_id", sa.Integer(), sa.ForeignKey("after_sales.id"), nullable=False),
        sa.Column("shop_id", sa.Integer(), sa.ForeignKey("shops.id"), nullable=False),
        sa.Column("cost_type", sa.String(20), nullable=False),
        sa.Column("amount_cny", sa.Numeric(12, 2), nullable=False),
        sa.Column("note", sa.String(255)),
        sa.Column("incurred_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_after_sale_costs_after_sale_id", "after_sale_costs", ["after_sale_id"])
    op.create_index("ix_after_sale_costs_shop_id", "after_sale_costs", ["shop_id"])


def downgrade():
    op.drop_index("ix_after_sale_costs_shop_id", "after_sale_costs")
    op.drop_index("ix_after_sale_costs_after_sale_id", "after_sale_costs")
    op.drop_table("after_sale_costs")
    op.drop_index("ix_after_sale_items_after_sale_id", "after_sale_items")
    op.drop_table("after_sale_items")
    op.drop_index("ix_after_sales_order_id", "after_sales")
    op.drop_index("ix_after_sales_shop_id", "after_sales")
    op.drop_table("after_sales")
