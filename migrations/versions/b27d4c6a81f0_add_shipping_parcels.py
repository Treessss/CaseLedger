"""Store each parcel label and physical scan separately.

Revision ID: b27d4c6a81f0
Revises: 79f2a1c6d8e3
"""

from alembic import op
import sqlalchemy as sa


revision = "b27d4c6a81f0"
down_revision = "79f2a1c6d8e3"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("orders") as batch_op:
        batch_op.add_column(sa.Column("expected_parcel_count", sa.Integer(), nullable=False, server_default="1"))
    op.create_table(
        "shipping_parcels",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("shop_id", sa.Integer(), sa.ForeignKey("shops.id"), nullable=False),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("label_number", sa.String(120), nullable=False),
        sa.Column("source", sa.String(30), nullable=False, server_default="manual"),
        sa.Column("scanned_at", sa.DateTime(), nullable=True),
        sa.Column("scanned_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("shop_id", "label_number", name="uq_shipping_parcels_shop_label"),
    )
    op.create_index("ix_shipping_parcels_shop_id", "shipping_parcels", ["shop_id"])
    op.create_index("ix_shipping_parcels_order_id", "shipping_parcels", ["order_id"])
    op.execute(
        "INSERT INTO shipping_parcels (shop_id, order_id, label_number, source, scanned_at, created_at) "
        "SELECT shop_id, id, shipping_label_number, COALESCE(shipping_label_source, 'manual'), "
        "CASE WHEN warehouse_status = 'shipped' "
        "THEN COALESCE(shipped_at, updated_at, created_at, CURRENT_TIMESTAMP) ELSE NULL END, "
        "COALESCE(created_at, order_date, updated_at, CURRENT_TIMESTAMP) "
        "FROM orders WHERE shop_id IS NOT NULL AND shipping_label_number IS NOT NULL"
    )


def downgrade():
    op.drop_index("ix_shipping_parcels_order_id", table_name="shipping_parcels")
    op.drop_index("ix_shipping_parcels_shop_id", table_name="shipping_parcels")
    op.drop_table("shipping_parcels")
    with op.batch_alter_table("orders") as batch_op:
        batch_op.drop_column("expected_parcel_count")
