"""web_searches: daily limit log for the web_search chat tool.

Revision ID: c3d4e5f6a7b8
Revises: 5f6a7b8c9d0e
Create Date: 2026-10-07 12:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from migrations.helpers import create_index_if_missing, drop_index_if_exists

revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, Sequence[str], None] = "5f6a7b8c9d0e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "web_searches",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("query", sa.String(length=400), nullable=False),
        sa.Column("engines", sa.String(length=32), nullable=False),
        sa.Column("result_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        if_not_exists=True,
    )
    create_index_if_missing("web_searches", "ix_web_searches_user_id", ["user_id"])
    create_index_if_missing("web_searches", "ix_web_searches_created_at", ["created_at"])


def downgrade() -> None:
    drop_index_if_exists("web_searches", "ix_web_searches_created_at")
    drop_index_if_exists("web_searches", "ix_web_searches_user_id")
    op.drop_table("web_searches", if_exists=True)
