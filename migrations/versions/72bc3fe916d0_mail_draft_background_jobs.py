"""Durable mail draft background jobs."""
from alembic import op
import sqlalchemy as sa

revision = '72bc3fe916d0'
down_revision = '6de14aa72c09'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('mail_draft_jobs',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('shop_id', sa.Integer(), sa.ForeignKey('shops.id'), nullable=False),
        sa.Column('status', sa.String(20), nullable=False, server_default='queued'),
        sa.Column('message_ids', sa.JSON(), nullable=False, server_default='[]'),
        sa.Column('results', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('error', sa.String(500)),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('heartbeat_at', sa.DateTime(), nullable=False),
        sa.Column('finished_at', sa.DateTime()))
    op.create_index('ix_mail_draft_jobs_shop_created', 'mail_draft_jobs', ['shop_id', 'created_at'])


def downgrade():
    op.drop_table('mail_draft_jobs')
