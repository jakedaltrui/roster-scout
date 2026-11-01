"""Lazy SQLAlchemy engine and session construction."""

import os
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def database_url():
    value = os.environ.get("DATABASE_URL", "").strip()
    if not value:
        raise RuntimeError("DATABASE_URL is required for persistence operations")
    return value


def get_engine(url=None, **options):
    """Construct an engine without opening a network connection."""
    return create_engine(url or database_url(), pool_pre_ping=True, **options)


def create_session_factory(engine):
    return sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def session_scope(url=None):
    engine = get_engine(url)
    Session = create_session_factory(engine)
    try:
        with Session.begin() as session:
            yield session
    finally:
        engine.dispose()
