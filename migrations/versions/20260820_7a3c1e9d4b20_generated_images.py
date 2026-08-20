"""Add generated_images for the generate_image chat tool.

Revision ID: 7a3c1e9d4b20
Revises: cc2b1969cbe6
Create Date: 2026-08-20 22:30:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "7a3c1e9d4b20"
down_revision: Union[str, Sequence[str], None] = "cc2b1969cbe6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "generated_images",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("object_key", sa.String(length=512), nullable=False),
        sa.Column("public_url", sa.String(length=1024), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column("mime", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("aspect_ratio", sa.String(length=16), nullable=True),
        sa.Column("cost_usd", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        if_not_exists=True,
    )
    with op.batch_alter_table("generated_images", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_generated_images_user_id"),
            ["user_id"],
            unique=False,
            if_not_exists=True,
        )
        batch_op.create_index(
            batch_op.f("ix_generated_images_created_at"),
            ["created_at"],
            unique=False,
            if_not_exists=True,
        )


def downgrade() -> None:
    with op.batch_alter_table("generated_images", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_generated_images_created_at"))
        batch_op.drop_index(batch_op.f("ix_generated_images_user_id"))
    op.drop_table("generated_images")
