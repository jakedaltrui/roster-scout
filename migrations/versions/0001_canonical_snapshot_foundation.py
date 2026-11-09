"""Create the canonical snapshot foundation.

Revision ID: 0001
Revises: None
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def _uuid_primary_key():
    return sa.Column("id", sa.Uuid(), nullable=False)


def _created_at():
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        server_default=sa.text("now()"),
        nullable=False,
    )


def upgrade():
    op.create_table(
        "organizations",
        sa.Column("name", sa.String(length=200), nullable=False),
        _uuid_primary_key(),
        _created_at(),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "teams",
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("source_team_id", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        _uuid_primary_key(),
        _created_at(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source", "source_team_id", name="uq_teams_source_identity"),
    )
    op.create_table(
        "seasons",
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("source_season_id", sa.String(length=100), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        _uuid_primary_key(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source", "source_season_id", name="uq_seasons_source_identity"),
    )
    op.create_table(
        "team_seasons",
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("season_id", sa.Uuid(), nullable=False),
        sa.Column("source_season_team_id", sa.String(length=100), nullable=False),
        sa.Column("division", sa.String(length=16), nullable=False),
        sa.Column("batting_stat_category_id", sa.String(length=100), nullable=True),
        sa.Column("pitching_stat_category_id", sa.String(length=100), nullable=True),
        _uuid_primary_key(),
        sa.ForeignKeyConstraint(["season_id"], ["seasons.id"], name="fk_team_seasons_season"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], name="fk_team_seasons_team"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_season_team_id", name="uq_team_seasons_source_id"),
        sa.UniqueConstraint("team_id", "season_id", name="uq_team_seasons_team_season"),
    )
    op.create_table(
        "players",
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("source_player_id", sa.String(length=100), nullable=True),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        _uuid_primary_key(),
        _created_at(),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_players_authoritative_source_id",
        "players",
        ["source", "source_player_id"],
        unique=True,
        postgresql_where=sa.text("source_player_id IS NOT NULL"),
    )
    op.create_table(
        "reports",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("team_season_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        _uuid_primary_key(),
        _created_at(),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], name="fk_reports_organization"),
        sa.ForeignKeyConstraint(["team_season_id"], ["team_seasons.id"], name="fk_reports_team_season"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "organization_id", name="uq_reports_id_organization"),
        sa.UniqueConstraint(
            "organization_id",
            "team_season_id",
            name="uq_reports_organization_team_season",
        ),
    )
    op.create_table(
        "report_snapshots",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("report_id", sa.Uuid(), nullable=False),
        sa.Column("predecessor_snapshot_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="draft", nullable=False),
        sa.Column("schema_version", sa.String(length=32), nullable=False),
        sa.Column("source_collected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(length=200), nullable=True),
        _uuid_primary_key(),
        _created_at(),
        sa.CheckConstraint(
            "(status IN ('ready', 'partial') AND published_at IS NOT NULL) OR "
            "(status IN ('draft', 'failed') AND published_at IS NULL)",
            name="ck_report_snapshots_publication_time",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'ready', 'partial', 'failed')",
            name="ck_report_snapshots_status",
        ),
        sa.ForeignKeyConstraint(
            ["predecessor_snapshot_id", "report_id", "organization_id"],
            [
                "report_snapshots.id",
                "report_snapshots.report_id",
                "report_snapshots.organization_id",
            ],
            name="fk_report_snapshots_predecessor_lineage",
        ),
        sa.ForeignKeyConstraint(
            ["report_id", "organization_id"],
            ["reports.id", "reports.organization_id"],
            name="fk_report_snapshots_report_organization",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "organization_id", name="uq_report_snapshots_id_organization"),
        sa.UniqueConstraint(
            "id",
            "report_id",
            "organization_id",
            name="uq_report_snapshots_lineage_key",
        ),
    )
    op.create_table(
        "player_team_seasons",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("player_id", sa.Uuid(), nullable=False),
        sa.Column("team_season_id", sa.Uuid(), nullable=False),
        sa.Column("identity_status", sa.String(length=20), nullable=False),
        sa.Column("identity_key", sa.String(length=300), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("jersey_number", sa.String(length=20), nullable=True),
        sa.Column("position", sa.String(length=50), nullable=True),
        _uuid_primary_key(),
        sa.CheckConstraint(
            "identity_status IN ('authoritative', 'fallback', 'unresolved', 'conflict')",
            name="ck_player_team_seasons_identity_status",
        ),
        sa.ForeignKeyConstraint(
            ["player_id"], ["players.id"], name="fk_player_team_seasons_player"
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id", "organization_id"],
            ["report_snapshots.id", "report_snapshots.organization_id"],
            name="fk_player_team_seasons_snapshot_organization",
        ),
        sa.ForeignKeyConstraint(
            ["team_season_id"],
            ["team_seasons.id"],
            name="fk_player_team_seasons_team_season",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id",
            "snapshot_id",
            "organization_id",
            name="uq_player_team_seasons_snapshot_owner",
        ),
        sa.UniqueConstraint(
            "snapshot_id",
            "identity_key",
            name="uq_player_team_seasons_snapshot_identity",
        ),
    )
    op.create_table(
        "report_sections",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("section", sa.String(length=50), nullable=False),
        sa.Column("critical", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("row_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("verified_empty", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_category", sa.String(length=100), nullable=True),
        _uuid_primary_key(),
        sa.CheckConstraint("row_count >= 0", name="ck_report_sections_row_count_nonnegative"),
        sa.CheckConstraint(
            "status IN ('pending', 'complete', 'unavailable', 'failed')",
            name="ck_report_sections_status",
        ),
        sa.CheckConstraint(
            "NOT verified_empty OR row_count = 0",
            name="ck_report_sections_verified_empty",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id", "organization_id"],
            ["report_snapshots.id", "report_snapshots.organization_id"],
            name="fk_report_sections_snapshot_organization",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id",
            "snapshot_id",
            "organization_id",
            name="uq_report_sections_snapshot_owner",
        ),
        sa.UniqueConstraint(
            "snapshot_id", "section", name="uq_report_sections_snapshot_section"
        ),
    )
    op.create_table(
        "source_records",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("section", sa.String(length=50), nullable=False),
        sa.Column("row_ordinal", sa.Integer(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("source_team_id", sa.String(length=100), nullable=False),
        sa.Column("source_season_id", sa.String(length=100), nullable=False),
        sa.Column("source_season_team_id", sa.String(length=100), nullable=False),
        sa.Column("source_stat_category_id", sa.String(length=100), nullable=False),
        sa.Column("source_player_id", sa.String(length=100), nullable=True),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("parser_version", sa.String(length=32), nullable=False),
        sa.Column("schema_version", sa.String(length=32), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("raw_values", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        _uuid_primary_key(),
        sa.CheckConstraint("row_ordinal >= 0", name="ck_source_records_row_ordinal_nonnegative"),
        sa.CheckConstraint(
            "char_length(payload_sha256) = 64",
            name="ck_source_records_sha256_length",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id", "organization_id"],
            ["report_snapshots.id", "report_snapshots.organization_id"],
            name="fk_source_records_snapshot_organization",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id",
            "snapshot_id",
            "organization_id",
            name="uq_source_records_snapshot_owner",
        ),
        sa.UniqueConstraint(
            "snapshot_id",
            "section",
            "row_ordinal",
            name="uq_source_records_snapshot_row",
        ),
    )
    op.create_table(
        "batting_facts",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("player_team_season_id", sa.Uuid(), nullable=True),
        sa.Column("source_record_id", sa.Uuid(), nullable=True),
        sa.Column("at_bats", sa.Integer(), nullable=True),
        sa.Column("hits", sa.Integer(), nullable=True),
        sa.Column("walks", sa.Integer(), nullable=True),
        sa.Column("strikeouts", sa.Integer(), nullable=True),
        _uuid_primary_key(),
        sa.CheckConstraint(
            "at_bats IS NULL OR at_bats >= 0",
            name="ck_batting_facts_at_bats_nonnegative",
        ),
        sa.CheckConstraint(
            "hits IS NULL OR hits >= 0",
            name="ck_batting_facts_hits_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["player_team_season_id", "snapshot_id", "organization_id"],
            [
                "player_team_seasons.id",
                "player_team_seasons.snapshot_id",
                "player_team_seasons.organization_id",
            ],
            name="fk_batting_facts_player_snapshot_organization",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id", "organization_id"],
            ["report_snapshots.id", "report_snapshots.organization_id"],
            name="fk_batting_facts_snapshot_organization",
        ),
        sa.ForeignKeyConstraint(
            ["source_record_id", "snapshot_id", "organization_id"],
            [
                "source_records.id",
                "source_records.snapshot_id",
                "source_records.organization_id",
            ],
            name="fk_batting_facts_source_snapshot_organization",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "pitching_facts",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("player_team_season_id", sa.Uuid(), nullable=True),
        sa.Column("source_record_id", sa.Uuid(), nullable=True),
        sa.Column("innings_outs", sa.Integer(), nullable=True),
        sa.Column("hits_allowed", sa.Integer(), nullable=True),
        sa.Column("earned_runs", sa.Integer(), nullable=True),
        sa.Column("walks", sa.Integer(), nullable=True),
        sa.Column("strikeouts", sa.Integer(), nullable=True),
        _uuid_primary_key(),
        sa.CheckConstraint(
            "innings_outs IS NULL OR innings_outs >= 0",
            name="ck_pitching_facts_innings_outs_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["player_team_season_id", "snapshot_id", "organization_id"],
            [
                "player_team_seasons.id",
                "player_team_seasons.snapshot_id",
                "player_team_seasons.organization_id",
            ],
            name="fk_pitching_facts_player_snapshot_organization",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id", "organization_id"],
            ["report_snapshots.id", "report_snapshots.organization_id"],
            name="fk_pitching_facts_snapshot_organization",
        ),
        sa.ForeignKeyConstraint(
            ["source_record_id", "snapshot_id", "organization_id"],
            [
                "source_records.id",
                "source_records.snapshot_id",
                "source_records.organization_id",
            ],
            name="fk_pitching_facts_source_snapshot_organization",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "metric_definitions",
        sa.Column("code", sa.String(length=100), nullable=False),
        sa.Column("version", sa.String(length=50), nullable=False),
        sa.Column("formula", sa.Text(), nullable=False),
        sa.Column("weight_set_version", sa.String(length=100), nullable=True),
        _uuid_primary_key(),
        _created_at(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", "version", name="uq_metric_definitions_code_version"),
    )
    op.create_table(
        "metric_values",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("metric_definition_id", sa.Uuid(), nullable=False),
        sa.Column("player_team_season_id", sa.Uuid(), nullable=True),
        sa.Column("value", sa.Numeric(precision=20, scale=8), nullable=True),
        sa.Column("unavailable_reason", sa.Text(), nullable=True),
        _uuid_primary_key(),
        sa.CheckConstraint(
            "value IS NOT NULL OR unavailable_reason IS NOT NULL",
            name="ck_metric_values_value_or_reason",
        ),
        sa.ForeignKeyConstraint(
            ["metric_definition_id"],
            ["metric_definitions.id"],
            name="fk_metric_values_definition",
        ),
        sa.ForeignKeyConstraint(
            ["player_team_season_id", "snapshot_id", "organization_id"],
            [
                "player_team_seasons.id",
                "player_team_seasons.snapshot_id",
                "player_team_seasons.organization_id",
            ],
            name="fk_metric_values_player_snapshot_organization",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id", "organization_id"],
            ["report_snapshots.id", "report_snapshots.organization_id"],
            name="fk_metric_values_snapshot_organization",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "report_jobs",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("report_id", sa.Uuid(), nullable=False),
        sa.Column("result_snapshot_id", sa.Uuid(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("failure_category", sa.String(length=100), nullable=True),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        _uuid_primary_key(),
        _created_at(),
        sa.CheckConstraint(
            "attempt_count >= 0", name="ck_report_jobs_attempt_count_nonnegative"
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed', 'blocked')",
            name="ck_report_jobs_status",
        ),
        sa.ForeignKeyConstraint(
            ["report_id", "organization_id"],
            ["reports.id", "reports.organization_id"],
            name="fk_report_jobs_report_organization",
        ),
        sa.ForeignKeyConstraint(
            ["result_snapshot_id", "organization_id"],
            ["report_snapshots.id", "report_snapshots.organization_id"],
            name="fk_report_jobs_result_snapshot_organization",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("result_snapshot_id"),
        sa.UniqueConstraint("id", "organization_id", name="uq_report_jobs_id_organization"),
        sa.UniqueConstraint(
            "organization_id",
            "idempotency_key",
            name="uq_report_jobs_organization_idempotency",
        ),
    )
    op.create_table(
        "report_artifacts",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("artifact_type", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("schema_version", sa.String(length=32), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("object_key", sa.Text(), nullable=True),
        _uuid_primary_key(),
        _created_at(),
        sa.CheckConstraint(
            "artifact_type IN ('xlsx', 'power_bi')",
            name="ck_report_artifacts_type",
        ),
        sa.CheckConstraint(
            "sha256 IS NULL OR char_length(sha256) = 64",
            name="ck_report_artifacts_sha256_length",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'building', 'ready', 'failed')",
            name="ck_report_artifacts_status",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id", "organization_id"],
            ["report_snapshots.id", "report_snapshots.organization_id"],
            name="fk_report_artifacts_snapshot_organization",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "snapshot_id", "artifact_type", name="uq_report_artifacts_snapshot_type"
        ),
    )
    op.create_table(
        "failure_evidence",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("compressed_html", sa.LargeBinary(), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True),
        _uuid_primary_key(),
        _created_at(),
        sa.CheckConstraint(
            "purged_at IS NULL OR compressed_html IS NULL",
            name="ck_failure_evidence_purged_payload",
        ),
        sa.CheckConstraint(
            "char_length(sha256) = 64",
            name="ck_failure_evidence_sha256_length",
        ),
        sa.ForeignKeyConstraint(
            ["job_id", "organization_id"],
            ["report_jobs.id", "report_jobs.organization_id"],
            name="fk_failure_evidence_job_organization",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.execute("""
        CREATE FUNCTION reject_published_snapshot_mutation() RETURNS trigger AS $$
        BEGIN
            IF OLD.status IN ('ready', 'partial') THEN
                RAISE EXCEPTION 'published snapshots are immutable';
            END IF;
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER trg_report_snapshots_immutable
        BEFORE UPDATE OR DELETE ON report_snapshots
        FOR EACH ROW EXECUTE FUNCTION reject_published_snapshot_mutation()
    """)
    op.execute("""
        CREATE FUNCTION reject_published_snapshot_child_mutation() RETURNS trigger AS $$
        DECLARE target_snapshot uuid;
        DECLARE target_status varchar;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                target_snapshot := OLD.snapshot_id;
            ELSE
                target_snapshot := NEW.snapshot_id;
            END IF;
            SELECT status INTO target_status FROM report_snapshots WHERE id = target_snapshot;
            IF target_status IN ('ready', 'partial') THEN
                RAISE EXCEPTION 'published snapshot rows are immutable';
            END IF;
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$ LANGUAGE plpgsql
    """)
    for table in (
        "player_team_seasons",
        "report_sections",
        "source_records",
        "batting_facts",
        "pitching_facts",
        "metric_values",
    ):
        op.execute(f"""
            CREATE TRIGGER trg_{table}_immutable
            BEFORE INSERT OR UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION reject_published_snapshot_child_mutation()
        """)
    op.execute("""
        CREATE FUNCTION reject_nonfailure_evidence() RETURNS trigger AS $$
        DECLARE job_status varchar;
        BEGIN
            SELECT status INTO job_status FROM report_jobs WHERE id = NEW.job_id;
            IF job_status NOT IN ('failed', 'blocked') THEN
                RAISE EXCEPTION 'failure evidence requires a failed or blocked job';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER trg_failure_evidence_failed_jobs_only
        BEFORE INSERT OR UPDATE ON failure_evidence
        FOR EACH ROW EXECUTE FUNCTION reject_nonfailure_evidence()
    """)


def downgrade():
    op.execute("DROP FUNCTION IF EXISTS reject_nonfailure_evidence() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS reject_published_snapshot_child_mutation() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS reject_published_snapshot_mutation() CASCADE")
    for table in (
        "failure_evidence",
        "report_artifacts",
        "report_jobs",
        "metric_values",
        "metric_definitions",
        "pitching_facts",
        "batting_facts",
        "source_records",
        "report_sections",
        "player_team_seasons",
        "report_snapshots",
        "reports",
    ):
        op.drop_table(table)
    op.drop_index("uq_players_authoritative_source_id", table_name="players")
    for table in ("players", "team_seasons", "seasons", "teams", "organizations"):
        op.drop_table(table)
