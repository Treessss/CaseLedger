"""Link after-sale procurement to the existing purchase-list workflow.

Revision ID: aa27a0f4d993
Revises: f194bd2e8a60
"""

from alembic import op
import sqlalchemy as sa

revision = "aa27a0f4d993"
down_revision = "f194bd2e8a60"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("purchase_lists") as batch_op:
        batch_op.add_column(sa.Column("after_sale_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key("fk_purchase_lists_after_sale_id", "after_sales", ["after_sale_id"], ["id"])
        batch_op.create_index("ix_purchase_lists_after_sale_id", ["after_sale_id"])


def downgrade():
    with op.batch_alter_table("purchase_lists") as batch_op:
        batch_op.drop_index("ix_purchase_lists_after_sale_id")
        batch_op.drop_constraint("fk_purchase_lists_after_sale_id", type_="foreignkey")
        batch_op.drop_column("after_sale_id")
