#!/usr/bin/env python3
"""Run the authoritative disposable-PostgreSQL Phase 3 release gate."""

from __future__ import annotations

import ipaddress
import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError, SQLAlchemyError


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_MIGRATION_HEAD = "0025"
SAFE_DATABASE_ERROR = "phase3_test_database_unavailable"

MIGRATION_TESTS = (
    "tests.test_phase3_release.MigrationLifecycleTests",
)
FOCUSED_TESTS = (
    "tests.test_report_jobs.FullSeasonMetricPublicationTests",
    (
        "tests.test_report_jobs.SectionHeaderPublicationTests."
        "test_real_collector_persists_schedule_history_and_typed_game_logs"
    ),
    (
        "tests.test_report_jobs.SectionHeaderPublicationTests."
        "test_real_collector_complete_phase3_evidence_publishes_ready"
    ),
    (
        "tests.test_report_jobs.SectionHeaderPublicationTests."
        "test_real_collector_budget_boundary_leaves_no_planned_ledgers"
    ),
    (
        "tests.test_report_jobs.SectionHeaderPublicationTests."
        "test_valid_d1_d2_d3_pages_publish_partial_when_split_transport_is_unavailable"
    ),
    (
        "tests.test_report_jobs.SectionHeaderPublicationTests."
        "test_live_page_reservation_is_atomic_under_concurrency"
    ),
    (
        "tests.test_report_jobs.SectionHeaderPublicationTests."
        "test_retry_generation_reuses_durable_budget_without_ledger_collision"
    ),
    (
        "tests.test_report_jobs.SectionHeaderPublicationTests."
        "test_complete_retry_generation_ignores_prior_failed_ledgers"
    ),
    (
        "tests.test_report_jobs.SectionHeaderPublicationTests."
        "test_reconciliation_terminalizes_planned_generation_before_redelivery"
    ),
    (
        "tests.test_report_jobs.SectionHeaderPublicationTests."
        "test_supplemental_deadline_persists_sanitized_unavailable_sections"
    ),
    (
        "tests.test_report_jobs.SectionHeaderPublicationTests."
        "test_public_collector_transport_cap_persists_budget_exhaustion"
    ),
    (
        "tests.test_ncaa_detail.FixtureCaptureSafetyTests."
        "test_schedule_transport_uses_the_smaller_navigation_or_wall_timeout"
    ),
    (
        "tests.test_ncaa_detail.FixtureCaptureSafetyTests."
        "test_terms_retry_reserves_the_followup_navigation"
    ),
    (
        "tests.test_ncaa_detail.FixtureCaptureSafetyTests."
        "test_player_transport_accepts_preloaded_first_url"
    ),
    (
        "tests.test_ncaa_detail.FixtureCaptureSafetyTests."
        "test_terms_dwell_stops_at_absolute_build_deadline"
    ),
    (
        "tests.test_browserless_lifecycle.BrowserlessLifecycleTests."
        "test_public_transport_preserves_collection_control_signals"
    ),
    (
        "tests.test_ncaa_detail.FixtureCaptureSafetyTests."
        "test_schedule_capture_rejects_pagination_unknown_results_and_malformed_rows"
    ),
    "tests.test_phase3_release.MetricConstantSetTests",
    "tests.test_ncaa_detail.FieldingTests",
    "tests.test_ncaa_detail.FieldingPublicationTests",
    "tests.test_ncaa_detail.SplitDiscoveryTests",
    "tests.test_ncaa_detail.BattingSplitTests",
    "tests.test_ncaa_detail.PitchingSplitTests",
    "tests.test_ncaa_detail.SplitProvenanceTests",
    "tests.test_ncaa_detail.SplitAvailabilityTests",
    "tests.test_ncaa_detail.SplitPublicationTests",
    "tests.test_player_history.CareerSeasonTests",
    "tests.test_player_history.CareerAggregateTests",
    "tests.test_player_history.GameLogTests",
    "tests.test_player_history.HistoryCompletenessTests",
    "tests.test_player_history.SnapshotDrillPathTests",
    "tests.test_report_library.SnapshotPlayerDetailRouteTests",
    "tests.test_scoped_stats.ScheduleFixtureTests",
    "tests.test_scoped_stats.CanonicalScopeModelTests",
    "tests.test_scoped_stats.LastTenScopeTests",
    "tests.test_scoped_stats.ConferenceScopeTests",
    "tests.test_scoped_stats.ScopedAggregationTests",
    "tests.test_player_history.CollectionBudgetTests",
    "tests.test_phase3_release.ExternalRefreshDiagnosticTests",
    "tests.test_phase3_release.OperatorDiagnosticsTests",
    "tests.test_phase3_release.DivisionParityTests",
    "tests.test_phase3_release.Phase3SchemaSecurityTests",
    "tests.test_snapshot_persistence.SnapshotMutationSerializationTests",
)


def _fail_database() -> None:
    raise SystemExit(SAFE_DATABASE_ERROR)


def _validate_database(environment: dict[str, str]) -> None:
    """Reject anything except the executor-owned loopback PostgreSQL test DB."""
    raw_url = environment.get("DATABASE_URL", "").strip()
    try:
        url = make_url(raw_url)
    except (ArgumentError, TypeError, ValueError):
        _fail_database()
    if (
        not raw_url
        or url.drivername != "postgresql+psycopg"
        or not (url.database or "").endswith("_test")
        or environment.get("PHASE2_DESTRUCTIVE_DATABASE") != "local-disposable"
    ):
        _fail_database()
    host = (url.host or "").strip().lower()
    is_loopback = host == "localhost"
    try:
        is_loopback = is_loopback or ipaddress.ip_address(host).is_loopback
    except ValueError:
        pass
    if not is_loopback:
        _fail_database()

    engine = create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        _fail_database()
    finally:
        engine.dispose()


def _required_paths() -> tuple[Path, ...]:
    paths = [
        PROJECT_ROOT / "templates" / "index.html",
        PROJECT_ROOT / "templates" / "demo.html",
        PROJECT_ROOT / "roster_scout" / "metrics.py",
        PROJECT_ROOT / "roster_scout" / "ncaa_detail.py",
        PROJECT_ROOT / "roster_scout" / "player_detail.py",
        PROJECT_ROOT / "roster_scout" / "scopes.py",
        PROJECT_ROOT / "scripts" / "capture_phase3_ncaa_fixtures.py",
        PROJECT_ROOT / "scripts" / "inspect_report_jobs.py",
        PROJECT_ROOT / "scripts" / "prepare_phase3_test_database.py",
        PROJECT_ROOT / "scripts" / "validate_phase3_release.py",
    ]
    fixture_root = PROJECT_ROOT / "tests" / "fixtures" / "ncaa_detail"
    fixture_names = (
        "fielding.html",
        "split_controls.html",
        "batting_split_result.html",
        "pitching_split_result.html",
        "player_detail.html",
        "schedule.html",
        "fixture_manifest.json",
    )
    paths.extend(
        fixture_root / division / name
        for division in ("d1", "d2", "d3")
        for name in fixture_names
    )
    return tuple(paths)


def _validate_required_paths() -> None:
    missing = [
        str(path.relative_to(PROJECT_ROOT))
        for path in _required_paths()
        if not path.is_file()
    ]
    if missing:
        raise SystemExit("phase3_required_file_missing")


def _python_sources() -> list[str]:
    roots = [
        PROJECT_ROOT / "app.py",
        PROJECT_ROOT / "roster_scraper.py",
        PROJECT_ROOT / "usage_limits.py",
    ]
    roots.extend(sorted((PROJECT_ROOT / "roster_scout").glob("*.py")))
    roots.extend(sorted((PROJECT_ROOT / "scripts").glob("*.py")))
    return [str(path.relative_to(PROJECT_ROOT)) for path in roots]


def release_command_manifest(python: str) -> tuple[tuple[str, list[str]], ...]:
    return (
        ("Alembic upgrade to head", [python, "-m", "alembic", "upgrade", "head"]),
        (
            f"Alembic current revision is {EXPECTED_MIGRATION_HEAD}",
            [python, "-m", "alembic", "current", "--check-heads"],
        ),
        (
            "Revision 0024 to 0025 preservation and lifecycle",
            [python, "-m", "unittest", *MIGRATION_TESTS, "-v"],
        ),
        ("Alembic model drift check", [python, "-m", "alembic", "check"]),
        (
            "Phase 3 focused parity, lineage, tenant, and security gates",
            [python, "-m", "unittest", *FOCUSED_TESTS, "-v"],
        ),
        (
            "Inherited Phase 2.1 customer and security gate",
            [python, "scripts/validate_phase21_release.py"],
        ),
        (
            "Clean-process full unittest discovery",
            [python, "-m", "unittest", "discover", "-s", "tests", "-v"],
        ),
        (
            "NCAA D1/D2/D3 catalog validation",
            [python, "scripts/validate_ncaa_catalog.py"],
        ),
        (
            "Python compilation",
            [python, "-m", "py_compile", *_python_sources()],
        ),
        ("Working-tree diff hygiene", ["git", "diff", "--check"]),
    )


def _run(label: str, command: list[str], *, environment: dict[str, str]) -> None:
    print(f"\n==> {label}", flush=True)
    subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        env=environment,
        check=True,
    )


def main() -> int:
    environment = os.environ.copy()
    _validate_database(environment)
    _validate_required_paths()
    for label, command in release_command_manifest(sys.executable):
        _run(label, command, environment=environment)
    print("\nPhase 3 release gate passed.", flush=True)
    print("Production remains unchanged and deployment is not authorized.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
