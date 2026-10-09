"""conversations.pinned_at: pinned chats stay at the top of the list.

Revision ID: a8b9c0d1e2f3
Revises: 5f6a7b8c9d0e
Create Date: 2026-10-09 17:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a8b9c0d1e2f3"
down_revision: Union[str, Sequence[str], None] = "5f6a7b8c9d0e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("pinned_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("conversations", "pinned_at")
