#!/usr/bin/env python3
"""Launch one Celery worker within the shared Browserless account limit."""

import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from roster_scout.evidence import (
    FailureEvidenceConfigurationError,
    get_failure_evidence_retention,
)
from roster_scout.artifacts import ArtifactStorageConfigurationError


WORKER_CACHE_DIR = "/tmp/roster-scout-worker"


def parse_concurrency(raw_value):
    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError("CELERY_WORKER_CONCURRENCY must be 1 or 2") from exc
    if value not in {1, 2}:
        raise ValueError("CELERY_WORKER_CONCURRENCY must be 1 or 2")
    return value


def parse_account_capacity(raw_value):
    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError("BROWSERLESS_ACCOUNT_MAX_SESSIONS must be 1 or 2") from exc
    if value not in {1, 2}:
        raise ValueError("BROWSERLESS_ACCOUNT_MAX_SESSIONS must be 1 or 2")
    return value


def worker_command(environ=None):
    environ = environ or os.environ
    get_failure_evidence_retention(environ)
    concurrency = parse_concurrency(
        environ.get(
            "CELERY_WORKER_CONCURRENCY",
            environ.get("BROWSERLESS_MAX_SESSIONS", "1"),
        )
    )
    account_capacity = parse_account_capacity(
        environ.get(
            "BROWSERLESS_ACCOUNT_MAX_SESSIONS",
            environ.get("BROWSERLESS_MAX_SESSIONS", str(concurrency)),
        )
    )
    if concurrency > account_capacity:
        raise ValueError(
            "CELERY_WORKER_CONCURRENCY cannot exceed "
            "BROWSERLESS_ACCOUNT_MAX_SESSIONS"
        )
    log_level = environ.get("LOG_LEVEL", "INFO").upper()
    return [
        "celery",
        "-A",
        "roster_scout.celery_app:celery_app",
        "worker",
        "--loglevel",
        log_level,
        "--concurrency",
        str(concurrency),
    ]


def main():
    from roster_scout.artifacts import artifact_store_from_environ

    artifact_store_from_environ(required=bool(os.environ.get("FLY_APP_NAME")))
    command = worker_command()
    inherited_pythonpath = os.environ.get("PYTHONPATH", "")
    pythonpath_entries = [str(PROJECT_ROOT)]
    if inherited_pythonpath:
        pythonpath_entries.append(inherited_pythonpath)
    os.environ["PYTHONPATH"] = os.pathsep.join(pythonpath_entries)
    os.environ["ROSTER_SCOUT_CACHE_DIR"] = WORKER_CACHE_DIR
    os.makedirs(WORKER_CACHE_DIR, mode=0o700, exist_ok=True)
    os.execvp(command[0], command)


if __name__ == "__main__":
    try:
        main()
    except FailureEvidenceConfigurationError:
        print("invalid failure evidence retention configuration", file=sys.stderr)
        raise SystemExit(2) from None
    except ArtifactStorageConfigurationError:
        print("invalid artifact storage configuration", file=sys.stderr)
        raise SystemExit(2) from None
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from None
