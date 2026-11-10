"""把履约作业、售后管理从「订单管理」里拆成独立权限

改动前：勾了「订单管理」的子账户会同时看到 采购管理 / 发货中心 / 装箱核对 / 售后管理，
没有粒度可言（仓库只开一个装箱核对做不到，客服只开订单+售后也会连带拿到采购发货）。

改动后拆成：
    - orders        订单管理（订单查询、详情、费率）
    - fulfillment   履约作业（采购管理、发货中心、装箱核对）
    - after_sales   售后管理

为了不让现有子账户突然丢权限，这里把原本有 orders 的账号补上两条新权限。

Revision ID: b7d41f8c2a35
Revises: e82f5c1a004d
Create Date: 2026-10-04 10:00:00.000000

"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "b7d41f8c2a35"
down_revision = "e82f5c1a004d"
branch_labels = None
depends_on = None

NEW_KEYS = ("fulfillment", "after_sales")


def upgrade():
    # 原本有订单权限的账号，按旧行为补上新拆出来的两条权限（幂等，重复执行无副作用）
    for key in NEW_KEYS:
        op.execute(
            f"""
            INSERT INTO user_permissions (user_id, permission, created_at)
            SELECT user_id, '{key}', CURRENT_TIMESTAMP
            FROM user_permissions
            WHERE permission = 'orders'
              AND NOT EXISTS (
                  SELECT 1 FROM user_permissions existing
                  WHERE existing.user_id = user_permissions.user_id
                    AND existing.permission = '{key}'
              )
            """
        )


def downgrade():
    # 回退时删掉这两条（旧代码不认它们；只删迁移期间补的，不动手工授予的）
    op.execute(
        "DELETE FROM user_permissions WHERE permission IN ('fulfillment', 'after_sales')"
    )
