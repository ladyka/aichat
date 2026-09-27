"""message meta for response info in UI

Revision ID: a1b2c3d4e5f6
Revises: 8f2c4d61a907
Create Date: 2026-09-27 15:50:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "8f2c4d61a907"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("meta", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "meta")
