"""Fail-closed NCAA detailed-stat parsing contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
import html as html_module
import hashlib
import re
from types import MappingProxyType
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from .metrics import (
    METRIC_DEFINITIONS,
    MetricResult,
    aggregate_compatible_counts,
    calculate_metric,
)


NCAA_BASE = "https://stats.ncaa.org"
FIELDING_CONTRACT_VERSION = "ncaa-fielding-v1"
SPLIT_CONTROL_CONTRACT_VERSION = "ncaa-split-controls-v1"
SPLIT_RESULT_CONTRACT_VERSION = "ncaa-split-result-v1"
SPLIT_SCOPE_KIND = "source_split"
SCHEDULE_CONTRACT_VERSION = "ncaa-schedule-v1"
PLAYER_DETAIL_CONTRACT_VERSION = "ncaa-player-detail-v1"
SPLIT_CODES = {
    "batting": (
        "vs_lhp", "vs_rhp", "runners_on", "bases_empty",
        "bases_loaded", "risp", "two_outs",
    ),
    "pitching": (
        "vs_lhb", "vs_rhb", "runners_on", "bases_empty",
        "bases_loaded", "risp", "two_outs",
    ),
}
_SPLIT_SOURCE_LABELS = {
    **{code: code for codes in SPLIT_CODES.values() for code in codes},
    "risp": "runners_in_scoring_position",
}
_SPLIT_DISPLAY_LABELS = {
    "vs_lhp": "vs. Left-Handed Pitchers",
    "vs_rhp": "vs. Right-Handed Pitchers",
    "vs_lhb": "vs. Left-Handed Batters",
    "vs_rhb": "vs. Right-Handed Batters",
    "runners_on": "Runners On",
    "bases_empty": "Bases Empty",
    "bases_loaded": "Bases Loaded",
    "risp": "Runners in Scoring Position",
    "two_outs": "Two Outs",
}
_SPLIT_UNAVAILABLE_REASONS = frozenset(
    {"source_not_published", "source_identifier_not_found"}
)
_LIVE_LABEL_PATTERNS = {
    "vs_lhp": (r"\bvs\.?\s*(?:lhp|left[- ]handed pitchers?)\b",),
    "vs_rhp": (r"\bvs\.?\s*(?:rhp|right[- ]handed pitchers?)\b",),
    "vs_lhb": (r"\bvs\.?\s*(?:lhb|left[- ]handed (?:batters?|hitters?))\b",),
    "vs_rhb": (r"\bvs\.?\s*(?:rhb|right[- ]handed (?:batters?|hitters?))\b",),
    "runners_on": (r"\brunners?\s+on\b", r"\bhits?[- ]ab\s+with\s+runners?\s+o(?:b|n)\b"),
    "bases_empty": (r"\bbases?\s+empty\b", r"\bno\s+runners?\s+on\b"),
    "bases_loaded": (r"\bbases?\s+loaded\b",),
    "risp": (r"\brunners?\s+in\s+scoring\s+position\b", r"\brisp\b", r"\b(?:h|hits?)[- ]ab\s+scorepos\b"),
    "two_outs": (
        r"^\s*(?:two|2)\s+outs?\s*$",
        r"\b(?:h|hits?)[- ]ab\b.*\b(?:two|2)\s+outs?\b",
        r"\bw2outs?\b",
    ),
}
_REQUIRED_HEADERS = ("player", "g", "gs", "po", "a", "e", "tc", "dp", "fpct")
_OPTIONAL_HEADERS = ("pb", "sba", "csb")
_FIELDING_HEADER_ALIASES = {
    "gp": "g",
    "fldpct": "fpct",
    "idp": "dp",
}
_IGNORED_FIELDING_HEADERS = frozenset({
    "#", "yr", "pos", "ht", "b/t", "ci", "tp", "sbapct",
})
_COUNT_FIELDS = {
    "g": "games",
    "gs": "games_started",
    "po": "putouts",
    "a": "assists",
    "e": "errors",
    "tc": "total_chances",
    "dp": "double_plays",
    "pb": "passed_balls",
    "sba": "stolen_base_attempts",
    "csb": "caught_stealing_by",
}
_EMPTY_VALUES = frozenset({"", "-", "--", "n/a", "na"})
_EMPTY_MARKERS = frozenset({
    "no data",
    "no data available",
    "no results found",
    "no statistics available",
})


class FieldingParseError(ValueError):
    """Sanitized parser failure with a fixed durable category."""

    def __init__(self, category: str):
        self.category = category
        message = {
            "source_blocked": "NCAA fielding source was unavailable.",
            "schema_validation": "NCAA fielding schema validation failed.",
        }[category]
        super().__init__(message)


class SplitContractError(ValueError):
    """Fixed-category rejection for untrusted split controls or result grids."""

    def __init__(self, category: str):
        self.category = category
        message = {
            "source_blocked": "NCAA split source was unavailable.",
            "schema_validation": "NCAA split schema validation failed.",
            "source_identity_mismatch": "NCAA split source identity did not match.",
        }[category]
        super().__init__(message)


class ScheduleContractError(ValueError):
    """Fixed-category rejection for sanitized schedule/conference evidence."""

    def __init__(self, category: str):
        self.category = category
        message = {
            "schema_validation": "NCAA schedule schema validation failed.",
            "source_identity_mismatch": "NCAA schedule source identity did not match.",
        }[category]
        super().__init__(message)


class PlayerDetailContractError(ValueError):
    """Fixed-category rejection for sanitized stable-player evidence."""

    def __init__(self, category: str):
        self.category = category
        message = {
            "schema_validation": "NCAA player detail schema validation failed.",
            "source_identity_mismatch": "NCAA player detail source identity did not match.",
        }[category]
        super().__init__(message)


@dataclass(frozen=True)
class DiscoveredSplitScope:
    scope_kind: str
    scope_code: str
    display_label: str
    stat_type: str
    source_label: str
    source_identifier: str | None
    source_path: str
    source_url: str | None
    source_season_id: str
    source_season_team_id: str
    source_stat_category_id: str
    status: str
    reason: str | None
    collected_at: datetime


@dataclass(frozen=True)
class BattingSplitRow:
    row_ordinal: int
    source_player_id: str
    source_player_url: str
    source_identifier: str
    source_payload_sha256: str
    at_bats: int
    hits: int
    sample: MappingProxyType
    raw_values: MappingProxyType


@dataclass(frozen=True)
class PitchingSplitRow:
    row_ordinal: int
    source_player_id: str
    source_player_url: str
    source_identifier: str
    source_payload_sha256: str
    opponent_at_bats: int
    hits_allowed: int
    sample: MappingProxyType
    raw_values: MappingProxyType

    @property
    def at_bats(self) -> int:
        """Expose the registry sample name without erasing pitching semantics."""
        return self.opponent_at_bats


@dataclass(frozen=True)
class SplitMetricResult:
    source_player_id: str
    code: str
    result: MetricResult


@dataclass(frozen=True)
class ParsedSplitResult:
    scope_kind: str
    scope_code: str
    display_label: str
    stat_type: str
    source_identifier: str | None
    source_path: str
    source_url: str | None
    source_season_id: str
    source_season_team_id: str
    source_stat_category_id: str
    source_payload_sha256: str
    fixture_sha256: str
    status: str
    reason: str | None
    collected_at: datetime
    rows: tuple[BattingSplitRow | PitchingSplitRow, ...]
    metric_results: tuple[SplitMetricResult, ...]


@dataclass(frozen=True)
class ScheduleGameEvidence:
    source_game_id: str | None
    game_date: date
    status: str
    opponent_team_id: str | None
    opponent_season_team_id: str | None
    opponent_conference_id: str | None
    conference_evidence_status: str
    opponent_identity_status: str
    doubleheader_ordinal: int
    result_sha256: str
    venue: str


@dataclass(frozen=True)
class ScheduleEvidence:
    source_path: str
    source_url: str
    source_payload_sha256: str
    source_season_id: str
    source_team_id: str
    source_season_team_id: str
    team_conference_id: str
    division: str
    schedule_complete: bool
    collected_at: datetime
    games: tuple[ScheduleGameEvidence, ...]

    def audited_games(self):
        from .scopes import AuditedGame

        return tuple(
            AuditedGame(
                source_game_id=item.source_game_id,
                fixture_fallback_key=None,
                season_id=self.source_season_id,
                game_date=item.game_date,
                completed_at=(
                    datetime.combine(item.game_date, datetime.min.time(), tzinfo=UTC)
                    if item.status == "completed" else None
                ),
                status=item.status,
                opponent_team_id=item.opponent_team_id,
                report_team_conference_id=self.team_conference_id,
                opponent_conference_id=item.opponent_conference_id,
                conference_evidence_season_id=(
                    self.source_season_id
                    if item.conference_evidence_status == "present" else None
                ),
                doubleheader_ordinal=item.doubleheader_ordinal,
                result_checksum=item.result_sha256,
                venue=item.venue,
            )
            for item in self.games
        )


@dataclass(frozen=True)
class CareerSeasonRow:
    row_id: str
    identity_key: str
    source_player_id: str
    stat_type: str
    season_label: str
    source_season_id: str | None
    school_name: str
    source_season_team_id: str | None
    division: str | None
    source_stat_category_id: str
    source_path: str
    source_payload_sha256: str
    counts: MappingProxyType
    rates: MappingProxyType


@dataclass(frozen=True)
class CurrentSeasonGameLogRow:
    row_id: str
    identity_key: str
    source_player_id: str
    stat_type: str
    source_season_id: str
    game_date: date
    opponent_label: str
    opponent_season_team_id: str | None
    source_game_id: str | None
    source_stat_category_id: str
    source_path: str
    source_payload_sha256: str
    counts: MappingProxyType
    rates: MappingProxyType


@dataclass(frozen=True)
class ParsedPlayerDetail:
    identity_key: str
    source_player_id: str
    source_path: str
    active_season_id: str
    active_season_year: int
    active_season_team_id: str
    division: str
    collected_at: datetime
    career_seasons: tuple[CareerSeasonRow, ...]
    game_logs: tuple[CurrentSeasonGameLogRow, ...]


@dataclass(frozen=True)
class CareerAggregateResult:
    stat_type: str
    status: str
    reason: str | None
    counts: dict
    metrics: dict[str, MetricResult]
    included_row_ids: tuple[str, ...]
    excluded_rows: dict[str, str]


@dataclass(frozen=True)
class HistoryAvailability:
    status: str
    reason: str | None
    completed: int


@dataclass(frozen=True)
class LinkedGameLog:
    row: CurrentSeasonGameLogRow
    source_game_id: str | None
    game_identity: str | None
    link_status: str


@dataclass(frozen=True)
class FieldingRow:
    row_ordinal: int
    source_url: str
    source_player_id: str
    player_url: str
    raw_values: MappingProxyType
    canonical_values: MappingProxyType
    payload_sha256: str


@dataclass(frozen=True)
class FieldingSection:
    name: str
    source_url: str
    stat_category_id: str
    html_sha256: str
    rows: tuple[FieldingRow, ...]
    verified_empty: bool
    collected_at: datetime
    contract_version: str = FIELDING_CONTRACT_VERSION

    def as_ncaa_section(self):
        """Adapt validated fielding evidence to the shared collector contract."""
        from roster_scraper import NcaaSection, NcaaSourceRow

        return NcaaSection(
            name=self.name,
            source_url=self.source_url,
            stat_category_id=self.stat_category_id,
            html_sha256=self.html_sha256,
            rows=tuple(
                NcaaSourceRow(
                    section=self.name,
                    row_ordinal=row.row_ordinal,
                    source_url=row.source_url,
                    source_player_id=row.source_player_id,
                    player_url=row.player_url,
                    raw_values=dict(row.raw_values),
                    payload_sha256=row.payload_sha256,
                )
                for row in self.rows
            ),
            verified_empty=self.verified_empty,
        )


_HISTORY_COUNT_HEADERS = {
    "batting": {
        "g": "games", "gp": "games", "r": "runs", "ab": "at_bats",
        "h": "hits", "2b": "doubles", "3b": "triples", "tb": "total_bases",
        "hr": "home_runs", "rbi": "runs_batted_in", "bb": "walks",
        "hbp": "hit_by_pitch", "sf": "sacrifice_flies",
        "sh": "sacrifice_hits", "k": "strikeouts", "cs": "caught_stealing",
        "sb": "stolen_bases", "gdp": "grounded_double_plays",
    },
    "pitching": {
        "app": "appearances", "gp": "appearances", "gs": "games_started",
        "h": "hits_allowed", "r": "runs_allowed", "er": "earned_runs",
        "bb": "walks", "so": "strikeouts", "bf": "batters_faced",
        "p-oab": "opponent_at_bats", "hr-a": "home_runs_allowed",
        "hb": "hit_batters", "w": "wins", "l": "losses", "sv": "saves",
        "pitches": "pitches",
    },
    "fielding": {
        "g": "games", "gp": "games", "po": "putouts", "a": "assists",
        "tc": "total_chances", "e": "errors", "pb": "passed_balls",
        "sba": "stolen_base_attempts", "csb": "caught_stealing_by",
        "idp": "double_plays", "tp": "triple_plays",
    },
}
_HISTORY_RATE_HEADERS = {
    "batting": {
        "ba": "batting_average", "obpct": "on_base_percentage",
        "slgpct": "slugging_percentage",
    },
    "pitching": {"era": "earned_run_average"},
    "fielding": {"fldpct": "fielding_percentage", "sbapct": "stolen_base_rate"},
}
_CAREER_REQUIRED_COUNTS = {
    "batting": ("hits", "at_bats"),
    "pitching": ("earned_runs", "innings_outs"),
    "fielding": ("putouts", "assists", "errors", "total_chances"),
}


def build_player_detail_url(player_id: str, stat_category_id: str) -> str:
    """Build the only permitted stable-player endpoint from numeric identities."""
    if not re.fullmatch(r"\d+", str(player_id or "")):
        raise ValueError("player ID must be numeric")
    if not re.fullmatch(r"\d+", str(stat_category_id or "")):
        raise ValueError("player stat category ID must be numeric")
    return (
        f"{NCAA_BASE}/players/{player_id}"
        f"?year_stat_category_id={stat_category_id}"
    )


def history_availability(source_player_id: str | None) -> HistoryAvailability:
    if not source_player_id or not re.fullmatch(r"\d+", str(source_player_id)):
        return HistoryAvailability("unavailable", "missing_stable_player_id", 0)
    return HistoryAvailability("pending", None, 0)


def _history_count(value: str, *, published_blank_zero: bool = False) -> int | None:
    text = str(value or "").strip()
    if text.casefold() in _EMPTY_VALUES:
        return 0 if published_blank_zero else None
    # Some NCAA game-log cells expose a truncated linked record (for example
    # ``1/``) instead of a count.  Treat it as unavailable rather than guessing.
    if re.fullmatch(r"\d+/", text):
        return None
    if not re.fullmatch(r"\d+", text):
        raise PlayerDetailContractError("schema_validation")
    return int(text)


def _history_decimal(value: str) -> Decimal | None:
    text = str(value or "").strip()
    if text.casefold() in _EMPTY_VALUES:
        return None
    try:
        parsed = Decimal(text)
    except InvalidOperation as exc:
        raise PlayerDetailContractError("schema_validation") from exc
    if parsed < 0:
        raise PlayerDetailContractError("schema_validation")
    return parsed


def _history_values(
    node,
    stat_type: str,
    *,
    published_blank_zero: bool = False,
) -> tuple[MappingProxyType, MappingProxyType]:
    counts = {}
    rates = {}
    seen = set()
    for value_node in node.find_all("value", recursive=False):
        header = _normalized(value_node.get("data-source-header") or "")
        if not header or header in seen:
            raise PlayerDetailContractError("schema_validation")
        seen.add(header)
        text = value_node.get_text(" ", strip=True)
        if header == "ip" and stat_type == "pitching":
            try:
                from .canonical import innings_to_outs

                counts["innings_outs"] = innings_to_outs(text)
            except ValueError as exc:
                raise PlayerDetailContractError("schema_validation") from exc
        elif header in _HISTORY_COUNT_HEADERS[stat_type]:
            counts[_HISTORY_COUNT_HEADERS[stat_type][header]] = _history_count(
                text,
                published_blank_zero=published_blank_zero,
            )
        elif header in _HISTORY_RATE_HEADERS[stat_type]:
            rates[_HISTORY_RATE_HEADERS[stat_type][header]] = _history_decimal(text)
    return MappingProxyType(counts), MappingProxyType(rates)


def _history_date(value: str, active_year: int) -> date:
    match = re.fullmatch(r"(\d{2}/\d{2}/\d{4})(?:\s*\(\d+\))?", value or "")
    if match is None:
        raise PlayerDetailContractError("schema_validation")
    try:
        parsed = datetime.strptime(match.group(1), "%m/%d/%Y").date()
    except ValueError as exc:
        raise PlayerDetailContractError("schema_validation") from exc
    if parsed.year != active_year:
        raise PlayerDetailContractError("source_identity_mismatch")
    return parsed


def parse_player_detail_fixture(
    html: str,
    *,
    expected_player_id: str,
    expected_season_id: str,
    expected_season_team_id: str,
    expected_division: str,
) -> ParsedPlayerDetail:
    """Parse the secret-free capture contract and reject guessed identities."""
    expected_values = (
        expected_player_id, expected_season_id, expected_season_team_id
    )
    if any(not re.fullmatch(r"\d+", str(value or "")) for value in expected_values):
        raise ValueError("expected player detail identities must be numeric")
    division = str(expected_division).lower().removeprefix("d")
    if division not in {"1", "2", "3"}:
        raise ValueError("expected division is invalid")
    soup = BeautifulSoup(html, "html.parser")
    root = soup.find("player-detail")
    if root is None or soup.find("script") is not None:
        raise PlayerDetailContractError("schema_validation")
    expected_path = f"/players/{expected_player_id}"
    identity = (
        root.get("data-player-detail-contract-version"),
        root.get("data-player-id"),
        root.get("data-report-season-id"),
        root.get("data-report-season-team-id"),
        root.get("data-report-division"),
        root.get("data-source-path"),
    )
    if identity != (
        PLAYER_DETAIL_CONTRACT_VERSION,
        expected_player_id,
        expected_season_id,
        expected_season_team_id,
        f"d{division}",
        expected_path,
    ):
        raise PlayerDetailContractError("source_identity_mismatch")
    try:
        active_year = int(root.get("data-report-season-year") or "")
    except ValueError as exc:
        raise PlayerDetailContractError("schema_validation") from exc
    if not 2000 <= active_year <= 2100:
        raise PlayerDetailContractError("schema_validation")
    try:
        collected_at = _iso_datetime(root.get("data-captured-at") or "")
    except SplitContractError as exc:
        raise PlayerDetailContractError("schema_validation") from exc
    sections = root.find_all("stat-section", recursive=False)
    by_type = {item.get("data-stat-type"): item for item in sections}
    if len(sections) != 3 or set(by_type) != {"batting", "pitching", "fielding"}:
        raise PlayerDetailContractError("schema_validation")
    identity_key = f"ncaa:{expected_player_id}"
    career_rows = []
    game_rows = []
    for stat_type in ("batting", "pitching", "fielding"):
        section = by_type[stat_type]
        category_id = section.get("data-source-stat-category-id") or ""
        payload_sha256 = section.get("data-source-payload-sha256") or ""
        if not category_id.isdigit() or not re.fullmatch(r"[0-9a-f]{64}", payload_sha256):
            raise PlayerDetailContractError("schema_validation")
        career = section.find("career-totals", recursive=False)
        game_log = section.find("game-log", recursive=False)
        if career is None or game_log is None:
            raise PlayerDetailContractError("schema_validation")
        career_identities = set()
        for row in career.find_all("row", recursive=False):
            season_label = row.get("data-season-label") or ""
            school_name = row.get("data-school-label") or ""
            season_team_id = row.get("data-source-season-team-id") or None
            source_season_id = row.get("data-source-season-id") or None
            row_division = row.get("data-division") or None
            if (
                not season_label or not school_name
                or (season_team_id is not None and not season_team_id.isdigit())
                or (source_season_id is not None and not source_season_id.isdigit())
                or (row_division is not None and row_division not in {"d1", "d2", "d3"})
            ):
                raise PlayerDetailContractError("schema_validation")
            if season_team_id == expected_season_team_id and (
                source_season_id != expected_season_id or row_division != f"d{division}"
            ):
                raise PlayerDetailContractError("source_identity_mismatch")
            source_identity = (season_label, season_team_id)
            if source_identity in career_identities:
                raise PlayerDetailContractError("schema_validation")
            career_identities.add(source_identity)
            counts, rates = _history_values(row, stat_type)
            if not counts and not rates:
                continue
            row_id = hashlib.sha256(
                f"{expected_player_id}:{stat_type}:{season_label}:{season_team_id}".encode()
            ).hexdigest()
            career_rows.append(CareerSeasonRow(
                row_id=row_id,
                identity_key=identity_key,
                source_player_id=expected_player_id,
                stat_type=stat_type,
                season_label=season_label,
                source_season_id=source_season_id,
                school_name=school_name,
                source_season_team_id=season_team_id,
                division=row_division,
                source_stat_category_id=category_id,
                source_path=expected_path,
                source_payload_sha256=payload_sha256,
                counts=counts,
                rates=rates,
            ))
        game_identities = set()
        for row in game_log.find_all("row", recursive=False):
            date_label = row.get("data-date-label") or ""
            opponent_label = row.get("data-opponent-label") or ""
            opponent_team_id = row.get("data-opponent-season-team-id") or None
            source_game_id = row.get("data-source-game-id") or None
            if (
                not opponent_label
                or (opponent_team_id is not None and not opponent_team_id.isdigit())
                or (source_game_id is not None and not source_game_id.isdigit())
            ):
                raise PlayerDetailContractError("schema_validation")
            game_date = _history_date(date_label, active_year)
            # A fully blank NCAA row is a non-participation placeholder, not
            # a zero-stat appearance.  Establish that the source published at
            # least one value before translating the remaining blank count
            # cells to sourced zeroes.
            published_counts, published_rates = _history_values(row, stat_type)
            if all(
                value is None
                for value in (*published_counts.values(), *published_rates.values())
            ):
                continue
            counts, rates = _history_values(
                row,
                stat_type,
                published_blank_zero=True,
            )
            if not counts and not rates:
                continue
            source_identity = (source_game_id, game_date, opponent_team_id)
            if source_identity in game_identities:
                raise PlayerDetailContractError("schema_validation")
            game_identities.add(source_identity)
            row_id = hashlib.sha256(
                f"{expected_player_id}:{stat_type}:{source_game_id}:{game_date}:"
                f"{opponent_team_id}".encode()
            ).hexdigest()
            game_rows.append(CurrentSeasonGameLogRow(
                row_id=row_id,
                identity_key=identity_key,
                source_player_id=expected_player_id,
                stat_type=stat_type,
                source_season_id=expected_season_id,
                game_date=game_date,
                opponent_label=opponent_label,
                opponent_season_team_id=opponent_team_id,
                source_game_id=source_game_id,
                source_stat_category_id=category_id,
                source_path=expected_path,
                source_payload_sha256=payload_sha256,
                counts=counts,
                rates=rates,
            ))
    return ParsedPlayerDetail(
        identity_key=identity_key,
        source_player_id=expected_player_id,
        source_path=expected_path,
        active_season_id=expected_season_id,
        active_season_year=active_year,
        active_season_team_id=expected_season_team_id,
        division=f"d{division}",
        collected_at=collected_at,
        career_seasons=tuple(career_rows),
        game_logs=tuple(game_rows),
    )


def aggregate_career_seasons(
    rows,
    *,
    stat_type: str,
    metric_codes: tuple[str, ...] = (),
) -> CareerAggregateResult:
    if stat_type not in _CAREER_REQUIRED_COUNTS:
        raise ValueError("career aggregate stat type is invalid")
    included = []
    excluded = {}
    required = _CAREER_REQUIRED_COUNTS[stat_type]
    for row in rows:
        if row.stat_type != stat_type:
            excluded[row.row_id] = "incompatible_stat_type"
        elif not row.source_season_team_id:
            excluded[row.row_id] = "missing_team_identity"
        elif not row.division:
            excluded[row.row_id] = "missing_division"
        elif not row.source_season_id:
            excluded[row.row_id] = "missing_season_identity"
        elif any(row.counts.get(key) is None for key in required):
            excluded[row.row_id] = "missing_required_count"
        else:
            included.append(row)
    if not included:
        return CareerAggregateResult(
            stat_type=stat_type,
            status="unavailable",
            reason="no_compatible_seasons",
            counts={},
            metrics={},
            included_row_ids=(),
            excluded_rows=excluded,
        )
    counts = aggregate_compatible_counts([
        {
            key: value
            for key, value in row.counts.items()
            if key not in METRIC_DEFINITIONS
        }
        for row in included
    ])
    metrics = {code: calculate_metric(code, counts) for code in metric_codes}
    return CareerAggregateResult(
        stat_type=stat_type,
        status="complete" if not excluded else "partial",
        reason=None if not excluded else "incompatible_seasons_excluded",
        counts=counts,
        metrics=metrics,
        included_row_ids=tuple(row.row_id for row in included),
        excluded_rows=excluded,
    )


def link_current_season_game_logs(rows, games) -> tuple[LinkedGameLog, ...]:
    """Link only exact contest IDs; labels and dates never create game identity."""
    games_by_source = {}
    duplicate_ids = set()
    for game in games:
        if game.source_game_id is None:
            continue
        if game.source_game_id in games_by_source:
            duplicate_ids.add(game.source_game_id)
        games_by_source[game.source_game_id] = game
    output = []
    for row in rows:
        source_game_id = row.source_game_id
        linked = (
            source_game_id is not None
            and source_game_id in games_by_source
            and source_game_id not in duplicate_ids
        )
        output.append(LinkedGameLog(
            row=row,
            source_game_id=source_game_id,
            game_identity=source_game_id if linked else None,
            link_status="linked" if linked else (
                "ambiguous" if source_game_id in duplicate_ids else "unmatched"
            ),
        ))
    return tuple(output)


def build_fielding_url(season_team_id: str, stat_category_id: str) -> str:
    """Construct the only allowed fielding endpoint from numeric source IDs."""
    if not re.fullmatch(r"\d+", str(season_team_id or "")):
        raise ValueError("season team ID must be numeric")
    if not re.fullmatch(r"\d+", str(stat_category_id or "")):
        raise ValueError("fielding category ID must be numeric")
    return (
        f"{NCAA_BASE}/teams/{season_team_id}/season_to_date_stats"
        f"?year_stat_category_id={stat_category_id}"
    )


def split_codes(stat_type: str) -> tuple[str, ...]:
    """Return the stable seven-code schema for one split stat type."""
    try:
        return SPLIT_CODES[stat_type]
    except KeyError as exc:
        raise ValueError("split stat type must be batting or pitching") from exc


def build_split_url(
    season_team_id: str,
    stat_category_id: str,
    source_identifier: str,
) -> str:
    """Construct a split endpoint from validated numeric NCAA identities."""
    values = (season_team_id, stat_category_id, source_identifier)
    if any(not re.fullmatch(r"\d+", str(value or "")) for value in values):
        raise ValueError("NCAA split source identities must be numeric")
    return (
        f"{NCAA_BASE}/teams/{season_team_id}/season_to_date_stats"
        f"?year_stat_category_id={stat_category_id}&split_id={source_identifier}"
    )


def _iso_datetime(value: str) -> datetime:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value or ""):
        raise SplitContractError("schema_validation")
    return datetime.fromisoformat(value.removesuffix("Z") + "+00:00")


def _validate_split_source_path(path: str, season_team_id: str) -> None:
    if path != f"/teams/{season_team_id}/season_to_date_stats":
        raise SplitContractError("source_identity_mismatch")


def discover_split_controls(
    html: str,
    *,
    manifest: dict,
    expected_season_id: str,
    expected_season_team_id: str,
    expected_stat_type: str,
    expected_source_stat_category_id: str,
) -> tuple[DiscoveredSplitScope, ...]:
    """Bind live-discovered controls to the certified season/type manifest."""
    split_codes(expected_stat_type)
    if not html or "access denied" in html.casefold():
        raise SplitContractError("source_blocked")
    if not all(
        re.fullmatch(r"\d+", str(value or ""))
        for value in (
            expected_season_id,
            expected_season_team_id,
            expected_source_stat_category_id,
        )
    ):
        raise ValueError("expected NCAA source identities must be numeric")

    soup = BeautifulSoup(html, "html.parser")
    root = soup.find("split-controls")
    if root is None:
        raise SplitContractError("source_blocked")
    sections = [
        item for item in root.find_all("section", recursive=False)
        if item.get("data-stat-type") == expected_stat_type
    ]
    if len(sections) != 1:
        raise SplitContractError("schema_validation")
    section = sections[0]
    expected_path = f"/teams/{expected_season_team_id}/season_to_date_stats"
    identity = (
        section.get("data-season-id"),
        section.get("data-season-team-id"),
        section.get("data-source-stat-category-id"),
        section.get("data-source-path"),
    )
    if identity != (
        expected_season_id,
        expected_season_team_id,
        expected_source_stat_category_id,
        expected_path,
    ):
        raise SplitContractError("source_identity_mismatch")

    if (
        manifest.get("control_contract_version") != SPLIT_CONTROL_CONTRACT_VERSION
        or manifest.get("season_id") != expected_season_id
        or manifest.get("season_team_id") != expected_season_team_id
        or manifest.get("controls_sha256")
        != hashlib.sha256(html.encode("utf-8")).hexdigest()
    ):
        raise SplitContractError("source_identity_mismatch")
    sources = {
        item.get("stat_type"): item for item in manifest.get("control_sources", ())
    }
    source = sources.get(expected_stat_type)
    required_headers = {"player", "ab", "h"} if expected_stat_type == "batting" else {"player", "ip", "era"}
    if (
        not isinstance(source, dict)
        or source.get("source_path") != expected_path
        or source.get("source_stat_category_id") != expected_source_stat_category_id
        or not required_headers <= set(source.get("normalized_headers", ()))
    ):
        raise SplitContractError("source_identity_mismatch")

    manifest_observations = {
        (item.get("stat_type"), item.get("canonical_label")): item
        for item in manifest.get("observations", ())
    }
    controls = {
        item.get("data-canonical-label"): item
        for item in section.find_all("control", recursive=False)
    }
    output = []
    collected_at = _iso_datetime(str(manifest.get("captured_at") or ""))
    for code in split_codes(expected_stat_type):
        source_label = _SPLIT_SOURCE_LABELS[code]
        control = controls.get(source_label)
        observed = manifest_observations.get((expected_stat_type, source_label))
        if control is None or not isinstance(observed, dict):
            raise SplitContractError("schema_validation")
        status = control.get("data-status")
        identifier = control.get("data-identifier") or None
        if (
            control.get("data-stat-type") != expected_stat_type
            or observed.get("status") != status
            or observed.get("identifier") != identifier
            or observed.get("source_path") != expected_path
            or observed.get("source_stat_category_id")
            != expected_source_stat_category_id
        ):
            raise SplitContractError("source_identity_mismatch")
        if status == "candidate":
            if not re.fullmatch(r"\d+", identifier or ""):
                raise SplitContractError("schema_validation")
            result_status, reason = "pending", None
            source_url = build_split_url(
                expected_season_team_id,
                expected_source_stat_category_id,
                identifier,
            )
        elif status in _SPLIT_UNAVAILABLE_REASONS:
            if identifier is not None:
                raise SplitContractError("schema_validation")
            result_status, reason, source_url = "unavailable", status, None
        else:
            raise SplitContractError("schema_validation")
        output.append(DiscoveredSplitScope(
            scope_kind=SPLIT_SCOPE_KIND,
            scope_code=code,
            display_label=_SPLIT_DISPLAY_LABELS[code],
            stat_type=expected_stat_type,
            source_label=source_label,
            source_identifier=identifier,
            source_path=expected_path,
            source_url=source_url,
            source_season_id=expected_season_id,
            source_season_team_id=expected_season_team_id,
            source_stat_category_id=expected_source_stat_category_id,
            status=result_status,
            reason=reason,
            collected_at=collected_at,
        ))
    return tuple(output)


def split_label_code(value: str, stat_type: str) -> str | None:
    normalized = _normalized(value)
    for code in split_codes(stat_type):
        if any(re.search(pattern, normalized, re.I) for pattern in _LIVE_LABEL_PATTERNS[code]):
            return code
    return None


_LIVE_SPLIT_IDENTIFIER_ATTRIBUTES = frozenset({
    "data-context-id",
    "data-situation-id",
    "data-split-id",
    "data-sport-stat-id",
    "data-stat-seq",
    "data-summary-id",
})
_LIVE_SPLIT_IDENTIFIER_QUERY = re.compile(
    r"^(?:context|situation|split|stat[_-]?seq|summary)(?:_id)?$", re.I
)


def _live_split_identifier(element, *, excluded: set[str]) -> str | None:
    candidates = set()
    for name in _LIVE_SPLIT_IDENTIFIER_ATTRIBUTES:
        raw = element.get(name)
        values = raw if isinstance(raw, list) else (raw,)
        candidates.update(
            str(value) for value in values
            if value is not None and str(value).isdigit() and str(value) not in excluded
        )
    if element.name == "option":
        value = str(element.get("value") or "")
        if value.isdigit() and value not in excluded:
            candidates.add(value)
    href = element.get("href") or ""
    javascript_identifier = re.fullmatch(
        r"javascript:toggleSituationalStat\((\d+)\);?",
        href,
    )
    if href.startswith("javascript:"):
        if javascript_identifier is None:
            return None
        identifier = javascript_identifier.group(1)
        if identifier not in excluded:
            candidates.add(identifier)
        parsed = urlparse("")
    else:
        parsed = urlparse(href)
        if parsed.scheme and (
            parsed.scheme != "https" or parsed.netloc != "stats.ncaa.org"
        ):
            return None
    for key, values in parse_qs(parsed.query, keep_blank_values=False).items():
        if not _LIVE_SPLIT_IDENTIFIER_QUERY.fullmatch(key):
            continue
        candidates.update(
            value for value in values
            if value.isdigit() and value not in excluded
        )
    if len(candidates) > 1:
        raise SplitContractError("schema_validation")
    return next(iter(candidates), None)


def discover_live_split_controls(
    html: str,
    *,
    season_id: str,
    season_team_id: str,
    stat_type: str,
    source_stat_category_id: str,
    collected_at: datetime | None = None,
) -> tuple[DiscoveredSplitScope, ...]:
    """Discover current numeric controls without assuming historical split IDs."""
    if not html or "access denied" in html.casefold():
        raise SplitContractError("source_blocked")
    if any(
        not re.fullmatch(r"\d+", str(value or ""))
        for value in (season_id, season_team_id, source_stat_category_id)
    ):
        raise ValueError("NCAA split source identities must be numeric")
    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one("#stat_grid")
    if table is None:
        raise SplitContractError("source_blocked")
    header_rows = table.select("thead tr")
    header_row = header_rows[-1] if header_rows else table.find("tr")
    headers = [
        _normalized(cell.get_text(" ", strip=True))
        for cell in header_row.find_all(["th", "td"], recursive=False)
    ] if header_row is not None else []
    required = {"player", "ab", "h"} if stat_type == "batting" else {"player", "ip", "era"}
    if any(headers.count(value) != 1 for value in required):
        raise SplitContractError("schema_validation")
    excluded = {season_id, season_team_id, source_stat_category_id}
    found = {}
    for element in soup.find_all(["option", "a", "button", "input", "label"]):
        visible = " ".join(filter(None, (
            element.get_text(" ", strip=True),
            element.get("value", "") if element.name == "input" else "",
            element.get("title", ""),
            element.get("aria-label", ""),
        )))
        code = split_label_code(visible, stat_type)
        if code is None:
            continue
        identifier = _live_split_identifier(element, excluded=excluded)
        if code in found and identifier is not None and found[code] not in {None, identifier}:
            raise SplitContractError("schema_validation")
        if code not in found or (found[code] is None and identifier is not None):
            found[code] = identifier
    now = collected_at or datetime.now(UTC)
    source_path = f"/teams/{season_team_id}/season_to_date_stats"
    return tuple(
        DiscoveredSplitScope(
            scope_kind=SPLIT_SCOPE_KIND,
            scope_code=code,
            display_label=_SPLIT_DISPLAY_LABELS[code],
            stat_type=stat_type,
            source_label=_SPLIT_SOURCE_LABELS[code],
            source_identifier=found.get(code),
            source_path=source_path,
            source_url=(
                build_split_url(season_team_id, source_stat_category_id, found[code])
                if found.get(code) else None
            ),
            source_season_id=season_id,
            source_season_team_id=season_team_id,
            source_stat_category_id=source_stat_category_id,
            status="pending" if found.get(code) else "unavailable",
            reason=None if found.get(code) else "source_identifier_not_found",
            collected_at=now,
        )
        for code in split_codes(stat_type)
    )


def parse_split_result(
    html: str,
    *,
    discovered_scope: DiscoveredSplitScope,
) -> ParsedSplitResult:
    """Accept one result only when it matches the discovered scope exactly."""
    if not html or "access denied" in html.casefold():
        raise SplitContractError("source_blocked")
    soup = BeautifulSoup(html, "html.parser")
    root = soup.find("split-result")
    if root is None:
        raise SplitContractError("source_blocked")
    if root.get("data-result-contract-version") != SPLIT_RESULT_CONTRACT_VERSION:
        raise SplitContractError("schema_validation")
    expected_identity = (
        discovered_scope.source_label,
        discovered_scope.source_identifier,
        discovered_scope.stat_type,
        discovered_scope.source_season_id,
        discovered_scope.source_season_team_id,
        discovered_scope.source_path,
        discovered_scope.source_stat_category_id,
    )
    observed_identity = (
        root.get("data-canonical-label"),
        root.get("data-identifier") or None,
        root.get("data-stat-type"),
        root.get("data-season-id"),
        root.get("data-season-team-id"),
        root.get("data-source-path"),
        root.get("data-source-stat-category-id"),
    )
    if observed_identity != expected_identity:
        raise SplitContractError("source_identity_mismatch")
    _validate_split_source_path(
        root.get("data-source-path") or "",
        discovered_scope.source_season_team_id,
    )
    collected_at = _iso_datetime(root.get("data-captured-at") or "")
    payload_sha256 = root.get("data-source-payload-sha256") or ""
    if not re.fullmatch(r"[0-9a-f]{64}", payload_sha256):
        raise SplitContractError("schema_validation")
    status = root.get("data-status")
    fixture_sha256 = hashlib.sha256(html.encode("utf-8")).hexdigest()
    if status == "unavailable":
        reason = root.get("data-reason")
        if reason not in _SPLIT_UNAVAILABLE_REASONS or root.find("table") is not None:
            raise SplitContractError("schema_validation")
        return ParsedSplitResult(
            scope_kind=SPLIT_SCOPE_KIND,
            scope_code=discovered_scope.scope_code,
            display_label=discovered_scope.display_label,
            stat_type=discovered_scope.stat_type,
            source_identifier=discovered_scope.source_identifier,
            source_path=discovered_scope.source_path,
            source_url=discovered_scope.source_url,
            source_season_id=discovered_scope.source_season_id,
            source_season_team_id=discovered_scope.source_season_team_id,
            source_stat_category_id=discovered_scope.source_stat_category_id,
            source_payload_sha256=payload_sha256,
            fixture_sha256=fixture_sha256,
            status="unavailable",
            reason=reason,
            collected_at=collected_at,
            rows=(),
            metric_results=(),
        )
    if status != "complete" or discovered_scope.source_identifier is None:
        raise SplitContractError("schema_validation")

    table = root.select_one("#split_result_grid")
    if table is None:
        raise SplitContractError("source_blocked")
    headers = [
        _normalized(cell.get_text(" ", strip=True))
        for cell in table.select("thead tr")[-1].find_all(["th", "td"], recursive=False)
    ] if table.select("thead tr") else []
    if headers != ["player", "h", "ab"]:
        raise SplitContractError("schema_validation")
    output = []
    metrics = []
    for source_row in table.select("tbody tr"):
        cells = source_row.find_all(["th", "td"], recursive=False)
        if len(cells) != 3:
            raise SplitContractError("schema_validation")
        link = cells[0].find("a")
        player_match = re.fullmatch(
            r"/players/(\d+)", link.get("href", "") if link is not None else ""
        )
        if player_match is None:
            raise SplitContractError("schema_validation")
        hits = _parse_split_count(cells[1].get_text(" ", strip=True))
        at_bats = _parse_split_count(cells[2].get_text(" ", strip=True))
        if hits > at_bats:
            raise SplitContractError("schema_validation")
        player_id = player_match.group(1)
        sample = MappingProxyType({"at_bats": at_bats})
        raw = MappingProxyType({
            "Player": cells[0].get_text(" ", strip=True),
            "H": str(hits),
            "AB": str(at_bats),
        })
        common = dict(
            row_ordinal=len(output),
            source_player_id=player_id,
            source_player_url=f"{NCAA_BASE}/players/{player_id}",
            source_identifier=discovered_scope.source_identifier,
            source_payload_sha256=payload_sha256,
            sample=sample,
            raw_values=raw,
        )
        if discovered_scope.stat_type == "batting":
            row = BattingSplitRow(at_bats=at_bats, hits=hits, **common)
        else:
            row = PitchingSplitRow(
                opponent_at_bats=at_bats,
                hits_allowed=hits,
                **common,
            )
        output.append(row)
        metrics.append(SplitMetricResult(
            source_player_id=player_id,
            code="avg",
            result=calculate_metric("avg", {"hits": hits, "at_bats": at_bats}),
        ))
    return ParsedSplitResult(
        scope_kind=SPLIT_SCOPE_KIND,
        scope_code=discovered_scope.scope_code,
        display_label=discovered_scope.display_label,
        stat_type=discovered_scope.stat_type,
        source_identifier=discovered_scope.source_identifier,
        source_path=discovered_scope.source_path,
        source_url=discovered_scope.source_url,
        source_season_id=discovered_scope.source_season_id,
        source_season_team_id=discovered_scope.source_season_team_id,
        source_stat_category_id=discovered_scope.source_stat_category_id,
        source_payload_sha256=payload_sha256,
        fixture_sha256=fixture_sha256,
        status="complete",
        reason=None,
        collected_at=collected_at,
        rows=tuple(output),
        metric_results=tuple(metrics),
    )


def parse_live_split_result(
    html: str,
    *,
    discovered_scope: DiscoveredSplitScope,
) -> ParsedSplitResult:
    """Normalize one current NCAA presentation grid into the certified contract."""
    identifier = discovered_scope.source_identifier
    if identifier is None:
        checksum = hashlib.sha256(
            f"{discovered_scope.stat_type}:{discovered_scope.scope_code}:{discovered_scope.reason}".encode()
        ).hexdigest()
        return ParsedSplitResult(
            scope_kind=SPLIT_SCOPE_KIND,
            scope_code=discovered_scope.scope_code,
            display_label=discovered_scope.display_label,
            stat_type=discovered_scope.stat_type,
            source_identifier=None,
            source_path=discovered_scope.source_path,
            source_url=None,
            source_season_id=discovered_scope.source_season_id,
            source_season_team_id=discovered_scope.source_season_team_id,
            source_stat_category_id=discovered_scope.source_stat_category_id,
            source_payload_sha256=checksum,
            fixture_sha256=checksum,
            status="unavailable",
            reason=discovered_scope.reason or "source_identifier_not_found",
            collected_at=discovered_scope.collected_at,
            rows=(),
            metric_results=(),
        )
    if not html or "access denied" in html.casefold():
        raise SplitContractError("source_blocked")
    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one("#stat_grid")
    if table is None:
        raise SplitContractError("source_blocked")
    header_rows = table.select("thead tr")
    header_row = header_rows[-1] if header_rows else table.find("tr")
    headers = [
        _normalized(cell.get_text(" ", strip=True))
        for cell in header_row.find_all(["th", "td"], recursive=False)
    ] if header_row is not None else []
    ab_header = "ab" if discovered_scope.stat_type == "batting" else "p-oab"
    if headers.count("h") != 1 or headers.count(ab_header) != 1:
        raise SplitContractError("schema_validation")
    extracted = {}
    for node in soup.select(f".sport_stat_{identifier}"):
        stat_row = node if node.name == "tr" else node.find_parent("tr")
        if stat_row is None:
            continue
        player_row = stat_row
        while player_row is not None and not re.fullmatch(
            r"player_\d+", str(player_row.get("id") or "")
        ):
            player_row = player_row.find_previous_sibling("tr")
        if player_row is None:
            raise SplitContractError("schema_validation")
        identity_labels = {
            _normalized(cell.get_text(" ", strip=True))
            for cell in player_row.find_all(["th", "td"], recursive=False)
        }
        if identity_labels & {
            "team", "total", "totals", "team total", "team totals",
            "opponent total", "opponent totals",
        }:
            continue
        link = player_row.find(
            "a", href=re.compile(r"^/players/\d+(?:\?.*)?$")
        )
        if link is not None:
            parsed_player_url = urlparse(link.get("href", ""))
            player_match = re.fullmatch(r"/players/(\d+)", parsed_player_url.path)
            try:
                player_query = parse_qs(parsed_player_url.query, strict_parsing=True)
            except ValueError as exc:
                raise SplitContractError("schema_validation") from exc
            if (
                player_match is None
                or parsed_player_url.scheme
                or parsed_player_url.netloc
                or parsed_player_url.fragment
                or (
                    player_query
                    and player_query != {
                        "year_stat_category_id": [
                            discovered_scope.source_stat_category_id
                        ]
                    }
                )
            ):
                raise SplitContractError("schema_validation")
            player_path = f"/players/{player_match.group(1)}"
        else:
            match = re.fullmatch(r"player_(\d+)", str(player_row.get("id") or ""))
            if match is None:
                raise SplitContractError("schema_validation")
            player_path = f"/players/{match.group(1)}"
        cells = stat_row.find_all(["th", "td"], recursive=False)
        offset = len(headers) - len(cells)
        hit_index = headers.index("h") - offset
        ab_index = headers.index(ab_header) - offset
        if min(offset, hit_index, ab_index) < 0 or max(hit_index, ab_index) >= len(cells):
            raise SplitContractError("schema_validation")
        hit_text = cells[hit_index].get_text(" ", strip=True)
        ab_text = cells[ab_index].get_text(" ", strip=True)
        if hit_text in {"", "-", "--"} and ab_text.isdigit():
            hit_text = "0"
        if ab_text in {"", "-", "--"} and hit_text == "0":
            ab_text = "0"
        hits = _parse_split_count(hit_text)
        at_bats = _parse_split_count(ab_text)
        if hits > at_bats:
            raise SplitContractError("schema_validation")
        previous = extracted.get(player_path)
        if previous is not None and previous != (hits, at_bats):
            raise SplitContractError("schema_validation")
        extracted[player_path] = (hits, at_bats)
    checksum = hashlib.sha256(html.encode("utf-8")).hexdigest()
    captured = discovered_scope.collected_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    attrs = {
        "data-canonical-label": discovered_scope.source_label,
        "data-captured-at": captured,
        "data-division": "live",
        "data-identifier": identifier,
        "data-result-contract-version": SPLIT_RESULT_CONTRACT_VERSION,
        "data-season-id": discovered_scope.source_season_id,
        "data-season-team-id": discovered_scope.source_season_team_id,
        "data-source-path": discovered_scope.source_path,
        "data-source-payload-sha256": checksum,
        "data-source-stat-category-id": discovered_scope.source_stat_category_id,
        "data-stat-type": discovered_scope.stat_type,
    }
    if not extracted:
        attrs.update({"data-status": "unavailable", "data-reason": "source_not_published"})
        attr_text = " ".join(
            f'{key}="{html_module.escape(value, quote=True)}"'
            for key, value in sorted(attrs.items())
        )
        return parse_split_result(f"<split-result {attr_text}></split-result>", discovered_scope=discovered_scope)
    attrs["data-status"] = "complete"
    attr_text = " ".join(
        f'{key}="{html_module.escape(value, quote=True)}"'
        for key, value in sorted(attrs.items())
    )
    rows = "".join(
        f'<tr><td><a href="{path}">Player {index}</a></td><td>{hits}</td><td>{at_bats}</td></tr>'
        for index, (path, (hits, at_bats)) in enumerate(
            sorted(extracted.items(), key=lambda item: int(item[0].rsplit("/", 1)[1])),
            start=1,
        )
    )
    normalized = (
        f"<split-result {attr_text}><table id=\"split_result_grid\">"
        "<thead><tr><th>Player</th><th>H</th><th>AB</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></split-result>"
    )
    return parse_split_result(normalized, discovered_scope=discovered_scope)


def _parse_split_count(value: str) -> int:
    text = str(value or "").strip()
    if not re.fullmatch(r"(?:\d+|\d{1,3}(?:,\d{3})+)", text):
        raise SplitContractError("schema_validation")
    return int(text.replace(",", ""))


def _validate_source_url(source_url: str, stat_category_id: str) -> None:
    parsed = urlparse(source_url)
    match = re.fullmatch(r"/teams/(\d+)/season_to_date_stats", parsed.path)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "stats.ncaa.org"
        or parsed.params
        or parsed.fragment
        or match is None
        or source_url != build_fielding_url(match.group(1), stat_category_id)
    ):
        raise ValueError("fielding source URL is not an allowed NCAA endpoint")


def parse_schedule_fixture(
    html: str,
    *,
    expected_season_id: str,
    expected_team_id: str,
    expected_season_team_id: str,
    expected_division: str,
) -> ScheduleEvidence:
    """Parse only the sanitized schedule contract emitted by the capture utility."""
    soup = BeautifulSoup(html, "html.parser")
    root = soup.find("schedule-evidence")
    if root is None or soup.find("script") is not None:
        raise ScheduleContractError("schema_validation")
    division = str(expected_division).lower().removeprefix("d")
    expected_path = f"/team/{expected_team_id}/{expected_season_id}"
    identity = (
        root.get("data-schedule-contract-version"),
        root.get("data-season-id"),
        root.get("data-team-id"),
        root.get("data-season-team-id"),
        root.get("data-division"),
        root.get("data-source-path"),
    )
    if identity != (
        SCHEDULE_CONTRACT_VERSION,
        expected_season_id,
        expected_team_id,
        expected_season_team_id,
        f"d{division}",
        expected_path,
    ):
        raise ScheduleContractError("source_identity_mismatch")
    if any(
        not re.fullmatch(r"\d+", value or "")
        for value in (
            expected_season_id, expected_team_id, expected_season_team_id,
            root.get("data-team-conference-id"),
        )
    ):
        raise ScheduleContractError("schema_validation")
    payload_sha256 = root.get("data-source-payload-sha256") or ""
    if not re.fullmatch(r"[0-9a-f]{64}", payload_sha256):
        raise ScheduleContractError("schema_validation")
    collected_at = _iso_datetime(root.get("data-captured-at") or "")
    complete = root.get("data-schedule-complete")
    if complete not in {"true", "false"}:
        raise ScheduleContractError("schema_validation")
    output = []
    identities = set()
    for node in root.find_all("game", recursive=False):
        status = node.get("data-status")
        if status not in {"completed", "scheduled", "canceled", "postponed", "no_contest"}:
            raise ScheduleContractError("schema_validation")
        try:
            game_date = date.fromisoformat(node.get("data-date") or "")
            ordinal = int(node.get("data-doubleheader-ordinal") or "")
        except (TypeError, ValueError) as exc:
            raise ScheduleContractError("schema_validation") from exc
        if ordinal < 1:
            raise ScheduleContractError("schema_validation")
        source_game_id = node.get("data-source-game-id") or None
        if source_game_id is not None and not source_game_id.isdigit():
            raise ScheduleContractError("schema_validation")
        if status == "completed" and source_game_id is None:
            raise ScheduleContractError("schema_validation")
        opponent_status = node.get("data-opponent-identity")
        conference_status = node.get("data-conference-evidence")
        if opponent_status not in {"present", "source_absent"}:
            raise ScheduleContractError("schema_validation")
        if conference_status not in {"present", "source_absent"}:
            raise ScheduleContractError("schema_validation")
        opponent_values = tuple(
            node.get(name) or None
            for name in (
                "data-opponent-team-id", "data-opponent-season-team-id",
                "data-opponent-conference-id",
            )
        )
        if opponent_status == "present" and any(value is None for value in opponent_values[:2]):
            raise ScheduleContractError("schema_validation")
        if opponent_status == "source_absent" and any(value is not None for value in opponent_values[:2]):
            raise ScheduleContractError("schema_validation")
        if conference_status == "present" and opponent_values[2] is None:
            raise ScheduleContractError("schema_validation")
        if conference_status == "source_absent" and opponent_values[2] is not None:
            raise ScheduleContractError("schema_validation")
        if any(value is not None and not value.isdigit() for value in opponent_values):
            raise ScheduleContractError("schema_validation")
        result_sha256 = node.get("data-result-sha256") or ""
        venue = node.get("data-venue") or ""
        if not re.fullmatch(r"[0-9a-f]{64}", result_sha256) or venue not in {
            "home", "away", "neutral"
        }:
            raise ScheduleContractError("schema_validation")
        identity = source_game_id or (
            game_date.isoformat(), opponent_values[1], venue, ordinal, result_sha256
        )
        if identity in identities:
            raise ScheduleContractError("schema_validation")
        identities.add(identity)
        output.append(ScheduleGameEvidence(
            source_game_id=source_game_id,
            game_date=game_date,
            status=status,
            opponent_team_id=opponent_values[0],
            opponent_season_team_id=opponent_values[1],
            opponent_conference_id=opponent_values[2],
            conference_evidence_status=conference_status,
            opponent_identity_status=opponent_status,
            doubleheader_ordinal=ordinal,
            result_sha256=result_sha256,
            venue=venue,
        ))
    if not output:
        raise ScheduleContractError("schema_validation")
    return ScheduleEvidence(
        source_path=expected_path,
        source_url=f"{NCAA_BASE}{expected_path}",
        source_payload_sha256=payload_sha256,
        source_season_id=expected_season_id,
        source_team_id=expected_team_id,
        source_season_team_id=expected_season_team_id,
        team_conference_id=root["data-team-conference-id"],
        division=f"d{division}",
        schedule_complete=complete == "true",
        collected_at=collected_at,
        games=tuple(output),
    )


def _normalized(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def _parse_count(value: str) -> int | None:
    text = str(value or "").strip()
    if text.casefold() in _EMPTY_VALUES:
        return None
    if not re.fullmatch(r"\d+", text):
        raise FieldingParseError("schema_validation")
    return int(text)


def _parse_percentage(value: str) -> Decimal | None:
    text = str(value or "").strip()
    if text.casefold() in _EMPTY_VALUES:
        return None
    try:
        parsed = Decimal(text)
    except InvalidOperation as exc:
        raise FieldingParseError("schema_validation") from exc
    if parsed < 0 or parsed > 1:
        raise FieldingParseError("schema_validation")
    return parsed


def parse_fielding_page(
    html: str,
    *,
    source_url: str,
    stat_category_id: str,
    collected_at: datetime | None = None,
) -> FieldingSection:
    """Validate one NCAA fielding grid before any source row is accepted."""
    _validate_source_url(source_url, stat_category_id)
    if not html:
        raise FieldingParseError("source_blocked")
    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one("#stat_grid")
    if table is None:
        raise FieldingParseError("source_blocked")

    header_rows = table.select("thead tr")
    header_row = header_rows[-1] if header_rows else table.find("tr")
    if header_row is None:
        raise FieldingParseError("schema_validation")
    raw_headers = [
        cell.get_text(" ", strip=True)
        for cell in header_row.find_all(["th", "td"], recursive=False)
    ]
    headers = [
        _FIELDING_HEADER_ALIASES.get(_normalized(value), _normalized(value))
        for value in raw_headers
    ]
    if any(headers.count(required) != 1 for required in _REQUIRED_HEADERS):
        raise FieldingParseError("schema_validation")
    if any(headers.count(optional) > 1 for optional in _OPTIONAL_HEADERS):
        raise FieldingParseError("schema_validation")
    if any(
        value not in {
            *_REQUIRED_HEADERS,
            *_OPTIONAL_HEADERS,
            *_IGNORED_FIELDING_HEADERS,
        }
        for value in headers
    ):
        raise FieldingParseError("schema_validation")

    player_index = headers.index("player")
    digest = hashlib.sha256(html.encode("utf-8")).hexdigest()
    rows = []
    saw_empty_marker = False
    body_rows = table.select("tbody tr") or table.find_all("tr")[1:]
    for source_row in body_rows:
        marker = _normalized(source_row.get_text(" ", strip=True))
        if marker in _EMPTY_MARKERS:
            saw_empty_marker = True
            continue
        cells = source_row.find_all(["th", "td"], recursive=False)
        if not any(cell.get_text(" ", strip=True) for cell in cells):
            continue
        if len(cells) != len(headers):
            raise FieldingParseError("schema_validation")
        values = [cell.get_text(" ", strip=True) for cell in cells]
        if not any(values):
            continue
        name = re.sub(r"^x\s+", "", values[player_index], flags=re.IGNORECASE).strip()
        if not name or _normalized(name) in {
            "team", "total", "totals", "team totals", "opponent totals",
        }:
            if name:
                continue
            raise FieldingParseError("schema_validation")
        link = cells[player_index].find("a")
        href = link.get("href", "") if link is not None else ""
        parsed_player_url = urlparse(href)
        player_match = re.fullmatch(r"/players/(\d+)", parsed_player_url.path)
        player_query = parse_qs(parsed_player_url.query, strict_parsing=True)
        if (
            player_match is None
            or parsed_player_url.scheme
            or parsed_player_url.netloc
            or parsed_player_url.fragment
            or (
                player_query
                and player_query != {"year_stat_category_id": [stat_category_id]}
            )
        ):
            raise FieldingParseError("schema_validation")
        player_id = player_match.group(1)
        raw = dict(zip(raw_headers, values))
        raw[raw_headers[player_index]] = name
        raw["player_url"] = f"{NCAA_BASE}/players/{player_id}"
        by_header = dict(zip(headers, values))
        canonical = {
            canonical_name: _parse_count(by_header.get(header, ""))
            for header, canonical_name in _COUNT_FIELDS.items()
        }
        canonical["fielding_percentage"] = _parse_percentage(by_header["fpct"])
        rows.append(FieldingRow(
            row_ordinal=len(rows),
            source_url=source_url,
            source_player_id=player_id,
            player_url=f"{NCAA_BASE}/players/{player_id}",
            raw_values=MappingProxyType(raw),
            canonical_values=MappingProxyType(canonical),
            payload_sha256=digest,
        ))

    if not rows and not saw_empty_marker:
        raise FieldingParseError("schema_validation")
    if rows and saw_empty_marker:
        raise FieldingParseError("schema_validation")

    return FieldingSection(
        name="fielding",
        source_url=source_url,
        stat_category_id=stat_category_id,
        html_sha256=digest,
        rows=tuple(rows),
        verified_empty=saw_empty_marker,
        collected_at=collected_at or datetime.now(UTC),
    )
