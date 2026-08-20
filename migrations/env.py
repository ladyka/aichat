"""Alembic env: URL and metadata come from the app, not from alembic.ini."""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection, Engine, create_engine

from app.config import get_settings
from app.db import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _migration_engine() -> Engine:
    settings = get_settings()
    url = settings.database_url
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, poolclass=pool.NullPool, connect_args=connect_args)


def _configure_context(connection: Connection | None = None, url: str | None = None) -> None:
    kwargs: dict = {
        "target_metadata": target_metadata,
        "compare_type": True,
        "compare_server_default": False,
        # SQLite cannot ALTER many constructs in-place; batch mode rewrites the table.
        "render_as_batch": True,
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
