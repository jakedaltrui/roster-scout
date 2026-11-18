#!/usr/bin/env python3
"""Run the detailed two-sheet workbook release gate without deploying."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.validate_phase3_release import _validate_database

SAFE_ENVIRONMENT_KEYS = (
    "HOME",
    "LANG",
    "LC_ALL",
    "PATH",
    "TEMP",
    "TMP",
    "TMPDIR",
)
DATABASE_LABELS = frozenset({"Inherited Phase 3 release gate"})


def _environment(parent, label):
    environment = {
        key: parent[key]
        for key in SAFE_ENVIRONMENT_KEYS
        if parent.get(key)
    }
    if label in DATABASE_LABELS:
        environment["DATABASE_URL"] = parent["DATABASE_URL"]
        environment["PHASE2_DESTRUCTIVE_DATABASE"] = parent[
            "PHASE2_DESTRUCTIVE_DATABASE"
        ]
    return environment


def release_command_manifest(python):
    preview = PROJECT_ROOT / "outputs" / "phase4-release" / "New Orleans Team Stats 11-16-26.xlsx"
    return (
        (
            "Phase 4 workbook acceptance",
            [
                python,
                "-m",
                "unittest",
                "tests.test_phase4_release",
                "tests.test_artifacts.ArtifactContractTests",
                "tests.test_app_routes.AppRouteTests",
                "-v",
            ],
        ),
        (
            "Production-renderer preview build",
            [
                python,
                "scripts/build_phase4_preview.py",
                "--output",
                str(preview),
            ],
        ),
        (
            "Inherited Phase 3 release gate",
            [python, "scripts/validate_phase3_release.py"],
        ),
        (
            "Phase 4 Python compilation",
            [
                python,
                "-m",
                "py_compile",
                "roster_scout/artifacts.py",
                "roster_scout/workbooks.py",
                "roster_scraper.py",
                "scripts/build_phase4_preview.py",
                "scripts/validate_phase4_release.py",
                "tests/test_phase4_release.py",
            ],
        ),
        ("Working-tree diff hygiene", ["git", "diff", "--check"]),
    )


def main():
    parent = dict(os.environ)
    _validate_database(parent)
    for label, command in release_command_manifest(sys.executable):
        print(f"\n==> {label}", flush=True)
        subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            env=_environment(parent, label),
            check=True,
        )
    print("\nPhase 4 release gate passed.", flush=True)
    print("Production remains unchanged and deployment is not authorized.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
