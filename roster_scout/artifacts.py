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
    Report,
    ReportArtifact,
    ReportSnapshot,
    Season,
    SourceRecord,
    Team,
    TeamSeason,
)
from .workbooks import create_workbook, workbook_branding


ARTIFACT_SCHEMA_VERSION = "basic-xlsx-v1"
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
    if parts[7] != f"roster-scout-{ARTIFACT_SCHEMA_VERSION}.xlsx":
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
    )


def build_snapshot_workbook(source):
    """Build a saved version with the exact homepage workbook renderer."""
    branding = workbook_branding(
        school_name=source.team_name,
        generated_on=source.published_at.date(),
        team_id=source.source_team_id,
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
