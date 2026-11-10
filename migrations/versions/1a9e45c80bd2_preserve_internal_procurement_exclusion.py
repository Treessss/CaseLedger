"""Preserve internal procurement exclusions independently of Shopify sync.

Revision ID: 1a9e45c80bd2
Revises: e36526f93fcb
"""
from alembic import op
import sqlalchemy as sa

revision = '1a9e45c80bd2'
down_revision = 'e36526f93fcb'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('orders', schema=None) as batch_op:
        batch_op.add_column(sa.Column('procurement_excluded_at', sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table('orders', schema=None) as batch_op:
        batch_op.drop_column('procurement_excluded_at')
