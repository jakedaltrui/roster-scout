"""Immutable snapshot workbooks and shared artifact-storage boundaries."""

from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from sqlalchemy import and_, or_, select

from roster_scraper import normalize_ncaa_players, split_roster

from .database import session_advisory_lock
from .models import (
    BattingFact,
    MetricDefinition,
    MetricValue,
    PitchingFact,
    PlayerTeamSeason,
    Report,
    ReportArtifact,
    ReportSnapshot,
    ScopedBattingFact,
    ScopedPitchingFact,
    Season,
    SourceRecord,
    StatisticScope,
    Team,
    TeamSeason,
)
from .metrics import DEFINITION_VERSION, calculate_metric
from .workbooks import create_workbook, workbook_branding


ARTIFACT_SCHEMA_VERSION = "detailed-two-sheet-xlsx-v2"
SUPPORTED_ARTIFACT_SCHEMA_VERSIONS = frozenset({
    "basic-xlsx-v1",
    ARTIFACT_SCHEMA_VERSION,
})
MAX_ARTIFACT_BYTES = 25 * 1024 * 1024
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_STALE_AFTER = timedelta(minutes=15)
DEFAULT_RETRY_DELAY = timedelta(seconds=30)

class ArtifactStorageConfigurationError(RuntimeError):
    """Raised when durable artifact storage is absent or unsafe."""


class ArtifactStorageError(RuntimeError):
    """Sanitized storage failure safe for service control flow."""


def _validated_object_key(key: str) -> str:
    parts = str(key or "").split("/")
    if len(parts) != 8 or parts[0] != "organizations" or parts[2] != "reports":
        raise ArtifactStorageError("invalid artifact key")
    if parts[4] != "snapshots" or parts[6] != "workbooks":
        raise ArtifactStorageError("invalid artifact key")
    try:
        uuid.UUID(parts[1])
        uuid.UUID(parts[3])
        uuid.UUID(parts[5])
    except (TypeError, ValueError, AttributeError):
        raise ArtifactStorageError("invalid artifact key") from None
    match = re.fullmatch(r"roster-scout-(.+)\.xlsx", parts[7])
    if match is None or match.group(1) not in SUPPORTED_ARTIFACT_SCHEMA_VERSIONS:
        raise ArtifactStorageError("invalid artifact key")
    return "/".join(parts)


def artifact_object_key(organization_id, report_id, snapshot_id) -> str:
    return _validated_object_key(
        f"organizations/{uuid.UUID(str(organization_id))}/"
        f"reports/{uuid.UUID(str(report_id))}/"
        f"snapshots/{uuid.UUID(str(snapshot_id))}/workbooks/"
        f"roster-scout-{ARTIFACT_SCHEMA_VERSION}.xlsx"
    )


class FilesystemArtifactStore:
    """Local-only immutable store used by development and deterministic tests."""

    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        if not self.root.is_absolute():
            raise ArtifactStorageConfigurationError(
                "artifact filesystem root must be absolute"
            )
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def _path(self, key):
        key = _validated_object_key(key)
        path = (self.root / key).resolve()
        if self.root not in path.parents:
            raise ArtifactStorageError("invalid artifact key")
        return path

    def write_immutable(self, key, payload, digest):
        _validate_payload(payload, digest)
        destination = self._path(key)
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = destination.with_name(f".{destination.name}.{uuid.uuid4()}.tmp")
        try:
            with temporary.open("xb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary, destination)
            except FileExistsError:
                try:
                    existing = destination.read_bytes()
                except OSError:
                    raise ArtifactStorageError(
                        "artifact storage verification failed"
                    ) from None
                _validate_payload(existing, digest)
        except ArtifactStorageError:
            raise
        except OSError:
            raise ArtifactStorageError("artifact storage write failed") from None
        finally:
            temporary.unlink(missing_ok=True)

    def read(self, key):
        try:
            payload = self._path(key).read_bytes()
        except OSError:
            raise ArtifactStorageError("artifact payload is unavailable") from None
        if not payload or len(payload) > MAX_ARTIFACT_BYTES:
            raise ArtifactStorageError("artifact payload is unavailable")
        return payload


class S3ArtifactStore:
    """Private S3-compatible immutable workbook store."""

    def __init__(
        self,
        *,
        bucket,
        endpoint_url=None,
        region=None,
        access_key_id=None,
        secret_access_key=None,
        session_token=None,
        server_side_encryption="AES256",
        client=None,
    ):
        self.bucket = str(bucket or "").strip()
        if not self.bucket or len(self.bucket) > 255:
            raise ArtifactStorageConfigurationError(
                "artifact bucket is required"
            )
        self.server_side_encryption = str(server_side_encryption or "").strip()
        self.client = client or boto3.client(
            "s3",
            endpoint_url=endpoint_url or None,
            region_name=region or None,
            aws_access_key_id=access_key_id or None,
            aws_secret_access_key=secret_access_key or None,
            aws_session_token=session_token or None,
            config=Config(
                signature_version="s3v4",
                connect_timeout=5,
                read_timeout=30,
                retries={"max_attempts": 3, "mode": "standard"},
            ),
        )

    def write_immutable(self, key, payload, digest):
        key = _validated_object_key(key)
        _validate_payload(payload, digest)
        parameters = {
            "Bucket": self.bucket,
            "Key": key,
            "Body": payload,
            "ContentType": (
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"
            ),
            "Metadata": {"sha256": digest},
            "IfNoneMatch": "*",
        }
        if self.server_side_encryption:
            parameters["ServerSideEncryption"] = self.server_side_encryption
        try:
            self.client.put_object(**parameters)
            return
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if code not in {
                "PreconditionFailed",
                "ConditionalRequestConflict",
                "412",
                "409",
            } and status not in {409, 412}:
                raise ArtifactStorageError("artifact storage write failed") from None

        try:
            existing = self.client.head_object(Bucket=self.bucket, Key=key)
        except ClientError:
            raise ArtifactStorageError("artifact storage verification failed") from None
        metadata = {
            str(name).lower(): str(value)
            for name, value in (existing.get("Metadata") or {}).items()
        }
        if (
            metadata.get("sha256") != digest
            or existing.get("ContentLength") != len(payload)
        ):
            raise ArtifactStorageError("immutable artifact conflict")

    def read(self, key):
        key = _validated_object_key(key)
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
            length = response.get("ContentLength")
            if not isinstance(length, int) or not 0 < length <= MAX_ARTIFACT_BYTES:
                raise ArtifactStorageError("artifact payload is unavailable")
            body = response["Body"]
            payload = body.read(MAX_ARTIFACT_BYTES + 1)
            close = getattr(body, "close", None)
            if callable(close):
                close()
        except ArtifactStorageError:
            raise
        except Exception:
            raise ArtifactStorageError("artifact storage read failed") from None
        if len(payload) != length or len(payload) > MAX_ARTIFACT_BYTES:
            raise ArtifactStorageError("artifact payload is unavailable")
        return payload


def artifact_store_from_environ(environ=None, *, required=False):
    environ = os.environ if environ is None else environ
    backend = str(environ.get("ROSTER_SCOUT_ARTIFACT_BACKEND", "")).strip().lower()
    if not backend:
        if required:
            raise ArtifactStorageConfigurationError(
                "ROSTER_SCOUT_ARTIFACT_BACKEND is required"
            )
        return None
    if backend == "filesystem":
        root = str(environ.get("ROSTER_SCOUT_ARTIFACT_DIRECTORY", "")).strip()
        if not root:
            raise ArtifactStorageConfigurationError(
                "ROSTER_SCOUT_ARTIFACT_DIRECTORY is required"
            )
        return FilesystemArtifactStore(root)
    if backend != "s3":
        raise ArtifactStorageConfigurationError(
            "ROSTER_SCOUT_ARTIFACT_BACKEND must be filesystem or s3"
        )
    bucket = (
        environ.get("ROSTER_SCOUT_ARTIFACT_BUCKET")
        or environ.get("BUCKET_NAME")
        or ""
    )
    access_key = environ.get("AWS_ACCESS_KEY_ID") or ""
    secret_key = environ.get("AWS_SECRET_ACCESS_KEY") or ""
    if not str(bucket).strip() or not access_key or not secret_key:
        raise ArtifactStorageConfigurationError(
            "S3 artifact bucket and credentials are required"
        )
    return S3ArtifactStore(
        bucket=bucket,
        endpoint_url=(
            environ.get("ROSTER_SCOUT_ARTIFACT_ENDPOINT_URL")
            or environ.get("AWS_ENDPOINT_URL_S3")
        ),
        region=environ.get("AWS_REGION") or environ.get("AWS_DEFAULT_REGION"),
        access_key_id=access_key,
        secret_access_key=secret_key,
        session_token=environ.get("AWS_SESSION_TOKEN"),
        server_side_encryption=environ.get(
            "ROSTER_SCOUT_ARTIFACT_SSE", "AES256"
        ),
    )


def _validate_payload(payload, digest):
    if (
        not isinstance(payload, bytes)
        or not payload
        or len(payload) > MAX_ARTIFACT_BYTES
        or not re.fullmatch(r"[0-9a-f]{64}", str(digest or ""))
        or sha256(payload).hexdigest() != digest
    ):
        raise ArtifactStorageError("artifact payload failed integrity validation")


@dataclass(frozen=True)
class SnapshotWorkbookSource:
    organization_id: uuid.UUID
    report_id: uuid.UUID
    snapshot_id: uuid.UUID
    source_team_id: str
    team_name: str
    season_year: int
    published_at: datetime
    hitters: tuple[dict, ...]
    pitchers: tuple[dict, ...]
    source_collected_at: datetime | None = None
    snapshot_status: str = "ready"


def _workbook_rows_from_source_records(rows):
    """Normalize immutable NCAA evidence through the homepage row pipeline."""
    batting_rows = []
    pitching_rows = []
    for source in rows:
        raw = dict(source.raw_values)
        raw["_player_id"] = source.source_player_id or ""
        raw["_player_url"] = raw.get("player_url", "")
        if source.section == "batting":
            batting_rows.append(raw)
        elif source.section == "pitching":
            pitching_rows.append(raw)
    players = normalize_ncaa_players(batting_rows, pitching_rows)
    return split_roster(players)


def _first_present(mapping, *keys):
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return ""


def _innings_text(outs):
    if outs is None:
        return ""
    return f"{int(outs) // 3}.{int(outs) % 3}"


def _metric_text(code, value):
    if value is None:
        return ""
    precision = 2 if code in {
        "era", "whip", "k_per_9", "bb_per_9", "h_per_9", "hr_per_9",
        "k_bb", "fip",
    } else 3
    return f"{value:.{precision}f}"


def _scope_label(status):
    return {
        "complete": "Complete",
        "partial": "Partial",
        "unavailable": "Unavailable",
        "failed": "Failed",
    }.get(str(status or "unavailable").lower(), "Unavailable")


def _project_snapshot_workbook_rows(
    player_rows,
    source_rows,
    batting_facts,
    pitching_facts,
    metric_rows,
    scopes,
    scoped_batting_facts,
    scoped_pitching_facts,
):
    """Project one immutable snapshot into renderer-ready rows."""
    players = {item.id: item for item in player_rows}
    sources = {(item.player_team_season_id, item.section): item for item in source_rows}
    source_by_id = {item.id: item for item in source_rows}
    batting_by_player = {item.player_team_season_id: item for item in batting_facts}
    pitching_by_player = {item.player_team_season_id: item for item in pitching_facts}
    scope_by_key = {(item.stat_type, item.scope_code): item for item in scopes}
    scoped_batting = {
        (item.statistic_scope_id, item.player_team_season_id): item
        for item in scoped_batting_facts
    }
    scoped_pitching = {
        (item.statistic_scope_id, item.player_team_season_id): item
        for item in scoped_pitching_facts
    }

    persisted_metrics = {}
    for item in metric_rows:
        metric, definition = item
        key = (metric.player_team_season_id, definition.code, metric.scope or "")
        persisted_metrics.setdefault(key, []).append((metric, definition))

    def metric_value(player_id, code, counts, *, scopes=("full_season", "season")):
        candidates = []
        for scope in scopes:
            candidates.extend(persisted_metrics.get((player_id, code, scope), ()))
        candidates.sort(
            key=lambda pair: (
                pair[0].status == "available",
                pair[1].version == DEFINITION_VERSION,
            ),
            reverse=True,
        )
        if candidates and candidates[0][0].status == "available":
            return _metric_text(code, candidates[0][0].value)
        result = calculate_metric(code, counts)
        return _metric_text(code, result.value) if result.status == "available" else ""

    def identity(player_id, preferred_section):
        player = players[player_id]
        source = sources.get((player_id, preferred_section))
        alternate = sources.get(
            (player_id, "pitching" if preferred_section == "batting" else "batting")
        )
        source = source or alternate
        alternate_raw = dict(alternate.raw_values) if alternate is not None else {}
        raw = {**alternate_raw, **(dict(source.raw_values) if source is not None else {})}
        identity_source = (
            source
            if source is not None and source.source_player_id
            else alternate or source
        )
        return {
            "_player_id": (
                identity_source.source_player_id if identity_source is not None else ""
            ),
            "_player_url": (
                _first_present(raw, "_player_url", "player_url", "Player URL")
                or (source.source_url if source is not None else "")
            ),
            "Name": player.display_name,
            "#": player.jersey_number or "",
            "Position": player.position or "",
            "Class": _first_present(raw, "Class", "Yr", "Year"),
        }

    def add_batting_scope(row, player_id, scope_code, prefix):
        scope = scope_by_key.get(("batting", scope_code))
        if scope is None:
            row[f"{prefix} Status"] = "Unavailable"
            return
        row[f"{prefix} Status"] = _scope_label(scope.status)
        if scope.status not in {"complete", "partial"}:
            return
        if scope.actual_game_count is not None:
            row[f"{prefix} G"] = scope.actual_game_count
        fact = scoped_batting.get((scope.id, player_id))
        if scope.status != "complete" or fact is None:
            return
        counts = dict(fact.counts_json or {})
        row[f"{prefix} PA"] = counts.get("plate_appearances", "")
        row[f"{prefix} AB"] = counts.get("at_bats", "")
        row[f"{prefix} AVG"] = metric_value(
            player_id,
            "avg",
            counts,
            scopes=(f"computed:batting:{scope_code}",),
        )
        row[f"{prefix} OPS"] = metric_value(
            player_id,
            "ops",
            counts,
            scopes=(f"computed:batting:{scope_code}",),
        )
        row[f"{prefix} K%"] = metric_value(
            player_id,
            "batting_k_pct",
            counts,
            scopes=(f"computed:batting:{scope_code}",),
        )

    def add_pitching_scope(row, player_id, scope_code, prefix):
        scope = scope_by_key.get(("pitching", scope_code))
        if scope is None:
            row[f"{prefix} Status"] = "Unavailable"
            return
        row[f"{prefix} Status"] = _scope_label(scope.status)
        if scope.status not in {"complete", "partial"}:
            return
        if scope.actual_game_count is not None:
            row[f"{prefix} G"] = scope.actual_game_count
        fact = scoped_pitching.get((scope.id, player_id))
        if scope.status != "complete" or fact is None:
            return
        counts = dict(fact.counts_json or {})
        row[f"{prefix} IP"] = _innings_text(counts.get("innings_outs"))
        for code, label in (
            ("era", "ERA"),
            ("whip", "WHIP"),
            ("k_per_9", "K/9"),
        ):
            row[f"{prefix} {label}"] = metric_value(
                player_id,
                code,
                counts,
                scopes=(f"computed:pitching:{scope_code}",),
            )

    hitters = []
    for player_id, fact in sorted(
        batting_by_player.items(),
        key=lambda pair: pair[1].at_bats or 0,
        reverse=True,
    ):
        row = identity(player_id, "batting")
        counts = {
            "games": fact.games,
            "games_started": fact.games_started,
            "plate_appearances": fact.plate_appearances,
            "at_bats": fact.at_bats,
            "runs": fact.runs,
            "hits": fact.hits,
            "doubles": fact.doubles,
            "triples": fact.triples,
            "home_runs": fact.home_runs,
            "runs_batted_in": fact.runs_batted_in,
            "walks": fact.walks,
            "hit_by_pitch": fact.hit_by_pitch,
            "strikeouts": fact.strikeouts,
            "sacrifice_flies": fact.sacrifice_flies,
            "sacrifice_hits": fact.sacrifice_hits,
            "stolen_bases": fact.stolen_bases,
        }
        row.update({
            "G": fact.games,
            "GS": fact.games_started,
            "PA": fact.plate_appearances,
            "AB": fact.at_bats,
            "H": fact.hits,
            "2B": fact.doubles,
            "3B": fact.triples,
            "HR": fact.home_runs,
            "RBI": fact.runs_batted_in,
            "R": fact.runs,
            "BB": fact.walks,
            "SO": fact.strikeouts,
            "SB": fact.stolen_bases,
        })
        for code, label in (
            ("avg", "AVG"),
            ("obp", "OBP"),
            ("slg", "SLG"),
            ("ops", "OPS"),
            ("iso", "ISO"),
            ("batting_bb_pct", "BB%"),
            ("batting_k_pct", "K%"),
            ("babip", "BABIP"),
            ("woba", "wOBA"),
        ):
            row[label] = metric_value(player_id, code, counts)
        add_batting_scope(row, player_id, "last_10", "Last 10")
        add_batting_scope(row, player_id, "conference", "Conference")
        hitters.append(row)

    pitchers = []
    for player_id, fact in sorted(
        pitching_by_player.items(),
        key=lambda pair: pair[1].innings_outs or 0,
        reverse=True,
    ):
        row = identity(player_id, "pitching")
        source = source_by_id.get(fact.source_record_id)
        raw = dict(source.raw_values) if source is not None else {}
        counts = {
            "appearances": fact.appearances,
            "games_started": fact.games_started,
            "innings_outs": fact.innings_outs,
            "hits_allowed": fact.hits_allowed,
            "earned_runs": fact.earned_runs,
            "walks": fact.walks,
            "strikeouts": fact.strikeouts,
            "home_runs_allowed": fact.home_runs_allowed,
            "hit_batters": fact.hit_batters,
            "batters_faced": fact.batters_faced,
            "opponent_at_bats": fact.opponent_at_bats,
            "opponent_sacrifice_flies": fact.opponent_sacrifice_flies,
        }
        row.update({
            "G": fact.appearances,
            "GS": fact.games_started,
            "IP": _innings_text(fact.innings_outs),
            "W": _first_present(raw, "W", "Wins"),
            "L": _first_present(raw, "L", "Losses"),
            "SV": _first_present(raw, "SV", "Saves"),
            "H": fact.hits_allowed,
            "ER": fact.earned_runs,
            "BB": fact.walks,
            "SO": fact.strikeouts,
            "HR": fact.home_runs_allowed,
            "BF": fact.batters_faced,
        })
        for code, label in (
            ("era", "ERA"),
            ("whip", "WHIP"),
            ("k_per_9", "K/9"),
            ("bb_per_9", "BB/9"),
            ("h_per_9", "H/9"),
            ("hr_per_9", "HR/9"),
            ("k_bb", "K/BB"),
            ("pitching_k_pct", "K%"),
            ("pitching_bb_pct", "BB%"),
            ("babip_allowed", "BABIP"),
            ("fip", "FIP"),
        ):
            row[label] = metric_value(player_id, code, counts)
        add_pitching_scope(row, player_id, "last_10", "Last 10")
        add_pitching_scope(row, player_id, "conference", "Conference")
        pitchers.append(row)

    return hitters, pitchers


def load_snapshot_workbook_source(session, snapshot_id):
    snapshot_id = uuid.UUID(str(snapshot_id))
    snapshot = session.get(ReportSnapshot, snapshot_id)
    if snapshot is None or snapshot.status not in {"ready", "partial"}:
        raise ArtifactStorageError("published snapshot is unavailable")
    report = session.get(Report, snapshot.report_id)
    team_season = session.get(TeamSeason, report.team_season_id) if report else None
    team = session.get(Team, team_season.team_id) if team_season else None
    season = session.get(Season, team_season.season_id) if team_season else None
    if report is None or team_season is None or team is None or season is None:
        raise ArtifactStorageError("snapshot identity is unavailable")
    rows = tuple(session.scalars(
        select(SourceRecord)
        .where(
            SourceRecord.organization_id == snapshot.organization_id,
            SourceRecord.snapshot_id == snapshot.id,
            SourceRecord.section.in_(("batting", "pitching")),
        )
        .order_by(SourceRecord.section, SourceRecord.row_ordinal)
    ))
    player_rows = tuple(session.scalars(
        select(PlayerTeamSeason)
        .where(
            PlayerTeamSeason.organization_id == snapshot.organization_id,
            PlayerTeamSeason.snapshot_id == snapshot.id,
        )
        .order_by(PlayerTeamSeason.display_name, PlayerTeamSeason.id)
    ))
    batting_facts = tuple(session.scalars(
        select(BattingFact).where(
            BattingFact.organization_id == snapshot.organization_id,
            BattingFact.snapshot_id == snapshot.id,
        )
    ))
    pitching_facts = tuple(session.scalars(
        select(PitchingFact).where(
            PitchingFact.organization_id == snapshot.organization_id,
            PitchingFact.snapshot_id == snapshot.id,
        )
    ))
    metric_rows = tuple(session.execute(
        select(MetricValue, MetricDefinition)
        .join(
            MetricDefinition,
            MetricDefinition.id == MetricValue.metric_definition_id,
        )
        .where(
            MetricValue.organization_id == snapshot.organization_id,
            MetricValue.snapshot_id == snapshot.id,
            MetricValue.player_team_season_id.is_not(None),
        )
    ))
    scopes = tuple(session.scalars(
        select(StatisticScope).where(
            StatisticScope.organization_id == snapshot.organization_id,
            StatisticScope.snapshot_id == snapshot.id,
            StatisticScope.scope_code.in_(("last_10", "conference")),
            StatisticScope.stat_type.in_(("batting", "pitching")),
        )
    ))
    scoped_batting = tuple(session.scalars(
        select(ScopedBattingFact).where(
            ScopedBattingFact.organization_id == snapshot.organization_id,
            ScopedBattingFact.snapshot_id == snapshot.id,
        )
    ))
    scoped_pitching = tuple(session.scalars(
        select(ScopedPitchingFact).where(
            ScopedPitchingFact.organization_id == snapshot.organization_id,
            ScopedPitchingFact.snapshot_id == snapshot.id,
        )
    ))
    if player_rows and (batting_facts or pitching_facts):
        hitters, pitchers = _project_snapshot_workbook_rows(
            player_rows,
            rows,
            batting_facts,
            pitching_facts,
            metric_rows,
            scopes,
            scoped_batting,
            scoped_pitching,
        )
    else:
        hitters, pitchers = _workbook_rows_from_source_records(rows)
    return SnapshotWorkbookSource(
        organization_id=snapshot.organization_id,
        report_id=report.id,
        snapshot_id=snapshot.id,
        source_team_id=team.source_team_id,
        team_name=team.name,
        season_year=season.year,
        published_at=snapshot.published_at,
        hitters=tuple(hitters),
        pitchers=tuple(pitchers),
        source_collected_at=snapshot.source_collected_at,
        snapshot_status=snapshot.status,
    )


def build_snapshot_workbook(source):
    """Build a saved version with the exact homepage workbook renderer."""
    subtitle = None
    if source.source_collected_at is not None:
        subtitle = (
            f"{source.season_year} SEASON · "
            f"PUBLISHED {source.published_at.astimezone(UTC).strftime('%b %d, %Y %H:%M UTC').upper()} · "
            f"SOURCE {source.source_collected_at.astimezone(UTC).strftime('%b %d, %Y %H:%M UTC').upper()} · "
            f"{source.snapshot_status.upper()} SNAPSHOT"
        )
    branding = workbook_branding(
        school_name=source.team_name,
        generated_on=source.published_at.date(),
        team_id=source.source_team_id,
        subtitle=subtitle,
    )
    payload = create_workbook(
        list(source.hitters),
        list(source.pitchers),
        branding=branding,
    ).getvalue()
    if not payload or len(payload) > MAX_ARTIFACT_BYTES:
        raise ArtifactStorageError("generated artifact is invalid")
    return payload


def ensure_snapshot_workbook_artifact(session, snapshot):
    existing = session.scalar(
        select(ReportArtifact).where(
            ReportArtifact.snapshot_id == snapshot.id,
            ReportArtifact.organization_id == snapshot.organization_id,
            ReportArtifact.artifact_type == "xlsx",
        )
    )
    if existing is not None:
        return existing
    artifact = ReportArtifact(
        organization_id=snapshot.organization_id,
        snapshot_id=snapshot.id,
        artifact_type="xlsx",
        status="pending",
        schema_version=ARTIFACT_SCHEMA_VERSION,
        attempt_count=0,
    )
    session.add(artifact)
    session.flush()
    return artifact


def ensure_missing_snapshot_artifacts(session_factory, *, limit=10):
    created = 0
    with session_factory.begin() as session:
        snapshots = tuple(session.scalars(
            select(ReportSnapshot)
            .outerjoin(
                ReportArtifact,
                and_(
                    ReportArtifact.snapshot_id == ReportSnapshot.id,
                    ReportArtifact.artifact_type == "xlsx",
                ),
            )
            .where(
                ReportSnapshot.status.in_(("ready", "partial")),
                ReportArtifact.id.is_(None),
            )
            .order_by(ReportSnapshot.published_at, ReportSnapshot.id)
            .limit(limit)
            .with_for_update(of=ReportSnapshot, skip_locked=True)
        ))
        for snapshot in snapshots:
            ensure_snapshot_workbook_artifact(session, snapshot)
            created += 1
    return created


def claim_artifact_build(
    session_factory,
    artifact_id,
    *,
    now=None,
    max_attempts=DEFAULT_MAX_ATTEMPTS,
    stale_after=DEFAULT_STALE_AFTER,
    retry_delay=DEFAULT_RETRY_DELAY,
):
    now = now or datetime.now(UTC)
    artifact_id = uuid.UUID(str(artifact_id))
    with session_factory.begin() as session:
        artifact = session.scalar(
            select(ReportArtifact)
            .where(ReportArtifact.id == artifact_id)
            .with_for_update()
        )
        if artifact is None or artifact.status == "ready":
            return None
        retryable = (
            artifact.status == "pending"
            or (
                artifact.status == "failed"
                and artifact.attempt_count < max_attempts
                and (
                    artifact.last_attempt_at is None
                    or artifact.last_attempt_at <= now - retry_delay
                )
            )
            or (
                artifact.status == "building"
                and artifact.attempt_count < max_attempts
                and artifact.last_attempt_at is not None
                and artifact.last_attempt_at <= now - stale_after
            )
        )
        if not retryable:
            return None
        artifact.status = "building"
        artifact.attempt_count += 1
        artifact.last_attempt_at = now
        artifact.failure_category = None
        return artifact.attempt_count


def build_claimed_snapshot_artifact(
    session_factory,
    artifact_id,
    attempt,
    *,
    store,
):
    artifact_id = uuid.UUID(str(artifact_id))
    with session_advisory_lock(session_factory, artifact_id) as claim:
        if claim is None:
            return "busy"
        sessions = claim.session_factory
        try:
            with sessions() as session:
                artifact = session.get(ReportArtifact, artifact_id)
                if artifact is None:
                    return "missing"
                if artifact.status == "ready":
                    return "ready"
                if artifact.status != "building" or artifact.attempt_count != attempt:
                    return "stale"
                source = load_snapshot_workbook_source(session, artifact.snapshot_id)
            payload = build_snapshot_workbook(source)
            digest = sha256(payload).hexdigest()
            key = artifact_object_key(
                source.organization_id,
                source.report_id,
                source.snapshot_id,
            )
            store.write_immutable(key, payload, digest)
            with sessions.begin() as session:
                artifact = session.scalar(
                    select(ReportArtifact)
                    .where(ReportArtifact.id == artifact_id)
                    .with_for_update()
                )
                if (
                    artifact is None
                    or artifact.status != "building"
                    or artifact.attempt_count != attempt
                ):
                    return "stale"
                artifact.status = "ready"
                artifact.schema_version = ARTIFACT_SCHEMA_VERSION
                artifact.sha256 = digest
                artifact.object_key = key
                artifact.failure_category = None
            return "ready"
        except Exception:
            with sessions.begin() as session:
                artifact = session.scalar(
                    select(ReportArtifact)
                    .where(ReportArtifact.id == artifact_id)
                    .with_for_update()
                )
                if (
                    artifact is not None
                    and artifact.status == "building"
                    and artifact.attempt_count == attempt
                ):
                    artifact.status = "failed"
                    artifact.failure_category = "storage"
            return "failed"


@dataclass(frozen=True)
class ArtifactDispatchResult:
    discovered: int = 0
    claimed: int = 0
    published: int = 0
    released: int = 0


def dispatch_pending_artifacts(
    session_factory,
    *,
    dispatch,
    limit=10,
    now=None,
):
    now = now or datetime.now(UTC)
    discovered = ensure_missing_snapshot_artifacts(session_factory, limit=limit)
    with session_factory() as session:
        candidates = tuple(session.scalars(
            select(ReportArtifact.id)
            .where(
                ReportArtifact.artifact_type == "xlsx",
                ReportArtifact.status != "ready",
                ReportArtifact.attempt_count < DEFAULT_MAX_ATTEMPTS,
                or_(
                    ReportArtifact.status == "pending",
                    and_(
                        ReportArtifact.status == "failed",
                        or_(
                            ReportArtifact.last_attempt_at.is_(None),
                            ReportArtifact.last_attempt_at <= now - DEFAULT_RETRY_DELAY,
                        ),
                    ),
                    and_(
                        ReportArtifact.status == "building",
                        ReportArtifact.last_attempt_at <= now - DEFAULT_STALE_AFTER,
                    ),
                ),
            )
            .order_by(ReportArtifact.created_at, ReportArtifact.id)
            .limit(limit)
        ))
    claims = []
    for artifact_id in candidates:
        attempt = claim_artifact_build(session_factory, artifact_id, now=now)
        if attempt is not None:
            claims.append((artifact_id, attempt))
    published = 0
    released = 0
    for artifact_id, attempt in claims:
        try:
            dispatch(str(artifact_id), attempt)
            published += 1
        except Exception:
            with session_factory.begin() as session:
                artifact = session.scalar(
                    select(ReportArtifact)
                    .where(ReportArtifact.id == artifact_id)
                    .with_for_update()
                )
                if (
                    artifact is not None
                    and artifact.status == "building"
                    and artifact.attempt_count == attempt
                ):
                    artifact.status = "failed"
                    artifact.failure_category = "queue"
                    released += 1
    return ArtifactDispatchResult(
        discovered=discovered,
        claimed=len(claims),
        published=published,
        released=released,
    )
