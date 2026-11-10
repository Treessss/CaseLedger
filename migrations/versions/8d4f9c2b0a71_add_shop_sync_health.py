"""Track latest order sync attempt and failure per shop."""

from alembic import op
import sqlalchemy as sa


revision = "8d4f9c2b0a71"
down_revision = "7b8e2f4a19c0"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("shops") as batch_op:
        batch_op.add_column(sa.Column("last_sync_attempt_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("last_sync_error", sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table("shops") as batch_op:
        batch_op.drop_column("last_sync_error")
        batch_op.drop_column("last_sync_attempt_at")
