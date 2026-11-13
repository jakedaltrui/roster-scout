#!/usr/bin/env python3
"""Run the fail-fast deterministic Phase 2 release gate."""

from __future__ import annotations

import json
import ipaddress
import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy.exc import ArgumentError
from sqlalchemy.engine import make_url


PROJECT_ROOT = Path(__file__).resolve().parents[1]
# The parent of the first Phase 2 implementation commit. Earlier Phase 1 and
# Phase 2 planning documents intentionally contain Markdown hard-break spaces,
# so extending this range backward would turn unrelated planning style into a
# false release failure while auditing no additional Phase 2 code.
AUDITED_RELEASE_DIFF_BASE = "fa18b73"
MVP_STORY = (
    "As a signed-in Roster Scout customer, I want to collaborate inside "
    "explicit organizations and manage persistent, refreshable opponent reports, "
    "so that my staff can reuse dated scouting reports without cross-organization "
    "access."
)
EXPECTED_MIGRATION_HEAD = "0020"

PHASE2_FOCUSED_TESTS = (
    "tests.test_phase2_release.Phase2ReleaseStoryTests",
    "tests.test_phase2_release.ExternalFailureStateTests",
    "tests.test_identity.PasswordlessBoundaryTests",
    "tests.test_identity.ExternalIdentityLinkingTests",
    "tests.test_identity.ServerSessionLifecycleTests",
    "tests.test_identity.AuthGateRouteMatrixTests",
    "tests.test_identity.EnterpriseSsoAuthorizationTests",
    "tests.test_identity.SecretRedactionTests",
    "tests.test_organizations.OrganizationOwnershipSchemaTests",
    "tests.test_organizations.InvitationCreationTests",
    "tests.test_organizations.InvitationAcceptanceTests",
    "tests.test_organizations.RoleAuthorizationTests",
    "tests.test_organizations.MembershipAdministrationTests",
    "tests.test_organizations.NoDomainAutoJoinTests",
    "tests.test_organizations.OrganizationBootstrapCliTests",
    "tests.test_organizations.OrganizationInvitationRouteTests",
    "tests.test_report_library.SharedLibraryTests",
    "tests.test_report_library.SnapshotHistoryTests",
    "tests.test_report_library.RefreshLineageTests",
    "tests.test_report_library.FailedBuildRetryTests",
    "tests.test_report_library.ReportAcceptanceIdempotencyTests",
    "tests.test_phase2_tenant_isolation.CrossOrganizationDenialTests",
    "tests.test_phase2_tenant_isolation.UntrustedScopeInputTests",
    "tests.test_phase2_tenant_isolation.SecurityAuditEventTests",
    "tests.test_app_routes.AuthenticatedSnapshotDownloadAcknowledgementTests",
    (
        "tests.test_app_routes.AppRouteTests."
        "test_rate_limit_is_per_client_and_returns_retry_header"
    ),
    "tests.test_app_routes.AppRouteTests.test_browser_capacity_returns_503",
    (
        "tests.test_browserless_lifecycle.BrowserlessLifecycleTests."
        "test_upstream_429_becomes_capacity_error_without_retry"
    ),
)

PHASE2_FOCUSED_PYTHON_LABEL = "Phase 2 focused story, security, download, and 429 gates"
PHASE2_FOCUSED_NODE_LABEL = "Phase 2 focused browser state machines"
FULL_MIGRATION_START_LABEL = "Full Alembic downgrade to base"
FULL_DISCOVERY_LABEL = "Clean-process full unittest discovery"
FULL_NODE_LABEL = "Full Node browser suite"
CATALOG_LABEL = "NCAA D1/D2/D3 catalog validation"
DEPLOYMENT_LABEL = "Rendered Compose and deployment contracts"
WORKBOOK_LABEL = "Readable Hitters and Pitchers workbook compatibility"
COMPILATION_LABEL = "Python compilation"
TEMPLATE_LABEL = "Required Flask template contracts"
COMMITTED_DIFF_LABEL = "Committed release-range diff hygiene"
WORKING_TREE_DIFF_LABEL = "Working-tree diff hygiene"


def _run(label, command, *, env):
    print(f"\n==> {label}", flush=True)
    subprocess.run(command, cwd=PROJECT_ROOT, env=env, check=True)


def _resolve_commit(ref, *, description):
    completed = subprocess.run(
        ["git", "rev-parse", "--verify", f"{ref}^{{commit}}"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise SystemExit(f"{description} must resolve to a Git commit")
    return completed.stdout.strip()


def _is_ancestor(ancestor, descendant):
    return subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=PROJECT_ROOT,
        check=False,
    ).returncode == 0


def resolve_release_diff_base(env):
    audited = _resolve_commit(
        AUDITED_RELEASE_DIFF_BASE,
        description="Audited Phase 2 release diff base",
    )
    if not _is_ancestor(audited, "HEAD"):
        raise SystemExit("Audited release diff base must remain an ancestor of HEAD")
    override = env.get("PHASE2_RELEASE_DIFF_BASE")
    if override is None:
        return audited
    override = override.strip()
    if not override:
        raise SystemExit("PHASE2_RELEASE_DIFF_BASE cannot be empty")
    selected = _resolve_commit(
        override,
        description="PHASE2_RELEASE_DIFF_BASE",
    )
    if not _is_ancestor(selected, audited):
        raise SystemExit(
            "PHASE2_RELEASE_DIFF_BASE must equal or predate the audited release base"
        )
    return selected


def _validate_story(env):
    print("==> Exact Phase 2 MVP user story", flush=True)
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
        raise SystemExit("Phase 2 MVP story validation failed")


def _validate_database_url(env):
    raw_url = env.get("DATABASE_URL", "").strip()
    if not raw_url:
        raise SystemExit("DATABASE_URL is required for the Phase 2 release gate")
    try:
        url = make_url(raw_url)
    except (ArgumentError, TypeError, ValueError):
        raise SystemExit("DATABASE_URL is invalid") from None
    if url.drivername != "postgresql+psycopg":
        raise SystemExit("DATABASE_URL must use PostgreSQL with psycopg")
    if env.get("PHASE2_DESTRUCTIVE_DATABASE") != "local-disposable":
        raise SystemExit(
            "PHASE2_DESTRUCTIVE_DATABASE=local-disposable is required before destructive lifecycle checks"
        )
    if url.query:
        raise SystemExit(
            "destructive lifecycle checks reject PostgreSQL URL query parameters"
        )
    try:
        positional, effective = url.get_dialect()().create_connect_args(url)
    except Exception:
        raise SystemExit("DATABASE_URL is invalid") from None
    if positional:
        raise SystemExit(
            "destructive lifecycle checks require explicit keyword connection targets"
        )
    database = effective.get("dbname")
    if not isinstance(database, str) or not database.endswith("_test"):
        raise SystemExit("DATABASE_URL must name a disposable database ending in '_test'")
    host_value = effective.get("host")
    if not isinstance(host_value, str) or not host_value:
        raise SystemExit(
            "destructive lifecycle checks require an explicit loopback PostgreSQL target"
        )
    host = host_value.strip().lower()
    local = host == "localhost"
    try:
        local = local or ipaddress.ip_address(host).is_loopback
    except ValueError:
        pass
    if not local:
        raise SystemExit(
            "destructive lifecycle checks require an explicit loopback PostgreSQL target"
        )
    redacted = url.render_as_string(hide_password=True)
    print(f"==> Disposable database target: {redacted}", flush=True)


def _python_sources():
    paths = [
        PROJECT_ROOT / "app.py",
        PROJECT_ROOT / "roster_scraper.py",
        PROJECT_ROOT / "usage_limits.py",
    ]
    paths.extend(sorted((PROJECT_ROOT / "roster_scout").glob("*.py")))
    paths.extend(sorted((PROJECT_ROOT / "scripts").glob("*.py")))
    return [str(path.relative_to(PROJECT_ROOT)) for path in paths]


def _validate_templates():
    required = (
        "index.html",
        "demo.html",
        "product_base.html",
        "sign_in.html",
        "auth_error.html",
        "organization_select.html",
        "organization_members.html",
        "invitation_outcome.html",
        "report_library.html",
        "report_new.html",
        "report_detail.html",
    )
    missing = [name for name in required if not (PROJECT_ROOT / "templates" / name).is_file()]
    if missing:
        raise SystemExit(f"Missing required templates: {', '.join(missing)}")
    print("Phase 1 and Phase 2 templates exist", flush=True)


def production_auth_verified(env):
    """Return true only for an explicit, complete operator-owned smoke record."""
    raw_path = env.get("ROSTER_SCOUT_PRODUCTION_AUTH_SMOKE_RECORD", "").strip()
    if not raw_path:
        return False
    try:
        record = json.loads(Path(raw_path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return False
    required_true = (
        "workspace_owned",
        "billing_verified",
        "secrets_installed",
        "dashboard_uris_verified",
        "live_passwordless_email_verified",
        "google_verified",
        "microsoft_verified",
        "customer_idp_verified",
    )
    return (
        record.get("production_auth_verified") is True
        and record.get("environment") == "production"
        and isinstance(record.get("recorded_at"), str)
        and all(record.get(name) is True for name in required_true)
    )


def require_production_launch_ready(env):
    if not production_auth_verified(env):
        raise SystemExit(
            "production_auth_verified=false; production launch remains blocked"
        )


def release_command_manifest(python, *, diff_base=None):
    """Return every deterministic Phase 2 release command in fail-fast order."""
    if diff_base is None:
        diff_base = resolve_release_diff_base(os.environ)
    return (
        (
            "Minimal Alembic upgrade to head",
            [python, "-m", "alembic", "upgrade", "head"],
        ),
        (
            "Minimal Alembic current-head assertion",
            [python, "-m", "alembic", "current", "--check-heads"],
        ),
        (
            PHASE2_FOCUSED_PYTHON_LABEL,
            [python, "-m", "unittest", *PHASE2_FOCUSED_TESTS, "-v"],
        ),
        (
            PHASE2_FOCUSED_NODE_LABEL,
            [
                "node",
                "--test",
                "tests/js/report_status.test.mjs",
                "tests/js/download_tracking.test.mjs",
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
            FULL_NODE_LABEL,
            [
                "node",
                "--test",
                "tests/js/download_tracking.test.mjs",
                "tests/js/report_status.test.mjs",
            ],
        ),
        (CATALOG_LABEL, [python, "scripts/validate_ncaa_catalog.py"]),
        (DEPLOYMENT_LABEL, ["docker", "compose", "config", "--quiet"]),
        (
            "Deployment and release contract tests",
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
                "tests.test_identity.AuthGateRouteMatrixTests",
                "-v",
            ],
        ),
        (
            COMMITTED_DIFF_LABEL,
            ["git", "diff", "--check", f"{diff_base}..HEAD", "--"],
        ),
        (WORKING_TREE_DIFF_LABEL, ["git", "diff", "--check"]),
    )


def main():
    env = os.environ.copy()
    _validate_story(env)
    _validate_database_url(env)
    diff_base = resolve_release_diff_base(env)
    print(f"==> Release diff base: {diff_base}", flush=True)
    for label, command in release_command_manifest(sys.executable, diff_base=diff_base):
        if label == TEMPLATE_LABEL:
            print("\n==> Required Flask templates", flush=True)
            _validate_templates()
        _run(label, command, env=env)

    live_verified = production_auth_verified(env)
    print("\ncode_verified=true", flush=True)
    print(
        f"production_auth_verified={'true' if live_verified else 'false'}",
        flush=True,
    )
    print(
        f"production_launch_ready={'true' if live_verified else 'false'}",
        flush=True,
    )
    if env.get("PHASE2_PRODUCTION_LAUNCH_CHECK") == "1":
        require_production_launch_ready(env)
    print("Phase 2 deterministic release gate: PASS", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
