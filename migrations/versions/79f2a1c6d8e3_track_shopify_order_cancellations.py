"""Track Shopify cancellations separately from payment state.

Revision ID: 79f2a1c6d8e3
Revises: 3f7b9c1e42ad
"""

from alembic import op
import sqlalchemy as sa


revision = "79f2a1c6d8e3"
down_revision = "3f7b9c1e42ad"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("orders") as batch_op:
        batch_op.add_column(sa.Column("cancelled_at", sa.DateTime(), nullable=True))
    op.execute("UPDATE orders SET cancelled_at = updated_at WHERE financial_status = 'cancelled'")


def downgrade():
    with op.batch_alter_table("orders") as batch_op:
        batch_op.drop_column("cancelled_at")
