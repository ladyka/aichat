"""conversation title_locked

Revision ID: 8f2c4d61a907
Revises: b4e8c1a07d92
Create Date: 2026-09-11 23:20:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "8f2c4d61a907"
down_revision: Union[str, Sequence[str], None] = "b4e8c1a07d92"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("title_locked", sa.Boolean(), server_default=sa.text("0"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("conversations", "title_locked")
