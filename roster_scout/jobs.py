"""PostgreSQL-authoritative report-job acceptance and execution."""

import hashlib
import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert

from usage_limits import BrowserCapacityExceeded

from .models import (
    BattingFact,
    MetricDefinition,
    MetricValue,
    PitchingFact,
    Player,
    PlayerTeamSeason,
    Report,
    ReportJob,
    ReportSection,
    ReportSnapshot,
    Season,
    SourceRecord,
    Team,
    TeamSeason,
)
from .snapshots import SnapshotPublicationError, publish_snapshot


TERMINAL_JOB_STATUSES = frozenset({"ready", "partial", "failed", "blocked"})
SCHEMA_VERSION = os.environ.get("REPORT_SCHEMA_VERSION", "schema-1")
PARSER_VERSION = os.environ.get("NCAA_PARSER_VERSION", "parser-1")
MAX_ATTEMPTS = max(1, min(10, int(os.environ.get("REPORT_JOB_MAX_ATTEMPTS", "3"))))
SAFE_FAILURE_MESSAGES = {
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
    """Safe signal that Celery may retry this job."""

    def __init__(self, category, message=None):
        self.category = category
        super().__init__(message or SAFE_FAILURE_MESSAGES[category])


class DeterministicJobError(ValueError):
    """Safe signal that retrying the same evidence cannot succeed."""

    def __init__(self, category, message=None):
        self.category = category
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


def _utcnow():
    return datetime.now(UTC)


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
        return session.get(ReportJob, inserted_id)
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
    return job


def accept_report_job(
    session_factory,
    organization_id,
    report_id,
    idempotency_key,
    *,
    dispatch,
):
    """Commit acceptance before best-effort broker publication.

    A failed broker handoff deliberately leaves ``enqueued_at`` null so a
    repeated accepted request can dispatch the same durable row.
    """
    with session_factory.begin() as session:
        job = create_or_get_job(
            session, organization_id, report_id, idempotency_key
        )
        job_id = job.id
    if dispatch is None:
        with session_factory() as session:
            return session.get(ReportJob, job_id)

    with session_factory() as session:
        stored = session.get(ReportJob, job_id)
        should_dispatch = (
            stored.status not in TERMINAL_JOB_STATUSES and stored.enqueued_at is None
        )
    if should_dispatch:
        try:
            message = dispatch(str(job_id))
        except Exception:
            # Broker details may include credentials or topology and are never
            # persisted as customer-visible failure state.
            pass
        else:
            with session_factory.begin() as session:
                stored = session.scalar(
                    select(ReportJob)
                    .where(ReportJob.id == job_id)
                    .with_for_update()
                )
                if stored.enqueued_at is None:
                    stored.enqueued_at = _utcnow()
                    stored.broker_message_id = str(message.id)
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


def _persist_sections(session, job, snapshot, sections):
    existing = {
        section.section: section
        for section in session.scalars(
            select(ReportSection).where(ReportSection.snapshot_id == snapshot.id)
        )
    }
    for collected in sections:
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
        elif section.status != "complete":
            for key, value in values.items():
                setattr(section, key, value)
        elif (
            section.schema_version != collected.schema_version
            or section.parser_version != collected.parser_version
        ):
            raise DeterministicJobError("schema_validation")

        if collected.status != "complete" or not collected.rows:
            continue
        existing_ordinals = set(
            session.scalars(
                select(SourceRecord.row_ordinal).where(
                    SourceRecord.snapshot_id == snapshot.id,
                    SourceRecord.section == collected.name,
                )
            )
        )
        for ordinal, row in enumerate(collected.rows):
            if ordinal in existing_ordinals:
                continue
            raw_values = {
                key: value for key, value in row.items()
                if key not in {"_player_id", "_player_url", "_payload_sha256"}
            }
            if row.get("_player_url"):
                raw_values["player_url"] = row["_player_url"]
            encoded = json.dumps(raw_values, sort_keys=True, separators=(",", ":")).encode()
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
                    payload_sha256=(
                        row.get("_payload_sha256") or hashlib.sha256(encoded).hexdigest()
                    ),
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
    from .canonical import canonicalize_ncaa_collection

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
        player = None
        if item.source_player_id:
            player = session.scalar(select(Player).where(
                Player.source == "ncaa",
                Player.source_player_id == item.source_player_id,
            ))
        if player is None:
            player = Player(
                source="ncaa",
                source_player_id=item.source_player_id,
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
        definition = session.scalar(select(MetricDefinition).where(
            MetricDefinition.code == metric.code,
            MetricDefinition.version == version,
        ))
        if definition is None:
            definition = MetricDefinition(
                code=metric.code,
                version=version,
                formula=metric.provenance,
            )
            session.add(definition)
            session.flush()
        session.add(MetricValue(
            organization_id=job.organization_id,
            snapshot_id=snapshot.id,
            metric_definition_id=definition.id,
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


def _record_failure(session_factory, job_id, error):
    now = _utcnow()
    with session_factory.begin() as session:
        job = session.scalar(
            select(ReportJob).where(ReportJob.id == job_id).with_for_update()
        )
        if job is None or job.status in TERMINAL_JOB_STATUSES:
            return error
        retryable = isinstance(error, RetryableJobError)
        if retryable and job.attempt_count < job.max_attempts:
            job.status = "retrying"
        else:
            if retryable:
                error = DeterministicJobError("retry_exhausted")
            job.status = "failed"
            job.finished_at = now
        job.failure_category = error.category
        job.failure_message = str(error)
    return error


def run_report_job(session_factory, job_id, *, collector):
    """Run or safely replay one accepted job using caller-injected collection."""
    job_id = uuid.UUID(str(job_id))
    exhausted = False
    with session_factory.begin() as session:
        job = session.scalar(
            select(ReportJob).where(ReportJob.id == job_id).with_for_update()
        )
        if job is None:
            raise DeterministicJobError("schema_validation", "Report job does not exist.")
        if job.status in {"ready", "partial"}:
            return session.get(ReportSnapshot, job.result_snapshot_id)
        if job.status in {"failed", "blocked"}:
            raise DeterministicJobError(job.failure_category or "unexpected")
        if job.attempt_count >= job.max_attempts:
            job.status = "failed"
            job.failure_category = "retry_exhausted"
            job.failure_message = SAFE_FAILURE_MESSAGES["retry_exhausted"]
            job.finished_at = _utcnow()
            exhausted = True
            cached = {}
        else:
            snapshot = _new_or_existing_snapshot(session, job)
            cached = _load_cached_sections(session, job, snapshot)
            job.status = "running"
            job.attempt_count += 1
            job.started_at = job.started_at or _utcnow()
            job.failure_category = None
            job.failure_message = None
    if exhausted:
        raise DeterministicJobError("retry_exhausted")

    try:
        sections = collector(str(job_id), cached_sections=frozenset(cached))
        with session_factory.begin() as session:
            job = session.scalar(
                select(ReportJob).where(ReportJob.id == job_id).with_for_update()
            )
            if job.status in {"ready", "partial"}:
                return session.get(ReportSnapshot, job.result_snapshot_id)
            snapshot = session.get(ReportSnapshot, job.result_snapshot_id)
            _persist_sections(session, job, snapshot, sections)
            collected_times = [
                item.collected_at for item in sections if item.collected_at is not None
            ]
            if collected_times:
                snapshot.source_collected_at = min(collected_times)

        # Exact source evidence commits before typed facts/publication. A worker
        # loss after this boundary can reuse only these same-job, versioned rows.
        with session_factory.begin() as session:
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
            return published
    except Exception as exc:
        safe_error = _record_failure(session_factory, job_id, _classified_error(exc))
        raise safe_error from None


def collect_ncaa_sections(session_factory, job_id, *, cached_sections=frozenset()):
    """Collect live NCAA sections or reuse only same-job version-matched evidence."""
    from roster_scraper import NcaaAccessError, collect_ncaa_by_id
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

        expected = {
            "batting": team_season.batting_stat_category_id,
            "pitching": team_season.pitching_stat_category_id,
        }
        matching = {
            key.section: key for key in cached_sections
            if key.job_id == job.id
            and key.source_team_id == session.get(Team, team_season.team_id).source_team_id
            and key.source_season_id == season.source_season_id
            and key.source_season_team_id == team_season.source_season_team_id
            and key.source_stat_category_id == expected.get(key.section)
            and key.schema_version == SCHEMA_VERSION
            and key.parser_version == PARSER_VERSION
        }
        if snapshot_id and matching.keys() >= {"batting", "pitching"}:
            stored = list(session.scalars(
                select(ReportSection).where(
                    ReportSection.snapshot_id == snapshot_id,
                    ReportSection.job_id == job.id,
                    ReportSection.section.in_(("batting", "pitching")),
                )
            ))
            rows = list(session.scalars(
                select(SourceRecord).where(SourceRecord.snapshot_id == snapshot_id)
            ))
            by_section = {}
            for row in rows:
                by_section.setdefault(row.section, []).append(row)
            return tuple(CollectedSection(
                name=section.section,
                critical=section.critical,
                rows=tuple({
                    **record.raw_values,
                    "_player_id": record.source_player_id,
                    "_player_url": record.raw_values.get("player_url"),
                    "_payload_sha256": record.payload_sha256,
                } for record in sorted(by_section.get(section.section, ()), key=lambda item: item.row_ordinal)),
                verified_empty=section.verified_empty,
                source_url=section.source_url,
                source_team_id=section.source_team_id,
                source_season_id=section.source_season_id,
                source_season_team_id=section.source_season_team_id,
                source_stat_category_id=section.source_stat_category_id,
                collected_at=section.collected_at,
                parser_version=section.parser_version,
                schema_version=section.schema_version,
            ) for section in stored)

    try:
        collection = collect_ncaa_by_id(
            team_season.source_season_team_id,
            division=team_season.division,
            year=season.year,
            force_refresh=True,
        )
    except NcaaAccessError as exc:
        raise RetryableJobError("source_blocked") from exc
    return to_collected_sections(collection)
