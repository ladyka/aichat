"""Dialect-safe Alembic helpers.

SQLite accepts ``CREATE INDEX IF NOT EXISTS``; MySQL/MariaDB on typical
shared hosting (ISPmanager) do not. Inspect the catalog instead.
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import inspect


def create_index_if_missing(
    table: str,
    name: str,
    columns: list[str],
    *,
    unique: bool = False,
) -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    inspector.clear_cache()
    if table not in inspector.get_table_names():
        return
    existing = {idx.get("name") for idx in inspector.get_indexes(table)}
    existing.discard(None)
    if name in existing:
        return
    op.create_index(name, table, columns, unique=unique)


def drop_index_if_exists(table: str, name: str) -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    inspector.clear_cache()
    if table not in inspector.get_table_names():
        return
    existing = {idx.get("name") for idx in inspector.get_indexes(table)}
    existing.discard(None)
    if name not in existing:
        return
    op.drop_index(name, table_name=table)
