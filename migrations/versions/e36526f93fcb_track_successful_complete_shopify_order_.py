"""Track successful complete Shopify order detail refresh

Revision ID: e36526f93fcb
Revises: 1733bc80367b
Create Date: 2026-10-09 16:48:48.539615

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e36526f93fcb'
down_revision = '1733bc80367b'
branch_labels = None
depends_on = None


def upgrade():
    # Existing orders deliberately start uncached: updated_at is NOT evidence
    # that a full, paginated Shopify detail read has succeeded.
    with op.batch_alter_table('orders', schema=None) as batch_op:
        batch_op.add_column(sa.Column('shopify_details_synced_at', sa.DateTime(), nullable=True))



def downgrade():
    with op.batch_alter_table('orders', schema=None) as batch_op:
        batch_op.drop_column('shopify_details_synced_at')
