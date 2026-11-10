"""给热点查询补索引

订单详情、手续费回填都要按 payments.order_id 查，之前没有索引，
每次都在 1 万多行里全表扫描；订单号批量查询（采购 / 装箱核对）也是同理。

Revision ID: c4a97e15b3d2
Revises: b7d41f8c2a35
Create Date: 2026-10-04 12:00:00.000000

"""
from alembic import op

revision = "c4a97e15b3d2"
down_revision = "b7d41f8c2a35"
branch_labels = None
depends_on = None

INDEXES = (
    ("ix_payments_order_id", "payments", ["order_id"]),
    ("ix_order_costs_order_id", "order_costs", ["order_id"]),
    ("ix_order_costs_order_number", "order_costs", ["order_number"]),
)


def upgrade():
    for name, table, columns in INDEXES:
        op.create_index(name, table, columns)


def downgrade():
    for name, table, _ in INDEXES:
        op.drop_index(name, table_name=table)
