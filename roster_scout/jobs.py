"""PostgreSQL-authoritative report-job acceptance and execution."""

import hashlib
import json
import os
import random
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert

from roster_scraper import NCAA_PARSER_VERSION
from usage_limits import BrowserCapacityExceeded

from .database import session_advisory_lock
from .models import (
    BattingFact,
    MetricDefinition,
    MetricValue,
    PitchingFact,
    Player,
    PlayerTeamSeason,
    Report,
    ReportJob,
    ReportJobDispatch,
    ReportSection,
    ReportSnapshot,
    Season,
    SourceRecord,
    Team,
    TeamSeason,
)
from .snapshots import SnapshotPublicationError, publish_snapshot
from .repositories import (
    acknowledge_report_dispatch,
    claim_report_dispatches,
    close_report_dispatches,
    ensure_report_dispatch,
    reopen_report_dispatch,
    release_report_dispatch,
)


TERMINAL_JOB_STATUSES = frozenset({"ready", "partial", "failed", "blocked"})
SCHEMA_VERSION = os.environ.get("REPORT_SCHEMA_VERSION", "schema-1")
PARSER_VERSION = NCAA_PARSER_VERSION
MAX_ATTEMPTS = max(1, min(10, int(os.environ.get("REPORT_JOB_MAX_ATTEMPTS", "3"))))
SAFE_FAILURE_MESSAGES = {
    "job_busy": "The report job is already running.",
    "browser_capacity": "Browser capacity is temporarily unavailable.",
    "network": "The NCAA source could not be reached.",
    "source_blocked": "The NCAA source blocked collection.",
    "schema_validation": "Collected data did not match the required schema.",
    "identity_conflict": "Collected player identity evidence conflicted.",
    "core_section_failed": "A required report section could not be validated.",
    "unexpected": "The report build failed unexpectedly.",
    "retry_exhausted": "The report build exhausted its retry limit.",
}


class RetryableJobError(RuntimeError):
    """Safe signal for PostgreSQL to schedule a later job generation."""

    def __init__(self, category, message=None, *, evidence=None):
        self.category = category
        self.evidence = evidence
        super().__init__(message or SAFE_FAILURE_MESSAGES[category])


class DeterministicJobError(ValueError):
    """Safe signal that retrying the same evidence cannot succeed."""

    def __init__(self, category, message=None, *, evidence=None):
        self.category = category
        self.evidence = evidence
        super().__init__(message or SAFE_FAILURE_MESSAGES[category])


@dataclass(frozen=True)
class SectionCacheKey:
    job_id: uuid.UUID
    section: str
    source_url: str | None
    source_team_id: str | None
    source_season_id: str | None
    source_season_team_id: str | None
    source_stat_category_id: str | None
    schema_version: str
    parser_version: str


@dataclass(frozen=True)
class CollectedSection:
    name: str
    critical: bool
    rows: tuple[dict, ...] = field(default_factory=tuple)
    verified_empty: bool = False
    source_url: str | None = None
    source_team_id: str | None = None
    source_season_id: str | None = None
    source_season_team_id: str | None = None
    source_stat_category_id: str | None = None
    collected_at: datetime | None = None
    parser_version: str = PARSER_VERSION
    schema_version: str = SCHEMA_VERSION
    status: str = "complete"
    failure_category: str | None = None

    @classmethod
    def failed(cls, name, *, critical, category):
        return cls(
            name=name,
            critical=critical,
            status="failed",
            failure_category=category,
        )


@dataclass(frozen=True)
class DispatchDrainResult:
    claimed: int = 0
    published: int = 0
    released: int = 0


@dataclass(frozen=True)
class ReconciliationResult:
    reopened: int = 0
    closed: int = 0
    active: int = 0


def _utcnow():
    return datetime.now(UTC)


def bounded_retry_delay(attempt_count, *, jitter=None):
    """Return capped exponential retry seconds with caller-controlled jitter."""
    if attempt_count < 1:
        raise ValueError("attempt_count must be positive")
    base = min(300, 2 ** (attempt_count - 1))
    extra = 0 if jitter is None else jitter(base)
    return min(300, max(0, base + extra))


def drain_report_dispatch_outbox(
    session_factory,
    *,
    dispatch,
    limit=10,
    now=None,
    lease_duration=timedelta(seconds=60),
    redispatch_delay=timedelta(minutes=5),
    failure_backoff=timedelta(seconds=30),
    job_id=None,
):
    """Publish bounded ``(job_id, generation)`` messages from durable leases."""
    now = now or _utcnow()
    with session_factory.begin() as session:
        leases = claim_report_dispatches(
            session,
            now=now,
            limit=limit,
            lease_duration=lease_duration,
            job_id=job_id,
        )
    published = 0
    released = 0
    for lease in leases:
        job_id = str(uuid.UUID(str(lease.job_id)))
        try:
            message = dispatch(job_id, lease.generation)
        except Exception:
            with session_factory.begin() as session:
                if release_report_dispatch(
                    session,
                    lease,
                    now=now,
                    next_attempt_at=now + failure_backoff,
                ):
                    released += 1
        else:
            with session_factory.begin() as session:
                if acknowledge_report_dispatch(
                    session,
                    lease,
                    broker_message_id=message.id,
                    now=now,
                    redispatch_after=now + redispatch_delay,
                ):
                    published += 1
    return DispatchDrainResult(
        claimed=len(leases), published=published, released=released
    )


def reconcile_stale_report_jobs(
    session_factory,
    *,
    now=None,
    queued_timeout=timedelta(minutes=5),
    running_timeout=timedelta(minutes=30),
):
    """Reopen lost queued messages and stale unclaimed running work safely."""
    now = now or _utcnow()
    reopened = 0
    closed = 0
    active = 0
    with session_factory.begin() as session:
        terminal_ids = list(
            session.scalars(
                select(ReportJob.id).where(
                    ReportJob.status.in_(TERMINAL_JOB_STATUSES)
                )
            )
        )
        for job_id in terminal_ids:
            closed += close_report_dispatches(session, job_id, now=now)

        queued_jobs = list(
            session.scalars(
                select(ReportJob)
                .where(ReportJob.status.in_(("queued", "retrying")))
                .with_for_update(skip_locked=True)
            )
        )
        for job in queued_jobs:
            intent = session.scalar(
                select(ReportJobDispatch)
                .where(
                    ReportJobDispatch.job_id == job.id,
                    ReportJobDispatch.generation == job.dispatch_generation,
                    ReportJobDispatch.status.in_(
                        ("pending", "leased", "acknowledged")
                    ),
                )
                .with_for_update()
            )
            if intent is None:
                age_from = job.enqueued_at or job.created_at
                if age_from is None or age_from <= now - queued_timeout:
                    close_report_dispatches(session, job.id, now=now)
                    ensure_report_dispatch(
                        session,
                        job,
                        generation=job.dispatch_generation,
                        now=now,
                    )
                    reopened += 1
            elif (
                intent.status == "acknowledged"
                and intent.redispatch_after is not None
                and intent.redispatch_after <= now
            ):
                reopened += int(reopen_report_dispatch(session, intent, now=now))

        stale_running_ids = list(
            session.scalars(
                select(ReportJob.id).where(
                    ReportJob.status == "running",
                    ReportJob.started_at.is_not(None),
                    ReportJob.started_at <= now - running_timeout,
                )
            )
        )

    for job_id in stale_running_ids:
        with session_advisory_lock(session_factory, job_id) as claim:
            if claim is None:
                active += 1
                continue
            with claim.session_factory.begin() as session:
                job = session.scalar(
                    select(ReportJob)
                    .where(ReportJob.id == job_id)
                    .with_for_update()
                )
                if (
                    job is not None
                    and job.status == "running"
                    and job.started_at is not None
                    and job.started_at <= now - running_timeout
                ):
                    generation = job.dispatch_generation
                    if job.started_generation == generation:
                        close_report_dispatches(
                            session,
                            job.id,
                            generation=generation,
                            now=now,
                        )
                        snapshot = (
                            session.get(ReportSnapshot, job.result_snapshot_id)
                            if job.result_snapshot_id is not None
                            else None
                        )
                        cached = (
                            _load_cached_sections(session, job, snapshot)
                            if snapshot is not None
                            else {}
                        )
                        matching = (
                            _matching_cached_sections(session, job, cached)
                            if snapshot is not None
                            else {}
                        )
                        can_finalize_committed_evidence = matching.keys() >= {
                            "batting",
                            "pitching",
                        }
                        if (
                            job.attempt_count < job.max_attempts
                            or can_finalize_committed_evidence
                        ):
                            job.dispatch_generation = generation + 1
                            job.status = "retrying"
                            job.failure_category = None
                            job.failure_message = None
                            ensure_report_dispatch(
                                session,
                                job,
                                generation=job.dispatch_generation,
                                next_attempt_at=now,
                                now=now,
                            )
                            reopened += 1
                        else:
                            job.status = "failed"
                            job.failure_category = "retry_exhausted"
                            job.failure_message = SAFE_FAILURE_MESSAGES[
                                "retry_exhausted"
                            ]
                            job.finished_at = now
                    elif job.started_generation < generation:
                        job.status = "retrying"
                        close_report_dispatches(session, job.id, now=now)
                        ensure_report_dispatch(
                            session,
                            job,
                            generation=generation,
                            next_attempt_at=now,
                            now=now,
                        )
                        reopened += 1
    return ReconciliationResult(reopened=reopened, closed=closed, active=active)


def create_or_get_job(session, organization_id, report_id, idempotency_key):
    """Accept one durable job per organization/idempotency key."""
    if not idempotency_key or len(idempotency_key) > 200:
        raise ValueError("idempotency_key must contain 1-200 characters")
    job_id = uuid.uuid4()
    statement = (
        postgresql_insert(ReportJob)
        .values(
            id=job_id,
            organization_id=organization_id,
            report_id=report_id,
            idempotency_key=idempotency_key,
            status="queued",
            max_attempts=MAX_ATTEMPTS,
        )
        .on_conflict_do_nothing(
            constraint="uq_report_jobs_organization_idempotency"
        )
        .returning(ReportJob.id)
    )
    inserted_id = session.scalar(statement)
    if inserted_id is not None:
        job = session.get(ReportJob, inserted_id)
        ensure_report_dispatch(session, job)
        return job
    job = session.scalar(
        select(ReportJob)
        .where(
            ReportJob.organization_id == organization_id,
            ReportJob.idempotency_key == idempotency_key,
        )
        .with_for_update()
    )
    if job is None:
        # A concurrent transaction may not have become visible under an unusual
        # transaction isolation override; let the caller retry the acceptance.
        raise RuntimeError("idempotent job conflict was not visible")
    if job.report_id != report_id:
        raise ValueError("idempotency_key is already bound to another report")
    ensure_report_dispatch(session, job)
    return job


def accept_report_job(
    session_factory,
    organization_id,
    report_id,
    idempotency_key,
    *,
    dispatch,
):
    """Atomically accept a job and durable dispatch intent, then publish a lease."""
    with session_factory.begin() as session:
        job = create_or_get_job(
            session, organization_id, report_id, idempotency_key
        )
        job_id = job.id
    if dispatch is None:
        with session_factory() as session:
            return session.get(ReportJob, job_id)

    drain_report_dispatch_outbox(
        session_factory,
        dispatch=dispatch,
        limit=1,
        failure_backoff=timedelta(0),
        job_id=job_id,
    )
    with session_factory() as session:
        return session.get(ReportJob, job_id)


def _new_or_existing_snapshot(session, job):
    if job.result_snapshot_id is not None:
        return session.get(ReportSnapshot, job.result_snapshot_id)
    previous = session.scalar(
        select(ReportSnapshot)
        .where(
            ReportSnapshot.report_id == job.report_id,
            ReportSnapshot.status.in_(("ready", "partial")),
        )
        .order_by(ReportSnapshot.published_at.desc())
        .limit(1)
    )
    snapshot = ReportSnapshot(
        organization_id=job.organization_id,
        report_id=job.report_id,
        predecessor_snapshot_id=previous.id if previous else None,
        schema_version=SCHEMA_VERSION,
    )
    session.add(snapshot)
    session.flush()
    job.result_snapshot_id = snapshot.id
    return snapshot


def _load_cached_sections(session, job, snapshot):
    sections = list(
        session.scalars(
            select(ReportSection).where(
                ReportSection.snapshot_id == snapshot.id,
                ReportSection.job_id == job.id,
                ReportSection.status == "complete",
                ReportSection.schema_version == SCHEMA_VERSION,
                ReportSection.parser_version == PARSER_VERSION,
            )
        )
    )
    return {
        SectionCacheKey(
            job_id=job.id,
            section=section.section,
            source_url=section.source_url,
            source_team_id=section.source_team_id,
            source_season_id=section.source_season_id,
            source_season_team_id=section.source_season_team_id,
            source_stat_category_id=section.source_stat_category_id,
            schema_version=section.schema_version,
            parser_version=section.parser_version,
        ): section
        for section in sections
    }


def _matching_cached_sections(session, job, cached):
    """Return complete current-version sections for this exact job source."""
    report = session.get(Report, job.report_id)
    team_season = session.get(TeamSeason, report.team_season_id)
    team = session.get(Team, team_season.team_id)
    season = session.get(Season, team_season.season_id)
    expected_categories = {
        "batting": team_season.batting_stat_category_id,
        "pitching": team_season.pitching_stat_category_id,
    }
    return {
        key.section: section
        for key, section in cached.items()
        if key.job_id == job.id
        and key.section in expected_categories
        and key.source_team_id == team.source_team_id
        and key.source_season_id == season.source_season_id
        and key.source_season_team_id == team_season.source_season_team_id
        and key.source_stat_category_id == expected_categories[key.section]
        and key.schema_version == SCHEMA_VERSION
        and key.parser_version == PARSER_VERSION
    }


def _reconstruct_cached_sections(session, snapshot, matching):
    """Rebuild immutable collected sections from their exact source records."""
    if not matching:
        return ()
    records = list(
        session.scalars(
            select(SourceRecord).where(
                SourceRecord.snapshot_id == snapshot.id,
                SourceRecord.section.in_(tuple(matching)),
            )
        )
    )
    by_section = {}
    for record in records:
        by_section.setdefault(record.section, []).append(record)

    reconstructed = []
    for name in sorted(matching):
        section = matching[name]
        section_records = sorted(
            by_section.get(name, ()), key=lambda item: item.row_ordinal
        )
        if [record.row_ordinal for record in section_records] != list(
            range(section.row_count)
        ):
            raise DeterministicJobError("schema_validation")
        if bool(section.row_count) == bool(section.verified_empty):
            raise DeterministicJobError("schema_validation")
        for record in section_records:
            if (
                record.source_url != section.source_url
                or record.source_team_id != section.source_team_id
                or record.source_season_id != section.source_season_id
                or record.source_season_team_id != section.source_season_team_id
                or record.source_stat_category_id != section.source_stat_category_id
                or record.collected_at != section.collected_at
                or record.parser_version != section.parser_version
                or record.schema_version != section.schema_version
            ):
                raise DeterministicJobError("schema_validation")
        reconstructed.append(CollectedSection(
            name=section.section,
            critical=section.critical,
            rows=tuple({
                **record.raw_values,
                "_player_id": record.source_player_id,
                "_player_url": record.raw_values.get("player_url"),
                "_payload_sha256": record.payload_sha256,
            } for record in section_records),
            verified_empty=section.verified_empty,
            source_url=section.source_url,
            source_team_id=section.source_team_id,
            source_season_id=section.source_season_id,
            source_season_team_id=section.source_season_team_id,
            source_stat_category_id=section.source_stat_category_id,
            collected_at=section.collected_at,
            parser_version=section.parser_version,
            schema_version=section.schema_version,
        ))
    return tuple(reconstructed)


def _source_row_values(collected, row):
    raw_values = {
        key: value for key, value in row.items()
        if key not in {"_player_id", "_player_url", "_payload_sha256"}
    }
    if row.get("_player_url"):
        raw_values["player_url"] = row["_player_url"]
    encoded = json.dumps(raw_values, sort_keys=True, separators=(",", ":")).encode()
    return raw_values, (
        row.get("_payload_sha256") or hashlib.sha256(encoded).hexdigest()
    )


def _collected_section_signature(collected):
    records = []
    for ordinal, row in enumerate(collected.rows):
        raw_values, payload_sha256 = _source_row_values(collected, row)
        records.append((
            ordinal,
            collected.source_url or "unknown",
            collected.source_team_id or "unknown",
            collected.source_season_id or "unknown",
            collected.source_season_team_id or "unknown",
            collected.source_stat_category_id or "unknown",
            row.get("_player_id"),
            collected.collected_at,
            collected.parser_version,
            collected.schema_version,
            payload_sha256,
            raw_values,
        ))
    return (
        collected.name,
        collected.critical,
        collected.status,
        len(collected.rows),
        collected.verified_empty,
        collected.collected_at,
        collected.failure_category,
        collected.source_url,
        collected.source_team_id,
        collected.source_season_id,
        collected.source_season_team_id,
        collected.source_stat_category_id,
        collected.schema_version,
        collected.parser_version,
        tuple(records),
    )


def _persisted_section_signature(session, snapshot, section):
    records = tuple(
        (
            record.row_ordinal,
            record.source_url,
            record.source_team_id,
            record.source_season_id,
            record.source_season_team_id,
            record.source_stat_category_id,
            record.source_player_id,
            record.collected_at,
            record.parser_version,
            record.schema_version,
            record.payload_sha256,
            record.raw_values,
        )
        for record in session.scalars(
            select(SourceRecord)
            .where(
                SourceRecord.snapshot_id == snapshot.id,
                SourceRecord.section == section.section,
            )
            .order_by(SourceRecord.row_ordinal)
        )
    )
    return (
        section.section,
        section.critical,
        section.status,
        section.row_count,
        section.verified_empty,
        section.collected_at,
        section.failure_category,
        section.source_url,
        section.source_team_id,
        section.source_season_id,
        section.source_season_team_id,
        section.source_stat_category_id,
        section.schema_version,
        section.parser_version,
        records,
    )


def _persist_sections(session, job, snapshot, sections):
    existing = {
        section.section: section
        for section in session.scalars(
            select(ReportSection).where(ReportSection.snapshot_id == snapshot.id)
        )
    }
    for collected in sections:
        if collected.status == "complete" and (
            collected.schema_version != SCHEMA_VERSION
            or collected.parser_version != PARSER_VERSION
        ):
            raise DeterministicJobError("schema_validation")
        section = existing.get(collected.name)
        values = {
            "organization_id": job.organization_id,
            "snapshot_id": snapshot.id,
            "job_id": job.id,
            "section": collected.name,
            "critical": collected.critical,
            "status": collected.status,
            "row_count": len(collected.rows),
            "verified_empty": collected.verified_empty,
            "collected_at": collected.collected_at,
            "failure_category": collected.failure_category,
            "source_url": collected.source_url,
            "source_team_id": collected.source_team_id,
            "source_season_id": collected.source_season_id,
            "source_season_team_id": collected.source_season_team_id,
            "source_stat_category_id": collected.source_stat_category_id,
            "schema_version": collected.schema_version,
            "parser_version": collected.parser_version,
        }
        if section is None:
            section = ReportSection(**values)
            session.add(section)
            existing[collected.name] = section
        elif section.status != "complete" or (
            section.schema_version != collected.schema_version
            or section.parser_version != collected.parser_version
        ):
            session.execute(
                delete(SourceRecord).where(
                    SourceRecord.snapshot_id == snapshot.id,
                    SourceRecord.section == collected.name,
                )
            )
            for key, value in values.items():
                setattr(section, key, value)
        elif _persisted_section_signature(
            session, snapshot, section
        ) != _collected_section_signature(collected):
            raise DeterministicJobError("schema_validation")
        else:
            continue

        if collected.status != "complete" or not collected.rows:
            continue
        for ordinal, row in enumerate(collected.rows):
            raw_values, payload_sha256 = _source_row_values(collected, row)
            session.add(
                SourceRecord(
                    organization_id=job.organization_id,
                    snapshot_id=snapshot.id,
                    section=collected.name,
                    row_ordinal=ordinal,
                    source_url=collected.source_url or "unknown",
                    source_team_id=collected.source_team_id or "unknown",
                    source_season_id=collected.source_season_id or "unknown",
                    source_season_team_id=collected.source_season_team_id or "unknown",
                    source_stat_category_id=collected.source_stat_category_id or "unknown",
                    source_player_id=row.get("_player_id"),
                    collected_at=collected.collected_at or _utcnow(),
                    parser_version=collected.parser_version,
                    schema_version=collected.schema_version,
                    payload_sha256=payload_sha256,
                    raw_values=raw_values,
                )
            )


def _persist_canonical(session, job, snapshot, sections):
    """Persist player identities and typed facts after exact source rows exist."""
    from roster_scraper import (
        NcaaCollection,
        NcaaCollectionMetadata,
        NcaaSection,
        NcaaSeason,
        NcaaSourceRow,
        NcaaTeam,
    )
    from .canonical import (
        IdentityConflictError,
        canonicalize_ncaa_collection,
        normalize_material_name,
    )

    report = session.get(Report, job.report_id)
    team_season = session.get(TeamSeason, report.team_season_id)
    team = session.get(Team, team_season.team_id)
    season = session.get(Season, team_season.season_id)
    complete = [item for item in sections if item.status == "complete"]
    if not complete:
        return
    collected_at = min(
        (item.collected_at for item in complete if item.collected_at),
        default=_utcnow(),
    )
    ncaa_sections = []
    for item in complete:
        source_rows = []
        for ordinal, row in enumerate(item.rows):
            raw_values = {
                key: value for key, value in row.items()
                if key not in {"_player_id", "_player_url", "_payload_sha256"}
            }
            source_rows.append(NcaaSourceRow(
                section=item.name,
                row_ordinal=ordinal,
                source_url=item.source_url or "unknown",
                source_player_id=row.get("_player_id") or None,
                player_url=row.get("_player_url") or None,
                raw_values=raw_values,
                payload_sha256=row.get("_payload_sha256") or hashlib.sha256(
                    json.dumps(raw_values, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest(),
            ))
        ncaa_sections.append(NcaaSection(
            name=item.name,
            source_url=item.source_url or "unknown",
            stat_category_id=item.source_stat_category_id or "unknown",
            html_sha256=(
                source_rows[0].payload_sha256 if source_rows
                else hashlib.sha256(b"").hexdigest()
            ),
            rows=tuple(source_rows),
            verified_empty=item.verified_empty,
        ))
    collection = NcaaCollection(
        team=NcaaTeam(
            team_id=team.source_team_id,
            name=team.name,
            season_team_id=team_season.source_season_team_id,
            division=team_season.division,
        ),
        season=NcaaSeason(
            season_id=season.source_season_id,
            year=season.year,
            batting_category_id=team_season.batting_stat_category_id or "unknown",
            pitching_category_id=team_season.pitching_stat_category_id or "unknown",
        ),
        metadata=NcaaCollectionMetadata(
            collected_at=collected_at,
            parser_version=complete[0].parser_version,
            schema_version=complete[0].schema_version,
        ),
        sections=tuple(ncaa_sections),
        players=(),
        team_label=team.name,
    )
    canonical = canonicalize_ncaa_collection(collection)
    source_by_key = {
        (record.section, record.row_ordinal): record
        for record in session.scalars(
            select(SourceRecord).where(SourceRecord.snapshot_id == snapshot.id)
        )
    }
    identities = {}
    for item in canonical.players:
        if item.source_player_id:
            player_id = session.scalar(
                postgresql_insert(Player)
                .values(
                    id=uuid.uuid4(),
                    source="ncaa",
                    source_player_id=item.source_player_id,
                    display_name=item.display_name,
                )
                .on_conflict_do_nothing(
                    index_elements=[Player.source, Player.source_player_id],
                    index_where=Player.source_player_id.is_not(None),
                )
                .returning(Player.id)
            )
            if player_id is None:
                player_id = session.scalar(
                    select(Player.id).where(
                        Player.source == "ncaa",
                        Player.source_player_id == item.source_player_id,
                    )
                )
            if player_id is None:
                raise RuntimeError("authoritative player conflict was not visible")
            player = session.get(Player, player_id)
            if normalize_material_name(player.display_name) != normalize_material_name(
                item.display_name
            ):
                raise IdentityConflictError(
                    f"NCAA player ID {item.source_player_id} conflicts with stored identity"
                )
        else:
            player = Player(
                source="ncaa",
                source_player_id=None,
                display_name=item.display_name,
            )
            session.add(player)
            session.flush()
        identity = PlayerTeamSeason(
            organization_id=job.organization_id,
            snapshot_id=snapshot.id,
            player_id=player.id,
            team_season_id=team_season.id,
            identity_status=item.identity_status,
            identity_key=item.identity_key,
            display_name=item.display_name,
            jersey_number=item.jersey_number,
            position=item.position,
        )
        session.add(identity)
        session.flush()
        identities[item.identity_key] = identity

    for fact in canonical.facts:
        source = source_by_key[fact.source_record]
        identity = identities[fact.identity_key]
        if source.player_team_season_id is None:
            source.player_team_season_id = identity.id
        elif source.player_team_season_id != identity.id:
            raise DeterministicJobError("schema_validation")
    session.flush()

    for fact in canonical.facts:
        source = source_by_key[fact.source_record]
        identity = identities[fact.identity_key]
        if fact.section == "batting":
            session.add(BattingFact(
                organization_id=job.organization_id,
                snapshot_id=snapshot.id,
                player_team_season_id=identity.id,
                source_record_id=source.id,
                **fact.values,
            ))
        else:
            values = dict(fact.values)
            values.pop("innings_raw", None)
            session.add(PitchingFact(
                organization_id=job.organization_id,
                snapshot_id=snapshot.id,
                player_team_season_id=identity.id,
                source_record_id=source.id,
                **values,
            ))

    for metric in canonical.metrics:
        version = f"{metric.definition_version}:{metric.provenance}"
        definition_id = session.scalar(
            postgresql_insert(MetricDefinition)
            .values(
                id=uuid.uuid4(),
                code=metric.code,
                version=version,
                formula=metric.provenance,
            )
            .on_conflict_do_nothing(
                constraint="uq_metric_definitions_code_version"
            )
            .returning(MetricDefinition.id)
        )
        if definition_id is None:
            definition_id = session.scalar(
                select(MetricDefinition.id).where(
                    MetricDefinition.code == metric.code,
                    MetricDefinition.version == version,
                )
            )
        if definition_id is None:
            raise RuntimeError("metric definition conflict was not visible")
        session.add(MetricValue(
            organization_id=job.organization_id,
            snapshot_id=snapshot.id,
            metric_definition_id=definition_id,
            player_team_season_id=identities[metric.identity_key].id,
            value=metric.value,
            unavailable_reason=(
                "source value unavailable" if metric.value is None else None
            ),
        ))


def _classified_error(exc):
    from .canonical import IdentityConflictError

    if isinstance(exc, RetryableJobError | DeterministicJobError):
        return exc
    if isinstance(exc, BrowserCapacityExceeded):
        return RetryableJobError("browser_capacity")
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return RetryableJobError("network")
    if isinstance(exc, SnapshotPublicationError):
        return DeterministicJobError("core_section_failed")
    if isinstance(exc, IdentityConflictError):
        return DeterministicJobError("identity_conflict")
    if isinstance(exc, ValueError):
        return DeterministicJobError("schema_validation")
    return DeterministicJobError("unexpected")


def _record_failure(
    session_factory,
    job_id,
    generation,
    error=None,
    *,
    now=None,
    jitter=None,
):
    if error is None:
        # Compatibility for callers updated in Task 2 of this plan.
        error = generation
        generation = None
    now = now or _utcnow()
    evidence = error.evidence
    with session_factory.begin() as session:
        job = session.scalar(
            select(ReportJob).where(ReportJob.id == job_id).with_for_update()
        )
        if job is None or job.status in TERMINAL_JOB_STATUSES:
            return error
        generation = generation or job.dispatch_generation
        if (
            generation != job.dispatch_generation
            or job.started_generation != generation
        ):
            return error
        retryable = isinstance(error, RetryableJobError)
        if retryable and job.attempt_count < job.max_attempts:
            job.status = "retrying"
            close_report_dispatches(
                session, job.id, generation=generation, now=now
            )
            job.dispatch_generation = generation + 1
            delay = bounded_retry_delay(
                job.attempt_count,
                jitter=(
                    jitter
                    if jitter is not None
                    else lambda base: random.uniform(0, base)
                ),
            )
            ensure_report_dispatch(
                session,
                job,
                generation=job.dispatch_generation,
                next_attempt_at=now + timedelta(seconds=delay),
                now=now,
            )
        else:
            if retryable:
                error = DeterministicJobError("retry_exhausted")
            job.status = "failed"
            job.finished_at = now
            close_report_dispatches(session, job.id, now=now)
        job.failure_category = error.category
        job.failure_message = str(error)
        if job.status in {"failed", "blocked"} and evidence is not None:
            from .evidence import store_failure_evidence

            store_failure_evidence(
                session,
                job.id,
                section=evidence.section,
                source_url=evidence.source_url,
                html=evidence.html,
                failure_category=evidence.failure_category,
                collected_at=evidence.collected_at,
            )
    return error


def _validated_generation(generation):
    if isinstance(generation, bool) or not isinstance(generation, int):
        raise ValueError("generation must be a positive integer")
    if generation < 1:
        raise ValueError("generation must be a positive integer")
    return generation


def run_report_job(session_factory, job_id, generation=None, *, collector):
    """Run or safely replay one accepted job using caller-injected collection."""
    job_id = uuid.UUID(str(job_id))
    if generation is not None:
        generation = _validated_generation(generation)
    with session_advisory_lock(session_factory, job_id) as claim:
        if claim is None:
            with session_factory() as session:
                job = session.get(ReportJob, job_id)
                if job is None:
                    raise DeterministicJobError(
                        "schema_validation", "Report job does not exist."
                    )
                if job.status in {"ready", "partial"}:
                    return session.get(ReportSnapshot, job.result_snapshot_id)
                if job.status in {"failed", "blocked"}:
                    return "terminal"
                delivery_generation = generation or job.dispatch_generation
                if delivery_generation != job.dispatch_generation:
                    return "stale"
                if job.started_generation >= delivery_generation:
                    return "duplicate"
            return "busy"

        claimed_sessions = claim.session_factory
        exhausted = False
        recovered_sections = ()
        with claimed_sessions.begin() as session:
            job = session.scalar(
                select(ReportJob).where(ReportJob.id == job_id).with_for_update()
            )
            if job is None:
                raise DeterministicJobError(
                    "schema_validation", "Report job does not exist."
                )
            if job.status in {"ready", "partial"}:
                return session.get(ReportSnapshot, job.result_snapshot_id)
            if job.status in {"failed", "blocked"}:
                return "terminal"
            delivery_generation = generation or job.dispatch_generation
            if delivery_generation != job.dispatch_generation:
                return "stale"
            if job.started_generation >= delivery_generation:
                return "duplicate"

            snapshot = (
                session.get(ReportSnapshot, job.result_snapshot_id)
                if job.result_snapshot_id is not None
                else None
            )
            cached = (
                _load_cached_sections(session, job, snapshot)
                if snapshot is not None
                else {}
            )
            matching = (
                _matching_cached_sections(session, job, cached)
                if snapshot is not None
                else {}
            )
            if matching.keys() >= {"batting", "pitching"}:
                claimed_generation = session.scalar(
                    update(ReportJob)
                    .where(
                        ReportJob.id == job_id,
                        ReportJob.dispatch_generation == delivery_generation,
                        ReportJob.started_generation < delivery_generation,
                        ReportJob.status.not_in(TERMINAL_JOB_STATUSES),
                    )
                    .values(
                        started_generation=delivery_generation,
                        status="running",
                        started_at=job.started_at or _utcnow(),
                        failure_category=None,
                        failure_message=None,
                    )
                    .returning(ReportJob.started_generation)
                    .execution_options(synchronize_session=False)
                )
                if claimed_generation is None:
                    return "duplicate"
                recovered_sections = _reconstruct_cached_sections(
                    session, snapshot, matching
                )
                close_report_dispatches(
                    session,
                    job.id,
                    generation=delivery_generation,
                )
            elif job.attempt_count >= job.max_attempts:
                job.status = "failed"
                job.failure_category = "retry_exhausted"
                job.failure_message = SAFE_FAILURE_MESSAGES["retry_exhausted"]
                job.finished_at = _utcnow()
                close_report_dispatches(session, job.id)
                exhausted = True
            else:
                claimed_attempt = session.scalar(
                    update(ReportJob)
                    .where(
                        ReportJob.id == job_id,
                        ReportJob.dispatch_generation == delivery_generation,
                        ReportJob.started_generation < delivery_generation,
                        ReportJob.attempt_count < ReportJob.max_attempts,
                        ReportJob.status.not_in(TERMINAL_JOB_STATUSES),
                    )
                    .values(
                        started_generation=delivery_generation,
                        attempt_count=ReportJob.attempt_count + 1,
                        status="running",
                        started_at=_utcnow(),
                        failure_category=None,
                        failure_message=None,
                    )
                    .returning(ReportJob.attempt_count)
                    .execution_options(synchronize_session=False)
                )
                if claimed_attempt is None:
                    return "duplicate"
                close_report_dispatches(
                    session,
                    job.id,
                    generation=delivery_generation,
                )
                snapshot = _new_or_existing_snapshot(session, job)
        if exhausted:
            raise DeterministicJobError("retry_exhausted")

        try:
            sections = recovered_sections or collector(
                str(job_id), cached_sections=frozenset(cached)
            )
            with claimed_sessions.begin() as session:
                job = session.scalar(
                    select(ReportJob).where(ReportJob.id == job_id).with_for_update()
                )
                if job.status in {"ready", "partial"}:
                    return session.get(ReportSnapshot, job.result_snapshot_id)
                snapshot = session.get(ReportSnapshot, job.result_snapshot_id)
                _persist_sections(session, job, snapshot, sections)
                collected_times = [
                    item.collected_at
                    for item in sections
                    if item.collected_at is not None
                ]
                if collected_times:
                    snapshot.source_collected_at = min(collected_times)

            # Exact source evidence commits before typed facts/publication. A worker
            # loss after this boundary can reuse only these same-job, versioned rows.
            with claimed_sessions.begin() as session:
                job = session.scalar(
                    select(ReportJob).where(ReportJob.id == job_id).with_for_update()
                )
                if job.status in {"ready", "partial"}:
                    return session.get(ReportSnapshot, job.result_snapshot_id)
                snapshot = session.get(ReportSnapshot, job.result_snapshot_id)
                _persist_canonical(session, job, snapshot, sections)
                published = publish_snapshot(session, snapshot.id)
                job.status = published.status
                job.finished_at = _utcnow()
                close_report_dispatches(session, job.id)
                return published
        except Exception as exc:
            safe_error = _record_failure(
                claimed_sessions,
                job_id,
                delivery_generation,
                _classified_error(exc),
            )
            raise safe_error from None


def collect_ncaa_sections(session_factory, job_id, *, cached_sections=frozenset()):
    """Collect live NCAA sections or reuse only same-job version-matched evidence."""
    from roster_scraper import (
        NcaaAccessError,
        _NcaaFailureEvidenceError,
        collect_ncaa_by_id,
    )
    from .canonical import to_collected_sections

    job_uuid = uuid.UUID(str(job_id))
    with session_factory() as session:
        job = session.get(ReportJob, job_uuid)
        if job is None:
            raise DeterministicJobError("schema_validation", "Report job does not exist.")
        report = session.get(Report, job.report_id)
        team_season = session.get(TeamSeason, report.team_season_id)
        season = session.get(Season, team_season.season_id)
        snapshot_id = job.result_snapshot_id

        matching_keys = {key: None for key in cached_sections}
        matching = _matching_cached_sections(session, job, matching_keys)
        reconstructed = ()
        if snapshot_id and matching:
            stored = {
                section.section: section
                for section in session.scalars(
                    select(ReportSection).where(
                        ReportSection.snapshot_id == snapshot_id,
                        ReportSection.job_id == job.id,
                        ReportSection.section.in_(tuple(matching)),
                    )
                )
            }
            reconstructed = _reconstruct_cached_sections(
                session,
                session.get(ReportSnapshot, snapshot_id),
                stored,
            )
        missing = frozenset({"batting", "pitching"} - matching.keys())
        season_team_id = team_season.source_season_team_id
        division = team_season.division
        year = season.year

    if not missing:
        return reconstructed

    try:
        collection_kwargs = {
            "division": division,
            "year": year,
            "force_refresh": True,
        }
        if missing != frozenset({"batting", "pitching"}):
            collection_kwargs["section_names"] = missing
        collection = collect_ncaa_by_id(season_team_id, **collection_kwargs)
    except _NcaaFailureEvidenceError as exc:
        category = exc.evidence.failure_category
        error_type = (
            DeterministicJobError
            if category == "schema_validation"
            else RetryableJobError
        )
        raise error_type(category, evidence=exc.evidence) from None
    except NcaaAccessError as exc:
        raise RetryableJobError("source_blocked") from exc
    fetched = to_collected_sections(collection)
    if {section.name for section in fetched} != set(missing):
        raise DeterministicJobError("schema_validation")
    return tuple(sorted(
        (*reconstructed, *fetched), key=lambda section: section.name
    ))
