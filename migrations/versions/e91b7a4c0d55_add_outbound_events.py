"""新增扫码出库/撤回的操作流水表

打包台要把「核对」和「出库」放在一页，出库后还必须能撤回。
撤回会直接改订单的出库状态（库存是从状态算出来的），属于可追溯的业务动作，
所以单独建一张流水表记录每次出库和每次撤回是谁操作的。

Revision ID: e91b7a4c0d55
Revises: c4a97e15b3d2
Create Date: 2026-10-04 21:30:00.000000

"""
import sqlalchemy as sa
from alembic import op

revision = "e91b7a4c0d55"
down_revision = "c4a97e15b3d2"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "outbound_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("shop_id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("parcel_id", sa.Integer(), nullable=True),
        sa.Column("after_sale_id", sa.Integer(), nullable=True),
        sa.Column("order_number", sa.String(length=100), nullable=False),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("code", sa.String(length=120), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("reason", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"]),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"]),
        sa.ForeignKeyConstraint(["parcel_id"], ["shipping_parcels.id"]),
        sa.ForeignKeyConstraint(["after_sale_id"], ["after_sales.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_outbound_events_shop_id", "outbound_events", ["shop_id"])
    op.create_index("ix_outbound_events_order_id", "outbound_events", ["order_id"])


def downgrade():
    op.drop_index("ix_outbound_events_order_id", table_name="outbound_events")
    op.drop_index("ix_outbound_events_shop_id", table_name="outbound_events")
    op.drop_table("outbound_events")
