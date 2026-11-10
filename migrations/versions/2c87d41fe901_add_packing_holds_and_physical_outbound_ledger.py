"""Packing holds, fulfillment priority and immutable outbound stock lines.

Revision ID: 2c87d41fe901
Revises: 1a9e45c80bd2
"""
from alembic import op
import sqlalchemy as sa

revision = '2c87d41fe901'
down_revision = '1a9e45c80bd2'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('orders') as batch:
        batch.add_column(sa.Column('fulfillment_priority', sa.Integer(), nullable=False, server_default='0'))
    op.create_table('packing_sessions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('shop_id', sa.Integer(), sa.ForeignKey('shops.id'), nullable=False),
        sa.Column('order_id', sa.Integer(), sa.ForeignKey('orders.id')),
        sa.Column('after_sale_id', sa.Integer(), sa.ForeignKey('after_sales.id')),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id')),
        sa.Column('state', sa.String(20), nullable=False, server_default='packing'),
        sa.Column('lines', sa.JSON(), nullable=False, server_default='[]'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('ended_at', sa.DateTime()),
        sa.CheckConstraint('(order_id IS NULL) <> (after_sale_id IS NULL)', name='ck_packing_one_target'))
    op.create_index('ix_packing_shop_state_expiry', 'packing_sessions', ['shop_id','state','expires_at'])
    op.create_index('ix_packing_sessions_order_id', 'packing_sessions', ['order_id'])
    op.create_index('ix_packing_sessions_after_sale_id', 'packing_sessions', ['after_sale_id'])
    op.create_table('inventory_outbound_lines',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('shop_id', sa.Integer(), sa.ForeignKey('shops.id'), nullable=False),
        sa.Column('order_id', sa.Integer(), sa.ForeignKey('orders.id'), nullable=False),
        sa.Column('group_key', sa.String(500), nullable=False),
        sa.Column('quantity_delta', sa.Integer(), nullable=False),
        sa.Column('action', sa.String(20), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id')),
        sa.Column('created_at', sa.DateTime(), nullable=False))
    op.create_index('ix_outbound_lines_shop_key','inventory_outbound_lines',['shop_id','group_key'])
    op.create_index('ix_inventory_outbound_lines_order_id','inventory_outbound_lines',['order_id'])


def downgrade():
    op.drop_table('inventory_outbound_lines')
    op.drop_table('packing_sessions')
    with op.batch_alter_table('orders') as batch:
        batch.drop_column('fulfillment_priority')
