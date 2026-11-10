"""add gateway fee fields to payments

Revision ID: c2f8e5a71b43
Revises: b3f1c9a24d10
Create Date: 2026-09-24 14:20:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "c2f8e5a71b43"
down_revision = "b3f1c9a24d10"
branch_labels = None
depends_on = None


GATEWAY_COLUMNS = (
    sa.Column("real_fee", sa.Numeric(precision=14, scale=4), nullable=True),
    sa.Column("real_net", sa.Numeric(precision=14, scale=4), nullable=True),
    sa.Column("fee_currency", sa.String(length=3), nullable=True),
    sa.Column("amount_in_order_currency", sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column("settlement_currency", sa.String(length=3), nullable=True),
    sa.Column("settlement_amount", sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column("exchange_rate", sa.Numeric(precision=20, scale=10), nullable=True),
    sa.Column("fee_source", sa.String(length=16), nullable=True, server_default="estimated"),
    sa.Column("capture_id", sa.String(length=64), nullable=True),
    sa.Column("gateway_reference", sa.String(length=64), nullable=True),
)


def upgrade():
    with op.batch_alter_table("payments", schema=None) as batch_op:
        # 网关真实费用字段：历史记录留空，由回填脚本补真实值
        for column in GATEWAY_COLUMNS:
            batch_op.add_column(column)
        batch_op.create_index(batch_op.f("ix_payments_capture_id"), ["capture_id"], unique=False)

    # 已有支付记录都是费率表估出来的，显式打标
    op.execute("UPDATE payments SET fee_source = 'estimated' WHERE fee_source IS NULL")


def downgrade():
    with op.batch_alter_table("payments", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_payments_capture_id"))
        for column in reversed(GATEWAY_COLUMNS):
            batch_op.drop_column(column.name)
