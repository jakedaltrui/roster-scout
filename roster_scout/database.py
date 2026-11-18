"""Lazy SQLAlchemy engine and session construction."""

import hashlib
import os
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy import create_engine, text
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


@dataclass(frozen=True)
class SessionAdvisoryClaim:
    """A dedicated PostgreSQL connection and sessions bound to its claim."""

    connection: object
    session_factory: object


@contextmanager
def session_advisory_heartbeat(claim, *, interval_seconds=30.0):
    """Keep a long-running job's detached advisory-lock connection alive."""
    stopped = threading.Event()
    failures = []

    def heartbeat():
        while not stopped.wait(interval_seconds):
            try:
                claim.connection.exec_driver_sql("SELECT 1")
                claim.connection.commit()
            except Exception as exc:  # checked on the owning thread below
                failures.append(exc)
                stopped.set()
                return

    thread = threading.Thread(
        target=heartbeat,
        name="report-advisory-heartbeat",
        daemon=True,
    )
    thread.start()
    try:
        yield
    finally:
        stopped.set()
        thread.join(timeout=max(1.0, interval_seconds + 1.0))
    if failures:
        raise ConnectionError("report advisory lock connection was lost") from None


def _advisory_lock_key(identifier):
    value = uuid.UUID(str(identifier))
    digest = hashlib.sha256(b"roster-scout:report-job:" + value.bytes).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


@contextmanager
def session_advisory_lock(session_factory, identifier):
    """Try one session-level advisory claim on a dedicated connection.

    The connection is detached from the engine pool so closing it represents a
    real PostgreSQL session loss and therefore releases the claim after a crash.
    """
    engine = session_factory.kw.get("bind")
    if engine is None:
        raise RuntimeError("session factory must be bound to an engine")
    connection = engine.connect()
    connection.detach()
    lock_key = _advisory_lock_key(identifier)
    acquired = False
    try:
        acquired = bool(
            connection.scalar(
                text("SELECT pg_try_advisory_lock(:lock_key)"),
                {"lock_key": lock_key},
            )
        )
        connection.commit()
        if not acquired:
            yield None
            return
        yield SessionAdvisoryClaim(
            connection=connection,
            session_factory=sessionmaker(
                bind=connection,
                expire_on_commit=False,
            ),
        )
    finally:
        if acquired and not connection.closed:
            connection.execute(
                text("SELECT pg_advisory_unlock(:lock_key)"),
                {"lock_key": lock_key},
            )
        if not connection.closed:
            connection.close()


@contextmanager
def session_scope(url=None):
    engine = get_engine(url)
    Session = create_session_factory(engine)
    try:
        with Session.begin() as session:
            yield session
    finally:
        engine.dispose()
