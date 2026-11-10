"""Persist human-reviewed support analysis and draft source fingerprints."""
from alembic import op
import sqlalchemy as sa

revision = '6de14aa72c09'
down_revision = '2c87d41fe901'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('mail_drafts') as batch:
        batch.add_column(sa.Column('analysis', sa.JSON(), nullable=False, server_default='{}'))
        batch.add_column(sa.Column('source_fingerprint', sa.String(64)))
        batch.add_column(sa.Column('generated_at', sa.DateTime()))


def downgrade():
    with op.batch_alter_table('mail_drafts') as batch:
        batch.drop_column('generated_at')
        batch.drop_column('source_fingerprint')
        batch.drop_column('analysis')
