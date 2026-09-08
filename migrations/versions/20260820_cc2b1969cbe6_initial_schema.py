"""Initial schema (current models).

Revision ID: cc2b1969cbe6
Revises:
Create Date: 2026-08-20 22:03:09.143011

Idempotent: existing databases created by ``create_all`` keep their tables;
missing additive columns (the old ``_ensure_column`` helpers) are added.
Indexes are created only when missing — MySQL has no ``CREATE INDEX IF NOT EXISTS``.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.sql.schema import Column

from migrations.helpers import create_index_if_missing, drop_index_if_exists

# revision identifiers, used by Alembic.
revision: str = "cc2b1969cbe6"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INDEXES: list[tuple[str, str, list[str], bool]] = [
    ("users", "ix_users_email", ["email"], True),
    ("api_tokens", "ix_api_tokens_token_hash", ["token_hash"], True),
    ("api_tokens", "ix_api_tokens_user_id", ["user_id"], False),
    ("conversations", "ix_conversations_user_id", ["user_id"], False),
    ("downloads", "ix_downloads_user_id", ["user_id"], False),
    ("oauth_identities", "ix_oauth_identities_user_id", ["user_id"], False),
    ("sessions", "ix_sessions_token_hash", ["token_hash"], True),
    ("sessions", "ix_sessions_user_id", ["user_id"], False),
    ("usage_logs", "ix_usage_logs_user_id", ["user_id"], False),
    ("api_token_usage", "ix_api_token_usage_token_id", ["token_id"], False),
    ("messages", "ix_messages_conversation_id", ["conversation_id"], False),
    ("share_links", "ix_share_links_conversation_id", ["conversation_id"], False),
    ("share_links", "ix_share_links_token_hash", ["token_hash"], True),
    ("share_accesses", "ix_share_accesses_share_id", ["share_id"], False),
    ("share_accesses", "ix_share_accesses_visitor_kind", ["visitor_kind"], False),
]


def _add_column_if_missing(table: str, column: Column) -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    inspector.clear_cache()
    names = set(inspector.get_table_names())
    if table not in names:
        return
    existing = {c["name"] for c in inspector.get_columns(table)}
    if column.name in existing:
        return
    op.add_column(table, column)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column(
            "preferred_model", sa.String(length=120), server_default="default", nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        if_not_exists=True,
    )
    op.create_table(
        "api_tokens",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("prefix", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        if_not_exists=True,
    )
    op.create_table(
        "conversations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        if_not_exists=True,
    )
    op.create_table(
        "downloads",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("url_hash", sa.String(length=64), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("file_path", sa.String(length=1024), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "url_hash", name="uq_download_user_url"),
        if_not_exists=True,
    )
    op.create_table(
        "oauth_identities",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "subject", name="uq_oauth_provider_subject"),
        if_not_exists=True,
    )
    op.create_table(
        "sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        if_not_exists=True,
    )
    op.create_table(
        "usage_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        if_not_exists=True,
    )
    op.create_table(
        "api_token_usage",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("token_id", sa.Integer(), nullable=False),
        sa.Column("day", sa.String(length=10), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["token_id"], ["api_tokens.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_id", "day", name="uq_api_token_usage_day"),
        if_not_exists=True,
    )
    op.create_table(
        "messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
        sa.PrimaryKeyConstraint("id"),
        if_not_exists=True,
    )
    op.create_table(
        "share_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("prefix", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
        sa.PrimaryKeyConstraint("id"),
        if_not_exists=True,
    )
    op.create_table(
        "share_accesses",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("share_id", sa.Integer(), nullable=False),
        sa.Column("ip", sa.String(length=64), nullable=False),
        sa.Column("visitor_kind", sa.String(length=16), server_default="human", nullable=False),
        sa.Column("visitor_label", sa.String(length=40), server_default="", nullable=False),
        sa.Column("user_agent", sa.String(length=512), server_default="", nullable=False),
        sa.Column("accessed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["share_id"], ["share_links.id"]),
        sa.PrimaryKeyConstraint("id"),
        if_not_exists=True,
    )

    # Existing create_all databases skip create_table; add columns that used to be
    # patched in init_db via ALTER TABLE.
    _add_column_if_missing(
        "users",
        sa.Column(
            "preferred_model", sa.String(length=120), server_default="default", nullable=False
        ),
    )
    _add_column_if_missing(
        "share_accesses",
        sa.Column("visitor_kind", sa.String(length=16), server_default="human", nullable=False),
    )
    _add_column_if_missing(
        "share_accesses",
        sa.Column("visitor_label", sa.String(length=40), server_default="", nullable=False),
    )
    _add_column_if_missing(
        "share_accesses",
        sa.Column("user_agent", sa.String(length=512), server_default="", nullable=False),
    )

    for table, name, columns, unique in _INDEXES:
        create_index_if_missing(table, name, columns, unique=unique)


def downgrade() -> None:
    for table, name, _columns, _unique in reversed(_INDEXES):
        drop_index_if_exists(table, name)

    op.drop_table("share_accesses", if_exists=True)
    op.drop_table("share_links", if_exists=True)
    op.drop_table("messages", if_exists=True)
    op.drop_table("api_token_usage", if_exists=True)
    op.drop_table("usage_logs", if_exists=True)
    op.drop_table("sessions", if_exists=True)
    op.drop_table("oauth_identities", if_exists=True)
    op.drop_table("downloads", if_exists=True)
    op.drop_table("conversations", if_exists=True)
    op.drop_table("api_tokens", if_exists=True)
    op.drop_table("users", if_exists=True)
