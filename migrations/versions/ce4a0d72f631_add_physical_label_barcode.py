"""Track the printed parcel barcode separately from 4PX tracking number.

Revision ID: ce4a0d72f631
Revises: b27d4c6a81f0
"""

from alembic import op
import sqlalchemy as sa


revision = "ce4a0d72f631"
down_revision = "b27d4c6a81f0"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("shipping_parcels") as batch_op:
        batch_op.add_column(sa.Column("label_barcode", sa.String(120), nullable=True))
        batch_op.create_unique_constraint("uq_shipping_parcels_shop_barcode", ["shop_id", "label_barcode"])
    op.execute("UPDATE shipping_parcels SET label_barcode = label_number WHERE source <> '4px'")


def downgrade():
    with op.batch_alter_table("shipping_parcels") as batch_op:
        batch_op.drop_constraint("uq_shipping_parcels_shop_barcode", type_="unique")
        batch_op.drop_column("label_barcode")
