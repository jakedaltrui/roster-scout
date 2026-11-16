"""Fail-closed NCAA detailed-stat parsing contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
import hashlib
import re
from types import MappingProxyType
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from .metrics import MetricResult, calculate_metric


NCAA_BASE = "https://stats.ncaa.org"
FIELDING_CONTRACT_VERSION = "ncaa-fielding-v1"
SPLIT_CONTROL_CONTRACT_VERSION = "ncaa-split-controls-v1"
SPLIT_RESULT_CONTRACT_VERSION = "ncaa-split-result-v1"
SPLIT_SCOPE_KIND = "source_split"
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
_REQUIRED_HEADERS = ("player", "g", "gs", "po", "a", "e", "tc", "dp", "fpct")
_OPTIONAL_HEADERS = ("pb", "sba", "csb")
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


def _parse_split_count(value: str) -> int:
    text = str(value or "").strip()
    if not re.fullmatch(r"\d+", text):
        raise SplitContractError("schema_validation")
    return int(text)


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
    if not html or "access denied" in html.casefold():
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
    headers = [_normalized(value) for value in raw_headers]
    if any(headers.count(required) != 1 for required in _REQUIRED_HEADERS):
        raise FieldingParseError("schema_validation")
    if any(headers.count(optional) > 1 for optional in _OPTIONAL_HEADERS):
        raise FieldingParseError("schema_validation")
    if any(value not in {*_REQUIRED_HEADERS, *_OPTIONAL_HEADERS} for value in headers):
        raise FieldingParseError("schema_validation")

    player_index = headers.index("player")
    digest = hashlib.sha256(html.encode("utf-8")).hexdigest()
    rows = []
    body_rows = table.select("tbody tr") or table.find_all("tr")[1:]
    for source_row in body_rows:
        marker = _normalized(source_row.get_text(" ", strip=True))
        if marker in _EMPTY_MARKERS:
            continue
        cells = source_row.find_all(["th", "td"], recursive=False)
        if len(cells) != len(headers):
            raise FieldingParseError("schema_validation")
        values = [cell.get_text(" ", strip=True) for cell in cells]
        if not any(values):
            continue
        name = re.sub(r"^x\s+", "", values[player_index], flags=re.IGNORECASE).strip()
        if not name or _normalized(name) in {"team", "total", "totals", "team totals"}:
            if name:
                continue
            raise FieldingParseError("schema_validation")
        link = cells[player_index].find("a")
        href = link.get("href", "") if link is not None else ""
        player_match = re.fullmatch(r"/players/(\d+)", href)
        if player_match is None:
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

    return FieldingSection(
        name="fielding",
        source_url=source_url,
        stat_category_id=stat_category_id,
        html_sha256=digest,
        rows=tuple(rows),
        verified_empty=not rows,
        collected_at=collected_at or datetime.now(UTC),
    )
