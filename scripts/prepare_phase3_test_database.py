#!/usr/bin/env python3
"""Prepare or validate the disposable PostgreSQL database used by Phase 3 tests."""

from __future__ import annotations

import argparse
import ipaddress
import os
import subprocess
import sys
import time
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError, SQLAlchemyError


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAFE_ERROR = "phase3_test_database_unavailable"
LOCAL_DATABASE = "roster_scout_test"
LOCAL_USER = "roster_scout"
LOCAL_PASSWORD = "roster_scout"
LOCAL_PORT = 5432


def _fail() -> None:
    raise SystemExit(SAFE_ERROR)


def _parse_target(environment: dict[str, str]) -> tuple[URL, bool]:
    raw_url = environment.get("DATABASE_URL", "").strip()
    if not raw_url:
        _fail()
    try:
        url = make_url(raw_url)
    except (ArgumentError, TypeError, ValueError):
        _fail()
    if url.drivername != "postgresql+psycopg":
        _fail()
    if not (url.database or "").endswith("_test"):
        _fail()
    if url.query:
        _fail()
    if environment.get("PHASE2_DESTRUCTIVE_DATABASE") != "local-disposable":
        _fail()
    host = (url.host or "").strip().lower()
    if not host:
        _fail()
    is_loopback = host == "localhost"
    try:
        is_loopback = is_loopback or ipaddress.ip_address(host).is_loopback
    except ValueError:
        pass
    return url, is_loopback


def _is_documented_local_target(url: URL, is_loopback: bool) -> bool:
    return (
        is_loopback
        and url.database == LOCAL_DATABASE
        and url.username == LOCAL_USER
        and url.password == LOCAL_PASSWORD
        and (url.port or LOCAL_PORT) == LOCAL_PORT
    )


def _can_connect(url: URL) -> bool:
    engine = create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except SQLAlchemyError:
        return False
    finally:
        engine.dispose()


def _start_local_postgres() -> None:
    try:
        subprocess.run(
            ["docker", "compose", "up", "-d", "postgres"],
            cwd=PROJECT_ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
            timeout=45,
        )
    except (OSError, subprocess.SubprocessError):
        _fail()


def _maintenance_url(url: URL) -> URL:
    return url.set(database="postgres")


def _create_local_database_if_absent(url: URL) -> None:
    engine = create_engine(
        _maintenance_url(url),
        isolation_level="AUTOCOMMIT",
        pool_pre_ping=True,
        connect_args={"connect_timeout": 3},
    )
    try:
        with engine.connect() as connection:
            exists = connection.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": LOCAL_DATABASE},
            )
            if exists is None:
                connection.execute(text(f'CREATE DATABASE "{LOCAL_DATABASE}"'))
    except SQLAlchemyError:
        _fail()
    finally:
        engine.dispose()


def _ensure_local_target(url: URL) -> None:
    if not _can_connect(url):
        _start_local_postgres()
        for _ in range(15):
            if _can_connect(_maintenance_url(url)):
                break
            time.sleep(1)
        else:
            _fail()
        _create_local_database_if_absent(url)
    if not _can_connect(url):
        _fail()


def _validate_reversible_ddl(url: URL) -> None:
    engine = create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 3})
    try:
        with engine.begin() as connection:
            connection.execute(
                text("CREATE TEMPORARY TABLE phase3_permission_probe (id integer)")
            )
            connection.execute(text("DROP TABLE phase3_permission_probe"))
    except SQLAlchemyError:
        _fail()
    finally:
        engine.dispose()


def ensure_database(environment: dict[str, str] | None = None) -> None:
    env = dict(os.environ if environment is None else environment)
    url, is_loopback = _parse_target(env)
    if _is_documented_local_target(url, is_loopback):
        _ensure_local_target(url)
    elif not _can_connect(url):
        _fail()
    _validate_reversible_ddl(url)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ensure", action="store_true", required=True)
    parser.parse_args(argv)
    try:
        ensure_database()
    except Exception:
        print(SAFE_ERROR, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
