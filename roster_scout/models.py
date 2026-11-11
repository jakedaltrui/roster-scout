"""Canonical organization-owned snapshot schema."""

import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .enums import (
    ARTIFACT_STATUSES,
    ARTIFACT_TYPES,
    IDENTITY_STATUSES,
    JOB_STATUSES,
    SECTION_STATUSES,
    SNAPSHOT_STATUSES,
)


AUTHORITATIVE_SOURCE_PLAYER_TRIGGER_NAMES = (
    "trg_source_records_authoritative_source_player",
    "trg_players_authoritative_source_player",
    "trg_player_team_seasons_authoritative_source_player",
)


def _allowed(column, values):
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


class Base(DeclarativeBase):
    pass


class UUIDPrimaryKey:
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


class Timestamped:
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Organization(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "organizations"
    name: Mapped[str] = mapped_column(String(200), nullable=False)


class Team(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "teams"
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_team_id: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    __table_args__ = (UniqueConstraint("source", "source_team_id", name="uq_teams_source_identity"),)


class Season(UUIDPrimaryKey, Base):
    __tablename__ = "seasons"
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_season_id: Mapped[str] = mapped_column(String(100), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    __table_args__ = (UniqueConstraint("source", "source_season_id", name="uq_seasons_source_identity"),)


class TeamSeason(UUIDPrimaryKey, Base):
    __tablename__ = "team_seasons"
    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teams.id", name="fk_team_seasons_team"), nullable=False)
    season_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("seasons.id", name="fk_team_seasons_season"), nullable=False)
    source_season_team_id: Mapped[str] = mapped_column(String(100), nullable=False)
    division: Mapped[str] = mapped_column(String(16), nullable=False)
    batting_stat_category_id: Mapped[str | None] = mapped_column(String(100))
    pitching_stat_category_id: Mapped[str | None] = mapped_column(String(100))
    __table_args__ = (
        UniqueConstraint("team_id", "season_id", name="uq_team_seasons_team_season"),
        UniqueConstraint("source_season_team_id", name="uq_team_seasons_source_id"),
    )


class Player(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "players"
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_player_id: Mapped[str | None] = mapped_column(String(100))
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    __table_args__ = (
        Index(
            "uq_players_authoritative_source_id",
            "source",
            "source_player_id",
            unique=True,
            postgresql_where=text("source_player_id IS NOT NULL"),
        ),
        {
            "info": {
                "database_triggers": (
                    "trg_players_authoritative_source_player",
                )
            }
        },
    )


class Report(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "reports"
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", name="fk_reports_organization"), nullable=False)
    team_season_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("team_seasons.id", name="fk_reports_team_season"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    __table_args__ = (
        UniqueConstraint("id", "organization_id", name="uq_reports_id_organization"),
        UniqueConstraint("organization_id", "team_season_id", name="uq_reports_organization_team_season"),
    )


class ReportSnapshot(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "report_snapshots"
    organization_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    report_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    predecessor_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft", server_default="draft")
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    source_collected_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str | None] = mapped_column(String(200))
    __table_args__ = (
        UniqueConstraint("id", "organization_id", name="uq_report_snapshots_id_organization"),
        UniqueConstraint("id", "report_id", "organization_id", name="uq_report_snapshots_lineage_key"),
        ForeignKeyConstraint(
            ["report_id", "organization_id"], ["reports.id", "reports.organization_id"],
            name="fk_report_snapshots_report_organization",
        ),
        ForeignKeyConstraint(
            ["predecessor_snapshot_id", "report_id", "organization_id"],
            ["report_snapshots.id", "report_snapshots.report_id", "report_snapshots.organization_id"],
            name="fk_report_snapshots_predecessor_lineage",
        ),
        CheckConstraint(_allowed("status", SNAPSHOT_STATUSES), name="ck_report_snapshots_status"),
        CheckConstraint(
            "(status IN ('ready', 'partial') AND published_at IS NOT NULL) OR "
            "(status IN ('draft', 'failed') AND published_at IS NULL)",
            name="ck_report_snapshots_publication_time",
        ),
    )


class PlayerTeamSeason(UUIDPrimaryKey, Base):
    __tablename__ = "player_team_seasons"
    organization_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    player_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("players.id", name="fk_player_team_seasons_player"), nullable=False)
    team_season_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("team_seasons.id", name="fk_player_team_seasons_team_season"), nullable=False)
    identity_status: Mapped[str] = mapped_column(String(20), nullable=False)
    identity_key: Mapped[str] = mapped_column(String(300), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    jersey_number: Mapped[str | None] = mapped_column(String(20))
    position: Mapped[str | None] = mapped_column(String(50))
    __table_args__ = (
        UniqueConstraint("id", "snapshot_id", "organization_id", name="uq_player_team_seasons_snapshot_owner"),
        UniqueConstraint("snapshot_id", "identity_key", name="uq_player_team_seasons_snapshot_identity"),
        ForeignKeyConstraint(
            ["snapshot_id", "organization_id"],
            ["report_snapshots.id", "report_snapshots.organization_id"],
            name="fk_player_team_seasons_snapshot_organization",
        ),
        CheckConstraint(_allowed("identity_status", IDENTITY_STATUSES), name="ck_player_team_seasons_identity_status"),
        {
            "info": {
                "database_triggers": (
                    "trg_player_team_seasons_authoritative_source_player",
                )
            }
        },
    )


class ReportSection(UUIDPrimaryKey, Base):
    __tablename__ = "report_sections"
    organization_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    section: Mapped[str] = mapped_column(String(50), nullable=False)
    critical: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", server_default="pending")
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    verified_empty: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    collected_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    failure_category: Mapped[str | None] = mapped_column(String(100))
    job_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    source_url: Mapped[str | None] = mapped_column(Text)
    source_team_id: Mapped[str | None] = mapped_column(String(100))
    source_season_id: Mapped[str | None] = mapped_column(String(100))
    source_season_team_id: Mapped[str | None] = mapped_column(String(100))
    source_stat_category_id: Mapped[str | None] = mapped_column(String(100))
    schema_version: Mapped[str | None] = mapped_column(String(32))
    parser_version: Mapped[str | None] = mapped_column(String(32))
    __table_args__ = (
        UniqueConstraint("snapshot_id", "section", name="uq_report_sections_snapshot_section"),
        UniqueConstraint("id", "snapshot_id", "organization_id", name="uq_report_sections_snapshot_owner"),
        ForeignKeyConstraint(
            ["snapshot_id", "organization_id"], ["report_snapshots.id", "report_snapshots.organization_id"],
            name="fk_report_sections_snapshot_organization",
        ),
        ForeignKeyConstraint(
            ["job_id", "organization_id"], ["report_jobs.id", "report_jobs.organization_id"],
            name="fk_report_sections_job_organization",
        ),
        CheckConstraint(_allowed("status", SECTION_STATUSES), name="ck_report_sections_status"),
        CheckConstraint("row_count >= 0", name="ck_report_sections_row_count_nonnegative"),
        CheckConstraint("NOT verified_empty OR row_count = 0", name="ck_report_sections_verified_empty"),
    )


class SourceRecord(UUIDPrimaryKey, Base):
    __tablename__ = "source_records"
    organization_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    player_team_season_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    section: Mapped[str] = mapped_column(String(50), nullable=False)
    row_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_team_id: Mapped[str] = mapped_column(String(100), nullable=False)
    source_season_id: Mapped[str] = mapped_column(String(100), nullable=False)
    source_season_team_id: Mapped[str] = mapped_column(String(100), nullable=False)
    source_stat_category_id: Mapped[str] = mapped_column(String(100), nullable=False)
    source_player_id: Mapped[str | None] = mapped_column(String(100))
    collected_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(32), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_values: Mapped[dict] = mapped_column(JSONB, nullable=False)
    __table_args__ = (
        UniqueConstraint("id", "snapshot_id", "organization_id", name="uq_source_records_snapshot_owner"),
        UniqueConstraint(
            "id",
            "player_team_season_id",
            "snapshot_id",
            "organization_id",
            name="uq_source_records_identity_owner",
        ),
        UniqueConstraint("snapshot_id", "section", "row_ordinal", name="uq_source_records_snapshot_row"),
        ForeignKeyConstraint(
            ["snapshot_id", "organization_id"], ["report_snapshots.id", "report_snapshots.organization_id"],
            name="fk_source_records_snapshot_organization",
        ),
        ForeignKeyConstraint(
            ["player_team_season_id", "snapshot_id", "organization_id"],
            [
                "player_team_seasons.id",
                "player_team_seasons.snapshot_id",
                "player_team_seasons.organization_id",
            ],
            name="fk_source_records_player_snapshot_organization",
        ),
        CheckConstraint("row_ordinal >= 0", name="ck_source_records_row_ordinal_nonnegative"),
        CheckConstraint("char_length(payload_sha256) = 64", name="ck_source_records_sha256_length"),
        {
            "info": {
                "database_triggers": (
                    "trg_source_records_authoritative_source_player",
                )
            }
        },
    )


class BattingFact(UUIDPrimaryKey, Base):
    __tablename__ = "batting_facts"
    organization_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    player_team_season_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    source_record_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    at_bats: Mapped[int | None] = mapped_column(Integer)
    hits: Mapped[int | None] = mapped_column(Integer)
    walks: Mapped[int | None] = mapped_column(Integer)
    strikeouts: Mapped[int | None] = mapped_column(Integer)
    __table_args__ = (
        ForeignKeyConstraint(
            ["snapshot_id", "organization_id"], ["report_snapshots.id", "report_snapshots.organization_id"],
            name="fk_batting_facts_snapshot_organization",
        ),
        ForeignKeyConstraint(
            ["player_team_season_id", "snapshot_id", "organization_id"],
            ["player_team_seasons.id", "player_team_seasons.snapshot_id", "player_team_seasons.organization_id"],
            name="fk_batting_facts_player_snapshot_organization",
        ),
        ForeignKeyConstraint(
            [
                "source_record_id",
                "player_team_season_id",
                "snapshot_id",
                "organization_id",
            ],
            [
                "source_records.id",
                "source_records.player_team_season_id",
                "source_records.snapshot_id",
                "source_records.organization_id",
            ],
            name="fk_batting_facts_source_identity",
        ),
        CheckConstraint("at_bats IS NULL OR at_bats >= 0", name="ck_batting_facts_at_bats_nonnegative"),
        CheckConstraint("hits IS NULL OR hits >= 0", name="ck_batting_facts_hits_nonnegative"),
        CheckConstraint("walks IS NULL OR walks >= 0", name="ck_batting_facts_walks_nonnegative"),
        CheckConstraint(
            "strikeouts IS NULL OR strikeouts >= 0",
            name="ck_batting_facts_strikeouts_nonnegative",
        ),
    )


class PitchingFact(UUIDPrimaryKey, Base):
    __tablename__ = "pitching_facts"
    organization_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    player_team_season_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    source_record_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    innings_outs: Mapped[int | None] = mapped_column(Integer)
    hits_allowed: Mapped[int | None] = mapped_column(Integer)
    earned_runs: Mapped[int | None] = mapped_column(Integer)
    walks: Mapped[int | None] = mapped_column(Integer)
    strikeouts: Mapped[int | None] = mapped_column(Integer)
    __table_args__ = (
        ForeignKeyConstraint(
            ["snapshot_id", "organization_id"], ["report_snapshots.id", "report_snapshots.organization_id"],
            name="fk_pitching_facts_snapshot_organization",
        ),
        ForeignKeyConstraint(
            ["player_team_season_id", "snapshot_id", "organization_id"],
            ["player_team_seasons.id", "player_team_seasons.snapshot_id", "player_team_seasons.organization_id"],
            name="fk_pitching_facts_player_snapshot_organization",
        ),
        ForeignKeyConstraint(
            [
                "source_record_id",
                "player_team_season_id",
                "snapshot_id",
                "organization_id",
            ],
            [
                "source_records.id",
                "source_records.player_team_season_id",
                "source_records.snapshot_id",
                "source_records.organization_id",
            ],
            name="fk_pitching_facts_source_identity",
        ),
        CheckConstraint("innings_outs IS NULL OR innings_outs >= 0", name="ck_pitching_facts_innings_outs_nonnegative"),
        CheckConstraint(
            "hits_allowed IS NULL OR hits_allowed >= 0",
            name="ck_pitching_facts_hits_allowed_nonnegative",
        ),
        CheckConstraint(
            "earned_runs IS NULL OR earned_runs >= 0",
            name="ck_pitching_facts_earned_runs_nonnegative",
        ),
        CheckConstraint("walks IS NULL OR walks >= 0", name="ck_pitching_facts_walks_nonnegative"),
        CheckConstraint(
            "strikeouts IS NULL OR strikeouts >= 0",
            name="ck_pitching_facts_strikeouts_nonnegative",
        ),
    )


class MetricDefinition(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "metric_definitions"
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    formula: Mapped[str] = mapped_column(Text, nullable=False)
    weight_set_version: Mapped[str | None] = mapped_column(String(100))
    __table_args__ = (UniqueConstraint("code", "version", name="uq_metric_definitions_code_version"),)


class MetricValue(UUIDPrimaryKey, Base):
    __tablename__ = "metric_values"
    organization_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    metric_definition_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("metric_definitions.id", name="fk_metric_values_definition"), nullable=False)
    player_team_season_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    value: Mapped[object | None] = mapped_column(Numeric(20, 8))
    unavailable_reason: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        ForeignKeyConstraint(
            ["snapshot_id", "organization_id"], ["report_snapshots.id", "report_snapshots.organization_id"],
            name="fk_metric_values_snapshot_organization",
        ),
        ForeignKeyConstraint(
            ["player_team_season_id", "snapshot_id", "organization_id"],
            ["player_team_seasons.id", "player_team_seasons.snapshot_id", "player_team_seasons.organization_id"],
            name="fk_metric_values_player_snapshot_organization",
        ),
        CheckConstraint("value IS NOT NULL OR unavailable_reason IS NOT NULL", name="ck_metric_values_value_or_reason"),
    )


class ReportJob(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "report_jobs"
    organization_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    report_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    result_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, unique=True)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued", server_default="queued")
    failure_category: Mapped[str | None] = mapped_column(String(100))
    failure_message: Mapped[str | None] = mapped_column(String(200))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3, server_default="3")
    dispatch_generation: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    started_generation: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    enqueued_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    broker_message_id: Mapped[str | None] = mapped_column(String(100))
    started_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("id", "organization_id", name="uq_report_jobs_id_organization"),
        UniqueConstraint("organization_id", "idempotency_key", name="uq_report_jobs_organization_idempotency"),
        ForeignKeyConstraint(
            ["report_id", "organization_id"], ["reports.id", "reports.organization_id"],
            name="fk_report_jobs_report_organization",
        ),
        ForeignKeyConstraint(
            ["result_snapshot_id", "organization_id"],
            ["report_snapshots.id", "report_snapshots.organization_id"],
            name="fk_report_jobs_result_snapshot_organization",
        ),
        CheckConstraint(_allowed("status", JOB_STATUSES), name="ck_report_jobs_status"),
        CheckConstraint("attempt_count >= 0", name="ck_report_jobs_attempt_count_nonnegative"),
        CheckConstraint("max_attempts BETWEEN 1 AND 10", name="ck_report_jobs_max_attempts_bounded"),
        CheckConstraint(
            "dispatch_generation >= 1",
            name="ck_report_jobs_dispatch_generation_positive",
        ),
        CheckConstraint(
            "started_generation >= 0",
            name="ck_report_jobs_started_generation_nonnegative",
        ),
        CheckConstraint(
            "started_generation <= dispatch_generation",
            name="ck_report_jobs_started_generation_current",
        ),
    )


class ReportJobDispatch(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "report_job_dispatch_outbox"
    organization_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    job_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending"
    )
    message_id: Mapped[str] = mapped_column(
        String(100), nullable=False, default=lambda: str(uuid.uuid4())
    )
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    lease_token: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    leased_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    publish_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    acknowledged_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    redispatch_after: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    __table_args__ = (
        ForeignKeyConstraint(
            ["job_id", "organization_id"],
            ["report_jobs.id", "report_jobs.organization_id"],
            name="fk_report_job_dispatch_job_organization",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            _allowed(
                "status", ("pending", "leased", "acknowledged", "closed")
            ),
            name="ck_report_job_dispatch_status",
        ),
        CheckConstraint(
            "publish_attempts >= 0",
            name="ck_report_job_dispatch_publish_attempts_nonnegative",
        ),
        CheckConstraint(
            "generation >= 1",
            name="ck_report_job_dispatch_generation_positive",
        ),
        CheckConstraint(
            "(status = 'leased' AND lease_token IS NOT NULL "
            "AND lease_expires_at IS NOT NULL) OR "
            "(status <> 'leased' AND lease_token IS NULL "
            "AND lease_expires_at IS NULL)",
            name="ck_report_job_dispatch_lease_state",
        ),
        Index(
            "uq_report_job_dispatch_active_job",
            "job_id",
            unique=True,
            postgresql_where=text(
                "status IN ('pending', 'leased', 'acknowledged')"
            ),
        ),
        Index(
            "ix_report_job_dispatch_due",
            "status",
            "next_attempt_at",
            "lease_expires_at",
        ),
        Index(
            "ix_report_job_dispatch_organization", "organization_id", "job_id"
        ),
    )


class ReportArtifact(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "report_artifacts"
    organization_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    artifact_type: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", server_default="pending")
    schema_version: Mapped[str | None] = mapped_column(String(32))
    sha256: Mapped[str | None] = mapped_column(String(64))
    object_key: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        UniqueConstraint("snapshot_id", "artifact_type", name="uq_report_artifacts_snapshot_type"),
        ForeignKeyConstraint(
            ["snapshot_id", "organization_id"], ["report_snapshots.id", "report_snapshots.organization_id"],
            name="fk_report_artifacts_snapshot_organization",
        ),
        CheckConstraint(_allowed("artifact_type", ARTIFACT_TYPES), name="ck_report_artifacts_type"),
        CheckConstraint(_allowed("status", ARTIFACT_STATUSES), name="ck_report_artifacts_status"),
        CheckConstraint("sha256 IS NULL OR char_length(sha256) = 64", name="ck_report_artifacts_sha256_length"),
    )


class FailureEvidence(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "failure_evidence"
    organization_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    job_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    compressed_html: Mapped[bytes | None] = mapped_column(LargeBinary)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    section: Mapped[str] = mapped_column(String(50), nullable=False, default="unknown", server_default="unknown")
    source_url: Mapped[str | None] = mapped_column(Text)
    failure_category: Mapped[str] = mapped_column(String(100), nullable=False, default="unknown", server_default="unknown")
    collected_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    purged_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        ForeignKeyConstraint(
            ["job_id", "organization_id"], ["report_jobs.id", "report_jobs.organization_id"],
            name="fk_failure_evidence_job_organization",
        ),
        CheckConstraint("char_length(sha256) = 64", name="ck_failure_evidence_sha256_length"),
        CheckConstraint("purged_at IS NULL OR compressed_html IS NULL", name="ck_failure_evidence_purged_payload"),
        CheckConstraint(
            "expires_at > collected_at "
            "AND expires_at <= collected_at + INTERVAL '28 days'",
            name="ck_failure_evidence_retention_window",
        ),
    )


class ProductUsageEvent(UUIDPrimaryKey, Base):
    """Minimal aggregate-only event ledger; never stores fallback identity data."""

    __tablename__ = "product_usage_events"
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    event_key: Mapped[str] = mapped_column(String(200), nullable=False)
    occurred_at: Mapped[object] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    actor_account_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "organizations.id",
            name="fk_product_usage_events_organization",
        )
    )
    __table_args__ = (
        UniqueConstraint(
            "event_type",
            "event_key",
            name="uq_product_usage_events_type_key",
        ),
        CheckConstraint(
            _allowed(
                "event_type",
                ("authenticated_use", "workbook_download_served"),
            ),
            name="ck_product_usage_events_type",
        ),
        CheckConstraint(
            "(actor_account_id IS NULL) = (organization_id IS NULL)",
            name="ck_product_usage_events_actor_scope",
        ),
        CheckConstraint(
            "event_type <> 'authenticated_use' "
            "OR (actor_account_id IS NOT NULL AND organization_id IS NOT NULL)",
            name="ck_product_usage_events_authenticated_scope",
        ),
        Index("ix_product_usage_events_occurred_at", "occurred_at"),
        Index(
            "ix_product_usage_events_organization_occurred_at",
            "organization_id",
            "occurred_at",
        ),
    )


class EvidencePurgeState(Base):
    __tablename__ = "evidence_purge_state"
    singleton_key: Mapped[int] = mapped_column(
        SmallInteger,
        primary_key=True,
        default=1,
        server_default="1",
    )
    last_success_at: Mapped[object] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    last_purged_count: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[object] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    __table_args__ = (
        CheckConstraint(
            "singleton_key = 1",
            name="ck_evidence_purge_state_singleton",
        ),
        CheckConstraint(
            "last_purged_count >= 0",
            name="ck_evidence_purge_state_count_nonnegative",
        ),
    )
