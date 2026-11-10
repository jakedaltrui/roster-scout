"""Deterministic NCAA collection-to-canonical transformation."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import re

from roster_scraper import NcaaCollection, NcaaSourceRow


METRIC_DEFINITION_VERSION = "ncaa-1.0"
_MISSING = {"", "-", "--", "n/a", "na"}


class IdentityConflictError(ValueError):
    """One authoritative NCAA player ID carries materially conflicting names."""


@dataclass(frozen=True)
class CanonicalSourceRecord:
    section: str
    row_ordinal: int
    source_url: str
    source_team_id: str
    source_season_id: str
    source_season_team_id: str
    source_stat_category_id: str
    source_player_id: str | None
    player_url: str | None
    collected_at: object
    parser_version: str
    schema_version: str
    payload_sha256: str
    raw_values: dict


@dataclass(frozen=True)
class CanonicalPlayer:
    identity_key: str
    identity_status: str
    source_player_id: str | None
    display_name: str
    jersey_number: str | None
    position: str | None
    batting_source: tuple[str, int] | None
    pitching_source: tuple[str, int] | None


@dataclass(frozen=True)
class CanonicalFact:
    section: str
    identity_key: str
    source_record: tuple[str, int]
    values: dict


@dataclass(frozen=True)
class MetricLineage:
    identity_key: str
    code: str
    value: Decimal | None
    provenance: str
    definition_version: str = METRIC_DEFINITION_VERSION


@dataclass(frozen=True)
class CanonicalCollection:
    source_records: tuple[CanonicalSourceRecord, ...]
    players: tuple[CanonicalPlayer, ...]
    facts: tuple[CanonicalFact, ...]
    metrics: tuple[MetricLineage, ...]


@dataclass(frozen=True)
class _IdentityGroup:
    rows: tuple[NcaaSourceRow, ...]
    identity_status: str
    identity_key: str


def _raw(row: NcaaSourceRow, *keys: str) -> str | None:
    lowered = {key.casefold(): value for key, value in row.raw_values.items()}
    for key in keys:
        value = lowered.get(key.casefold())
        if value is None:
            continue
        text = str(value).strip()
        if text.casefold() not in _MISSING:
            return text
    return None


def _integer(row: NcaaSourceRow, *keys: str) -> int | None:
    value = _raw(row, *keys)
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"Invalid integer {value!r} for {keys[0]}.") from exc
    if parsed < 0:
        raise ValueError(f"Integer for {keys[0]} cannot be negative: {value!r}.")
    return parsed


def _decimal(row: NcaaSourceRow, *keys: str) -> Decimal | None:
    value = _raw(row, *keys)
    if value is None:
        return None
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"Invalid decimal {value!r} for {keys[0]}.") from exc


def innings_to_outs(value: str | None) -> int | None:
    if value is None or str(value).strip().casefold() in _MISSING:
        return None
    text = str(value).strip()
    match = re.fullmatch(r"(\d+)(?:\.([012]))?", text)
    if not match:
        raise ValueError(f"Invalid baseball innings value {text!r}.")
    return int(match.group(1)) * 3 + int(match.group(2) or 0)


def normalize_material_name(value: str | None) -> str:
    """Normalize NCAA names for authoritative identity evidence comparison."""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", (value or "").casefold()).split())


def _support(row: NcaaSourceRow) -> tuple[str | None, str | None]:
    return (_raw(row, "Jersey", "#"), _raw(row, "Pos", "Position"))


def _normalized_support(row: NcaaSourceRow) -> tuple[str, str]:
    jersey, position = _support(row)
    return (
        (jersey or "").strip().casefold(),
        " ".join((position or "").casefold().split()),
    )


def _compatible(left: NcaaSourceRow, right: NcaaSourceRow) -> bool:
    for a, b in zip(_normalized_support(left), _normalized_support(right)):
        if a and b and a != b:
            return False
    return True


def _corroborated(left: NcaaSourceRow, right: NcaaSourceRow) -> bool:
    return any(
        a and b and a == b
        for a, b in zip(_normalized_support(left), _normalized_support(right))
    )


def _support_signature(row: NcaaSourceRow) -> tuple[str, str, str]:
    jersey, position = _normalized_support(row)
    return (
        normalize_material_name(_raw(row, "Player")),
        jersey,
        position,
    )


def _identity_key(
    collection: NcaaCollection,
    rows: tuple[NcaaSourceRow, ...],
    identity_status: str,
) -> str:
    evidence = "|".join(
        f"{row.section}:{row.row_ordinal}:{_support_signature(row)}"
        for row in rows
    )
    digest = hashlib.sha256(evidence.encode("utf-8")).hexdigest()[:20]
    return f"{identity_status}:{collection.team.season_team_id}:{digest}"


def _identity_groups(collection: NcaaCollection) -> list[_IdentityGroup]:
    rows = [row for section in collection.sections for row in section.rows]
    authoritative = {}
    idless = []
    for row in rows:
        if row.source_player_id:
            authoritative.setdefault(row.source_player_id, []).append(row)
        else:
            idless.append(row)

    groups = []
    for player_id, player_rows in authoritative.items():
        names = {normalize_material_name(_raw(row, "Player")) for row in player_rows}
        names.discard("")
        if len(names) > 1:
            raise IdentityConflictError(
                f"NCAA player ID {player_id} has conflicting names: {sorted(names)}"
            )
        groups.append(_IdentityGroup(
            rows=tuple(player_rows),
            identity_status="authoritative",
            identity_key=f"ncaa:{player_id}",
        ))

    by_name = {}
    for row in idless:
        by_name.setdefault(normalize_material_name(_raw(row, "Player")), []).append(row)

    for name, same_name_rows in by_name.items():
        if len(same_name_rows) == 1 and name:
            grouped_rows = (same_name_rows[0],)
            groups.append(_IdentityGroup(
                rows=grouped_rows,
                identity_status="fallback",
                identity_key=_identity_key(collection, grouped_rows, "fallback"),
            ))
            continue

        batting = [row for row in same_name_rows if row.section == "batting"]
        pitching = [row for row in same_name_rows if row.section == "pitching"]
        bat_candidates = {
            id(bat): [pit for pit in pitching if _compatible(bat, pit)]
            for bat in batting
        }
        pit_candidates = {
            id(pit): [bat for bat in batting if _compatible(bat, pit)]
            for pit in pitching
        }
        resolved = set()
        for bat in batting:
            candidates = bat_candidates[id(bat)]
            if len(candidates) != 1:
                continue
            pit = candidates[0]
            if len(pit_candidates[id(pit)]) != 1:
                continue
            if not _corroborated(bat, pit):
                continue
            grouped_rows = (bat, pit)
            groups.append(_IdentityGroup(
                rows=grouped_rows,
                identity_status="fallback",
                identity_key=_identity_key(collection, grouped_rows, "fallback"),
            ))
            resolved.update((id(bat), id(pit)))

        remaining = [row for row in same_name_rows if id(row) not in resolved]
        signatures = [_support_signature(row) for row in remaining]
        signature_counts = {
            signature: signatures.count(signature) for signature in signatures
        }
        ambiguous = {
            id(row)
            for row, signature in zip(remaining, signatures, strict=True)
            if not name
            or not any(signature[1:])
            or signature_counts[signature] != 1
        }
        for bat in remaining:
            for pit in remaining:
                if (
                    bat.section == "batting"
                    and pit.section == "pitching"
                    and _compatible(bat, pit)
                ):
                    ambiguous.update((id(bat), id(pit)))

        for row in remaining:
            grouped_rows = (row,)
            identity_status = "unresolved" if id(row) in ambiguous else "fallback"
            groups.append(_IdentityGroup(
                rows=grouped_rows,
                identity_status=identity_status,
                identity_key=_identity_key(
                    collection, grouped_rows, identity_status
                ),
            ))
    return groups


def _metric(identity_key: str, row: NcaaSourceRow, code: str, aliases, calculated=None):
    source = _decimal(row, *aliases)
    if source is not None:
        return MetricLineage(identity_key, code, source, "source-provided")
    if calculated is None:
        return MetricLineage(identity_key, code, None, "unavailable")
    return MetricLineage(identity_key, code, calculated(), "calculated")


def canonicalize_ncaa_collection(collection: NcaaCollection) -> CanonicalCollection:
    source_records = []
    category_ids = {
        section.name: section.stat_category_id for section in collection.sections
    }
    for section in collection.sections:
        for row in section.rows:
            source_records.append(CanonicalSourceRecord(
                section=row.section,
                row_ordinal=row.row_ordinal,
                source_url=row.source_url,
                source_team_id=collection.team.team_id,
                source_season_id=collection.season.season_id,
                source_season_team_id=collection.team.season_team_id,
                source_stat_category_id=category_ids[row.section],
                source_player_id=row.source_player_id,
                player_url=row.player_url,
                collected_at=collection.metadata.collected_at,
                parser_version=collection.metadata.parser_version,
                schema_version=collection.metadata.schema_version,
                payload_sha256=row.payload_sha256,
                raw_values=row.raw_values,
            ))

    players = []
    facts = []
    metrics = []
    for group in _identity_groups(collection):
        rows = group.rows
        source_player_id = next((row.source_player_id for row in rows if row.source_player_id), None)
        identity_key = group.identity_key
        first = rows[0]
        batting = next((row for row in rows if row.section == "batting"), None)
        pitching = next((row for row in rows if row.section == "pitching"), None)
        players.append(CanonicalPlayer(
            identity_key=identity_key,
            identity_status=group.identity_status,
            source_player_id=source_player_id,
            display_name=_raw(first, "Player") or "",
            jersey_number=_raw(first, "Jersey", "#"),
            position=_raw(first, "Pos", "Position"),
            batting_source=(batting.section, batting.row_ordinal) if batting else None,
            pitching_source=(pitching.section, pitching.row_ordinal) if pitching else None,
        ))
        if batting:
            facts.append(CanonicalFact("batting", identity_key, (batting.section, batting.row_ordinal), {
                "at_bats": _integer(batting, "AB"),
                "hits": _integer(batting, "H"),
                "walks": _integer(batting, "BB"),
                "strikeouts": _integer(batting, "K", "SO"),
            }))

            def calculated_avg():
                hits = _integer(batting, "H")
                at_bats = _integer(batting, "AB")
                return Decimal(hits) / Decimal(at_bats) if hits is not None and at_bats else None

            metrics.append(_metric(identity_key, batting, "AVG", ("BA", "AVG"), calculated_avg))
        if pitching:
            facts.append(CanonicalFact("pitching", identity_key, (pitching.section, pitching.row_ordinal), {
                "innings_outs": innings_to_outs(_raw(pitching, "IP")),
                "innings_raw": _raw(pitching, "IP"),
                "hits_allowed": _integer(pitching, "H"),
                "earned_runs": _integer(pitching, "ER"),
                "walks": _integer(pitching, "BB"),
                "strikeouts": _integer(pitching, "SO", "K"),
            }))
            metrics.append(_metric(identity_key, pitching, "ERA", ("ERA",)))

    return CanonicalCollection(
        source_records=tuple(source_records),
        players=tuple(players),
        facts=tuple(facts),
        metrics=tuple(metrics),
    )


def to_collected_sections(collection: NcaaCollection):
    """Adapt live NCAA evidence to the durable job section contract."""
    from .jobs import CollectedSection

    return tuple(
        CollectedSection(
            name=section.name,
            critical=True,
            rows=tuple({
                **row.raw_values,
                "_player_id": row.source_player_id,
                "_player_url": row.player_url,
                "_payload_sha256": row.payload_sha256,
            } for row in section.rows),
            verified_empty=section.verified_empty,
            source_url=section.source_url,
            source_team_id=collection.team.team_id,
            source_season_id=collection.season.season_id,
            source_season_team_id=collection.team.season_team_id,
            source_stat_category_id=section.stat_category_id,
            collected_at=collection.metadata.collected_at,
            parser_version=collection.metadata.parser_version,
            schema_version=collection.metadata.schema_version,
        )
        for section in collection.sections
    )
