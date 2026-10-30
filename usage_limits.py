"""Shared, single-machine limits for requests and Browserless sessions."""

import fcntl
import hashlib
import math
import os
import sqlite3
import time
from contextlib import closing, contextmanager
from pathlib import Path


class BrowserCapacityExceeded(Exception):
    """No Browserless session slot is available."""


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


@contextmanager
def browserless_slot():
    """Acquire a process-safe slot; the OS releases it if a worker exits."""
    count = max(1, min(2, int(os.environ.get("BROWSERLESS_MAX_SESSIONS", "2"))))
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
