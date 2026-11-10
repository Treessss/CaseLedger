"""Separate local shipping labels and physical outbound from Shopify fulfillment.

Existing Shopify-fulfilled orders retain their prior inventory accounting as
legacy_unverified; this is not evidence that a parcel physically left the warehouse.
"""

from alembic import op
import sqlalchemy as sa


revision = "f3a4d82b91c0"
down_revision = "8211976880ab"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("orders") as batch_op:
        batch_op.add_column(sa.Column("shipping_label_number", sa.String(120), nullable=True))
        batch_op.add_column(sa.Column("shipping_label_source", sa.String(30), nullable=True))
        batch_op.add_column(sa.Column("warehouse_status", sa.String(30), nullable=False, server_default="pending"))
        batch_op.add_column(sa.Column("shipped_at", sa.DateTime(), nullable=True))
        batch_op.create_unique_constraint("uq_orders_shop_shipping_label_number", ["shop_id", "shipping_label_number"])
        batch_op.create_index("ix_orders_warehouse_status", ["warehouse_status"])

    op.execute(
        "UPDATE orders SET warehouse_status = 'legacy_unverified' "
        "WHERE fulfillment_status = 'fulfilled'"
    )
    op.execute(
        "UPDATE orders SET warehouse_status = 'legacy_partial_unverified' "
        "WHERE fulfillment_status = 'partial'"
    )


def downgrade():
    with op.batch_alter_table("orders") as batch_op:
        batch_op.drop_index("ix_orders_warehouse_status")
        batch_op.drop_constraint("uq_orders_shop_shipping_label_number", type_="unique")
        batch_op.drop_column("shipped_at")
        batch_op.drop_column("warehouse_status")
        batch_op.drop_column("shipping_label_source")
        batch_op.drop_column("shipping_label_number")
