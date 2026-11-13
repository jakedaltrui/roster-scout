"""PostgreSQL-only integration-test support for the canonical data model."""

import os
import re
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def without_csrf_tokens(body):
    """Normalize per-response signed CSRF values before byte-contract checks."""
    body = re.sub(
        rb'(<meta name="csrf-token" content=")[^"]+("[^>]*>)',
        rb'\1<csrf>\2',
        body,
    )
    return re.sub(
        rb'(name="csrf_token" value=")[^"]+("[^>]*>)',
        rb'\1<csrf>\2',
        body,
    )


def require_test_database_url():
    """Return an explicit disposable PostgreSQL URL or raise a safe error."""
    raw_url = os.environ.get("DATABASE_URL", "").strip()
    if not raw_url:
        raise unittest.SkipTest("DATABASE_URL is required for PostgreSQL integration tests")
    url = make_url(raw_url)
    if url.get_backend_name() != "postgresql":
        raise ValueError("Database integration tests require PostgreSQL; SQLite is forbidden")
    if not (url.database or "").endswith("_test"):
        raise ValueError("DATABASE_URL must name a disposable database ending in '_test'")
    return raw_url


def alembic_config(database_url=None):
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url or require_test_database_url())
    return config


def upgrade_database(database_url=None):
    command.upgrade(alembic_config(database_url), "head")


def downgrade_database(database_url=None):
    command.downgrade(alembic_config(database_url), "base")


def clean_database(database_url=None):
    """Delete test rows without mutating schemas or accepting a production URL."""
    url = database_url or require_test_database_url()
    engine = create_engine(url)
    try:
        tables = [name for name in inspect(engine).get_table_names() if name != "alembic_version"]
        if tables:
            quoted = ", ".join(f'"{name}"' for name in tables)
            with engine.begin() as connection:
                connection.execute(text(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE"))
    finally:
        engine.dispose()


# Imported late so the missing environment path can skip without touching a database.
import unittest  # noqa: E402
