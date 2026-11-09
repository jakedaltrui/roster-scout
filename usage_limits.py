"""Request limits and shared Browserless account-capacity leases."""

import fcntl
import hashlib
import math
import os
import sqlite3
import time
from contextlib import closing, contextmanager
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.pool import NullPool


_DEFAULT_CAPACITY_NAMESPACE = "roster-scout-browserless-account"


class BrowserCapacityError(Exception):
    """Safe base error for Browserless capacity failures."""


class BrowserCapacityExceeded(BrowserCapacityError):
    """No Browserless session slot is available."""


class BrowserCapacityUnavailable(BrowserCapacityError):
    """The configured shared capacity authority is unavailable."""


def _state_dir():
    path = Path(os.environ.get("ROSTER_SCOUT_CACHE_DIR") or Path(__file__).resolve().parent / ".cache")
    path.mkdir(parents=True, exist_ok=True)
    return path


def check_request_limit(client):
    """Return seconds until retry, or zero if this request is allowed."""
    limit = max(1, int(os.environ.get("SCRAPE_RATE_LIMIT", "10")))
    window = max(1, int(os.environ.get("SCRAPE_RATE_WINDOW_SECONDS", "600")))
    now = time.time()
    client_hash = hashlib.sha256(client.encode()).hexdigest()
    with closing(sqlite3.connect(_state_dir() / "request-limits.sqlite3", timeout=5)) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS requests (client TEXT NOT NULL, at REAL NOT NULL)")
        db.execute("CREATE INDEX IF NOT EXISTS requests_client_at ON requests (client, at)")
        db.execute("BEGIN IMMEDIATE")
        db.execute("DELETE FROM requests WHERE at <= ?", (now - window,))
        count, oldest = db.execute(
            "SELECT COUNT(*), MIN(at) FROM requests WHERE client = ?", (client_hash,)
        ).fetchone()
        if count >= limit:
            return max(1, math.ceil(oldest + window - now))
        db.execute("INSERT INTO requests (client, at) VALUES (?, ?)", (client_hash, now))
    return 0


def _capacity_limit():
    raw_value = os.environ.get(
        "BROWSERLESS_ACCOUNT_MAX_SESSIONS",
        os.environ.get("BROWSERLESS_MAX_SESSIONS", "2"),
    )
    try:
        count = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise BrowserCapacityUnavailable(
            "Browserless capacity service is unavailable. Please try again shortly."
        ) from exc
    if count not in {1, 2}:
        raise BrowserCapacityUnavailable(
            "Browserless capacity service is unavailable. Please try again shortly."
        )
    return count


def _capacity_namespace_key():
    namespace = os.environ.get(
        "BROWSERLESS_CAPACITY_NAMESPACE",
        _DEFAULT_CAPACITY_NAMESPACE,
    ).strip()
    if not namespace:
        raise BrowserCapacityUnavailable(
            "Browserless capacity service is unavailable. Please try again shortly."
        )
    digest = hashlib.sha256(namespace.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], byteorder="big", signed=True)


@contextmanager
def _local_browserless_slot(count):
    """Acquire a machine-local development slot."""
    for index in range(count):
        handle = (_state_dir() / f"browserless-slot-{index}.lock").open("a")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            continue
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()
        return
    raise BrowserCapacityExceeded("Roster Scout is busy. Please try again shortly.")


@contextmanager
def _postgresql_browserless_slot(count):
    """Hold one PostgreSQL session advisory lock for the Browserless work."""
    from roster_scout.database import get_engine

    engine = None
    connection = None
    slot_index = None
    namespace_key = _capacity_namespace_key()
    try:
        engine = get_engine(poolclass=NullPool)
        connection = engine.connect()
        for index in range(count):
            acquired = connection.execute(
                text("SELECT pg_try_advisory_lock(:namespace_key, :slot_index)"),
                {"namespace_key": namespace_key, "slot_index": index},
            ).scalar_one()
            if acquired:
                slot_index = index
                break
    except Exception:
        if connection is not None:
            connection.close()
        if engine is not None:
            engine.dispose()
        raise BrowserCapacityUnavailable(
            "Browserless capacity service is unavailable. Please try again shortly."
        ) from None

    if slot_index is None:
        connection.close()
        engine.dispose()
        raise BrowserCapacityExceeded("Roster Scout is busy. Please try again shortly.")

    try:
        yield
    finally:
        try:
            connection.execute(
                text("SELECT pg_advisory_unlock(:namespace_key, :slot_index)"),
                {"namespace_key": namespace_key, "slot_index": slot_index},
            )
        except Exception:
            # Closing a lost/detached connection is the crash-safe release path.
            pass
        finally:
            connection.close()
            engine.dispose()


@contextmanager
def browserless_slot():
    """Acquire one Browserless slot from the configured capacity authority."""
    count = _capacity_limit()
    backend = os.environ.get("BROWSERLESS_CAPACITY_BACKEND", "local").strip().lower()
    if backend == "local":
        with _local_browserless_slot(count):
            yield
        return
    if backend == "postgresql":
        with _postgresql_browserless_slot(count):
            yield
        return
    raise BrowserCapacityUnavailable(
        "Browserless capacity service is unavailable. Please try again shortly."
    )
