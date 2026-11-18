#!/usr/bin/env python3
"""Run the native-dashboard and retention release gate without deploying."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.validate_phase3_release import _validate_database
from tests.db_support import clean_database


SAFE_ENVIRONMENT_KEYS = (
    "HOME", "LANG", "LC_ALL", "PATH", "TEMP", "TMP", "TMPDIR",
)
DATABASE_LABELS = frozenset({
    "Inherited detailed workbook release gate",
    "Retention migration downgrade",
    "Retention migration upgrade",
    "Migration head and drift",
    "Alembic model drift",
    "Native dashboard and retention contracts",
})


def _environment(parent, label):
    environment = {
        key: parent[key] for key in SAFE_ENVIRONMENT_KEYS if parent.get(key)
    }
    if label in DATABASE_LABELS:
        environment["DATABASE_URL"] = parent["DATABASE_URL"]
    if label == "Inherited detailed workbook release gate":
        environment["PHASE2_DESTRUCTIVE_DATABASE"] = parent[
            "PHASE2_DESTRUCTIVE_DATABASE"
        ]
    return environment


def release_command_manifest(python):
    return (
        (
            "Inherited detailed workbook release gate",
            [python, "scripts/validate_phase4_release.py"],
        ),
        (
            "Retention migration downgrade",
            [python, "-m", "alembic", "downgrade", "0026"],
        ),
        (
            "Retention migration upgrade",
            [python, "-m", "alembic", "upgrade", "head"],
        ),
        (
            "Migration head and drift",
            [python, "-m", "alembic", "current", "--check-heads"],
        ),
        (
            "Alembic model drift",
            [python, "-m", "alembic", "check"],
        ),
        (
            "Native dashboard and retention contracts",
            [
                python, "-m", "unittest",
                "tests.test_native_dashboard",
                "tests.test_retention",
                "tests.test_power_bi_ui",
                "-v",
            ],
        ),
        (
            "Dashboard JavaScript syntax",
            ["node", "--check", "static/native_dashboard.js"],
        ),
        (
            "Phase 5 compilation",
            [
                python, "-m", "py_compile",
                "roster_scout/native_dashboard.py",
                "roster_scout/report_routes.py",
                "roster_scout/retention.py",
                "roster_scout/tasks.py",
                "scripts/run_purge.py",
                "scripts/validate_phase5_release.py",
                "tests/test_native_dashboard.py",
                "tests/test_retention.py",
                "tests/test_power_bi_ui.py",
            ],
        ),
        ("Working-tree diff hygiene", ["git", "diff", "--check"]),
    )


def main():
    parent = dict(os.environ)
    _validate_database(parent)
    for label, command in release_command_manifest(sys.executable):
        if label == "Retention migration downgrade":
            print("\n==> Reset disposable migration fixture", flush=True)
            clean_database(parent["DATABASE_URL"])
        print(f"\n==> {label}", flush=True)
        subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            env=_environment(parent, label),
            check=True,
        )
    print("\nPhase 5 deterministic release gate passed.", flush=True)
    print(
        "The native dashboard and bounded retention policy are release-tested; "
        "deployment is not authorized by this command.",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
