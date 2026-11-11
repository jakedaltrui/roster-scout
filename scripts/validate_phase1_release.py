#!/usr/bin/env python3
"""Run the fail-fast, deterministic Phase 1 release gate."""

import json
import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy.engine import make_url


PROJECT_ROOT = Path(__file__).resolve().parents[1]
AUDITED_RELEASE_DIFF_BASE = "95776ed"
MVP_STORY = (
    "As a Roster Scout operator, I want to publish existing NCAA D1/D2/D3 "
    "results through a durable job into an immutable, provenance-rich canonical "
    "snapshot, so that every downstream report can rely on one auditable source "
    "of truth without breaking today's workbook workflow."
)
EXPECTED_MIGRATION_HEAD = "0013"
GAP_REGRESSION_TESTS = (
    "tests.test_report_jobs.SectionHeaderPublicationTests",
    "tests.test_snapshot_persistence.SnapshotMutationSerializationTests",
    "tests.test_snapshot_persistence.SnapshotMutationRaceTests",
    "tests.test_report_jobs.AttemptGenerationSchemaTests",
    "tests.test_report_jobs.AttemptGenerationDeliveryTests",
    "tests.test_report_jobs.CrashAfterGenerationCasRecoveryTests",
    "tests.test_snapshot_persistence.SourceIdentityConstraintTests",
    "tests.test_canonical_publish.SourceIdentityBindingTests",
    "tests.test_canonical_data.CanonicalIdentityResolutionTests",
    "tests.test_canonical_publish.UnresolvedIdentityPersistenceTests",
    "tests.test_evidence.EvidencePurgePersistenceTests",
    "tests.test_evidence.PersistedPurgeReadinessTests",
    "tests.test_deployment_config.EvidencePurgeEntrypointTests",
    "tests.test_deployment_config.PurgeIsolationTests",
    "tests.test_deployment_config.EvidencePurgeDeploymentTests",
    "tests.test_app_routes.AppReadinessTests",
    "tests.test_deployment_config.ProcessHealthCheckTests",
    "tests.test_deployment_config.IndependentPurgeConfigTests",
    "tests.test_phase1_release.Phase1ReleaseTests",
    (
        "tests.test_app_routes.AppRouteTests."
        "test_d2_selector_id_downloads_workbook_without_fuzzy_matching"
    ),
    # Plans 01-22 through 01-29: round-3 verifier counterexamples and metrics.
    "tests.test_ncaa_workflow.NcaaParserCorruptionTests",
    "tests.test_report_jobs.ParserContractRecoveryTests",
    "tests.test_canonical_publish.IdlessCorroborationPublicationTests",
    "tests.test_snapshot_persistence.AuthoritativeSourcePlayerConstraintTests",
    "tests.test_snapshot_persistence.AuthoritativeSourcePlayerPublicationTests",
    "tests.test_evidence.FailureEvidenceDatabaseRetentionTests",
    "tests.test_snapshot_persistence.FailureEvidenceRetentionMigrationTests",
    "tests.test_deployment_config.WorkerRetentionPreflightTests",
    "tests.test_report_jobs.InvalidRetentionTerminalStateTests",
    "tests.test_evidence.BatchedEvidencePurgeTests",
    "tests.test_deployment_config.BatchedPurgeRunnerTests",
    "tests.test_usage_metrics.UsageEventSchemaTests",
    "tests.test_usage_metrics.UsageServiceTests",
    "tests.test_usage_metrics.UsageReportCliTests",
    "tests.test_app_routes.DownloadEventContractTests",
    "tests.test_app_routes.WorkbookDownloadUsageTests",
)

# Round-four fixes must fail fast before the slower lifecycle and downstream
# release groups. Keep this manifest at class granularity so a renamed or
# omitted counterexample is visible to the release-contract tests.
ROUND_FOUR_COUNTEREXAMPLE_TESTS = (
    "tests.test_snapshot_persistence.AuthoritativeIdentitySerializationTests",
    (
        "tests.test_canonical_publish."
        "AuthoritativeIdentityPublicationSerializationTests"
    ),
    "tests.test_ncaa_workflow.NcaaResultCacheContractTests",
    "tests.test_browserless_lifecycle.NcaaSameTeamCollectionLockTests",
    "tests.test_usage_metrics.UsageServiceTests",
    "tests.test_app_routes.NativeWorkbookDatabaseOptionalTests",
    "tests.test_app_routes.WorkbookPreparationReceiptTests",
    "tests.test_app_routes.DownloadAcknowledgementSecretConfigurationTests",
    "tests.test_app_routes.WorkbookDownloadAcknowledgementTests",
    "tests.test_evidence.EvidencePurgeFinalizationSerializationTests",
)

MINIMAL_HEAD_SETUP_LABEL = "Minimal Alembic upgrade to head"
MINIMAL_HEAD_ASSERTION_LABEL = "Minimal Alembic current-head assertion"
ROUND_FOUR_PYTHON_LABEL = "Round-four focused Python counterexamples"
ROUND_FOUR_NODE_LABEL = "Round-four focused browser counterexamples"
MIGRATION_SERIALIZATION_LABEL = (
    "Revision 0013 authoritative serialization migration lifecycle"
)
FULL_MIGRATION_START_LABEL = "Full Alembic downgrade to base"
FULL_DISCOVERY_LABEL = "Clean-process full unittest discovery"
CATALOG_LABEL = "NCAA D1/D2/D3 catalog validation"
DEPLOYMENT_LABEL = "Rendered Compose and deployment contracts"
WORKBOOK_LABEL = "Two-sheet workbook validation"
COMPILATION_LABEL = "Python compilation"
TEMPLATE_LABEL = "Required Flask template contracts"
COMMITTED_DIFF_LABEL = "Committed release-range diff hygiene"
WORKING_TREE_DIFF_LABEL = "Working-tree diff hygiene"
DIFF_LABEL = WORKING_TREE_DIFF_LABEL


def _run(label, command, *, env):
    print(f"\n==> {label}", flush=True)
    subprocess.run(command, cwd=PROJECT_ROOT, env=env, check=True)


def _resolve_commit(ref, *, project_root, description):
    completed = subprocess.run(
        ["git", "rev-parse", "--verify", f"{ref}^{{commit}}"],
        cwd=project_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise SystemExit(f"{description} must resolve to a Git commit")
    return completed.stdout.strip()


def _is_ancestor(ancestor, descendant, *, project_root):
    completed = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=project_root,
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.returncode == 0


def resolve_release_diff_base(
    env, *, project_root=PROJECT_ROOT, audited_ref=AUDITED_RELEASE_DIFF_BASE
):
    """Return a full SHA whose range cannot omit the audited Phase 1 history."""
    audited_base = _resolve_commit(
        audited_ref,
        project_root=project_root,
        description="Audited release diff base",
    )
    if not _is_ancestor(audited_base, "HEAD", project_root=project_root):
        raise SystemExit("Audited release diff base must remain an ancestor of HEAD")

    if "PHASE1_RELEASE_DIFF_BASE" not in env:
        return audited_base
    override = env["PHASE1_RELEASE_DIFF_BASE"].strip()
    if not override:
        raise SystemExit("PHASE1_RELEASE_DIFF_BASE cannot be empty")
    selected_base = _resolve_commit(
        override,
        project_root=project_root,
        description="PHASE1_RELEASE_DIFF_BASE",
    )
    if not _is_ancestor(selected_base, audited_base, project_root=project_root):
        raise SystemExit(
            "PHASE1_RELEASE_DIFF_BASE must equal or predate the audited release base"
        )
    return selected_base


def committed_diff_command(diff_base):
    return ["git", "diff", "--check", f"{diff_base}..HEAD", "--"]


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


def release_command_manifest(python, *, diff_base=None):
    """Return every deterministic release command in fail-fast order."""
    if diff_base is None:
        diff_base = resolve_release_diff_base(os.environ)
    return (
        (
            MINIMAL_HEAD_SETUP_LABEL,
            [python, "-m", "alembic", "upgrade", "head"],
        ),
        (
            MINIMAL_HEAD_ASSERTION_LABEL,
            [python, "-m", "alembic", "current", "--check-heads"],
        ),
        (
            ROUND_FOUR_PYTHON_LABEL,
            [python, "-m", "unittest", *ROUND_FOUR_COUNTEREXAMPLE_TESTS, "-v"],
        ),
        (
            ROUND_FOUR_NODE_LABEL,
            ["node", "--test", "tests/js/download_tracking.test.mjs"],
        ),
        (
            MIGRATION_SERIALIZATION_LABEL,
            [
                python,
                "-m",
                "unittest",
                (
                    "tests.test_snapshot_persistence."
                    "AuthoritativeIdentitySerializationMigrationTests"
                ),
                "-v",
            ],
        ),
        (
            FULL_MIGRATION_START_LABEL,
            [python, "-m", "alembic", "downgrade", "base"],
        ),
        (
            "Full Alembic base-to-head upgrade",
            [python, "-m", "alembic", "upgrade", "head"],
        ),
        (
            "Full Alembic re-upgrade to head",
            [python, "-m", "alembic", "upgrade", "head"],
        ),
        (
            f"Full Alembic current revision is {EXPECTED_MIGRATION_HEAD}",
            [python, "-m", "alembic", "current", "--check-heads"],
        ),
        (
            "Alembic model drift check",
            [python, "-m", "alembic", "check"],
        ),
        (
            FULL_DISCOVERY_LABEL,
            [python, "-m", "unittest", "discover", "-s", "tests", "-v"],
        ),
        (
            CATALOG_LABEL,
            [python, "scripts/validate_ncaa_catalog.py"],
        ),
        (
            DEPLOYMENT_LABEL,
            ["docker", "compose", "config", "--quiet"],
        ),
        (
            "Deployment contract tests",
            [python, "-m", "unittest", "tests.test_deployment_config", "-v"],
        ),
        (
            WORKBOOK_LABEL,
            [
                python,
                "-m",
                "unittest",
                (
                    "tests.test_app_routes.AppRouteTests."
                    "test_d2_selector_id_downloads_workbook_without_fuzzy_matching"
                ),
                "-v",
            ],
        ),
        (
            COMPILATION_LABEL,
            [python, "-m", "py_compile", *_python_sources()],
        ),
        (
            TEMPLATE_LABEL,
            [
                python,
                "-m",
                "unittest",
                "tests.test_app_routes.DownloadEventContractTests",
                "-v",
            ],
        ),
        (COMMITTED_DIFF_LABEL, committed_diff_command(diff_base)),
        (WORKING_TREE_DIFF_LABEL, ["git", "diff", "--check"]),
    )


def main():
    env = os.environ.copy()
    _validate_story(env)
    _validate_database_url(env)

    python = sys.executable
    diff_base = resolve_release_diff_base(env)
    print(f"==> Release diff base: {diff_base}", flush=True)
    commands = release_command_manifest(python, diff_base=diff_base)
    for label, command in commands:
        if label == TEMPLATE_LABEL:
            _validate_templates()
        _run(label, command, env=env)
    print("\nPhase 1 release gate: PASS", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
