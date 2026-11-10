"""Bind financial accounts to a shop and backfill legacy ownership."""

from datetime import datetime

from alembic import op
import sqlalchemy as sa


revision = "7b8e2f4a19c0"
down_revision = "0b9ab0954340"
branch_labels = None
depends_on = None


def _as_datetime(value):
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def upgrade():
    with op.batch_alter_table("accounts") as batch_op:
        batch_op.add_column(sa.Column("shop_id", sa.Integer(), nullable=True, server_default="1"))

    connection = op.get_bind()
    shops = connection.execute(sa.text("SELECT id, created_at FROM shops ORDER BY created_at, id")).all()
    accounts = connection.execute(sa.text("SELECT id, created_at FROM accounts ORDER BY id")).all()
    for account_id, created_at in accounts:
        owner_ids = {
            row[0]
            for row in connection.execute(
                sa.text("SELECT DISTINCT shop_id FROM expenses WHERE account_id = :id"),
                {"id": account_id},
            )
        }
        if len(owner_ids) > 1:
            raise RuntimeError(f"Account {account_id} is used by multiple shops; split it manually")
        if owner_ids:
            shop_id = owner_ids.pop()
        elif len(shops) == 1:
            shop_id = shops[0][0]
        elif shops and len(shops) > 1 and _as_datetime(created_at) < _as_datetime(shops[1][1]):
            # An account created before the second shop existed is legacy first-shop data.
            shop_id = shops[0][0]
        else:
            raise RuntimeError(f"Account {account_id} has no unambiguous shop ownership")
        connection.execute(
            sa.text("UPDATE accounts SET shop_id = :shop_id WHERE id = :id"),
            {"shop_id": shop_id, "id": account_id},
        )

    with op.batch_alter_table("accounts") as batch_op:
        batch_op.alter_column("shop_id", existing_type=sa.Integer(), nullable=False, server_default=None)
        batch_op.create_foreign_key("fk_accounts_shop_id", "shops", ["shop_id"], ["id"])
        batch_op.create_index("ix_accounts_shop_id", ["shop_id"])


def downgrade():
    with op.batch_alter_table("accounts") as batch_op:
        batch_op.drop_index("ix_accounts_shop_id")
        batch_op.drop_constraint("fk_accounts_shop_id", type_="foreignkey")
        batch_op.drop_column("shop_id")
