"""PostgreSQL-authoritative report-job acceptance and execution."""

import hashlib
import json
import logging
import os
import random
import re
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from threading import RLock

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.exc import IntegrityError, OperationalError

from roster_scraper import NCAA_PARSER_VERSION
from usage_limits import BrowserCapacityExceeded

from .database import session_advisory_heartbeat, session_advisory_lock
from .enums import BUILD_PROFILES
from .models import (
    BattingGameLogFact,
    BattingFact,
    CareerAggregate,
    CareerSeasonFact,
    FieldingFact,
    FieldingGameLogFact,
    Game,
    MetricConstantSet,
    MetricDefinition,
    MetricValue,
    PitchingFact,
    PitchingGameLogFact,
    Player,
    PlayerTeamSeason,
    Report,
    ReportJob,
    ReportJobDispatch,
    ReportSection,
    ReportSnapshot,
    RequestLedger,
    Season,
    SourceRecord,
    ScopedBattingFact,
    ScopedFieldingFact,
    ScopedPitchingFact,
    SeasonConferenceEvidence,
    StatisticScope,
    StatisticScopeGame,
    Team,
    TeamSeason,
)
from .collection_control import (
    CollectionBudgetExceeded,
    CollectionDeadlineExceeded,
    remaining_timeout_seconds,
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


LOGGER = logging.getLogger(__name__)


def _map_with_bounded_concurrency(items, worker, *, max_workers):
    """Run independent navigation work concurrently while preserving order."""
    items = tuple(items)
    if not items:
        return ()
    if max_workers == 1:
        return tuple(worker(item) for item in items)
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        return tuple(pool.map(worker, items))


def _bounded_history_deadline(build_deadline, history_seconds, *, now=None):
    """Reserve the remaining build allowance for team-level dashboard work."""
    current = time.monotonic() if now is None else now
    return min(build_deadline, current + history_seconds)


def _reserve_until_deadline(reserve, *, deadline_monotonic):
    """Turn a connection loss at the hard wall into a classified timeout."""
    try:
        return reserve()
    except OperationalError:
        if time.monotonic() >= deadline_monotonic:
            raise CollectionDeadlineExceeded(
                "phase3_collection_deadline_exceeded"
            ) from None
        raise


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
    "collection_timeout": "The report build exceeded its collection time limit.",
}

_SAFE_LEDGER_TOKEN = re.compile(r"^[a-z0-9][a-z0-9:_-]{0,199}$")
_SAFE_FAMILY_TOKEN = re.compile(r"^[a-z][a-z0-9_:]{0,49}$")


@dataclass(frozen=True)
class Phase3CollectionLimits:
    navigation_concurrency: int
    live_page_budget: int
    navigation_timeout_seconds: int
    history_wall_time_seconds: int
    wall_time_seconds: int


def _bounded_collection_integer(environment, name, default, minimum, maximum):
    raw = environment.get(name, str(default))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValueError("phase3_collection_configuration_invalid") from None
    if value < minimum or value > maximum:
        raise ValueError("phase3_collection_configuration_invalid")
    return value


def load_phase3_collection_limits(environment=None):
    """Validate every fan-out limit before database or browser construction."""
    environment = os.environ if environment is None else environment
    return Phase3CollectionLimits(
        navigation_concurrency=_bounded_collection_integer(
            environment, "PHASE3_NAVIGATION_CONCURRENCY", 2, 1, 2
        ),
        live_page_budget=_bounded_collection_integer(
            environment, "PHASE3_LIVE_PAGE_BUDGET", 200, 1, 500
        ),
        navigation_timeout_seconds=_bounded_collection_integer(
            environment, "PHASE3_NAVIGATION_TIMEOUT_SECONDS", 45, 5, 120
        ),
        history_wall_time_seconds=_bounded_collection_integer(
            environment, "PHASE3_HISTORY_WALL_TIME_SECONDS", 10 * 60, 30, 600
        ),
        wall_time_seconds=_bounded_collection_integer(
            environment, "PHASE3_BUILD_WALL_TIME_SECONDS", 18 * 60, 60, 1800
        ),
    )


@dataclass(frozen=True)
class CollectionBudgetEntry:
    family: str
    source_identity: str


class CollectionBudget:
    """One build-local ledger for cache and live supplemental request families."""

    def __init__(self, limits, *, initial_live_count=0):
        if (
            isinstance(initial_live_count, bool)
            or not isinstance(initial_live_count, int)
            or initial_live_count < 0
            or initial_live_count > limits.live_page_budget
        ):
            raise ValueError("request_budget_reservation_invalid")
        self.limits = limits
        self._lock = RLock()
        self._planned = set()
        self._settled = set()
        self._reserved = set()
        self.cache_count = 0
        self.live_count = initial_live_count

    @property
    def remaining(self):
        with self._lock:
            return self.limits.live_page_budget - self.live_count

    @property
    def unsettled(self):
        with self._lock:
            return frozenset(self._planned - self._settled)

    def plan(self, family, source_identity):
        if not _SAFE_FAMILY_TOKEN.fullmatch(str(family)):
            raise ValueError("request_ledger_identity_invalid")
        if not _SAFE_LEDGER_TOKEN.fullmatch(str(source_identity)):
            raise ValueError("request_ledger_identity_invalid")
        entry = CollectionBudgetEntry(str(family), str(source_identity))
        with self._lock:
            self._planned.add(entry)
        return entry

    def reserve_live(self, entries, *, page_count=1):
        """Atomically reserve page capacity for one logical request batch."""
        entries = tuple(entries)
        if (
            isinstance(page_count, bool)
            or not isinstance(page_count, int)
            or page_count < 0
        ):
            raise ValueError("request_budget_reservation_invalid")
        with self._lock:
            if (
                not entries
                or len(set(entries)) != len(entries)
                or any(
                    entry not in self._planned
                    or entry in self._settled
                    or entry in self._reserved
                    for entry in entries
                )
            ):
                raise ValueError("request_ledger_lifecycle_invalid")
            if page_count > self.remaining:
                return False
            self.live_count += page_count
            self._reserved.update(entries)
            return True

    def sync_live_count(self, consumed):
        if (
            isinstance(consumed, bool)
            or not isinstance(consumed, int)
            or consumed < self.live_count
            or consumed > self.limits.live_page_budget
        ):
            raise ValueError("request_budget_reservation_invalid")
        with self._lock:
            self.live_count = consumed

    def settle(self, entry, *, outcome):
        with self._lock:
            if entry not in self._planned or entry in self._settled:
                raise ValueError("request_ledger_lifecycle_invalid")
            if outcome == "cache_hit":
                if entry in self._reserved:
                    raise ValueError("request_ledger_lifecycle_invalid")
                self.cache_count += 1
            elif outcome in {
                "live_complete", "live_failed", "capacity_exhausted",
            }:
                if entry not in self._reserved:
                    if self.remaining <= 0:
                        raise ValueError("request_budget_exhausted")
                    self.live_count += 1
            elif outcome != "budget_exhausted":
                raise ValueError("request_ledger_outcome_invalid")
            self._reserved.discard(entry)
            self._settled.add(entry)
            return self.remaining


def _record_request_ledger(
    session_factory,
    *,
    job_id,
    snapshot_id,
    organization_id,
    dispatch_generation,
    entry,
    request_kind,
    outcome,
    budget_remaining,
    pages_consumed=0,
    safe_category=None,
    started_at=None,
):
    """Insert or settle an exact same-build ledger row under its tenant keys."""
    now = _utcnow()
    with session_factory.begin() as session:
        stored = session.scalar(
            select(RequestLedger).where(
                RequestLedger.organization_id == organization_id,
                RequestLedger.job_id == job_id,
                RequestLedger.dispatch_generation == dispatch_generation,
                RequestLedger.family == entry.family,
                RequestLedger.source_identity == entry.source_identity,
            ).with_for_update()
        )
        if stored is None:
            stored = RequestLedger(
                organization_id=organization_id,
                job_id=job_id,
                snapshot_id=snapshot_id,
                dispatch_generation=dispatch_generation,
                family=entry.family,
                source_identity=entry.source_identity,
                request_kind=request_kind,
                outcome=outcome,
                started_at=started_at or now,
                completed_at=None if outcome == "planned" else now,
                safe_category=safe_category,
                budget_remaining=budget_remaining,
                pages_consumed=pages_consumed,
            )
            session.add(stored)
            return
        if stored.outcome != "planned":
            if (
                stored.request_kind != request_kind
                or stored.outcome != outcome
                or stored.safe_category != safe_category
                or stored.budget_remaining != budget_remaining
                or stored.pages_consumed != pages_consumed
            ):
                raise DeterministicJobError("schema_validation")
            return
        stored.request_kind = request_kind
        stored.outcome = outcome
        stored.completed_at = None if outcome == "planned" else now
        stored.safe_category = safe_category
        stored.budget_remaining = budget_remaining
        stored.pages_consumed = pages_consumed


def _request_source_identity(family, *parts):
    material = "\x1f".join("" if part is None else str(part) for part in parts)
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return f"ncaa:{str(family).replace(':', '_')}:{digest}"


def _reserve_job_live_pages(
    session_factory,
    *,
    job_id,
    dispatch_generation,
    page_count,
    page_budget,
):
    """Atomically reserve durable per-build capacity before source requests."""
    if (
        isinstance(page_count, bool)
        or not isinstance(page_count, int)
        or page_count < 1
    ):
        raise ValueError("request_budget_reservation_invalid")
    with session_factory.begin() as session:
        job = session.scalar(
            select(ReportJob).where(ReportJob.id == job_id).with_for_update()
        )
        if (
            job is None
            or job.dispatch_generation != dispatch_generation
            or job.status in TERMINAL_JOB_STATUSES
        ):
            raise DeterministicJobError("schema_validation")
        consumed = job.phase3_live_pages_consumed
        if consumed + page_count > page_budget:
            return None
        job.phase3_live_pages_consumed = consumed + page_count
        session.flush()
        return page_budget - job.phase3_live_pages_consumed


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
    scopes: tuple["CollectedScope", ...] = field(default_factory=tuple)

    @classmethod
    def failed(cls, name, *, critical, category):
        return cls(
            name=name,
            critical=critical,
            status="failed",
            failure_category=category,
            collected_at=_utcnow(),
        )

    @classmethod
    def supplemental_failure(
        cls,
        name,
        *,
        status,
        category,
        collected_at,
        source_url=None,
        source_team_id=None,
        source_season_id=None,
        source_season_team_id=None,
        source_stat_category_id=None,
    ):
        if status not in {"failed", "unavailable", "partial"}:
            raise ValueError("supplemental failure status is invalid")
        return cls(
            name=name,
            critical=False,
            status=status,
            failure_category=category,
            collected_at=collected_at,
            source_url=source_url,
            source_team_id=source_team_id,
            source_season_id=source_season_id,
            source_season_team_id=source_season_team_id,
            source_stat_category_id=source_stat_category_id,
        )


@dataclass(frozen=True)
class CollectedScope:
    scope_code: str
    display_label: str
    stat_type: str
    source_identifier: str | None
    source_path: str
    source_url: str | None
    source_season_id: str
    source_season_team_id: str
    source_stat_category_id: str
    status: str
    reason: str | None
    collected_at: datetime
    source_payload_sha256: str | None = None
    sample: dict = field(default_factory=dict)


@dataclass(frozen=True)
class CollectedGameFact:
    """One audited player/game count row used by computed game scopes."""

    fact_id: str
    source_game_id: str | None
    source_player_id: str
    stat_type: str
    counts: dict
    link_status: str = "linked"
    source_url: str | None = None
    source_stat_category_id: str | None = None
    source_payload_sha256: str | None = None
    collected_at: datetime | None = None


def to_collected_history_sections(
    details,
    schedule,
    *,
    source_team_id,
    unavailable_player_count=0,
    complete=True,
):
    """Adapt parsed player detail into durable source rows and scoped game facts."""
    from .canonical import canonicalize_player_history
    from .metrics import METRIC_DEFINITIONS
    from .ncaa_detail import NCAA_BASE

    typed_rows = {name: [] for name in ("batting", "pitching", "fielding")}
    game_facts = []
    statuses = []
    for detail in details:
        canonical = canonicalize_player_history(detail, schedule.games)
        statuses.append(canonical.completeness["status"])
        for row in canonical.career_seasons:
            typed_rows[row.stat_type].append({
                "row_kind": "career_season",
                "row_id": row.row_id,
                "season_label": row.season_label,
                "source_season_id": row.source_season_id,
                "school_name": row.school_name,
                "source_season_team_id": row.source_season_team_id,
                "division": row.division,
                "source_stat_category_id": row.source_stat_category_id,
                "counts": dict(row.counts),
                "rates": {key: str(value) for key, value in row.rates.items()},
                "_player_id": row.source_player_id,
                "_source_url": f"{NCAA_BASE}{row.source_path}",
                "_source_stat_category_id": row.source_stat_category_id,
                "_source_collected_at": detail.collected_at,
                "_payload_sha256": row.source_payload_sha256,
            })
        for linked in canonical.game_logs:
            row = linked.row
            typed_rows[row.stat_type].append({
                "row_kind": "game_log",
                "row_id": row.row_id,
                "source_game_id": row.source_game_id,
                "game_date": row.game_date.isoformat(),
                "opponent_label": row.opponent_label,
                "opponent_season_team_id": row.opponent_season_team_id,
                "source_season_id": row.source_season_id,
                "source_stat_category_id": row.source_stat_category_id,
                "counts": dict(row.counts),
                "rates": {key: str(value) for key, value in row.rates.items()},
                "link_status": linked.link_status,
                "_player_id": row.source_player_id,
                "_source_url": f"{NCAA_BASE}{row.source_path}",
                "_source_stat_category_id": row.source_stat_category_id,
                "_source_collected_at": detail.collected_at,
                "_payload_sha256": row.source_payload_sha256,
            })
            game_facts.append(CollectedGameFact(
                fact_id=row.row_id,
                source_game_id=row.source_game_id,
                source_player_id=row.source_player_id,
                stat_type=row.stat_type,
                counts={
                    key: value for key, value in row.counts.items()
                    if key not in METRIC_DEFINITIONS
                },
                link_status=linked.link_status,
                source_url=f"{NCAA_BASE}{row.source_path}",
                source_stat_category_id=row.source_stat_category_id,
                source_payload_sha256=row.source_payload_sha256,
                collected_at=detail.collected_at,
            ))
    fully_complete = complete and unavailable_player_count == 0 and all(
        status == "complete" for status in statuses
    )
    collected_at = min(
        (detail.collected_at for detail in details), default=schedule.collected_at
    )
    sections = tuple(
        CollectedSection(
            name=f"history:{stat_type}",
            critical=False,
            rows=tuple(rows),
            verified_empty=fully_complete and not rows,
            source_url=None,
            source_team_id=str(source_team_id),
            source_season_id=schedule.source_season_id,
            source_season_team_id=schedule.source_season_team_id,
            source_stat_category_id=None,
            collected_at=collected_at,
            status="complete" if fully_complete else "partial",
            failure_category=(
                None if fully_complete else
                "missing_stable_player_id" if unavailable_player_count
                else "player_history_incomplete"
            ),
        )
        for stat_type, rows in typed_rows.items()
    )
    return sections, tuple(game_facts)


def to_collected_schedule_sections(
    schedule, *, game_facts=(), complete_stat_types=frozenset()
):
    """Adapt validated schedule evidence and exact game logs to source sections."""
    if not schedule.games:
        raise DeterministicJobError("schema_validation")
    schedule_rows = tuple(
        {
            "source_game_id": item.source_game_id,
            "game_date": item.game_date.isoformat(),
            "status": item.status,
            "opponent_team_id": item.opponent_team_id,
            "opponent_season_team_id": item.opponent_season_team_id,
            "opponent_conference_id": item.opponent_conference_id,
            "conference_evidence_status": item.conference_evidence_status,
            "opponent_identity_status": item.opponent_identity_status,
            "doubleheader_ordinal": item.doubleheader_ordinal,
            "result_sha256": item.result_sha256,
            "venue": item.venue,
            "team_conference_id": schedule.team_conference_id,
            "schedule_complete": schedule.schedule_complete,
            "division": schedule.division,
        }
        for item in schedule.games
    )
    sections = [CollectedSection(
        name="schedule",
        critical=False,
        rows=schedule_rows,
        source_url=schedule.source_url,
        source_team_id=schedule.source_team_id,
        source_season_id=schedule.source_season_id,
        source_season_team_id=schedule.source_season_team_id,
        source_stat_category_id="schedule",
        collected_at=schedule.collected_at,
        status="complete" if schedule.schedule_complete else "partial",
        failure_category=None if schedule.schedule_complete else "schedule_evidence_incomplete",
    )]
    typed = {name: [] for name in ("batting", "pitching", "fielding")}
    for fact in game_facts:
        if fact.stat_type not in typed:
            raise DeterministicJobError("schema_validation")
        typed[fact.stat_type].append({
            "source_game_id": fact.source_game_id,
            "fact_id": fact.fact_id,
            "stat_type": fact.stat_type,
            "source_stat_category_id": fact.source_stat_category_id,
            "counts": dict(fact.counts),
            "link_status": fact.link_status,
            "_player_id": fact.source_player_id,
            "_source_url": fact.source_url or schedule.source_url,
            "_source_stat_category_id": (
                fact.source_stat_category_id or f"game_logs:{fact.stat_type}"
            ),
            "_source_collected_at": fact.collected_at or schedule.collected_at,
            "_payload_sha256": fact.source_payload_sha256,
        })
    unknown_complete_types = set(complete_stat_types) - set(typed)
    if unknown_complete_types:
        raise DeterministicJobError("schema_validation")
    for stat_type, rows in typed.items():
        available = stat_type in complete_stat_types
        if available:
            section_status = "complete"
            failure_category = None
        elif rows:
            section_status = "partial"
            failure_category = "game_log_evidence_incomplete"
        else:
            section_status = "unavailable"
            failure_category = "game_log_evidence_unavailable"
        sections.append(CollectedSection(
            name=f"game_logs:{stat_type}",
            critical=False,
            rows=tuple(rows),
            verified_empty=available and not rows,
            source_url=None,
            source_team_id=schedule.source_team_id,
            source_season_id=schedule.source_season_id,
            source_season_team_id=schedule.source_season_team_id,
            source_stat_category_id=None,
            collected_at=schedule.collected_at,
            status=section_status,
            failure_category=failure_category,
        ))
    return tuple(sections)


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


def to_collected_split_sections(results, *, source_team_id):
    """Materialize all seven requested scopes per stat type for one build."""
    from .ncaa_detail import SPLIT_CODES

    by_type = {}
    for result in results:
        by_type.setdefault(result.stat_type, []).append(result)
    sections = []
    for stat_type, expected_codes in SPLIT_CODES.items():
        typed = by_type.get(stat_type, [])
        by_code = {result.scope_code: result for result in typed}
        if set(by_code) != set(expected_codes) or len(typed) != len(expected_codes):
            raise DeterministicJobError("schema_validation")
        ordered = [by_code[code] for code in expected_codes]
        rows = []
        scopes = []
        for result in ordered:
            scopes.append(CollectedScope(
                scope_code=result.scope_code,
                display_label=result.display_label,
                stat_type=result.stat_type,
                source_identifier=result.source_identifier,
                source_path=result.source_path,
                source_url=result.source_url,
                source_season_id=result.source_season_id,
                source_season_team_id=result.source_season_team_id,
                source_stat_category_id=result.source_stat_category_id,
                status=result.status,
                reason=result.reason,
                collected_at=result.collected_at,
                source_payload_sha256=result.source_payload_sha256,
                sample={"row_count": len(result.rows)},
            ))
            for row in result.rows:
                values = {
                    "Player": row.raw_values["Player"],
                    "H": str(row.hits if stat_type == "batting" else row.hits_allowed),
                    "AB": str(row.at_bats),
                    "scope_code": result.scope_code,
                    "stat_type": stat_type,
                    "sample": dict(row.sample),
                    "_player_id": row.source_player_id,
                    "_player_url": row.source_player_url,
                    "_payload_sha256": row.source_payload_sha256,
                }
                rows.append(values)
        complete = all(scope.status == "complete" for scope in scopes)
        first = ordered[0]
        sections.append(CollectedSection(
            name=f"splits:{stat_type}",
            critical=False,
            rows=tuple(rows),
            verified_empty=complete and not rows,
            source_url=(
                f"https://stats.ncaa.org{first.source_path}"
                if first.source_path else None
            ),
            source_team_id=str(source_team_id),
            source_season_id=first.source_season_id,
            source_season_team_id=first.source_season_team_id,
            source_stat_category_id=first.source_stat_category_id,
            collected_at=min(scope.collected_at for scope in scopes),
            status="complete" if complete else "partial",
            failure_category=None if complete else "split_scope_incomplete",
            scopes=tuple(scopes),
        ))
    return tuple(sections)


def unavailable_collected_split_sections(
    *,
    source_team_id,
    source_season_id,
    source_season_team_id,
    category_ids,
    status,
    reason,
    collected_at,
):
    """Persist all requested scopes when bounded live collection cannot complete."""
    from .ncaa_detail import SPLIT_CODES, _SPLIT_DISPLAY_LABELS

    if status not in {"unavailable", "failed"}:
        raise ValueError("split failure status is invalid")
    if any(
        not str(category_ids.get(stat_type) or "").strip()
        for stat_type in ("batting", "pitching")
    ):
        raise ValueError("split category identities are incomplete")
    sections = []
    source_path = f"/teams/{source_season_team_id}/season_to_date_stats"
    for stat_type, codes in SPLIT_CODES.items():
        scopes = tuple(
            CollectedScope(
                scope_code=code,
                display_label=_SPLIT_DISPLAY_LABELS[code],
                stat_type=stat_type,
                source_identifier=None,
                source_path=source_path,
                source_url=None,
                source_season_id=source_season_id,
                source_season_team_id=source_season_team_id,
                source_stat_category_id=category_ids[stat_type],
                status=status,
                reason=reason,
                collected_at=collected_at,
            )
            for code in codes
        )
        sections.append(CollectedSection(
            name=f"splits:{stat_type}",
            critical=False,
            source_url=f"https://stats.ncaa.org{source_path}",
            source_team_id=source_team_id,
            source_season_id=source_season_id,
            source_season_team_id=source_season_team_id,
            source_stat_category_id=category_ids[stat_type],
            collected_at=collected_at,
            status="partial",
            failure_category=reason,
            scopes=scopes,
        ))
    return tuple(sections)


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
                        session.execute(
                            update(RequestLedger)
                            .where(
                                RequestLedger.job_id == job.id,
                                RequestLedger.organization_id
                                == job.organization_id,
                                RequestLedger.dispatch_generation == generation,
                                RequestLedger.outcome == "planned",
                            )
                            .values(
                                outcome="interrupted",
                                completed_at=now,
                                safe_category="worker_interrupted",
                            )
                            .execution_options(synchronize_session=False)
                        )
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
                        session.execute(
                            update(RequestLedger)
                            .where(
                                RequestLedger.job_id == job.id,
                                RequestLedger.organization_id
                                == job.organization_id,
                                RequestLedger.dispatch_generation < generation,
                                RequestLedger.outcome == "planned",
                            )
                            .values(
                                outcome="interrupted",
                                completed_at=now,
                                safe_category="worker_interrupted",
                            )
                            .execution_options(synchronize_session=False)
                        )
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


def create_or_get_job(
    session,
    organization_id,
    report_id,
    idempotency_key,
    *,
    requested_by_account_id=None,
    build_profile="detailed",
):
    """Accept one durable job per organization/idempotency key."""
    if not idempotency_key or len(idempotency_key) > 200:
        raise ValueError("idempotency_key must contain 1-200 characters")
    if build_profile not in BUILD_PROFILES:
        raise ValueError("build_profile must be quick or detailed")
    job_id = uuid.uuid4()
    statement = (
        postgresql_insert(ReportJob)
        .values(
            id=job_id,
            organization_id=organization_id,
            report_id=report_id,
            requested_by_account_id=requested_by_account_id,
            idempotency_key=idempotency_key,
            build_profile=build_profile,
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
    if job.build_profile != build_profile:
        raise ValueError("idempotency_key is already bound to another build profile")
    ensure_report_dispatch(session, job)
    return job


def ensure_detailed_successor(session, job):
    """Create exactly one background detailed build after a quick publication."""
    if job.build_profile != "quick" or job.status not in {"ready", "partial"}:
        return None
    return create_or_get_job(
        session,
        job.organization_id,
        job.report_id,
        f"detailed:{job.id}",
        requested_by_account_id=job.requested_by_account_id,
        build_profile="detailed",
    )


def accept_report_job(
    session_factory,
    organization_id,
    report_id,
    idempotency_key,
    *,
    dispatch,
    build_profile="detailed",
):
    """Atomically accept a job and durable dispatch intent, then publish a lease."""
    with session_factory.begin() as session:
        job = create_or_get_job(
            session,
            organization_id,
            report_id,
            idempotency_key,
            build_profile=build_profile,
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
        collection_contract_version=(
            "quick-season-v1"
            if job.build_profile == "quick"
            else "phase3-detailed-v1"
        ),
        created_by_account_id=job.requested_by_account_id,
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
    matched = {}
    for key, section in cached.items():
        common_match = (
            key.job_id == job.id
            and key.source_team_id == team.source_team_id
            and key.source_season_id == season.source_season_id
            and key.source_season_team_id == team_season.source_season_team_id
            and key.schema_version == SCHEMA_VERSION
            and key.parser_version == PARSER_VERSION
        )
        if not common_match:
            continue
        if key.section in expected_categories:
            if key.source_stat_category_id == expected_categories[key.section]:
                matched[key.section] = section
            continue
        if key.section == "fielding" and key.source_stat_category_id:
            from .ncaa_detail import build_fielding_url

            try:
                expected_url = build_fielding_url(
                    team_season.source_season_team_id,
                    key.source_stat_category_id,
                )
            except ValueError:
                continue
            if key.source_url == expected_url:
                matched[key.section] = section
            continue
        if key.section in {"splits:batting", "splits:pitching"}:
            from .ncaa_detail import SPLIT_CODES, build_split_url

            stat_type = key.section.removeprefix("splits:")
            scopes = list(session.scalars(
                select(StatisticScope).where(
                    StatisticScope.snapshot_id == section.snapshot_id,
                    StatisticScope.organization_id == job.organization_id,
                    StatisticScope.scope_kind == "source_split",
                    StatisticScope.stat_type == stat_type,
                    StatisticScope.status == "complete",
                    StatisticScope.schema_version == SCHEMA_VERSION,
                    StatisticScope.parser_version == PARSER_VERSION,
                )
            ))
            if {scope.scope_code for scope in scopes} != set(SPLIT_CODES[stat_type]):
                continue
            try:
                valid = all(
                    scope.source_season_id == season.source_season_id
                    and scope.source_season_team_id
                    == team_season.source_season_team_id
                    and scope.source_stat_category_id
                    == expected_categories[stat_type]
                    and scope.source_url
                    == build_split_url(
                        team_season.source_season_team_id,
                        expected_categories[stat_type],
                        scope.source_identifier,
                    )
                    for scope in scopes
                )
            except ValueError:
                valid = False
            if valid:
                matched[key.section] = section
            continue
        if key.section == "schedule" or key.section.startswith("game_logs:"):
            matched[key.section] = section
    return matched


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
    persisted_scopes = list(session.scalars(
        select(StatisticScope)
        .where(
            StatisticScope.snapshot_id == snapshot.id,
            StatisticScope.scope_kind == "source_split",
        )
        .order_by(StatisticScope.stat_type, StatisticScope.scope_code)
    ))

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
            scopes=tuple(
                CollectedScope(
                    scope_code=scope.scope_code,
                    display_label=scope.display_label,
                    stat_type=scope.stat_type,
                    source_identifier=scope.source_identifier,
                    source_path=scope.source_path,
                    source_url=scope.source_url,
                    source_season_id=scope.source_season_id,
                    source_season_team_id=scope.source_season_team_id,
                    source_stat_category_id=scope.source_stat_category_id,
                    status=scope.status,
                    reason=scope.availability_reason,
                    collected_at=scope.collected_at,
                    source_payload_sha256=scope.source_payload_sha256,
                    sample=scope.sample_json,
                )
                for scope in persisted_scopes
                if section.section == f"splits:{scope.stat_type}"
            ),
        ))
    return tuple(reconstructed)


def _source_row_values(collected, row):
    raw_values = {
        key: value for key, value in row.items()
        if key not in {
            "_player_id", "_player_url", "_payload_sha256", "_source_url",
            "_source_stat_category_id", "_source_collected_at",
        }
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
            row.get("_source_url") or collected.source_url or "unknown",
            collected.source_team_id or "unknown",
            collected.source_season_id or "unknown",
            collected.source_season_team_id or "unknown",
            (
                row.get("_source_stat_category_id")
                or collected.source_stat_category_id
                or "unknown"
            ),
            row.get("_player_id"),
            row.get("_source_collected_at") or collected.collected_at,
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
        tuple(
            (
                scope.scope_code,
                scope.display_label,
                scope.stat_type,
                scope.source_identifier,
                scope.source_path,
                scope.source_url,
                scope.source_season_id,
                scope.source_season_team_id,
                scope.source_stat_category_id,
                scope.status,
                scope.reason,
                scope.collected_at,
                scope.source_payload_sha256,
                scope.sample,
            )
            for scope in sorted(
                collected.scopes, key=lambda item: (item.stat_type, item.scope_code)
            )
        ),
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
    scopes = tuple(
        (
            scope.scope_code,
            scope.display_label,
            scope.stat_type,
            scope.source_identifier,
            scope.source_path,
            scope.source_url,
            scope.source_season_id,
            scope.source_season_team_id,
            scope.source_stat_category_id,
            scope.status,
            scope.availability_reason,
            scope.collected_at,
            scope.source_payload_sha256,
            scope.sample_json,
        )
        for scope in session.scalars(
            select(StatisticScope)
            .where(
                StatisticScope.snapshot_id == snapshot.id,
                StatisticScope.scope_kind == "source_split",
            )
            .order_by(StatisticScope.stat_type, StatisticScope.scope_code)
        )
        if section.section == f"splits:{scope.stat_type}"
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
        scopes,
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
            if collected.scopes:
                session.execute(
                    delete(StatisticScope).where(
                        StatisticScope.snapshot_id == snapshot.id,
                        StatisticScope.scope_kind == "source_split",
                        StatisticScope.stat_type
                        == collected.name.removeprefix("splits:"),
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

        if collected.scopes:
            expected_codes = {
                "batting": {
                    "vs_lhp", "vs_rhp", "runners_on", "bases_empty",
                    "bases_loaded", "risp", "two_outs",
                },
                "pitching": {
                    "vs_lhb", "vs_rhb", "runners_on", "bases_empty",
                    "bases_loaded", "risp", "two_outs",
                },
            }
            stat_type = collected.name.removeprefix("splits:")
            if (
                stat_type not in expected_codes
                or {scope.scope_code for scope in collected.scopes}
                != expected_codes[stat_type]
                or any(scope.stat_type != stat_type for scope in collected.scopes)
            ):
                raise DeterministicJobError("schema_validation")
            for scope in collected.scopes:
                session.add(StatisticScope(
                    organization_id=job.organization_id,
                    snapshot_id=snapshot.id,
                    scope_kind="source_split",
                    scope_code=scope.scope_code,
                    stat_type=scope.stat_type,
                    display_label=scope.display_label,
                    source_identifier=scope.source_identifier,
                    source_path=scope.source_path,
                    source_url=scope.source_url,
                    source_season_id=scope.source_season_id,
                    source_season_team_id=scope.source_season_team_id,
                    source_stat_category_id=scope.source_stat_category_id,
                    status=scope.status,
                    availability_reason=scope.reason,
                    collected_at=scope.collected_at,
                    parser_version=collected.parser_version,
                    schema_version=collected.schema_version,
                    sample_json=scope.sample,
                    source_payload_sha256=scope.source_payload_sha256,
                ))

        # Complete and partial sections both contain validated source evidence.
        # Unavailable/failed sections may carry diagnostic row counts but must
        # not become canonical source records.
        if collected.status not in {"complete", "partial"} or not collected.rows:
            continue
        for ordinal, row in enumerate(collected.rows):
            raw_values, payload_sha256 = _source_row_values(collected, row)
            session.add(
                SourceRecord(
                    organization_id=job.organization_id,
                    snapshot_id=snapshot.id,
                    section=collected.name,
                    row_ordinal=ordinal,
                    source_url=(
                        row.get("_source_url") or collected.source_url or "unknown"
                    ),
                    source_team_id=collected.source_team_id or "unknown",
                    source_season_id=collected.source_season_id or "unknown",
                    source_season_team_id=collected.source_season_team_id or "unknown",
                    source_stat_category_id=(
                        row.get("_source_stat_category_id")
                        or collected.source_stat_category_id
                        or "unknown"
                    ),
                    source_player_id=row.get("_player_id"),
                    collected_at=(
                        row.get("_source_collected_at")
                        or collected.collected_at
                        or _utcnow()
                    ),
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
    complete = [
        item for item in sections
        if item.status == "complete"
        and item.name in {"batting", "pitching", "fielding"}
    ]
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
            fielding_category_id=next(
                (
                    item.source_stat_category_id
                    for item in complete
                    if item.name == "fielding"
                ),
                None,
            ),
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
    from .metrics import DEFINITION_VERSION, METRIC_DEFINITIONS, calculate_metric
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

    metric_definition_ids = {}
    metric_definitions = sorted(
        {
            (
                metric.code,
                metric.definition_version,
                METRIC_DEFINITIONS[metric.code].formula,
            )
            for metric in canonical.detailed_metrics
        }
        | {
            (
                metric.code,
                f"{metric.definition_version}:{metric.provenance}",
                metric.provenance,
            )
            for metric in canonical.metrics
        }
    )
    for code, version, formula in metric_definitions:
        definition_id = session.scalar(
            postgresql_insert(MetricDefinition)
            .values(
                id=uuid.uuid4(),
                code=code,
                version=version,
                formula=formula,
            )
            .on_conflict_do_nothing(
                constraint="uq_metric_definitions_code_version"
            )
            .returning(MetricDefinition.id)
        )
        if definition_id is None:
            definition_id = session.scalar(
                select(MetricDefinition.id).where(
                    MetricDefinition.code == code,
                    MetricDefinition.version == version,
                )
            )
        if definition_id is None:
            raise RuntimeError("metric definition conflict was not visible")
        metric_definition_ids[(code, version)] = definition_id

    woba_definition_id = metric_definition_ids.get(("woba", DEFINITION_VERSION))
    if woba_definition_id is not None:
        from .metric_constants import ensure_woba_constant_set

        ensure_woba_constant_set(
            session,
            metric_definition_id=woba_definition_id,
            season_id=season.id,
            year=season.year,
            division=team_season.division,
        )

    fip_definition_id = metric_definition_ids.get(("fip", DEFINITION_VERSION))
    if fip_definition_id is not None:
        from .metric_constants import ensure_fip_constant_set

        ensure_fip_constant_set(
            session,
            metric_definition_id=fip_definition_id,
            season_id=season.id,
            year=season.year,
            division=team_season.division,
        )

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
                **fact.detailed_values,
            ))
        elif fact.section == "pitching":
            values = dict(fact.detailed_values)
            values.pop("innings_raw", None)
            session.add(PitchingFact(
                organization_id=job.organization_id,
                snapshot_id=snapshot.id,
                player_team_season_id=identity.id,
                source_record_id=source.id,
                **values,
            ))
        elif fact.section == "fielding":
            session.add(FieldingFact(
                organization_id=job.organization_id,
                snapshot_id=snapshot.id,
                player_team_season_id=identity.id,
                source_record_id=source.id,
                **dict(fact.detailed_values),
            ))
        else:
            raise DeterministicJobError("schema_validation")

    for metric in canonical.metrics:
        version = f"{metric.definition_version}:{metric.provenance}"
        available = metric.value is not None
        session.add(MetricValue(
            organization_id=job.organization_id,
            snapshot_id=snapshot.id,
            metric_definition_id=metric_definition_ids[(metric.code, version)],
            player_team_season_id=identities[metric.identity_key].id,
            value=metric.value,
            unavailable_reason=(
                None if available else "source value unavailable"
            ),
            status="available" if available else "missing_input",
            reason_code=None if available else "source_value_unavailable",
            inputs_json={"provenance": metric.provenance},
            sample_json={},
            scope="full_season",
        ))

    verified_constants = {}
    for code in ("woba", "fip"):
        definition_id = metric_definition_ids.get((code, DEFINITION_VERSION))
        if definition_id is None:
            continue
        matches = list(session.scalars(
            select(MetricConstantSet).where(
                MetricConstantSet.metric_definition_id == definition_id,
                MetricConstantSet.metric_code == code,
                MetricConstantSet.season_id == season.id,
                MetricConstantSet.division == team_season.division,
                MetricConstantSet.verification_status == "verified",
            )
        ))
        if len(matches) == 1:
            verified_constants[code] = matches[0]

    for metric in canonical.detailed_metrics:
        definition_id = metric_definition_ids[
            (metric.code, metric.definition_version)
        ]
        value = metric.value
        status = metric.status
        reason_code = metric.reason_code
        inputs = metric.inputs
        sample = metric.sample
        constant_set = verified_constants.get(metric.code)
        constant_set_id = None
        if constant_set is not None:
            result = calculate_metric(
                metric.code,
                metric.inputs,
                season_id=str(season.id),
                division=team_season.division,
                constant_set={
                    "metric_code": constant_set.metric_code,
                    "version": constant_set.version,
                    "season_id": str(constant_set.season_id),
                    "division": constant_set.division,
                    "verification_status": constant_set.verification_status,
                    "values": constant_set.values_json,
                },
            )
            value = result.value
            status = result.status
            reason_code = result.reason_code
            inputs = result.inputs
            sample = result.sample
            if result.status == "available":
                constant_set_id = constant_set.id
        session.add(MetricValue(
            organization_id=job.organization_id,
            snapshot_id=snapshot.id,
            metric_definition_id=definition_id,
            player_team_season_id=identities[metric.identity_key].id,
            value=value,
            unavailable_reason=None,
            status=status,
            reason_code=reason_code,
            inputs_json=inputs,
            sample_json=sample,
            scope="full_season",
            constant_set_id=constant_set_id,
        ))

    split_sections = [item for item in sections if item.scopes]
    if split_sections:
        identity_by_source = {
            item.source_player_id: identities[item.identity_key]
            for item in canonical.players
            if item.source_player_id is not None
        }
        avg_definition_id = metric_definition_ids.get(("avg", DEFINITION_VERSION))
        if avg_definition_id is None:
            avg_definition_id = session.scalar(
                postgresql_insert(MetricDefinition)
                .values(
                    id=uuid.uuid4(),
                    code="avg",
                    version=DEFINITION_VERSION,
                    formula=METRIC_DEFINITIONS["avg"].formula,
                )
                .on_conflict_do_nothing(
                    constraint="uq_metric_definitions_code_version"
                )
                .returning(MetricDefinition.id)
            )
            if avg_definition_id is None:
                avg_definition_id = session.scalar(
                    select(MetricDefinition.id).where(
                        MetricDefinition.code == "avg",
                        MetricDefinition.version == DEFINITION_VERSION,
                    )
                )
        if avg_definition_id is None:
            raise RuntimeError("split metric definition conflict was not visible")

        scopes = {
            (scope.stat_type, scope.scope_code): scope
            for scope in session.scalars(
                select(StatisticScope).where(
                    StatisticScope.snapshot_id == snapshot.id,
                    StatisticScope.organization_id == job.organization_id,
                    StatisticScope.scope_kind == "source_split",
                )
            )
        }
        for collected in split_sections:
            stat_type = collected.name.removeprefix("splits:")
            records = list(session.scalars(
                select(SourceRecord)
                .where(
                    SourceRecord.snapshot_id == snapshot.id,
                    SourceRecord.organization_id == job.organization_id,
                    SourceRecord.section == collected.name,
                )
                .order_by(SourceRecord.row_ordinal)
            ))
            for source in records:
                scope_code = source.raw_values.get("scope_code")
                scope = scopes.get((stat_type, scope_code))
                identity = identity_by_source.get(source.source_player_id)
                if scope is None or identity is None or scope.status != "complete":
                    raise DeterministicJobError("schema_validation")
                source.player_team_season_id = identity.id
                session.flush()
                try:
                    hits = int(source.raw_values["H"])
                    at_bats = int(source.raw_values["AB"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise DeterministicJobError("schema_validation") from exc
                if hits < 0 or at_bats < 0 or hits > at_bats:
                    raise DeterministicJobError("schema_validation")
                sample = source.raw_values.get("sample")
                if not isinstance(sample, dict) or sample.get("at_bats") != at_bats:
                    raise DeterministicJobError("schema_validation")
                fact_values = {
                    "organization_id": job.organization_id,
                    "snapshot_id": snapshot.id,
                    "statistic_scope_id": scope.id,
                    "player_team_season_id": identity.id,
                    "source_record_id": source.id,
                    "sample_json": sample,
                }
                if stat_type == "batting":
                    session.add(ScopedBattingFact(
                        at_bats=at_bats,
                        hits=hits,
                        **fact_values,
                    ))
                elif stat_type == "pitching":
                    session.add(ScopedPitchingFact(
                        opponent_at_bats=at_bats,
                        hits_allowed=hits,
                        **fact_values,
                    ))
                else:
                    raise DeterministicJobError("schema_validation")
                metric = calculate_metric(
                    "avg", {"at_bats": at_bats, "hits": hits}
                )
                session.add(MetricValue(
                    organization_id=job.organization_id,
                    snapshot_id=snapshot.id,
                    metric_definition_id=avg_definition_id,
                    player_team_season_id=identity.id,
                    statistic_scope_id=scope.id,
                    value=metric.value,
                    unavailable_reason=None,
                    status=metric.status,
                    reason_code=metric.reason_code,
                    inputs_json=metric.inputs,
                    sample_json=metric.sample,
                    scope=f"split:{stat_type}:{scope.scope_code}",
                ))

    _persist_schedule_scopes(session, job, snapshot, sections, identities)
    _persist_player_history(session, job, snapshot, sections, identities)


def _persist_player_history(session, job, snapshot, sections, identities):
    """Persist career, aggregate, and exact-game drill rows from detail evidence."""
    from types import SimpleNamespace
    from .ncaa_detail import aggregate_career_seasons

    history_names = {
        f"history:{stat_type}" for stat_type in ("batting", "pitching", "fielding")
    }
    if not history_names.intersection(item.name for item in sections):
        return
    identity_by_source = {
        player.source_player_id: identity
        for identity in identities.values()
        for player in (session.get(Player, identity.player_id),)
        if player is not None and player.source_player_id
    }
    games_by_source = {
        game.source_game_id: game for game in session.scalars(select(Game).where(
            Game.snapshot_id == snapshot.id,
            Game.organization_id == job.organization_id,
            Game.source_game_id.is_not(None),
        ))
    }
    career_by_player_type = {}
    game_models = {
        "batting": (BattingGameLogFact, (
            "at_bats", "hits", "runs", "home_runs", "walks", "strikeouts",
        )),
        "pitching": (PitchingGameLogFact, (
            "innings_outs", "hits_allowed", "runs_allowed", "earned_runs",
            "walks", "strikeouts", "batters_faced",
        )),
        "fielding": (FieldingGameLogFact, (
            "putouts", "assists", "errors", "total_chances", "passed_balls",
            "stolen_base_attempts", "caught_stealing_by",
        )),
    }
    for source in session.scalars(select(SourceRecord).where(
        SourceRecord.snapshot_id == snapshot.id,
        SourceRecord.organization_id == job.organization_id,
        SourceRecord.section.in_(tuple(history_names)),
    ).order_by(SourceRecord.section, SourceRecord.row_ordinal)):
        stat_type = source.section.removeprefix("history:")
        values = source.raw_values
        identity = identity_by_source.get(source.source_player_id)
        if identity is None or stat_type not in game_models:
            raise DeterministicJobError("schema_validation")
        source.player_team_season_id = identity.id
        session.flush()
        counts = values.get("counts")
        rates = values.get("rates")
        if not isinstance(counts, dict) or not isinstance(rates, dict):
            raise DeterministicJobError("schema_validation")
        if values.get("row_kind") == "career_season":
            row = SimpleNamespace(
                row_id=values["row_id"],
                stat_type=stat_type,
                source_season_team_id=values.get("source_season_team_id"),
                division=values.get("division"),
                source_season_id=values.get("source_season_id"),
                counts=counts,
            )
            career_by_player_type.setdefault((identity.id, stat_type), []).append(row)
            session.add(CareerSeasonFact(
                organization_id=job.organization_id,
                snapshot_id=snapshot.id,
                player_team_season_id=identity.id,
                source_record_id=source.id,
                row_id=values["row_id"],
                stat_type=stat_type,
                season_label=values["season_label"],
                source_season_id=values.get("source_season_id"),
                school_name=values["school_name"],
                source_season_team_id=values.get("source_season_team_id"),
                division=values.get("division"),
                source_stat_category_id=values["source_stat_category_id"],
                counts_json=counts,
                rates_json=rates,
                **{key: counts.get(key) for key in (
                    "at_bats", "hits", "innings_outs", "earned_runs", "putouts",
                    "assists", "errors", "total_chances",
                )},
            ))
        elif values.get("row_kind") == "game_log":
            if values.get("link_status") != "linked":
                continue
            game = games_by_source.get(values.get("source_game_id"))
            if game is None:
                raise DeterministicJobError("schema_validation")
            model, columns = game_models[stat_type]
            session.add(model(
                organization_id=job.organization_id,
                snapshot_id=snapshot.id,
                player_team_season_id=identity.id,
                source_record_id=source.id,
                game_id=game.id,
                row_id=values["row_id"],
                counts_json=counts,
                **{key: counts.get(key) for key in columns},
            ))
        else:
            raise DeterministicJobError("schema_validation")
    for identity in identity_by_source.values():
        for stat_type in ("batting", "pitching", "fielding"):
            career_by_player_type.setdefault((identity.id, stat_type), [])
    for (identity_id, stat_type), rows in career_by_player_type.items():
        aggregate = aggregate_career_seasons(
            rows,
            stat_type=stat_type,
            metric_codes=("avg",) if stat_type == "batting" else (
                ("era",) if stat_type == "pitching" else ()
            ),
        )
        session.add(CareerAggregate(
            organization_id=job.organization_id,
            snapshot_id=snapshot.id,
            player_team_season_id=identity_id,
            stat_type=stat_type,
            status=aggregate.status,
            availability_reason=aggregate.reason,
            definition_version="career-v1",
            counts_json=dict(aggregate.counts),
            included_row_ids=list(aggregate.included_row_ids),
            excluded_rows_json=dict(aggregate.excluded_rows),
        ))


def _schedule_row_to_audited(row, *, season_id):
    from .scopes import AuditedGame

    try:
        game_date = datetime.strptime(row["game_date"], "%Y-%m-%d").date()
        ordinal = int(row["doubleheader_ordinal"])
    except (KeyError, TypeError, ValueError) as exc:
        raise DeterministicJobError("schema_validation") from exc
    source_game_id = row.get("source_game_id")
    if source_game_id is not None:
        source_game_id = str(source_game_id)
    return AuditedGame(
        source_game_id=source_game_id,
        fixture_fallback_key=None,
        season_id=season_id,
        game_date=game_date,
        completed_at=(
            datetime.combine(game_date, datetime.min.time(), tzinfo=UTC)
            if row.get("status") == "completed" else None
        ),
        status=row.get("status"),
        opponent_team_id=row.get("opponent_team_id"),
        report_team_conference_id=row.get("team_conference_id"),
        opponent_conference_id=row.get("opponent_conference_id"),
        conference_evidence_season_id=(
            season_id
            if row.get("conference_evidence_status") == "present" else None
        ),
        doubleheader_ordinal=ordinal,
        result_checksum=row.get("result_sha256") or "",
        venue=row.get("venue"),
    )


def _metric_definition_id(session, code):
    from .metrics import DEFINITION_VERSION, METRIC_DEFINITIONS

    definition_id = session.scalar(
        postgresql_insert(MetricDefinition)
        .values(
            id=uuid.uuid4(),
            code=code,
            version=DEFINITION_VERSION,
            formula=METRIC_DEFINITIONS[code].formula,
        )
        .on_conflict_do_nothing(constraint="uq_metric_definitions_code_version")
        .returning(MetricDefinition.id)
    )
    if definition_id is None:
        definition_id = session.scalar(select(MetricDefinition.id).where(
            MetricDefinition.code == code,
            MetricDefinition.version == DEFINITION_VERSION,
        ))
    if definition_id is None:
        raise RuntimeError("scoped metric definition conflict was not visible")
    return definition_id


def _persist_schedule_scopes(session, job, snapshot, sections, identities):
    """Create canonical games and exact computed scopes from committed evidence."""
    from .scopes import (
        ScopedCountFact,
        aggregate_scope_facts,
        select_conference_games,
        select_last_ten_completed_games,
    )

    schedule_section = next((item for item in sections if item.name == "schedule"), None)
    if schedule_section is None:
        return
    if schedule_section.status not in {"complete", "partial"} or not schedule_section.rows:
        return
    records = list(session.scalars(
        select(SourceRecord).where(
            SourceRecord.snapshot_id == snapshot.id,
            SourceRecord.organization_id == job.organization_id,
            SourceRecord.section == "schedule",
        ).order_by(SourceRecord.row_ordinal)
    ))
    if len(records) != len(schedule_section.rows) or not records:
        raise DeterministicJobError("schema_validation")
    if list(session.scalars(select(Game.id).where(Game.snapshot_id == snapshot.id))):
        raise DeterministicJobError("schema_validation")

    report = session.get(Report, job.report_id)
    team_season = session.get(TeamSeason, report.team_season_id)
    team = session.get(Team, team_season.team_id)
    season = session.get(Season, team_season.season_id)
    canonical_source_season_id = str(
        schedule_section.source_season_id or season.source_season_id or ""
    ).strip()
    category_by_stat_type = {
        stat_type: next(
            (
                item.source_stat_category_id
                for item in sections
                if item.name == stat_type and item.source_stat_category_id
            ),
            (
                team_season.batting_stat_category_id
                if stat_type == "batting"
                else team_season.pitching_stat_category_id
            ),
        )
        for stat_type in ("batting", "pitching")
    }
    if (
        not canonical_source_season_id
        or any(not str(value or "").strip() for value in category_by_stat_type.values())
    ):
        raise DeterministicJobError("schema_validation")
    audited = tuple(
        _schedule_row_to_audited(
            record.raw_values, season_id=canonical_source_season_id
        )
        for record in records
    )
    schedule_complete = (
        schedule_section.status == "complete"
        and all(row.raw_values.get("schedule_complete") is True for row in records)
    )

    evidence_by_team_season = {}
    first = records[0]
    report_conference_id = first.raw_values.get("team_conference_id")
    report_evidence = SeasonConferenceEvidence(
        organization_id=job.organization_id,
        snapshot_id=snapshot.id,
        source_record_id=first.id,
        source_season_id=canonical_source_season_id,
        source_team_id=team.source_team_id,
        source_season_team_id=team_season.source_season_team_id,
        conference_id=report_conference_id,
        evidence_status="present" if report_conference_id else "source_absent",
        collected_at=first.collected_at,
        payload_sha256=first.payload_sha256,
    )
    session.add(report_evidence)
    session.flush()
    for record in records:
        values = record.raw_values
        opponent_season_team_id = values.get("opponent_season_team_id")
        opponent_team_id = values.get("opponent_team_id")
        if not opponent_season_team_id or not opponent_team_id:
            continue
        evidence = evidence_by_team_season.get(opponent_season_team_id)
        if evidence is None:
            conference_id = values.get("opponent_conference_id")
            evidence = SeasonConferenceEvidence(
                organization_id=job.organization_id,
                snapshot_id=snapshot.id,
                source_record_id=record.id,
                source_season_id=canonical_source_season_id,
                source_team_id=str(opponent_team_id),
                source_season_team_id=str(opponent_season_team_id),
                conference_id=conference_id,
                evidence_status="present" if conference_id else "source_absent",
                collected_at=record.collected_at,
                payload_sha256=record.payload_sha256,
            )
            session.add(evidence)
            session.flush()
            evidence_by_team_season[opponent_season_team_id] = evidence
        elif (
            evidence.source_team_id != str(opponent_team_id)
            or evidence.conference_id != values.get("opponent_conference_id")
        ):
            raise DeterministicJobError("schema_validation")

    games_by_source = {}
    for record, item in zip(records, audited, strict=True):
        if not item.source_game_id:
            continue
        opponent_season_team_id = record.raw_values.get("opponent_season_team_id")
        game = Game(
            organization_id=job.organization_id,
            snapshot_id=snapshot.id,
            source_record_id=record.id,
            source_season_id=canonical_source_season_id,
            game_date=item.game_date,
            completed_at=item.completed_at,
            status=item.status,
            result_code=None,
            result_sha256=item.result_checksum,
            opponent_team_id=item.opponent_team_id,
            opponent_season_team_id=opponent_season_team_id,
            report_team_conference_evidence_id=report_evidence.id,
            opponent_conference_evidence_id=(
                evidence_by_team_season[opponent_season_team_id].id
                if opponent_season_team_id in evidence_by_team_season else None
            ),
            doubleheader_ordinal=item.doubleheader_ordinal,
            venue=item.venue,
            source_game_id=item.source_game_id,
            fixture_fallback_key=None,
            identity_kind="source",
            fixture_test_mode=False,
        )
        session.add(game)
        games_by_source[item.source_game_id] = game
    session.flush()

    selections = {
        "last_10": select_last_ten_completed_games(
            audited, schedule_complete=schedule_complete
        ),
        "conference": select_conference_games(
            audited,
            report_season_id=canonical_source_season_id,
            schedule_complete=schedule_complete,
        ),
    }
    identity_by_source = {
        player.source_player_id: identity
        for identity in identities.values()
        for player in (session.get(Player, identity.player_id),)
        if player is not None and player.source_player_id
    }
    log_sections = {
        stat_type: next(
            (item for item in sections if item.name == f"game_logs:{stat_type}"), None
        )
        for stat_type in ("batting", "pitching", "fielding")
    }
    log_records = {
        stat_type: list(session.scalars(select(SourceRecord).where(
            SourceRecord.snapshot_id == snapshot.id,
            SourceRecord.organization_id == job.organization_id,
            SourceRecord.section == f"game_logs:{stat_type}",
        ).order_by(SourceRecord.row_ordinal)))
        for stat_type in log_sections
    }

    for scope_code, selected in selections.items():
        for stat_type in ("batting", "pitching", "fielding"):
            log_section = log_sections[stat_type]
            scope_status, reason = selected.status, selected.reason
            if scope_status == "complete" and (
                log_section is None or log_section.status != "complete"
            ):
                scope_status, reason = "partial", "game_log_evidence_unavailable"
            raw_facts = []
            invalid_link = False
            for source in log_records[stat_type]:
                values = source.raw_values
                identity = identity_by_source.get(source.source_player_id)
                source_game_id = values.get("source_game_id")
                if (
                    values.get("link_status") != "linked"
                    or source_game_id not in games_by_source
                    or identity is None
                    or not isinstance(values.get("counts"), dict)
                ):
                    invalid_link = True
                    continue
                source.player_team_season_id = identity.id
                raw_facts.append((source, identity, ScopedCountFact(
                    fact_id=str(values.get("fact_id") or source.id),
                    game_id=source_game_id,
                    player_id=str(identity.id),
                    stat_type=stat_type,
                    counts=values["counts"],
                )))
            if invalid_link and scope_status == "complete":
                scope_status, reason = "partial", "ambiguous_game_link"
            scope = StatisticScope(
                organization_id=job.organization_id,
                snapshot_id=snapshot.id,
                scope_kind="computed_game",
                scope_code=scope_code,
                stat_type=stat_type,
                display_label=("Last 10 Completed Games" if scope_code == "last_10" else "Conference Only"),
                source_identifier=None,
                source_path=(
                    f"/team/{team.source_team_id}/{canonical_source_season_id}"
                ),
                source_url=schedule_section.source_url,
                source_season_id=canonical_source_season_id,
                source_season_team_id=team_season.source_season_team_id,
                source_stat_category_id=(
                    category_by_stat_type[stat_type]
                    if stat_type in category_by_stat_type
                    else "fielding"
                ),
                status=scope_status,
                availability_reason=reason,
                collected_at=schedule_section.collected_at or first.collected_at,
                parser_version=schedule_section.parser_version,
                schema_version=schedule_section.schema_version,
                sample_json={
                    "game_count": selected.actual_game_count,
                    "game_ids": [item.identity for item in selected.games],
                },
                source_payload_sha256=first.payload_sha256,
                schedule_source_record_id=first.id,
                start_date=selected.start_date,
                end_date=selected.end_date,
                actual_game_count=selected.actual_game_count,
                report_conference_id=(report_conference_id if scope_code == "conference" else None),
            )
            session.add(scope)
            session.flush()
            for ordinal, item in enumerate(selected.games, 1):
                game = games_by_source.get(item.identity)
                if game is None:
                    raise DeterministicJobError("schema_validation")
                session.add(StatisticScopeGame(
                    organization_id=job.organization_id,
                    snapshot_id=snapshot.id,
                    statistic_scope_id=scope.id,
                    game_id=game.id,
                    membership_ordinal=ordinal,
                ))
            if scope_status != "complete":
                continue
            selected_ids = tuple(item.identity for item in selected.games)
            by_player = {}
            sources_by_fact_id = {}
            for source, identity, fact in raw_facts:
                by_player.setdefault(identity.id, []).append(fact)
                sources_by_fact_id[fact.fact_id] = source
            for identity_id, facts in by_player.items():
                aggregate = aggregate_scope_facts(
                    facts,
                    game_ids=selected_ids,
                    metric_codes=(
                        ("avg",) if stat_type == "batting"
                        else ("era",) if stat_type == "pitching" else ()
                    ),
                    season_id=canonical_source_season_id,
                    division=team_season.division,
                )
                if aggregate.status != "complete" or not aggregate.contributing_fact_ids:
                    continue
                source = sources_by_fact_id[aggregate.contributing_fact_ids[0]]
                common = dict(
                    organization_id=job.organization_id,
                    snapshot_id=snapshot.id,
                    statistic_scope_id=scope.id,
                    player_team_season_id=identity_id,
                    source_record_id=source.id,
                    sample_json=dict(aggregate.sample),
                    counts_json=dict(aggregate.counts),
                    contributing_fact_ids=list(aggregate.contributing_fact_ids),
                )
                if stat_type == "batting":
                    session.add(ScopedBattingFact(
                        at_bats=aggregate.counts.get("at_bats"),
                        hits=aggregate.counts.get("hits"),
                        **common,
                    ))
                elif stat_type == "pitching":
                    session.add(ScopedPitchingFact(
                        opponent_at_bats=aggregate.counts.get("opponent_at_bats"),
                        hits_allowed=aggregate.counts.get("hits_allowed"),
                        **common,
                    ))
                else:
                    session.add(ScopedFieldingFact(
                        putouts=aggregate.counts.get("putouts"),
                        assists=aggregate.counts.get("assists"),
                        errors=aggregate.counts.get("errors"),
                        total_chances=aggregate.counts.get("total_chances"),
                        **common,
                    ))
                for code, result in aggregate.metrics.items():
                    session.add(MetricValue(
                        organization_id=job.organization_id,
                        snapshot_id=snapshot.id,
                        metric_definition_id=_metric_definition_id(session, code),
                        player_team_season_id=identity_id,
                        statistic_scope_id=scope.id,
                        value=result.value,
                        unavailable_reason=None,
                        status=result.status,
                        reason_code=result.reason_code,
                        inputs_json=result.inputs,
                        sample_json={**result.sample, "game_count": aggregate.game_count},
                        scope=f"computed:{stat_type}:{scope_code}",
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
    evidence_retention = None
    if evidence is not None:
        from .evidence import (
            FailureEvidenceConfigurationError,
            get_failure_evidence_retention,
            store_failure_evidence,
        )

        try:
            evidence_retention = get_failure_evidence_retention()
        except FailureEvidenceConfigurationError:
            # Invalid diagnostic retention is an operational configuration
            # failure, not source evidence. Preserve a safe terminal state and
            # omit optional bytes instead of rolling the job back to running.
            evidence = None
            error = DeterministicJobError("unexpected")
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
            store_failure_evidence(
                session,
                job.id,
                section=evidence.section,
                source_url=evidence.source_url,
                html=evidence.html,
                failure_category=evidence.failure_category,
                collected_at=evidence.collected_at,
                retention=evidence_retention,
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
            if recovered_sections:
                sections = recovered_sections
            else:
                with session_advisory_heartbeat(claim):
                    sections = collector(
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
                from .artifacts import ensure_snapshot_workbook_artifact

                ensure_snapshot_workbook_artifact(session, published)
                job.status = published.status
                job.finished_at = _utcnow()
                close_report_dispatches(session, job.id)
                ensure_detailed_successor(session, job)
                return published
        except Exception as exc:
            classified = _classified_error(exc)
            failure_frame = traceback.extract_tb(exc.__traceback__)[-1]
            missing_module = re.sub(
                r"[^A-Za-z0-9_.-]",
                "?",
                str(getattr(exc, "name", ""))[:120],
            )
            LOGGER.error(
                "report_job_failed error_type=%s category=%s module=%s "
                "location=%s:%s:%s constraint=%s table=%s column=%s",
                type(exc).__name__,
                classified.category,
                missing_module or "none",
                os.path.basename(failure_frame.filename),
                failure_frame.lineno,
                failure_frame.name,
                re.sub(
                    r"[^A-Za-z0-9_.-]",
                    "?",
                    str(
                        getattr(
                            getattr(getattr(exc, "orig", None), "diag", None),
                            "constraint_name",
                            "",
                        )
                        if isinstance(exc, IntegrityError)
                        else ""
                    )[:120],
                ) or "none",
                re.sub(
                    r"[^A-Za-z0-9_.-]",
                    "?",
                    str(
                        getattr(
                            getattr(getattr(exc, "orig", None), "diag", None),
                            "table_name",
                            "",
                        )
                        if isinstance(exc, IntegrityError)
                        else ""
                    )[:120],
                ) or "none",
                re.sub(
                    r"[^A-Za-z0-9_.-]",
                    "?",
                    str(
                        getattr(
                            getattr(getattr(exc, "orig", None), "diag", None),
                            "column_name",
                            "",
                        )
                        if isinstance(exc, IntegrityError)
                        else ""
                    )[:120],
                ) or "none",
            )
            safe_error = _record_failure(
                claimed_sessions,
                job_id,
                delivery_generation,
                classified,
            )
            raise safe_error from None


def collect_ncaa_sections(session_factory, job_id, *, cached_sections=frozenset()):
    """Collect live NCAA sections or reuse only same-job version-matched evidence."""
    limits = load_phase3_collection_limits()
    from roster_scraper import (
        NcaaAccessError,
        NcaaError,
        _NcaaFailureEvidenceError,
        _find_ncaa_season,
        collect_ncaa_by_id,
        collect_ncaa_player_detail_batch_evidence,
        collect_ncaa_schedule_evidence,
        collect_ncaa_split_results,
    )
    from .canonical import to_collected_sections

    job_uuid = uuid.UUID(str(job_id))
    cache_entries = []
    collection_now = _utcnow()
    with session_factory.begin() as session:
        collection_job = session.scalar(
            select(ReportJob).where(ReportJob.id == job_uuid).with_for_update()
        )
        if collection_job is None:
            raise DeterministicJobError(
                "schema_validation", "Report job does not exist."
            )
        if collection_job.phase3_collection_started_at is None:
            collection_job.phase3_collection_started_at = collection_now
            session.flush()
        elapsed_seconds = max(
            0.0,
            (
                collection_now - collection_job.phase3_collection_started_at
            ).total_seconds(),
        )
    remaining_wall_seconds = limits.wall_time_seconds - elapsed_seconds
    if remaining_wall_seconds <= 0:
        raise DeterministicJobError("collection_timeout")
    deadline_monotonic = time.monotonic() + remaining_wall_seconds
    with session_factory() as session:
        job = session.get(ReportJob, job_uuid)
        if job is None:
            raise DeterministicJobError("schema_validation", "Report job does not exist.")
        dispatch_generation = job.dispatch_generation
        build_profile = job.build_profile
        budget = CollectionBudget(
            limits,
            initial_live_count=job.phase3_live_pages_consumed,
        )
        report = session.get(Report, job.report_id)
        team_season = session.get(TeamSeason, report.team_season_id)
        team = session.get(Team, team_season.team_id)
        season = session.get(Season, team_season.season_id)
        snapshot_id = job.result_snapshot_id

        matching_keys = {key: None for key in cached_sections}
        matching = _matching_cached_sections(session, job, matching_keys)
        matching = {
            name: section for name, section in matching.items()
            if name != "schedule"
            and not name.startswith("game_logs:")
            and not name.startswith("history:")
        }
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
        season_team_id = team_season.source_season_team_id
        division = team_season.division
        year = season.year
        ncaa_season_contract = _find_ncaa_season(year)
        discovered_fielding_id = str(
            ncaa_season_contract.get("fielding_id") or ""
        ).strip()
        fielding_requested = discovered_fielding_id.isdigit()
        missing_core = frozenset({"batting", "pitching"} - matching.keys())
        fielding_missing = fielding_requested and "fielding" not in matching
        split_transport_available = (
            os.environ.get("NCAA_BROWSER_PROVIDER") == "browserless"
        )
        split_missing = not {
            "splits:batting", "splits:pitching"
        } <= matching.keys()
        phase3_missing = True
        source_team_id = team.source_team_id
        contract_source_season_id = str(
            ncaa_season_contract.get("id") or ""
        ).strip()
        source_season_id = (
            contract_source_season_id
            if contract_source_season_id.isdigit()
            else str(season.source_season_id or "").strip()
        )
        split_category_ids = {
            "batting": str(
                team_season.batting_stat_category_id
                or ncaa_season_contract.get("batting_id")
                or ""
            ).strip(),
            "pitching": str(
                team_season.pitching_stat_category_id
                or ncaa_season_contract.get("pitching_id")
                or ""
            ).strip(),
        }
        if (
            not source_season_id
            or any(not value for value in split_category_ids.values())
        ):
            raise DeterministicJobError("schema_validation")

        for section in reconstructed:
            entry = budget.plan(
                section.name,
                _request_source_identity(
                    section.name,
                    source_team_id,
                    source_season_id,
                    season_team_id,
                    section.source_stat_category_id,
                ),
            )
            budget.settle(entry, outcome="cache_hit")
            cache_entries.append(entry)

    for entry in cache_entries:
        _record_request_ledger(
            session_factory,
            job_id=job_uuid,
            snapshot_id=snapshot_id,
            organization_id=job.organization_id,
            dispatch_generation=dispatch_generation,
            entry=entry,
            request_kind="cache",
            outcome="cache_hit",
            budget_remaining=budget.remaining,
        )

    if not missing_core and not fielding_missing and not split_missing and not phase3_missing:
        return reconstructed

    page_counts = {}
    reservation_lock = RLock()

    def begin_live_batch(requests):
        entries = {
            family: budget.plan(
                family, _request_source_identity(family, *identity_parts)
            )
            for family, identity_parts in requests
        }
        budget.reserve_live(entries.values(), page_count=0)
        for entry in entries.values():
            page_counts[entry] = 0
            _record_request_ledger(
                session_factory,
                job_id=job_uuid,
                snapshot_id=snapshot_id,
                organization_id=job.organization_id,
                dispatch_generation=dispatch_generation,
                entry=entry,
                request_kind="planned",
                outcome="planned",
                budget_remaining=budget.remaining,
            )
        return entries

    def begin_live(family, *identity_parts):
        entries = begin_live_batch(
            ((family, identity_parts),),
        )
        return None if entries is None else entries[family]

    def finish_live(entry, *, outcome, safe_category=None):
        remaining = budget.settle(entry, outcome=outcome)
        _record_request_ledger(
            session_factory,
            job_id=job_uuid,
            snapshot_id=snapshot_id,
            organization_id=job.organization_id,
            dispatch_generation=dispatch_generation,
            entry=entry,
            request_kind="live",
            outcome=outcome,
            budget_remaining=remaining,
            pages_consumed=page_counts.get(entry, 0),
            safe_category=safe_category,
        )

    def request_reserver(entries):
        def reserve(family):
            # Player-detail sessions may navigate concurrently. Serialize only
            # the durable reservation so local and PostgreSQL counters advance
            # in the same monotonic order.
            with reservation_lock:
                remaining_timeout_seconds(
                    deadline_monotonic,
                    limits.navigation_timeout_seconds,
                )
                entry = entries.get(family)
                if entry is None:
                    raise DeterministicJobError("schema_validation")
                remaining = _reserve_until_deadline(
                    lambda: _reserve_job_live_pages(
                        session_factory,
                        job_id=job_uuid,
                        dispatch_generation=dispatch_generation,
                        page_count=1,
                        page_budget=limits.live_page_budget,
                    ),
                    deadline_monotonic=deadline_monotonic,
                )
                if remaining is None:
                    raise CollectionBudgetExceeded(
                        "phase3_request_budget_exhausted"
                    )
                page_counts[entry] += 1
                budget.sync_live_count(limits.live_page_budget - remaining)
                _record_request_ledger(
                    session_factory,
                    job_id=job_uuid,
                    snapshot_id=snapshot_id,
                    organization_id=job.organization_id,
                    dispatch_generation=dispatch_generation,
                    entry=entry,
                    request_kind="planned",
                    outcome="planned",
                    budget_remaining=remaining,
                    pages_consumed=page_counts[entry],
                )
        return reserve

    def exhaust_live(entries):
        for entry in entries.values():
            finish_live(
                entry,
                outcome="budget_exhausted",
                safe_category="request_budget_exhausted",
            )

    def timeout_live(entries):
        for entry in entries.values():
            finish_live(
                entry,
                outcome="live_failed",
                safe_category="collection_timeout",
            )

    def finalized_sections():
        if budget.unsettled:
            raise DeterministicJobError("schema_validation")
        return tuple(sorted(
            (*reconstructed, *fetched), key=lambda section: section.name
        ))

    fetched = []
    if missing_core:
        core_entries = begin_live_batch(
            tuple(
                (
                    family,
                    (
                        source_team_id, source_season_id, season_team_id,
                        split_category_ids[family],
                    ),
                )
                for family in sorted(missing_core)
            ),
        )
        if core_entries is None:
            fetched.extend(
                CollectedSection.failed(
                    family,
                    critical=True,
                    category="request_budget_exhausted",
                )
                for family in sorted(missing_core)
            )
            return finalized_sections()
        try:
            collection_kwargs = {
                "division": division,
                "year": year,
                "force_refresh": True,
                "request_reserver": request_reserver(core_entries),
                "navigation_timeout_seconds": limits.navigation_timeout_seconds,
                "deadline_monotonic": deadline_monotonic,
            }
            if (
                fielding_requested
                or missing_core != frozenset({"batting", "pitching"})
            ):
                collection_kwargs["section_names"] = missing_core
            collection = collect_ncaa_by_id(
                season_team_id,
                **collection_kwargs,
            )
        except _NcaaFailureEvidenceError as exc:
            category = exc.evidence.failure_category
            for entry in core_entries.values():
                finish_live(entry, outcome="live_failed", safe_category=category)
            error_type = (
                DeterministicJobError
                if category == "schema_validation"
                else RetryableJobError
            )
            raise error_type(category, evidence=exc.evidence) from None
        except CollectionBudgetExceeded:
            exhaust_live(core_entries)
            fetched.extend(
                CollectedSection.failed(
                    family,
                    critical=True,
                    category="request_budget_exhausted",
                )
                for family in sorted(missing_core)
            )
            return finalized_sections()
        except CollectionDeadlineExceeded:
            timeout_live(core_entries)
            raise DeterministicJobError("collection_timeout") from None
        except BrowserCapacityExceeded as exc:
            for entry in core_entries.values():
                finish_live(
                    entry,
                    outcome="capacity_exhausted",
                    safe_category="browser_capacity",
                )
            raise RetryableJobError("browser_capacity") from exc
        except NcaaAccessError as exc:
            for entry in core_entries.values():
                finish_live(
                    entry, outcome="live_failed", safe_category="source_blocked"
                )
            raise RetryableJobError("source_blocked") from exc
        core_fetched = to_collected_sections(collection)
        if {section.name for section in core_fetched} != set(missing_core):
            for entry in core_entries.values():
                finish_live(
                    entry,
                    outcome="live_failed",
                    safe_category="schema_validation",
                )
            raise DeterministicJobError("schema_validation")
        for entry in core_entries.values():
            finish_live(entry, outcome="live_complete")
        fetched.extend(core_fetched)

    if build_profile == "quick":
        return finalized_sections()

    if fielding_missing:
        fielding_entry = begin_live(
            "fielding", source_team_id, source_season_id, season_team_id,
            discovered_fielding_id,
        )
        if fielding_entry is None:
            fetched.append(CollectedSection.supplemental_failure(
                "fielding",
                status="unavailable",
                category="request_budget_exhausted",
                collected_at=_utcnow(),
                source_team_id=source_team_id,
                source_season_id=source_season_id,
                source_season_team_id=season_team_id,
            ))
        else:
            try:
                fielding_collection = collect_ncaa_by_id(
                    season_team_id,
                    division=division,
                    year=year,
                    force_refresh=True,
                    section_names=frozenset({"fielding"}),
                    request_reserver=request_reserver(
                        {"fielding": fielding_entry}
                    ),
                    navigation_timeout_seconds=limits.navigation_timeout_seconds,
                    deadline_monotonic=deadline_monotonic,
                )
                fielding_sections = to_collected_sections(fielding_collection)
                if {section.name for section in fielding_sections} != {"fielding"}:
                    raise DeterministicJobError("schema_validation")
                finish_live(fielding_entry, outcome="live_complete")
                fetched.extend(fielding_sections)
            except CollectionBudgetExceeded:
                exhaust_live({"fielding": fielding_entry})
                fetched.append(CollectedSection.supplemental_failure(
                    "fielding",
                    status="unavailable",
                    category="request_budget_exhausted",
                    collected_at=_utcnow(),
                    source_team_id=source_team_id,
                    source_season_id=source_season_id,
                    source_season_team_id=season_team_id,
                ))
            except CollectionDeadlineExceeded:
                timeout_live({"fielding": fielding_entry})
                fetched.append(CollectedSection.supplemental_failure(
                    "fielding",
                    status="unavailable",
                    category="collection_timeout",
                    collected_at=_utcnow(),
                    source_team_id=source_team_id,
                    source_season_id=source_season_id,
                    source_season_team_id=season_team_id,
                ))
            except _NcaaFailureEvidenceError as exc:
                evidence = exc.evidence
                finish_live(
                    fielding_entry,
                    outcome="live_failed",
                    safe_category=evidence.failure_category,
                )
                fetched.append(CollectedSection.supplemental_failure(
                    "fielding",
                    status=(
                        "failed"
                        if evidence.failure_category == "schema_validation"
                        else "unavailable"
                    ),
                    category=evidence.failure_category,
                    collected_at=evidence.collected_at,
                    source_url=evidence.source_url,
                    source_team_id=source_team_id,
                    source_season_id=source_season_id,
                    source_season_team_id=season_team_id,
                ))
            except BrowserCapacityExceeded:
                finish_live(
                    fielding_entry,
                    outcome="capacity_exhausted",
                    safe_category="browser_capacity",
                )
                fetched.append(CollectedSection.supplemental_failure(
                    "fielding",
                    status="unavailable",
                    category="browser_capacity",
                    collected_at=_utcnow(),
                    source_team_id=source_team_id,
                    source_season_id=source_season_id,
                    source_season_team_id=season_team_id,
                ))
            except (NcaaAccessError, NcaaError, ValueError):
                finish_live(
                    fielding_entry,
                    outcome="live_failed",
                    safe_category="source_unavailable",
                )
                fetched.append(CollectedSection.supplemental_failure(
                    "fielding",
                    status="unavailable",
                    category="source_unavailable",
                    collected_at=_utcnow(),
                    source_team_id=source_team_id,
                    source_season_id=source_season_id,
                    source_season_team_id=season_team_id,
                ))
    phase3_transport_available = (
        os.environ.get("NCAA_BROWSER_PROVIDER") == "browserless"
    )
    current_sections = (*reconstructed, *fetched)
    source_player_ids = []
    unavailable_player_count = 0
    for section in current_sections:
        if section.name not in {"batting", "pitching", "fielding"}:
            continue
        for row in section.rows:
            player_id = str(row.get("_player_id") or "")
            if player_id.isdigit():
                if player_id not in source_player_ids:
                    source_player_ids.append(player_id)
            else:
                unavailable_player_count += 1

    schedule_entry = begin_live(
        "schedule", source_team_id, source_season_id, season_team_id
    )
    if schedule_entry is None:
        for name in (
            "schedule", "game_logs:batting", "game_logs:pitching",
            "game_logs:fielding", "history:batting", "history:pitching",
            "history:fielding",
        ):
            fetched.append(CollectedSection.supplemental_failure(
                name,
                status="unavailable",
                category="request_budget_exhausted",
                collected_at=_utcnow(),
                source_team_id=source_team_id,
                source_season_id=source_season_id,
                source_season_team_id=season_team_id,
                source_stat_category_id=name,
            ))
    elif not phase3_transport_available:
        finish_live(
            schedule_entry,
            outcome="live_failed",
            safe_category="schedule_transport_unavailable",
        )
        for name in (
            "schedule", "game_logs:batting", "game_logs:pitching",
            "game_logs:fielding", "history:batting", "history:pitching",
            "history:fielding",
        ):
            fetched.append(CollectedSection.supplemental_failure(
                name,
                status="unavailable",
                category="schedule_transport_unavailable",
                collected_at=_utcnow(),
                source_team_id=source_team_id,
                source_season_id=source_season_id,
                source_season_team_id=season_team_id,
                source_stat_category_id=name,
            ))
    else:
        schedule_finished = False
        try:
            schedule = collect_ncaa_schedule_evidence(
                season_team_id,
                division=division,
                year=year,
                request_reserver=request_reserver(
                    {"schedule": schedule_entry}
                ),
                navigation_timeout_seconds=limits.navigation_timeout_seconds,
                deadline_monotonic=deadline_monotonic,
            )
            finish_live(schedule_entry, outcome="live_complete")
            schedule_finished = True
            details = []
            history_complete = True
            history_requests = []
            for player_id in source_player_ids:
                history_entries = begin_live_batch(
                    tuple(
                        (
                            f"history:{stat_type}",
                            (
                                source_team_id, source_season_id, season_team_id,
                                player_id, category_id,
                            ),
                        )
                        for stat_type, category_id in (
                            ("batting", split_category_ids["batting"]),
                            ("pitching", split_category_ids["pitching"]),
                            ("fielding", discovered_fielding_id),
                        )
                    ),
                )
                if history_entries is None:
                    history_complete = False
                    continue
                history_requests.append((player_id, history_entries))

            from scripts.capture_phase3_ncaa_fixtures import (
                PLAYER_HISTORY_BATCH_MAX_PLAYERS,
            )

            history_batches = tuple(
                tuple(history_requests[index:index + PLAYER_HISTORY_BATCH_MAX_PLAYERS])
                for index in range(
                    0, len(history_requests), PLAYER_HISTORY_BATCH_MAX_PLAYERS
                )
            )

            def collect_player_history_batch(requests):
                entries_by_player = dict(requests)

                def reserve(player_id, stat_type):
                    request_reserver(entries_by_player[player_id])(
                        f"history:{stat_type}"
                    )

                try:
                    batch_details = collect_ncaa_player_detail_batch_evidence(
                        season_team_id,
                        tuple(entries_by_player),
                        division=division,
                        year=year,
                        request_reserver=reserve,
                        navigation_timeout_seconds=(
                            limits.navigation_timeout_seconds
                        ),
                        deadline_monotonic=history_deadline_monotonic,
                    )
                except CollectionBudgetExceeded:
                    outcome = "budget_exhausted"
                except CollectionDeadlineExceeded:
                    outcome = "collection_timeout"
                except (NcaaAccessError, NcaaError, ValueError):
                    outcome = "player_history_unavailable"
                else:
                    return tuple(
                        ("complete", entries, batch_details[player_id])
                        for player_id, entries in requests
                    )
                return tuple(
                    (outcome, entries, None) for _, entries in requests
                )

            # Career and per-player game logs are valuable, but they must not
            # consume the entire detailed-build allowance before team splits
            # can be collected and the immutable dashboard snapshot can be
            # published. Any rows that do not finish inside this sub-budget
            # remain explicitly partial.
            history_deadline_monotonic = _bounded_history_deadline(
                deadline_monotonic,
                limits.history_wall_time_seconds,
            )
            batched_history_results = _map_with_bounded_concurrency(
                history_batches,
                collect_player_history_batch,
                max_workers=limits.navigation_concurrency,
            )
            history_results = tuple(
                result
                for batch_results in batched_history_results
                for result in batch_results
            )
            for outcome, history_entries, detail in history_results:
                if outcome == "budget_exhausted":
                    history_complete = False
                    exhaust_live(history_entries)
                elif outcome == "collection_timeout":
                    history_complete = False
                    timeout_live(history_entries)
                elif outcome == "player_history_unavailable":
                    history_complete = False
                    for entry in history_entries.values():
                        finish_live(
                            entry, outcome="live_failed",
                            safe_category="player_history_unavailable",
                        )
                else:
                    details.append(detail)
                    for entry in history_entries.values():
                        finish_live(entry, outcome="live_complete")
            history_sections, game_facts = to_collected_history_sections(
                details,
                schedule,
                source_team_id=source_team_id,
                unavailable_player_count=unavailable_player_count,
                complete=history_complete,
            )
            complete_stat_types = (
                frozenset({"batting", "pitching", "fielding"})
                if history_complete and unavailable_player_count == 0
                else frozenset()
            )
            fetched.extend(to_collected_schedule_sections(
                schedule,
                game_facts=game_facts,
                complete_stat_types=complete_stat_types,
            ))
            fetched.extend(history_sections)
        except CollectionBudgetExceeded:
            if not schedule_finished:
                exhaust_live({"schedule": schedule_entry})
            for name in (
                "schedule", "game_logs:batting", "game_logs:pitching",
                "game_logs:fielding", "history:batting", "history:pitching",
                "history:fielding",
            ):
                fetched.append(CollectedSection.supplemental_failure(
                    name,
                    status="unavailable",
                    category="request_budget_exhausted",
                    collected_at=_utcnow(),
                    source_team_id=source_team_id,
                    source_season_id=source_season_id,
                    source_season_team_id=season_team_id,
                    source_stat_category_id=name,
                ))
        except CollectionDeadlineExceeded:
            if not schedule_finished:
                timeout_live({"schedule": schedule_entry})
            for name in (
                "schedule", "game_logs:batting", "game_logs:pitching",
                "game_logs:fielding", "history:batting", "history:pitching",
                "history:fielding",
            ):
                fetched.append(CollectedSection.supplemental_failure(
                    name,
                    status="unavailable",
                    category="collection_timeout",
                    collected_at=_utcnow(),
                    source_team_id=source_team_id,
                    source_season_id=source_season_id,
                    source_season_team_id=season_team_id,
                    source_stat_category_id=name,
                ))
        except (NcaaAccessError, NcaaError, ValueError) as exc:
            if not schedule_finished:
                finish_live(
                    schedule_entry,
                    outcome="live_failed",
                    safe_category="schedule_unavailable",
                )
                capture_stage = getattr(exc.__cause__, "stage", None)
                LOGGER.warning(
                    "NCAA supplemental schedule unavailable job_id=%s capture_stage=%s",
                    job.id,
                    capture_stage or "not_reported",
                )
            for name in (
                "schedule", "game_logs:batting", "game_logs:pitching",
                "game_logs:fielding", "history:batting", "history:pitching",
                "history:fielding",
            ):
                fetched.append(CollectedSection.supplemental_failure(
                    name,
                    status="unavailable",
                    category="schedule_unavailable",
                    collected_at=_utcnow(),
                    source_team_id=source_team_id,
                    source_season_id=source_season_id,
                    source_season_team_id=season_team_id,
                    source_stat_category_id=name,
                ))

    if split_missing:
        split_entries = begin_live_batch(
            tuple(
                (
                    f"splits:{stat_type}",
                    (
                        source_team_id, source_season_id, season_team_id,
                        category_id,
                    ),
                )
                for stat_type, category_id in sorted(split_category_ids.items())
            ),
        )
        if split_entries is None:
            fetched.extend(unavailable_collected_split_sections(
                source_team_id=source_team_id,
                source_season_id=source_season_id,
                source_season_team_id=season_team_id,
                category_ids=split_category_ids,
                status="unavailable",
                reason="request_budget_exhausted",
                collected_at=_utcnow(),
            ))
            return finalized_sections()
        if not split_transport_available:
            for entry in split_entries.values():
                finish_live(
                    entry,
                    outcome="live_failed",
                    safe_category="split_transport_unavailable",
                )
            fetched.extend(unavailable_collected_split_sections(
                source_team_id=source_team_id,
                source_season_id=source_season_id,
                source_season_team_id=season_team_id,
                category_ids=split_category_ids,
                status="unavailable",
                reason="split_transport_unavailable",
                collected_at=_utcnow(),
            ))
            return finalized_sections()
        try:
            split_results = collect_ncaa_split_results(
                season_team_id,
                division=division,
                year=year,
                request_reserver=request_reserver(split_entries),
                navigation_timeout_seconds=limits.navigation_timeout_seconds,
                deadline_monotonic=deadline_monotonic,
            )
            fetched.extend(to_collected_split_sections(
                split_results,
                source_team_id=source_team_id,
            ))
            for entry in split_entries.values():
                finish_live(entry, outcome="live_complete")
        except CollectionBudgetExceeded:
            exhaust_live(split_entries)
            fetched.extend(unavailable_collected_split_sections(
                source_team_id=source_team_id,
                source_season_id=source_season_id,
                source_season_team_id=season_team_id,
                category_ids=split_category_ids,
                status="unavailable",
                reason="request_budget_exhausted",
                collected_at=_utcnow(),
            ))
        except CollectionDeadlineExceeded:
            timeout_live(split_entries)
            fetched.extend(unavailable_collected_split_sections(
                source_team_id=source_team_id,
                source_season_id=source_season_id,
                source_season_team_id=season_team_id,
                category_ids=split_category_ids,
                status="unavailable",
                reason="collection_timeout",
                collected_at=_utcnow(),
            ))
        except _NcaaFailureEvidenceError as exc:
            category = exc.evidence.failure_category
            for entry in split_entries.values():
                finish_live(
                    entry, outcome="live_failed", safe_category=category
                )
            fetched.extend(unavailable_collected_split_sections(
                source_team_id=source_team_id,
                source_season_id=source_season_id,
                source_season_team_id=season_team_id,
                category_ids=split_category_ids,
                status="failed" if category == "schema_validation" else "unavailable",
                reason=category,
                collected_at=exc.evidence.collected_at,
            ))
        except BrowserCapacityExceeded:
            for entry in split_entries.values():
                finish_live(
                    entry,
                    outcome="capacity_exhausted",
                    safe_category="browser_capacity",
                )
            fetched.extend(unavailable_collected_split_sections(
                source_team_id=source_team_id,
                source_season_id=source_season_id,
                source_season_team_id=season_team_id,
                category_ids=split_category_ids,
                status="unavailable",
                reason="browser_capacity",
                collected_at=_utcnow(),
            ))
        except (NcaaAccessError, NcaaError, ValueError):
            for entry in split_entries.values():
                finish_live(
                    entry,
                    outcome="live_failed",
                    safe_category="source_unavailable",
                )
            fetched.extend(unavailable_collected_split_sections(
                source_team_id=source_team_id,
                source_season_id=source_season_id,
                source_season_team_id=season_team_id,
                category_ids=split_category_ids,
                status="unavailable",
                reason="source_unavailable",
                collected_at=_utcnow(),
            ))
    return finalized_sections()
