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


NCAA_BASE = "https://stats.ncaa.org"
FIELDING_CONTRACT_VERSION = "ncaa-fielding-v1"
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
