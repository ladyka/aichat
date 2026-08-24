"""Alembic env: URL and metadata come from the app, not from alembic.ini."""

from __future__ import annotations

import logging
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection, Engine, create_engine

from app.config import get_settings
from app.db import Base

config = context.config
if config.config_file_name is not None and not logging.getLogger().handlers:
    # Skip when the app already configured logging (init_db during startup).
    # Default fileConfig(disable_existing_loggers=True) would silence aichat.* .
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _migration_engine() -> Engine:
    settings = get_settings()
    url = settings.database_url
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, poolclass=pool.NullPool, connect_args=connect_args)


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


def _configure_context(connection: Connection | None = None, url: str | None = None) -> None:
    if url is not None:
        db_url = url
    elif connection is not None:
        db_url = str(connection.engine.url)
    else:
        db_url = get_settings().database_url
    kwargs: dict = {
        "target_metadata": target_metadata,
        "compare_type": True,
        "compare_server_default": False,
        # SQLite cannot ALTER many constructs in-place; batch mode rewrites the table.
        # Do not enable on MySQL: batch CREATE INDEX still emits IF NOT EXISTS.
        "render_as_batch": _is_sqlite(db_url),
    }
    if connection is not None:
        kwargs["connection"] = connection
    else:
        kwargs["url"] = url
        kwargs["literal_binds"] = True
        kwargs["dialect_opts"] = {"paramstyle": "named"}
    context.configure(**kwargs)


def run_migrations_offline() -> None:
    _configure_context(url=get_settings().database_url)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = _migration_engine()
    with connectable.connect() as connection:
        _configure_context(connection=connection)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
