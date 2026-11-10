"""normalize expense/consumption timestamps to UTC

背景：费用与消耗记录的时间戳此前用 ``datetime.now()``（服务器本地时间，本机为
北京时间 UTC+8）写入，而订单、用户等其它表用 ``datetime.utcnow()``（UTC）。
两种口径混用会导致：展示层无法用统一的「UTC → 北京时间」规则，
一旦部署到 UTC 时区的服务器，费用时间会整体偏移 8 小时。

本迁移把这些历史时间戳从本地时间换算成 UTC（本机时区固定为 Asia/Shanghai），
配合代码改为统一写入 UTC，之后展示层可以无差别地做 UTC → 北京时间转换。

Revision ID: b3f1c9a24d10
Revises: 84115a2c4674
"""

from datetime import timedelta

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "b3f1c9a24d10"
down_revision = "84115a2c4674"
branch_labels = None
depends_on = None

# 本机历史写入用的时区偏移（Asia/Shanghai = UTC+8）
LOCAL_UTC_OFFSET_HOURS = 8

# 需要换算的表与字段（created_at/updated_at 都是写入时间）
TARGETS = (
    ("expenses", ("created_at", "updated_at")),
    ("consumptions", ("created_at", "updated_at")),
)


def _shift(delta_hours):
    conn = op.get_bind()
    for table, columns in TARGETS:
        for column in columns:
            if conn.dialect.name == "sqlite":
                # SQLite does not bind Python timedelta values. datetime() accepts
                # a signed modifier and works for the historical text timestamps.
                conn.execute(
                    sa.text(
                        f"UPDATE {table} SET {column} = datetime({column}, :modifier) "
                        f"WHERE {column} IS NOT NULL"
                    ),
                    {"modifier": f"{delta_hours:+d} hours"},
                )
            else:
                conn.execute(
                    sa.text(
                        f"UPDATE {table} SET {column} = {column} + :delta "
                        f"WHERE {column} IS NOT NULL"
                    ),
                    {"delta": timedelta(hours=delta_hours)},
                )


def upgrade():
    # 本地时间 → UTC：往回推 8 小时
    _shift(-LOCAL_UTC_OFFSET_HOURS)


def downgrade():
    # UTC → 本地时间：加回 8 小时
    _shift(LOCAL_UTC_OFFSET_HOURS)
