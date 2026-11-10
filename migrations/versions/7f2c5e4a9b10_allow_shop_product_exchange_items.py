"""Allow after-sale exchange lines to snapshot shop catalog variants.

Revision ID: 7f2c5e4a9b10
Revises: aa27a0f4d993
"""

from alembic import op
import sqlalchemy as sa

revision = "7f2c5e4a9b10"
down_revision = "aa27a0f4d993"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("after_sale_items") as batch_op:
        batch_op.alter_column("catalog_order_item_id", existing_type=sa.Integer(), nullable=True)
        batch_op.add_column(sa.Column("catalog_product_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("shopify_product_id", sa.String(length=50), nullable=True))
        batch_op.add_column(sa.Column("shopify_variant_id", sa.String(length=50), nullable=True))
        batch_op.add_column(sa.Column("vendor", sa.String(length=120), nullable=True))


def downgrade():
    with op.batch_alter_table("after_sale_items") as batch_op:
        batch_op.drop_column("vendor")
        batch_op.drop_column("shopify_variant_id")
        batch_op.drop_column("shopify_product_id")
        batch_op.drop_column("catalog_product_id")
        batch_op.alter_column("catalog_order_item_id", existing_type=sa.Integer(), nullable=False)
