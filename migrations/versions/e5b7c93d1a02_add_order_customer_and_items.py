"""add order customer fields and order_items table

Revision ID: e5b7c93d1a02
Revises: d9a4c17e5f28
Create Date: 2026-09-24 16:05:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "e5b7c93d1a02"
down_revision = "d9a4c17e5f28"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("orders", schema=None) as batch_op:
        # 客户联系方式与收货地址：历史数据为空，回填脚本会补
        batch_op.add_column(sa.Column("customer_phone", sa.String(length=50), nullable=True))
        batch_op.add_column(sa.Column("shipping_address", sa.JSON(), nullable=True))

    op.create_table(
        "order_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("shop_id", sa.Integer(), nullable=True),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("shopify_line_item_id", sa.String(length=50), nullable=False),
        sa.Column("shopify_product_id", sa.String(length=50), nullable=True),
        sa.Column("shopify_variant_id", sa.String(length=50), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("variant_title", sa.String(length=255), nullable=True),
        sa.Column("sku", sa.String(length=120), nullable=True),
        sa.Column("vendor", sa.String(length=120), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=True),
        sa.Column("price", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("total_discount", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"]),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_id", "shopify_line_item_id", name="uq_order_line_item"),
    )
    op.create_index(op.f("ix_order_items_order_id"), "order_items", ["order_id"], unique=False)
    op.create_index(op.f("ix_order_items_shop_id"), "order_items", ["shop_id"], unique=False)


def downgrade():
    op.drop_index(op.f("ix_order_items_shop_id"), table_name="order_items")
    op.drop_index(op.f("ix_order_items_order_id"), table_name="order_items")
    op.drop_table("order_items")

    with op.batch_alter_table("orders", schema=None) as batch_op:
        batch_op.drop_column("shipping_address")
        batch_op.drop_column("customer_phone")
