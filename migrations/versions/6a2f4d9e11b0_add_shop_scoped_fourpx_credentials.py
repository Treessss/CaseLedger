"""Add encrypted, shop-scoped 4PX connection settings.

Revision ID: 6a2f4d9e11b0
Revises: f3a4d82b91c0
"""

from alembic import op
import sqlalchemy as sa


revision = "6a2f4d9e11b0"
down_revision = "f3a4d82b91c0"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("shops") as batch_op:
        batch_op.add_column(sa.Column("fourpx_app_name", sa.String(100), nullable=True))
        batch_op.add_column(sa.Column("fourpx_app_key_encrypted", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("fourpx_app_secret_encrypted", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("fourpx_resource_code", sa.String(100), nullable=True))
        batch_op.add_column(sa.Column("fourpx_environment", sa.String(20), nullable=False, server_default="test"))


def downgrade():
    with op.batch_alter_table("shops") as batch_op:
        batch_op.drop_column("fourpx_environment")
        batch_op.drop_column("fourpx_resource_code")
        batch_op.drop_column("fourpx_app_secret_encrypted")
        batch_op.drop_column("fourpx_app_key_encrypted")
        batch_op.drop_column("fourpx_app_name")
