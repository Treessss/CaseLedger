"""add presentment currency to orders

Revision ID: d9a4c17e5f28
Revises: c2f8e5a71b43
Create Date: 2026-09-24 14:45:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "d9a4c17e5f28"
down_revision = "c2f8e5a71b43"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("orders", schema=None) as batch_op:
        # 买家实际结算币种：订单记店铺币种（如 USD），买家可能用 AUD/CAD/GBP 付
        batch_op.add_column(sa.Column("presentment_currency", sa.String(length=3), nullable=True))


def downgrade():
    with op.batch_alter_table("orders", schema=None) as batch_op:
        batch_op.drop_column("presentment_currency")
