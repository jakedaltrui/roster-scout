#!/usr/bin/env python3
"""Run the fail-fast corrective Phase 2.1 release gate."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from scripts import validate_phase2_release as phase2


PROJECT_ROOT = phase2.PROJECT_ROOT
EXPECTED_MIGRATION_HEAD = "0024"
AUDITED_RELEASE_DIFF_BASE = "fa18b73"
MVP_STORY = (
    "As a coach, I want to choose an opponent before signing in and verify my "
    "school email only when I request the report, so that the workbook resumes "
    "automatically and the saved report is shared with my school staff."
)

FOCUSED_TESTS = (
    "tests.test_phase21_release.Phase21CustomerJourneyTests",
    "tests.test_organizations.InstitutionalDomainMembershipTests",
    "tests.test_identity.PasswordlessBoundaryTests",
    "tests.test_identity.AuthGateRouteMatrixTests",
    "tests.test_report_library.SharedLibraryTests",
    "tests.test_report_library.SnapshotHistoryTests",
    "tests.test_report_library.RefreshLineageTests",
    "tests.test_report_library.FailedBuildRetryTests",
    "tests.test_phase2_tenant_isolation.CrossOrganizationDenialTests",
    "tests.test_phase2_tenant_isolation.UntrustedScopeInputTests",
    "tests.test_phase2_tenant_isolation.SecurityAuditEventTests",
    "tests.test_app_routes.AuthenticatedSnapshotDownloadAcknowledgementTests",
    "tests.test_phase2_release.Phase2ReleaseStoryTests",
    "tests.test_phase2_release.ExternalFailureStateTests",
    "tests.test_artifacts.ArtifactStorageUnitTests",
    "tests.test_artifacts.ArtifactLifecycleTests",
)


def _validate_story(env):
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
    result = json.loads(completed.stdout)
    if result.get("valid") is not True or result.get("input") != MVP_STORY:
        raise SystemExit("Phase 2.1 customer story validation failed")
    print("==> Exact Phase 2.1 customer story", flush=True)


def release_command_manifest(python, *, diff_base=AUDITED_RELEASE_DIFF_BASE):
    return (
        ("Alembic upgrade to head", [python, "-m", "alembic", "upgrade", "head"]),
        (
            f"Alembic current revision is {EXPECTED_MIGRATION_HEAD}",
            [python, "-m", "alembic", "current", "--check-heads"],
        ),
        (
            "Phase 2.1 customer journey and security gates",
            [python, "-m", "unittest", *FOCUSED_TESTS, "-v"],
        ),
        (
            "Browser resume, download, and status state machines",
            [
                "node",
                "--test",
                "tests/js/home_flow.test.mjs",
                "tests/js/download_tracking.test.mjs",
                "tests/js/report_status.test.mjs",
            ],
        ),
        ("Alembic downgrade to base", [python, "-m", "alembic", "downgrade", "base"]),
        ("Alembic base-to-head upgrade", [python, "-m", "alembic", "upgrade", "head"]),
        ("Alembic model drift check", [python, "-m", "alembic", "check"]),
        (
            "Clean-process full unittest discovery",
            [python, "-m", "unittest", "discover", "-s", "tests", "-v"],
        ),
        (
            "Full Node browser suite",
            [
                "node",
                "--test",
                "tests/js/home_flow.test.mjs",
                "tests/js/download_tracking.test.mjs",
                "tests/js/report_status.test.mjs",
            ],
        ),
        ("NCAA D1/D2/D3 catalog validation", [python, "scripts/validate_ncaa_catalog.py"]),
        ("Rendered Compose contract", ["docker", "compose", "config", "--quiet"]),
        (
            "Deployment and release contracts",
            [python, "-m", "unittest", "tests.test_deployment_config", "-v"],
        ),
        (
            "Python compilation",
            [python, "-m", "py_compile", *phase2._python_sources()],
        ),
        (
            "Committed release-range diff hygiene",
            ["git", "diff", "--check", f"{diff_base}..HEAD", "--"],
        ),
        ("Working-tree diff hygiene", ["git", "diff", "--check"]),
    )


def main():
    env = os.environ.copy()
    _validate_story(env)
    phase2._validate_database_url(env)
    phase2._validate_templates()
    diff_base = phase2._resolve_commit(
        AUDITED_RELEASE_DIFF_BASE,
        description="Audited Phase 2.1 release diff base",
    )
    if not phase2._is_ancestor(diff_base, "HEAD"):
        raise SystemExit("Audited Phase 2.1 release diff base must remain an ancestor")
    for label, command in release_command_manifest(sys.executable, diff_base=diff_base):
        phase2._run(label, command, env=env)
    print("\nPhase 2.1 corrective release gate passed.", flush=True)
    print("Production launch remains blocked pending explicit user approval.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
