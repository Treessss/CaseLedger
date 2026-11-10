"""Require every expense and income record to belong to a shop."""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0b9ab0954340"
down_revision = "f7c2a91d4e63"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    missing = connection.execute(
        sa.text("SELECT id, order_id FROM expenses WHERE shop_id IS NULL")
    ).mappings().all()
    shops = [row[0] for row in connection.execute(sa.text("SELECT id FROM shops"))]

    for expense in missing:
        linked_shop_ids = {
            row[0]
            for row in connection.execute(
                sa.text(
                    """
                    SELECT shop_id FROM orders WHERE id = :order_id
                    UNION
                    SELECT o.shop_id FROM expense_order_association AS eo
                      JOIN orders AS o ON o.id = eo.order_id
                     WHERE eo.expense_id = :expense_id
                    """
                ),
                {"order_id": expense["order_id"], "expense_id": expense["id"]},
            )
            if row[0] is not None
        }
        if len(linked_shop_ids) == 1:
            shop_id = linked_shop_ids.pop()
        elif not linked_shop_ids and len(shops) == 1:
            shop_id = shops[0]
        else:
            raise RuntimeError(
                f"Expense {expense['id']} has no unambiguous shop. "
                "Assign its shop_id before upgrading."
            )
        connection.execute(
            sa.text("UPDATE expenses SET shop_id = :shop_id WHERE id = :expense_id"),
            {"shop_id": shop_id, "expense_id": expense["id"]},
        )

    with op.batch_alter_table("expenses") as batch_op:
        batch_op.alter_column("shop_id", existing_type=sa.Integer(), nullable=False)


def downgrade():
    with op.batch_alter_table("expenses") as batch_op:
        batch_op.alter_column("shop_id", existing_type=sa.Integer(), nullable=True)
