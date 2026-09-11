"""add skills

Revision ID: b4e8c1a07d92
Revises: e76d08841e35
Create Date: 2026-08-21 23:51:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "b4e8c1a07d92"
down_revision: Union[str, Sequence[str], None] = "e76d08841e35"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "skills",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=200), server_default="Навык", nullable=False),
        sa.Column("description", sa.String(length=500), server_default="", nullable=False),
        sa.Column(
            "body",
            sa.Text().with_variant(mysql.MEDIUMTEXT(), "mysql"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["parent_id"], ["skills.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("skills", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_skills_user_id"), ["user_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_skills_parent_id"), ["parent_id"], unique=False)

    op.create_table(
        "user_skill_defaults",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("skill_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["skill_id"], ["skills.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "skill_id"),
    )
    with op.batch_alter_table("user_skill_defaults", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_user_skill_defaults_skill_id"), ["skill_id"], unique=False
        )

    op.create_table(
        "conversation_skills",
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("skill_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["skill_id"], ["skills.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("conversation_id", "skill_id"),
    )
    with op.batch_alter_table("conversation_skills", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_conversation_skills_skill_id"), ["skill_id"], unique=False
        )

    op.create_table(
        "skill_shares",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("skill_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("prefix", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["skill_id"], ["skills.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("skill_shares", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_skill_shares_skill_id"), ["skill_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_skill_shares_token_hash"), ["token_hash"], unique=True)


def downgrade() -> None:
    with op.batch_alter_table("skill_shares", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_skill_shares_token_hash"))
        batch_op.drop_index(batch_op.f("ix_skill_shares_skill_id"))
    op.drop_table("skill_shares")

    with op.batch_alter_table("conversation_skills", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_conversation_skills_skill_id"))
    op.drop_table("conversation_skills")

    with op.batch_alter_table("user_skill_defaults", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_user_skill_defaults_skill_id"))
    op.drop_table("user_skill_defaults")

    with op.batch_alter_table("skills", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_skills_parent_id"))
        batch_op.drop_index(batch_op.f("ix_skills_user_id"))
    op.drop_table("skills")
