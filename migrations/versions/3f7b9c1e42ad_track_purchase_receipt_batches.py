"""Track multiple receiving batches on a purchase list.

Revision ID: 3f7b9c1e42ad
Revises: 6a2f4d9e11b0
"""

from alembic import op
import sqlalchemy as sa


revision = "3f7b9c1e42ad"
down_revision = "6a2f4d9e11b0"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("purchase_lists") as batch_op:
        batch_op.add_column(sa.Column("receipt_batches", sa.JSON(), nullable=False, server_default="[]"))


def downgrade():
    with op.batch_alter_table("purchase_lists") as batch_op:
        batch_op.drop_column("receipt_batches")
