"""Alembic migrations: empty DB, legacy create_all DB, models stay in sync."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect, text

ROOT = Path(__file__).resolve().parent.parent

EXPECTED_TABLES = {
    "users",
    "api_tokens",
    "conversations",
    "downloads",
    "oauth_identities",
    "sessions",
    "usage_logs",
    "api_token_usage",
    "messages",
    "share_links",
    "share_accesses",
    "alembic_version",
}


def _alembic(db_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{db_path}"
    env["PYTHONPATH"] = str(ROOT)
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(f"alembic {' '.join(args)} failed:\n{result.stdout}\n{result.stderr}")
    return result


def test_upgrade_creates_schema_on_empty_db(tmp_path):
    db_path = tmp_path / "empty.db"
    _alembic(db_path, "upgrade", "head")
    engine = create_engine(f"sqlite:///{db_path}")
    tables = set(inspect(engine).get_table_names())
    assert EXPECTED_TABLES <= tables
    user_cols = {c["name"] for c in inspect(engine).get_columns("users")}
    assert "preferred_model" in user_cols
    _alembic(db_path, "upgrade", "head")


def test_upgrade_adds_columns_on_legacy_create_all_db(tmp_path):
    db_path = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(text("""
                CREATE TABLE users (
                    id INTEGER NOT NULL PRIMARY KEY,
                    email VARCHAR(255) NOT NULL,
                    password_hash VARCHAR(255) NOT NULL,
                    created_at DATETIME NOT NULL
                )
                """))
        conn.execute(text("""
                CREATE TABLE share_links (
                    id INTEGER NOT NULL PRIMARY KEY,
                    conversation_id INTEGER NOT NULL,
                    token_hash VARCHAR(64) NOT NULL,
                    prefix VARCHAR(20) NOT NULL,
                    created_at DATETIME NOT NULL,
                    expires_at DATETIME,
                    revoked_at DATETIME
                )
                """))
        conn.execute(text("""
                CREATE TABLE share_accesses (
                    id INTEGER NOT NULL PRIMARY KEY,
                    share_id INTEGER NOT NULL,
                    ip VARCHAR(64) NOT NULL,
                    accessed_at DATETIME NOT NULL
                )
                """))
        conn.execute(
            text(
                "INSERT INTO users (id, email, password_hash, created_at)"
                " VALUES (1, 'a@b.c', 'x', '2020-01-01')"
            )
        )

    _alembic(db_path, "upgrade", "head")
    insp = inspect(engine)
    user_cols = {c["name"] for c in insp.get_columns("users")}
    assert "preferred_model" in user_cols
    share_cols = {c["name"] for c in insp.get_columns("share_accesses")}
    assert {"visitor_kind", "visitor_label", "user_agent"} <= share_cols
    assert "conversations" in insp.get_table_names()

    with engine.connect() as conn:
        model = conn.execute(text("SELECT preferred_model FROM users WHERE id = 1")).scalar()
    assert model == "default"
    _alembic(db_path, "upgrade", "head")


def test_models_match_migrated_schema(tmp_path):
    db_path = tmp_path / "sync.db"
    _alembic(db_path, "upgrade", "head")
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{db_path}"
    env["PYTHONPATH"] = str(ROOT)
    probe = r"""
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine
import os
from app.db import Base

engine = create_engine(os.environ["DATABASE_URL"])
with engine.connect() as conn:
    ctx = MigrationContext.configure(
        conn,
        opts={
            "compare_type": True,
            "compare_server_default": False,
            "render_as_batch": True,
        },
    )
    diffs = compare_metadata(ctx, Base.metadata)
assert not diffs, diffs
"""
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(f"schema drift:\n{result.stdout}\n{result.stderr}")
