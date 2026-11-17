"""Bounded retention for saved reports, workbook objects, and raw usage events."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import os
import uuid

from sqlalchemy import func, select, text

from .artifacts import ArtifactStorageError
from .models import ProductUsageEvent, ReportArtifact, ReportSnapshot


REPORT_RETENTION_ENV = "REPORT_RETENTION_DAYS"
USAGE_RETENTION_ENV = "USAGE_EVENT_RETENTION_DAYS"
DEFAULT_REPORT_RETENTION_DAYS = 30
DEFAULT_USAGE_RETENTION_DAYS = 90
MIN_REPORT_RETENTION_DAYS = 14
MAX_REPORT_RETENTION_DAYS = 90
MIN_USAGE_RETENTION_DAYS = 30
MAX_USAGE_RETENTION_DAYS = 365


@dataclass(frozen=True)
class RetentionConfig:
    report_days: int = DEFAULT_REPORT_RETENTION_DAYS
    usage_days: int = DEFAULT_USAGE_RETENTION_DAYS

    @classmethod
    def from_environ(cls, environ=None):
        values = os.environ if environ is None else environ
        return cls(
            report_days=_bounded_days(
                values.get(REPORT_RETENTION_ENV, DEFAULT_REPORT_RETENTION_DAYS),
                name=REPORT_RETENTION_ENV,
                minimum=MIN_REPORT_RETENTION_DAYS,
                maximum=MAX_REPORT_RETENTION_DAYS,
            ),
            usage_days=_bounded_days(
                values.get(USAGE_RETENTION_ENV, DEFAULT_USAGE_RETENTION_DAYS),
                name=USAGE_RETENTION_ENV,
                minimum=MIN_USAGE_RETENTION_DAYS,
                maximum=MAX_USAGE_RETENTION_DAYS,
            ),
        )


@dataclass(frozen=True)
class RetentionResult:
    snapshots_deleted: int
    artifacts_deleted: int
    usage_events_deleted: int
    skipped_snapshots: int = 0


@dataclass(frozen=True)
class _SnapshotCandidate:
    snapshot_id: uuid.UUID
    organization_id: uuid.UUID
    report_id: uuid.UUID
    object_keys: tuple[str, ...]


def _bounded_days(value, *, name, minimum, maximum):
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a whole number from {minimum} to {maximum}") from None
    if not minimum <= parsed <= maximum:
        raise ValueError(f"{name} must be from {minimum} to {maximum} days")
    return parsed


def _candidate_snapshots(session, *, cutoff, limit):
    rows = session.execute(
        select(
            ReportSnapshot.id,
            ReportSnapshot.organization_id,
            ReportSnapshot.report_id,
        )
        .where(
            func.coalesce(
                ReportSnapshot.published_at,
                ReportSnapshot.created_at,
            )
            < cutoff
        )
        .order_by(
            func.coalesce(
                ReportSnapshot.published_at,
                ReportSnapshot.created_at,
            ),
            ReportSnapshot.id,
        )
        .limit(limit)
    ).all()
    candidates = []
    for snapshot_id, organization_id, report_id in rows:
        keys = tuple(
            key
            for key in session.scalars(
                select(ReportArtifact.object_key).where(
                    ReportArtifact.snapshot_id == snapshot_id,
                    ReportArtifact.organization_id == organization_id,
                    ReportArtifact.object_key.is_not(None),
                )
            )
            if key
        )
        candidates.append(
            _SnapshotCandidate(
                snapshot_id=snapshot_id,
                organization_id=organization_id,
                report_id=report_id,
                object_keys=keys,
            )
        )
    return tuple(candidates)


_SNAPSHOT_CHILD_TABLES = (
    "batting_game_log_facts",
    "pitching_game_log_facts",
    "fielding_game_log_facts",
    "statistic_scope_games",
    "scoped_batting_facts",
    "scoped_pitching_facts",
    "scoped_fielding_facts",
    "metric_values",
    "career_aggregates",
    "career_season_facts",
    "batting_facts",
    "pitching_facts",
    "fielding_facts",
    "games",
    "season_conference_evidence",
    "statistic_scopes",
    "report_sections",
    "request_ledgers",
    "source_records",
    "player_team_seasons",
    "external_refresh_diagnostics",
    "report_artifacts",
)


def _delete_snapshot_graph(session, candidate, *, cutoff):
    snapshot_id = candidate.snapshot_id
    organization_id = candidate.organization_id
    report_id = candidate.report_id
    retained = session.execute(
        text(
            """
            SELECT 1
            FROM report_snapshots
            WHERE id = :snapshot_id
              AND organization_id = :organization_id
              AND COALESCE(published_at, created_at) < :cutoff
            FOR UPDATE
            """
        ),
        {
            "snapshot_id": snapshot_id,
            "organization_id": organization_id,
            "cutoff": cutoff,
        },
    ).first()
    if retained is None:
        return False

    session.execute(text("SET LOCAL roster_scout.retention_purge = 'on'"))
    job_ids = tuple(
        row[0]
        for row in session.execute(
            text(
                """
                SELECT id FROM report_jobs
                WHERE result_snapshot_id = :snapshot_id
                  AND organization_id = :organization_id
                """
            ),
            {
                "snapshot_id": snapshot_id,
                "organization_id": organization_id,
            },
        )
    )

    session.execute(
        text(
            """
            UPDATE report_snapshots
            SET predecessor_snapshot_id = NULL
            WHERE predecessor_snapshot_id = :snapshot_id
              AND report_id = :report_id
              AND organization_id = :organization_id
            """
        ),
        {
            "snapshot_id": snapshot_id,
            "report_id": report_id,
            "organization_id": organization_id,
        },
    )

    parameters = {
        "snapshot_id": snapshot_id,
        "organization_id": organization_id,
    }
    for table_name in _SNAPSHOT_CHILD_TABLES:
        session.execute(
            text(
                f"DELETE FROM {table_name} "
                "WHERE snapshot_id = :snapshot_id "
                "AND organization_id = :organization_id"
            ),
            parameters,
        )

    if job_ids:
        job_parameters = {
            "job_ids": list(job_ids),
            "organization_id": organization_id,
        }
        for table_name in (
            "failure_evidence",
            "report_job_dispatch_outbox",
            "request_ledgers",
            "report_sections",
        ):
            session.execute(
                text(
                    f"DELETE FROM {table_name} "
                    "WHERE job_id = ANY(:job_ids) "
                    "AND organization_id = :organization_id"
                ),
                job_parameters,
            )
        session.execute(
            text(
                "DELETE FROM report_jobs WHERE id = ANY(:job_ids) "
                "AND organization_id = :organization_id"
            ),
            job_parameters,
        )

    session.execute(
        text(
            "DELETE FROM report_snapshots "
            "WHERE id = :snapshot_id AND organization_id = :organization_id"
        ),
        parameters,
    )
    session.execute(
        text(
            """
            DELETE FROM reports
            WHERE id = :report_id
              AND organization_id = :organization_id
              AND NOT EXISTS (
                  SELECT 1 FROM report_snapshots
                  WHERE report_id = :report_id
                    AND organization_id = :organization_id
              )
              AND NOT EXISTS (
                  SELECT 1 FROM report_jobs
                  WHERE report_id = :report_id
                    AND organization_id = :organization_id
              )
            """
        ),
        {
            "report_id": report_id,
            "organization_id": organization_id,
        },
    )
    return True


def purge_expired_report_data(
    session_factory,
    *,
    store,
    config=None,
    now=None,
    batch_size=25,
):
    """Delete expired workbook objects before their exact database graph."""
    config = config or RetentionConfig.from_environ()
    current = now or datetime.now(UTC)
    cutoff = current - timedelta(days=config.report_days)
    with session_factory() as session:
        candidates = _candidate_snapshots(
            session,
            cutoff=cutoff,
            limit=max(1, min(int(batch_size), 100)),
        )

    snapshots_deleted = 0
    artifacts_deleted = 0
    skipped = 0
    for candidate in candidates:
        if candidate.object_keys and store is None:
            skipped += 1
            continue
        try:
            for key in candidate.object_keys:
                store.delete(key)
                artifacts_deleted += 1
        except ArtifactStorageError:
            skipped += 1
            continue
        with session_factory.begin() as session:
            if _delete_snapshot_graph(session, candidate, cutoff=cutoff):
                snapshots_deleted += 1

    usage_cutoff = current - timedelta(days=config.usage_days)
    with session_factory.begin() as session:
        usage_events_deleted = session.execute(
            text(
                "DELETE FROM product_usage_events "
                "WHERE occurred_at < :cutoff"
            ),
            {"cutoff": usage_cutoff},
        ).rowcount

    return RetentionResult(
        snapshots_deleted=snapshots_deleted,
        artifacts_deleted=artifacts_deleted,
        usage_events_deleted=max(0, int(usage_events_deleted or 0)),
        skipped_snapshots=skipped,
    )
