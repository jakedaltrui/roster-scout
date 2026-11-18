"""Pure deterministic selectors and count-first aggregation for game scopes."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Mapping, Sequence

from .metrics import MetricResult, aggregate_compatible_counts, calculate_metric


_COMPLETED = "completed"


@dataclass(frozen=True)
class AuditedGame:
    source_game_id: str | None
    fixture_fallback_key: str | None
    season_id: str
    game_date: date
    completed_at: datetime | None
    status: str
    opponent_team_id: str | None
    report_team_conference_id: str | None
    opponent_conference_id: str | None
    conference_evidence_season_id: str | None
    doubleheader_ordinal: int
    result_checksum: str
    venue: str

    @property
    def identity(self) -> str | None:
        return self.source_game_id or self.fixture_fallback_key


@dataclass(frozen=True)
class ScopeSelection:
    status: str
    reason: str | None
    games: tuple[AuditedGame, ...]
    actual_game_count: int
    start_date: date | None
    end_date: date | None


@dataclass(frozen=True)
class ScopedCountFact:
    fact_id: str
    game_id: str | None
    player_id: str
    stat_type: str
    counts: Mapping[str, int | Decimal | None]
    link_status: str = "linked"


@dataclass(frozen=True)
class ScopeAggregate:
    status: str
    reason: str | None
    stat_type: str | None
    counts: Mapping[str, int | Decimal | None]
    metrics: Mapping[str, MetricResult]
    sample: Mapping[str, int | None]
    contributing_fact_ids: tuple[str, ...]
    game_ids: tuple[str, ...]
    game_count: int


def completed_game_sort_key(game: AuditedGame) -> tuple[datetime, int, str]:
    """Return the locked completed-time, doubleheader, source-identity key."""
    moment = game.completed_at or datetime.combine(
        game.game_date, datetime.min.time(), tzinfo=UTC
    )
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment, game.doubleheader_ordinal, game.identity or ""


def _selection(
    *,
    status: str,
    reason: str | None,
    games: Sequence[AuditedGame],
) -> ScopeSelection:
    ordered = tuple(sorted(games, key=completed_game_sort_key))
    return ScopeSelection(
        status=status,
        reason=reason,
        games=ordered,
        actual_game_count=len(ordered),
        start_date=ordered[0].game_date if ordered else None,
        end_date=ordered[-1].game_date if ordered else None,
    )


def _identity_failure(
    games: Sequence[AuditedGame], *, allow_fixture_fallback: bool
) -> bool:
    for item in games:
        if item.source_game_id:
            continue
        if allow_fixture_fallback and item.fixture_fallback_key:
            continue
        return True
    return False


def select_last_ten_completed_games(
    games: Sequence[AuditedGame],
    *,
    schedule_complete: bool,
    allow_fixture_fallback: bool = False,
) -> ScopeSelection:
    """Select the latest ten completed team contests and retain exact membership."""
    completed = tuple(item for item in games if item.status == _COMPLETED)
    if _identity_failure(completed, allow_fixture_fallback=allow_fixture_fallback):
        return _selection(
            status="unavailable",
            reason="source_game_identity_missing",
            games=(),
        )
    ordered = sorted(completed, key=completed_game_sort_key)
    selected = ordered[-10:]
    return _selection(
        status="complete" if schedule_complete else "partial",
        reason=None if schedule_complete else "schedule_evidence_incomplete",
        games=selected,
    )


def select_conference_games(
    games: Sequence[AuditedGame],
    *,
    report_season_id: str,
    schedule_complete: bool,
    allow_fixture_fallback: bool = False,
) -> ScopeSelection:
    """Classify conference games only from exact same-season conference IDs."""
    completed = tuple(item for item in games if item.status == _COMPLETED)
    if _identity_failure(completed, allow_fixture_fallback=allow_fixture_fallback):
        return _selection(
            status="unavailable",
            reason="source_game_identity_missing",
            games=(),
        )
    incomplete = tuple(
        item for item in completed
        if (
            item.season_id != report_season_id
            or item.conference_evidence_season_id != report_season_id
            or not item.report_team_conference_id
            or not item.opponent_conference_id
        )
    )
    if incomplete:
        return _selection(
            status="partial" if schedule_complete else "unavailable",
            reason="conference_evidence_incomplete",
            games=(),
        )
    selected = tuple(
        item for item in completed
        if item.report_team_conference_id == item.opponent_conference_id
    )
    return _selection(
        status="complete" if schedule_complete else "partial",
        reason=None if schedule_complete else "schedule_evidence_incomplete",
        games=selected,
    )


def aggregate_scope_facts(
    facts: Sequence[ScopedCountFact],
    *,
    game_ids: Sequence[str],
    metric_codes: Sequence[str],
    season_id: str | None = None,
    division: str | None = None,
    constant_sets: Mapping[str, Mapping[str, object]] | None = None,
) -> ScopeAggregate:
    """Sum compatible counts before evaluating the shared metric registry."""
    exact_game_ids = tuple(game_ids)
    allowed = set(exact_game_ids)
    if any(item.link_status != "linked" or item.game_id is None for item in facts):
        return ScopeAggregate(
            status="partial",
            reason="ambiguous_game_link",
            stat_type=None,
            counts=MappingProxyType({}),
            metrics=MappingProxyType({}),
            sample=MappingProxyType({"game_count": len(exact_game_ids)}),
            contributing_fact_ids=(),
            game_ids=exact_game_ids,
            game_count=len(exact_game_ids),
        )
    selected = tuple(item for item in facts if item.game_id in allowed)
    stat_types = {item.stat_type for item in selected}
    players = {item.player_id for item in selected}
    if len(stat_types) > 1 or len(players) > 1:
        raise ValueError("Scoped facts must describe one player and one stat type")
    counts = aggregate_compatible_counts([item.counts for item in selected])
    metrics = {
        code: calculate_metric(
            code,
            counts,
            season_id=season_id,
            division=division,
            constant_set=(constant_sets or {}).get(code),
        )
        for code in metric_codes
    }
    sample = {
        "pa": _sample_count(counts.get("plate_appearances")),
        "ab": _sample_count(counts.get("at_bats")),
        "bf": _sample_count(counts.get("batters_faced")),
        "outs": _sample_count(counts.get("innings_outs")),
        "opportunities": _sample_count(counts.get("total_chances")),
        "game_count": len(exact_game_ids),
    }
    if sample["pa"] is None:
        parts = tuple(
            _sample_count(counts.get(key))
            for key in (
                "at_bats", "walks", "hit_by_pitch", "sacrifice_flies",
                "sacrifice_hits",
            )
        )
        if all(value is not None for value in parts):
            sample["pa"] = sum(value for value in parts if value is not None)
    return ScopeAggregate(
        status="complete",
        reason=None,
        stat_type=next(iter(stat_types), None),
        counts=MappingProxyType(counts),
        metrics=MappingProxyType(metrics),
        sample=MappingProxyType(sample),
        contributing_fact_ids=tuple(item.fact_id for item in selected),
        game_ids=exact_game_ids,
        game_count=len(exact_game_ids),
    )


def _sample_count(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        return None
    parsed = Decimal(value)
    if parsed < 0 or parsed != parsed.to_integral_value():
        return None
    return int(parsed)
