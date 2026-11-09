#!/usr/bin/env python3
"""Run the fail-fast, deterministic Phase 1 release gate."""

import json
import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy.engine import make_url


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MVP_STORY = (
    "As a Roster Scout operator, I want to publish existing NCAA D1/D2/D3 "
    "results through a durable job into an immutable, provenance-rich canonical "
    "snapshot, so that every downstream report can rely on one auditable source "
    "of truth without breaking today's workbook workflow."
)


def _run(label, command, *, env):
    print(f"\n==> {label}", flush=True)
    subprocess.run(command, cwd=PROJECT_ROOT, env=env, check=True)


def _validate_story(env):
    print("==> Exact Phase 1 MVP user story", flush=True)
    completed = subprocess.run(
        [
            "gsd-sdk",
            "query",
            "user-story.validate",
            "--story",
            MVP_STORY,
            "--raw",
        ],
        cwd=PROJECT_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    print(completed.stdout, end="" if completed.stdout.endswith("\n") else "\n")
    result = json.loads(completed.stdout)
    if result.get("valid") is not True or result.get("input") != MVP_STORY:
        raise SystemExit("Phase 1 MVP story validation failed")


def _validate_database_url(env):
    raw_url = env.get("DATABASE_URL", "").strip()
    if not raw_url:
        raise SystemExit("DATABASE_URL is required for the Phase 1 release gate")
    url = make_url(raw_url)
    if url.get_backend_name() != "postgresql":
        raise SystemExit("DATABASE_URL must use PostgreSQL")
    if not (url.database or "").endswith("_test"):
        raise SystemExit("DATABASE_URL must name a disposable database ending in '_test'")


def _python_sources():
    roots = [
        PROJECT_ROOT / "app.py",
        PROJECT_ROOT / "roster_scraper.py",
        PROJECT_ROOT / "usage_limits.py",
    ]
    roots.extend(sorted((PROJECT_ROOT / "roster_scout").glob("*.py")))
    roots.extend(sorted((PROJECT_ROOT / "scripts").glob("*.py")))
    return [str(path.relative_to(PROJECT_ROOT)) for path in roots]


def _validate_templates():
    print("\n==> Required Flask templates", flush=True)
    required = (
        PROJECT_ROOT / "templates" / "index.html",
        PROJECT_ROOT / "templates" / "demo.html",
    )
    missing = [str(path.relative_to(PROJECT_ROOT)) for path in required if not path.is_file()]
    if missing:
        raise SystemExit(f"Missing required templates: {', '.join(missing)}")
    print("templates/index.html and templates/demo.html exist", flush=True)


def main():
    env = os.environ.copy()
    _validate_story(env)
    _validate_database_url(env)

    python = sys.executable
    _run(
        "Python compilation",
        [python, "-m", "py_compile", *_python_sources()],
        env=env,
    )
    _validate_templates()
    _run(
        "Alembic upgrade to head",
        [python, "-m", "alembic", "upgrade", "head"],
        env=env,
    )
    _run(
        "Alembic downgrade to base",
        [python, "-m", "alembic", "downgrade", "base"],
        env=env,
    )
    _run(
        "Alembic re-upgrade to head",
        [python, "-m", "alembic", "upgrade", "head"],
        env=env,
    )
    _run(
        "Alembic model drift check",
        [python, "-m", "alembic", "check"],
        env=env,
    )
    _run(
        "Full unittest discovery with PostgreSQL integrations",
        [python, "-m", "unittest", "discover", "-s", "tests", "-v"],
        env=env,
    )
    _run(
        "NCAA D1/D2/D3 catalog validation",
        [python, "scripts/validate_ncaa_catalog.py"],
        env=env,
    )
    _run(
        "Rendered Docker Compose deployment configuration",
        ["docker", "compose", "config", "--quiet"],
        env=env,
    )
    _run("Git diff hygiene", ["git", "diff", "--check"], env=env)
    print("\nPhase 1 release gate: PASS", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
