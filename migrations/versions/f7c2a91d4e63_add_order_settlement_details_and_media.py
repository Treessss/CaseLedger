"""add order settlement details, note, fulfillment and item image

Revision ID: f7c2a91d4e63
Revises: ca6d0f155901
Create Date: 2026-09-24 18:30:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "f7c2a91d4e63"
down_revision = "ca6d0f155901"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("orders", schema=None) as batch_op:
        batch_op.add_column(sa.Column("settlement_amount", sa.Numeric(14, 2), nullable=True))
        batch_op.add_column(sa.Column("settlement_currency", sa.String(length=3), nullable=True))
        batch_op.add_column(sa.Column("note", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("fulfillments", sa.JSON(), nullable=True))

    with op.batch_alter_table("order_items", schema=None) as batch_op:
        batch_op.add_column(sa.Column("image_url", sa.String(length=2048), nullable=True))

    # 已有支付数据直接回填到订单级真实结算字段。只有单一结算币种的订单才合计，
    # 避免把 HKD/USD 等不同币种直接相加。
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            """
            SELECT order_id, MIN(settlement_currency) AS currency,
                   SUM(settlement_amount) AS amount
              FROM payments
             WHERE fee_source = 'gateway'
               AND settlement_amount IS NOT NULL
               AND settlement_currency IS NOT NULL
             GROUP BY order_id
            HAVING COUNT(DISTINCT settlement_currency) = 1
            """
        )
    ).mappings()
    for row in rows:
        connection.execute(
            sa.text(
                """
                UPDATE orders
                   SET settlement_amount = :amount,
                       settlement_currency = :currency
                 WHERE id = :order_id
                """
            ),
            dict(row),
        )


def downgrade():
    with op.batch_alter_table("order_items", schema=None) as batch_op:
        batch_op.drop_column("image_url")

    with op.batch_alter_table("orders", schema=None) as batch_op:
        batch_op.drop_column("fulfillments")
        batch_op.drop_column("note")
        batch_op.drop_column("settlement_currency")
        batch_op.drop_column("settlement_amount")
