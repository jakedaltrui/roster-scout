"""Connection-free report-library and authorized acceptance services."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from math import ceil
import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert

from .audit import append_security_audit_event
from .jobs import TERMINAL_JOB_STATUSES, create_or_get_job
from .models import (
    OrganizationMembership,
    Report,
    ReportJob,
    Season,
    SecurityAuditEvent,
    Team,
    TeamSeason,
)

from .repositories import (
    REPORT_LIBRARY_PAGE_SIZE,
    count_published_snapshots_for_report,
    count_reports_for_organization,
    count_reports_updated_since,
    ensure_report_dispatch,
    get_report_detail_record,
    get_report_for_organization,
    get_ready_artifact_for_snapshot,
    list_report_library_records,
)
from .usage import record_authenticated_report_request_accepted


class ReportLibraryFailure(RuntimeError):
    def __init__(self, category):
        self.category = category
        super().__init__(category)


class ReportAcceptanceFailure(RuntimeError):
    """Fixed report-acceptance failure with no tenant or selector detail."""

    def __init__(self, category):
        self.category = category
        super().__init__(category)


def _bounded_identifier(value, *, field, optional=False):
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a canonical NCAA identifier")
    candidate = value.strip()
    if (
        not candidate
        or len(candidate) > 100
        or not candidate.isascii()
        or not candidate.isdigit()
    ):
        raise ValueError(f"{field} must be a canonical NCAA identifier")
    return candidate


def _canonical_uuid(value, *, field):
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        raise ValueError(f"{field} must be a UUID") from None


@dataclass(frozen=True)
class CanonicalReportRequest:
    """Bounded NCAA selector identity already resolved by the catalog."""

    source_team_id: str
    team_name: str
    source_season_id: str
    season_year: int
    source_season_team_id: str
    division: str
    batting_stat_category_id: str | None = None
    pitching_stat_category_id: str | None = None

    def __post_init__(self):
        for field in (
            "source_team_id",
            "source_season_id",
            "source_season_team_id",
        ):
            object.__setattr__(
                self,
                field,
                _bounded_identifier(getattr(self, field), field=field),
            )
        for field in ("batting_stat_category_id", "pitching_stat_category_id"):
            object.__setattr__(
                self,
                field,
                _bounded_identifier(
                    getattr(self, field), field=field, optional=True
                ),
            )
        if not isinstance(self.team_name, str):
            raise ValueError("team_name must be bounded canonical text")
        team_name = " ".join(self.team_name.split())
        if not team_name or len(team_name) > 200:
            raise ValueError("team_name must be bounded canonical text")
        object.__setattr__(self, "team_name", team_name)
        if (
            not isinstance(self.season_year, int)
            or isinstance(self.season_year, bool)
            or not 1900 <= self.season_year <= 2200
        ):
            raise ValueError("season_year must be a bounded year")
        if self.division not in {"d1", "d2", "d3"}:
            raise ValueError("division must be d1, d2, or d3")


@dataclass(frozen=True)
class AcceptedReportRequest:
    report_id: uuid.UUID
    job_id: uuid.UUID
    created_report: bool


@dataclass(frozen=True)
class AcceptedReportRefresh:
    report_id: uuid.UUID
    job_id: uuid.UUID
    created_job: bool


@dataclass(frozen=True)
class AcceptedReportRetry:
    report_id: uuid.UUID
    job_id: uuid.UUID
    failed_job_id: uuid.UUID
    created_job: bool


@dataclass(frozen=True)
class AuthorizedArtifact:
    id: uuid.UUID
    snapshot_id: uuid.UUID
    object_key: str
    sha256: str


def _upsert_team(session, request):
    team_id = session.scalar(
        postgresql_insert(Team)
        .values(
            id=uuid.uuid4(),
            source="ncaa",
            source_team_id=request.source_team_id,
            name=request.team_name,
        )
        .on_conflict_do_update(
            constraint="uq_teams_source_identity",
            set_={"name": request.team_name},
        )
        .returning(Team.id)
    )
    return session.get(Team, team_id)


def _upsert_season(session, request):
    season_id = session.scalar(
        postgresql_insert(Season)
        .values(
            id=uuid.uuid4(),
            source="ncaa",
            source_season_id=request.source_season_id,
            year=request.season_year,
        )
        .on_conflict_do_update(
            constraint="uq_seasons_source_identity",
            set_={"year": request.season_year},
        )
        .returning(Season.id)
    )
    return session.get(Season, season_id)


def _upsert_team_season(session, team, season, request):
    inserted_id = session.scalar(
        postgresql_insert(TeamSeason)
        .values(
            id=uuid.uuid4(),
            team_id=team.id,
            season_id=season.id,
            source_season_team_id=request.source_season_team_id,
            division=request.division.upper(),
            batting_stat_category_id=request.batting_stat_category_id,
            pitching_stat_category_id=request.pitching_stat_category_id,
        )
        .on_conflict_do_nothing(
            constraint="uq_team_seasons_team_season"
        )
        .returning(TeamSeason.id)
    )
    team_season = (
        session.get(TeamSeason, inserted_id)
        if inserted_id is not None
        else session.scalar(
            select(TeamSeason)
            .where(
                TeamSeason.team_id == team.id,
                TeamSeason.season_id == season.id,
            )
            .with_for_update()
        )
    )
    if (
        team_season is None
        or team_season.source_season_team_id != request.source_season_team_id
        or team_season.division != request.division.upper()
    ):
        raise ReportAcceptanceFailure("canonical_identity_conflict")
    for field in ("batting_stat_category_id", "pitching_stat_category_id"):
        requested = getattr(request, field)
        existing = getattr(team_season, field)
        if existing is not None and requested is not None and existing != requested:
            raise ReportAcceptanceFailure("canonical_identity_conflict")
        if existing is None and requested is not None:
            setattr(team_season, field, requested)
    return team_season


def _locked_report(session, organization_id, team_season, team_name):
    inserted_id = session.scalar(
        postgresql_insert(Report)
        .values(
            id=uuid.uuid4(),
            organization_id=organization_id,
            team_season_id=team_season.id,
            name=team_name,
        )
        .on_conflict_do_nothing(
            constraint="uq_reports_organization_team_season"
        )
        .returning(Report.id)
    )
    report = session.scalar(
        select(Report)
        .where(
            Report.organization_id == organization_id,
            Report.team_season_id == team_season.id,
        )
        .with_for_update()
    )
    if report is None:
        raise RuntimeError("report upsert did not produce a durable row")
    return report, inserted_id is not None


def accept_authorized_report_request(
    session,
    *,
    actor_account_id,
    organization_id,
    request,
    idempotency_key,
):
    """Accept one authorized report build inside the caller's transaction."""
    if not isinstance(request, CanonicalReportRequest):
        raise ValueError("request must be a CanonicalReportRequest")
    actor_account_id = _canonical_uuid(
        actor_account_id, field="actor_account_id"
    )
    organization_id = _canonical_uuid(
        organization_id, field="organization_id"
    )
    idempotency_key = str(
        _canonical_uuid(idempotency_key, field="idempotency_key")
    )

    membership = session.scalar(
        select(OrganizationMembership)
        .where(
            OrganizationMembership.account_id == actor_account_id,
            OrganizationMembership.organization_id == organization_id,
            OrganizationMembership.status == "active",
        )
        .with_for_update()
    )
    if membership is None:
        raise ReportAcceptanceFailure("membership_required")

    team = _upsert_team(session, request)
    season = _upsert_season(session, request)
    team_season = _upsert_team_season(session, team, season, request)
    report, created_report = _locked_report(
        session,
        organization_id,
        team_season,
        request.team_name,
    )
    job = session.scalar(
        select(ReportJob)
        .where(
            ReportJob.organization_id == organization_id,
            ReportJob.report_id == report.id,
            ReportJob.status.not_in(TERMINAL_JOB_STATUSES),
        )
        .order_by(ReportJob.created_at, ReportJob.id)
        .limit(1)
        .with_for_update()
    )
    if job is None:
        job = create_or_get_job(
            session,
            organization_id,
            report.id,
            idempotency_key,
            requested_by_account_id=actor_account_id,
        )
    else:
        ensure_report_dispatch(session, job)

    record_authenticated_report_request_accepted(
        session,
        event_key=str(job.id),
        actor_account_id=actor_account_id,
        organization_id=organization_id,
    )
    existing_audit = session.scalar(
        select(SecurityAuditEvent.id).where(
            SecurityAuditEvent.organization_id == organization_id,
            SecurityAuditEvent.target_type == "job",
            SecurityAuditEvent.target_id == job.id,
            SecurityAuditEvent.action.in_(("report.create", "report.refresh")),
            SecurityAuditEvent.reason.in_(("job_accepted", "refresh_accepted")),
        )
    )
    if existing_audit is None:
        append_security_audit_event(
            session,
            actor_account_id=actor_account_id,
            organization_id=organization_id,
            target_type="job",
            target_id=job.id,
            action="report.create" if created_report else "report.refresh",
            outcome="pending",
            reason="job_accepted" if created_report else "refresh_accepted",
        )
    return AcceptedReportRequest(
        report_id=report.id,
        job_id=job.id,
        created_report=created_report,
    )


def accept_authorized_report_refresh(
    session,
    *,
    actor_account_id,
    organization_id,
    report_id,
    idempotency_key,
):
    """Accept one explicit live successor inside the caller's transaction."""
    actor_account_id = _canonical_uuid(
        actor_account_id, field="actor_account_id"
    )
    organization_id = _canonical_uuid(
        organization_id, field="organization_id"
    )
    report_id = _canonical_uuid(report_id, field="report_id")
    idempotency_key = str(
        _canonical_uuid(idempotency_key, field="idempotency_key")
    )

    membership = session.scalar(
        select(OrganizationMembership)
        .where(
            OrganizationMembership.account_id == actor_account_id,
            OrganizationMembership.organization_id == organization_id,
            OrganizationMembership.status == "active",
        )
        .with_for_update()
    )
    if membership is None:
        raise ReportAcceptanceFailure("membership_required")

    report = get_report_for_organization(
        session,
        organization_id,
        report_id,
        for_update=True,
    )
    if report is None:
        raise ReportLibraryFailure("not_found")

    job = session.scalar(
        select(ReportJob)
        .where(
            ReportJob.organization_id == organization_id,
            ReportJob.report_id == report.id,
            ReportJob.status.not_in(TERMINAL_JOB_STATUSES),
        )
        .order_by(ReportJob.created_at, ReportJob.id)
        .limit(1)
        .with_for_update()
    )
    created_job = job is None
    if created_job:
        job = create_or_get_job(
            session,
            organization_id,
            report.id,
            idempotency_key,
            requested_by_account_id=actor_account_id,
        )
    else:
        ensure_report_dispatch(session, job)

    record_authenticated_report_request_accepted(
        session,
        event_key=str(job.id),
        actor_account_id=actor_account_id,
        organization_id=organization_id,
    )
    existing_audit = session.scalar(
        select(SecurityAuditEvent.id).where(
            SecurityAuditEvent.organization_id == organization_id,
            SecurityAuditEvent.target_type == "job",
            SecurityAuditEvent.target_id == job.id,
            SecurityAuditEvent.action == "report.refresh",
            SecurityAuditEvent.reason == "refresh_accepted",
        )
    )
    if existing_audit is None:
        append_security_audit_event(
            session,
            actor_account_id=actor_account_id,
            organization_id=organization_id,
            target_type="job",
            target_id=job.id,
            action="report.refresh",
            outcome="pending",
            reason="refresh_accepted",
        )
    return AcceptedReportRefresh(
        report_id=report.id,
        job_id=job.id,
        created_job=created_job,
    )


def accept_authorized_report_retry(
    session,
    *,
    actor_account_id,
    organization_id,
    report_id,
    failed_job_id,
    idempotency_key,
):
    """Accept one new durable attempt without mutating its terminal predecessor."""
    actor_account_id = _canonical_uuid(
        actor_account_id, field="actor_account_id"
    )
    organization_id = _canonical_uuid(
        organization_id, field="organization_id"
    )
    report_id = _canonical_uuid(report_id, field="report_id")
    failed_job_id = _canonical_uuid(failed_job_id, field="failed_job_id")
    action_id = str(_canonical_uuid(idempotency_key, field="idempotency_key"))

    membership = session.scalar(
        select(OrganizationMembership)
        .where(
            OrganizationMembership.account_id == actor_account_id,
            OrganizationMembership.organization_id == organization_id,
            OrganizationMembership.status == "active",
        )
        .with_for_update()
    )
    if membership is None:
        raise ReportAcceptanceFailure("membership_required")

    report = get_report_for_organization(
        session,
        organization_id,
        report_id,
        for_update=True,
    )
    failed_job = session.scalar(
        select(ReportJob)
        .where(
            ReportJob.id == failed_job_id,
            ReportJob.organization_id == organization_id,
            ReportJob.report_id == report_id,
            ReportJob.status.in_(("failed", "blocked")),
        )
        .with_for_update()
    )
    if report is None or failed_job is None:
        raise ReportLibraryFailure("not_found")

    retry_key = f"retry:{failed_job.id}:{action_id}"
    job = session.scalar(
        select(ReportJob)
        .where(
            ReportJob.organization_id == organization_id,
            ReportJob.report_id == report_id,
            ReportJob.status.not_in(TERMINAL_JOB_STATUSES),
        )
        .order_by(ReportJob.created_at, ReportJob.id)
        .limit(1)
        .with_for_update()
    )
    created_job = job is None
    if job is None:
        job = session.scalar(
            select(ReportJob)
            .where(
                ReportJob.organization_id == organization_id,
                ReportJob.report_id == report_id,
                ReportJob.idempotency_key == retry_key,
            )
            .with_for_update()
        )
        created_job = job is None
    if job is None:
        job = create_or_get_job(
            session,
            organization_id,
            report_id,
            retry_key,
            requested_by_account_id=actor_account_id,
        )
    elif job.status not in TERMINAL_JOB_STATUSES:
        ensure_report_dispatch(session, job)

    record_authenticated_report_request_accepted(
        session,
        event_key=str(job.id),
        actor_account_id=actor_account_id,
        organization_id=organization_id,
    )
    existing_audit = session.scalar(
        select(SecurityAuditEvent.id).where(
            SecurityAuditEvent.organization_id == organization_id,
            SecurityAuditEvent.target_type == "job",
            SecurityAuditEvent.target_id == job.id,
            SecurityAuditEvent.action == "report.retry",
            SecurityAuditEvent.reason == "retry_accepted",
        )
    )
    if existing_audit is None:
        append_security_audit_event(
            session,
            actor_account_id=actor_account_id,
            organization_id=organization_id,
            target_type="job",
            target_id=job.id,
            action="report.retry",
            outcome="pending",
            reason="retry_accepted",
        )
    return AcceptedReportRetry(
        report_id=report_id,
        job_id=job.id,
        failed_job_id=failed_job.id,
        created_job=created_job,
    )


def resolve_authorized_artifact(
    session,
    *,
    organization_id,
    report_id,
    snapshot_id,
    artifact_id,
):
    """Return metadata for one exact stored historical artifact or fixed 404."""
    organization_id = _canonical_uuid(
        organization_id, field="organization_id"
    )
    report_id = _canonical_uuid(report_id, field="report_id")
    snapshot_id = _canonical_uuid(snapshot_id, field="snapshot_id")
    artifact_id = _canonical_uuid(artifact_id, field="artifact_id")
    artifact = get_ready_artifact_for_snapshot(
        session,
        organization_id,
        report_id,
        snapshot_id,
        artifact_id,
    )
    if artifact is None:
        raise ReportLibraryFailure("not_found")
    return AuthorizedArtifact(
        id=artifact.id,
        snapshot_id=artifact.snapshot_id,
        object_key=artifact.object_key,
        sha256=artifact.sha256,
    )


@dataclass(frozen=True)
class ReportLibraryItem:
    id: object
    opponent_name: str
    source_team_id: str
    division: str
    season_year: int
    latest_snapshot_at: object | None
    completeness: str
    unavailable_count: int
    build_status: str
    snapshot_count: int


@dataclass(frozen=True)
class ReportLibraryPage:
    items: tuple[ReportLibraryItem, ...]
    total_count: int
    updated_today_count: int
    page: int
    page_count: int
    previous_page: int | None
    next_page: int | None


_BUILD_LABELS = {
    "queued": "Queued",
    "running": "Collecting",
    "retrying": "Saving",
    "failed": "Failed",
    "blocked": "Failed",
}


def load_report_library(session, organization_id, *, page=1, now=None):
    """Build one truthful, fixed-size page for the active organization."""
    if not isinstance(page, int) or page < 1:
        raise ReportLibraryFailure("not_found")
    now = now or datetime.now(UTC)
    page_size = REPORT_LIBRARY_PAGE_SIZE
    total_count = count_reports_for_organization(session, organization_id)
    page_count = max(1, ceil(total_count / page_size))
    if total_count and page > page_count:
        raise ReportLibraryFailure("not_found")
    records = list_report_library_records(
        session,
        organization_id,
        offset=(page - 1) * page_size,
        limit=page_size,
    )
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    items = tuple(
        ReportLibraryItem(
            id=record.report_id,
            opponent_name=record.team_name or record.report_name,
            source_team_id=record.source_team_id,
            division=record.division,
            season_year=record.season_year,
            latest_snapshot_at=(
                record.latest_snapshot.published_at
                if record.latest_snapshot is not None
                else None
            ),
            completeness=(
                "Ready"
                if record.latest_snapshot is not None
                and record.latest_snapshot.status == "ready"
                else "Partial"
                if record.latest_snapshot is not None
                and record.latest_snapshot.status == "partial"
                else "—"
            ),
            unavailable_count=record.unavailable_section_count,
            build_status=(
                _BUILD_LABELS.get(record.latest_build.status, "No active build")
                if record.latest_build is not None
                else "No active build"
            ),
            snapshot_count=record.snapshot_count,
        )
        for record in records
    )
    return ReportLibraryPage(
        items=items,
        total_count=total_count,
        updated_today_count=count_reports_updated_since(
            session, organization_id, start_of_day
        ),
        page=page,
        page_count=page_count,
        previous_page=page - 1 if page > 1 else None,
        next_page=page + 1 if page < page_count else None,
    )


@dataclass(frozen=True)
class ReportSectionView:
    name: str
    state: str
    explanation: str | None

    def as_dict(self):
        return {
            "name": self.name,
            "state": self.state,
            "explanation": self.explanation,
        }


@dataclass(frozen=True)
class ReportStatusView:
    id: uuid.UUID | None
    state: str
    heading: str
    message: str
    updated_at: datetime
    terminal: bool
    sections: tuple[ReportSectionView, ...]

    def as_dict(self):
        return {
            "state": self.state,
            "heading": self.heading,
            "message": self.message,
            "updated_at": self.updated_at.isoformat(),
            "terminal": self.terminal,
            "sections": [section.as_dict() for section in self.sections],
        }


@dataclass(frozen=True)
class SnapshotHistoryItem:
    id: uuid.UUID
    status: str
    published_at: datetime
    source_collected_at: datetime | None
    predecessor_published_at: datetime | None
    artifact_available: bool
    artifact_id: uuid.UUID | None


@dataclass(frozen=True)
class ReportDetailView:
    id: uuid.UUID
    opponent_name: str
    division: str
    season_year: int
    latest_snapshot: SnapshotHistoryItem | None
    latest_sections: tuple[ReportSectionView, ...]
    active_build: ReportStatusView | None
    failed_builds: tuple[ReportStatusView, ...]
    history: tuple[SnapshotHistoryItem, ...]
    history_total_count: int
    history_page: int
    history_page_count: int
    history_previous_page: int | None
    history_next_page: int | None


_JOB_COPY = {
    "queued": (
        "queued",
        "Queued",
        "Waiting to collect live NCAA data.",
        False,
    ),
    "running": (
        "collecting",
        "Collecting",
        "Collecting live NCAA data.",
        False,
    ),
    "retrying": (
        "saving",
        "Saving snapshot",
        "Saving the collected report.",
        False,
    ),
    "ready": (
        "ready",
        "Ready",
        "The refreshed snapshot is ready.",
        True,
    ),
    "partial": (
        "partial",
        "Partial",
        "The refreshed snapshot is available with some sections unavailable.",
        True,
    ),
}

_SECTION_LABELS = {
    "pending": "Waiting",
    "complete": "Ready",
    "unavailable": "Unavailable",
    "failed": "Failed",
}

_SAFE_FAILURE_COPY = {
    "source": "NCAA source unavailable",
    "browser": "NCAA source unavailable",
    "source_unavailable": "NCAA source unavailable",
    "database": "Report service unavailable",
    "queue": "Build service unavailable",
    "report_service": "Report service unavailable",
    "artifact": "Storage unavailable",
    "storage": "Storage unavailable",
}


def _safe_failure_message(category):
    return _SAFE_FAILURE_COPY.get(category, "Something went wrong")


def _section_views(sections):
    views = []
    for section in sections:
        state = _SECTION_LABELS.get(section.status, "Waiting")
        explanation = None
        if section.status in {"unavailable", "failed"}:
            explanation = _safe_failure_message(section.failure_category)
        views.append(ReportSectionView(
            name=section.section.replace("_", " ").title(),
            state=state,
            explanation=explanation,
        ))
    return tuple(views)


def _status_view(job, sections):
    if job.status in {"failed", "blocked"}:
        state, heading, terminal = "failed", "Failed", True
        message = _safe_failure_message(job.failure_category)
    else:
        state, heading, message, terminal = _JOB_COPY.get(
            job.status,
            ("queued", "Queued", "Waiting to collect live NCAA data.", False),
        )
    updated_at = (
        job.finished_at
        or job.started_at
        or job.created_at
        or datetime.now(UTC)
    )
    return ReportStatusView(
        id=job.id,
        state=state,
        heading=heading,
        message=message,
        updated_at=updated_at,
        terminal=terminal,
        sections=_section_views(sections),
    )


def _detail_record(
    session, organization_id, report_id, *, history_offset=0
):
    record = get_report_detail_record(
        session,
        organization_id,
        report_id,
        history_offset=history_offset,
    )
    if record is None:
        raise ReportLibraryFailure("not_found")
    return record


def load_report_detail(session, organization_id, report_id, *, history_page=1):
    """Build a saved-snapshot-first detail model for one active tenant."""
    if not isinstance(history_page, int) or history_page < 1:
        raise ReportLibraryFailure("not_found")
    total_count = count_published_snapshots_for_report(
        session, organization_id, report_id
    )
    page_count = max(1, ceil(total_count / REPORT_LIBRARY_PAGE_SIZE))
    if total_count and history_page > page_count:
        raise ReportLibraryFailure("not_found")
    record = _detail_record(
        session,
        organization_id,
        report_id,
        history_offset=(history_page - 1) * REPORT_LIBRARY_PAGE_SIZE,
    )
    predecessors = dict(record.snapshot_predecessors)
    artifacts_by_snapshot = {
        artifact.snapshot_id: artifact for artifact in record.artifacts
    }
    history = tuple(SnapshotHistoryItem(
        id=snapshot.id,
        status=snapshot.status,
        published_at=snapshot.published_at,
        source_collected_at=snapshot.source_collected_at,
        predecessor_published_at=predecessors.get(
            snapshot.predecessor_snapshot_id
        ),
        artifact_available=snapshot.id in artifacts_by_snapshot,
        artifact_id=(
            artifacts_by_snapshot[snapshot.id].id
            if snapshot.id in artifacts_by_snapshot
            else None
        ),
    ) for snapshot in record.snapshots)
    latest_snapshot = None
    if record.latest_snapshot is not None:
        latest_predecessor = None
        if record.latest_snapshot.predecessor_snapshot_id is not None:
            latest_predecessor = predecessors.get(
                record.latest_snapshot.predecessor_snapshot_id
            )
        latest_snapshot = SnapshotHistoryItem(
            id=record.latest_snapshot.id,
            status=record.latest_snapshot.status,
            published_at=record.latest_snapshot.published_at,
            source_collected_at=record.latest_snapshot.source_collected_at,
            predecessor_published_at=latest_predecessor,
            artifact_available=(
                record.latest_snapshot.id in artifacts_by_snapshot
            ),
            artifact_id=(
                artifacts_by_snapshot[record.latest_snapshot.id].id
                if record.latest_snapshot.id in artifacts_by_snapshot
                else None
            ),
        )
    active_build = None
    if (
        record.latest_job is not None
        and record.latest_job.status not in TERMINAL_JOB_STATUSES
    ):
        active_build = _status_view(record.latest_job, record.job_sections)
    return ReportDetailView(
        id=record.report_id,
        opponent_name=record.team_name or record.report_name,
        division=record.division,
        season_year=record.season_year,
        latest_snapshot=latest_snapshot,
        latest_sections=_section_views(record.snapshot_sections),
        active_build=active_build,
        failed_builds=tuple(
            _status_view(job, ()) for job in record.failed_jobs
        ),
        history=history,
        history_total_count=total_count,
        history_page=history_page,
        history_page_count=page_count,
        history_previous_page=history_page - 1 if history_page > 1 else None,
        history_next_page=(
            history_page + 1 if history_page < page_count else None
        ),
    )


def load_report_status(session, organization_id, report_id):
    """Return a complete, sanitized status model or the shared not-found error."""
    record = _detail_record(session, organization_id, report_id)
    if record.latest_job is None:
        latest = record.latest_snapshot
        if latest is None:
            return ReportStatusView(
                id=None,
                state="queued",
                heading="Queued",
                message="Waiting to collect live NCAA data.",
                updated_at=datetime.now(UTC),
                terminal=False,
                sections=(),
            )
        return ReportStatusView(
            id=None,
            state=latest.status,
            heading="Ready" if latest.status == "ready" else "Partial",
            message=(
                "The saved snapshot is ready."
                if latest.status == "ready"
                else "The saved snapshot has some unavailable sections."
            ),
            updated_at=latest.published_at,
            terminal=True,
            sections=_section_views(record.snapshot_sections),
        )
    return _status_view(record.latest_job, record.job_sections)
