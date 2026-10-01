"""user_sites: one site per user (domain + published timestamp)

Revision ID: 5f6a7b8c9d0e
Revises: a1b2c3d4e5f6
Create Date: 2026-10-01 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5f6a7b8c9d0e"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "user_sites",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("domain", sa.String(length=63), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_user_sites_user"),
    )
    with op.batch_alter_table("user_sites", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_user_sites_user_id"), ["user_id"], unique=True)
        batch_op.create_index(batch_op.f("ix_user_sites_domain"), ["domain"], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("user_sites", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_user_sites_domain"))
        batch_op.drop_index(batch_op.f("ix_user_sites_user_id"))
    op.drop_table("user_sites")
