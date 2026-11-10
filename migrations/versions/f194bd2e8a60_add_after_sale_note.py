"""Add independent notes to after-sale cases.

Revision ID: f194bd2e8a60
Revises: ea1f6c20b7d9
"""

from alembic import op
import sqlalchemy as sa

revision = "f194bd2e8a60"
down_revision = "ea1f6c20b7d9"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("after_sales", sa.Column("note", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("after_sales", "note")
