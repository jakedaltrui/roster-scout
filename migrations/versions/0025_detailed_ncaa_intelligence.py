"""Add auditable detailed NCAA season metrics.

Revision ID: 0025
Revises: 0024
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


BATTING_COUNT_COLUMNS = (
    "games",
    "games_started",
    "plate_appearances",
    "runs",
    "doubles",
    "triples",
    "home_runs",
    "runs_batted_in",
    "hit_by_pitch",
    "sacrifice_flies",
    "sacrifice_hits",
    "stolen_bases",
    "caught_stealing",
    "total_bases",
)
PITCHING_COUNT_COLUMNS = (
    "appearances",
    "games_started",
    "runs_allowed",
    "home_runs_allowed",
    "hit_batters",
    "batters_faced",
    "opponent_at_bats",
    "opponent_sacrifice_flies",
)


def _add_count_columns(table_name, columns):
    for column in columns:
        op.add_column(table_name, sa.Column(column, sa.Integer(), nullable=True))
        op.create_check_constraint(
            f"ck_{table_name}_{column}_nonnegative",
            table_name,
            f"{column} IS NULL OR {column} >= 0",
        )


def _drop_count_columns(table_name, columns):
    for column in reversed(columns):
        op.drop_constraint(
            f"ck_{table_name}_{column}_nonnegative",
            table_name,
            type_="check",
        )
        op.drop_column(table_name, column)


def _history_owner_constraints(table_name, *, source_record=False):
    constraints = [
        sa.ForeignKeyConstraint(
            ["snapshot_id", "organization_id"],
            ["report_snapshots.id", "report_snapshots.organization_id"],
            name=f"fk_{table_name}_snapshot_organization",
        ),
        sa.ForeignKeyConstraint(
            ["player_team_season_id", "snapshot_id", "organization_id"],
            [
                "player_team_seasons.id", "player_team_seasons.snapshot_id",
                "player_team_seasons.organization_id",
            ],
            name=f"fk_{table_name}_player_snapshot_organization",
        ),
    ]
    if source_record:
        constraints.append(sa.ForeignKeyConstraint(
            [
                "source_record_id", "player_team_season_id", "snapshot_id",
                "organization_id",
            ],
            [
                "source_records.id", "source_records.player_team_season_id",
                "source_records.snapshot_id", "source_records.organization_id",
            ],
            name=f"fk_{table_name}_source_identity",
        ))
    return tuple(constraints)


def _create_player_history_tables():
    op.create_table(
        "career_season_facts",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("player_team_season_id", sa.Uuid(), nullable=False),
        sa.Column("source_record_id", sa.Uuid(), nullable=False),
        sa.Column("row_id", sa.String(length=64), nullable=False),
        sa.Column("stat_type", sa.String(length=16), nullable=False),
        sa.Column("season_label", sa.String(length=32), nullable=False),
        sa.Column("source_season_id", sa.String(length=100)),
        sa.Column("school_name", sa.String(length=200), nullable=False),
        sa.Column("source_season_team_id", sa.String(length=100)),
        sa.Column("division", sa.String(length=16)),
        sa.Column("source_stat_category_id", sa.String(length=100), nullable=False),
        sa.Column("counts_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("rates_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        *(sa.Column(column, sa.Integer()) for column in (
            "at_bats", "hits", "innings_outs", "earned_runs", "putouts",
            "assists", "errors", "total_chances",
        )),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "stat_type IN ('batting', 'pitching', 'fielding')",
            name="ck_career_season_facts_stat_type",
        ),
        sa.CheckConstraint(
            "division IS NULL OR division IN ('d1', 'd2', 'd3')",
            name="ck_career_season_facts_division",
        ),
        sa.CheckConstraint(
            "char_length(row_id) = 64", name="ck_career_season_facts_row_id_length"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(counts_json) = 'object'",
            name="ck_career_season_facts_counts_object",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(rates_json) = 'object'",
            name="ck_career_season_facts_rates_object",
        ),
        *(sa.CheckConstraint(
            f"{column} IS NULL OR {column} >= 0",
            name=f"ck_career_season_facts_{column}_nonnegative",
        ) for column in (
            "at_bats", "hits", "innings_outs", "earned_runs", "putouts",
            "assists", "errors", "total_chances",
        )),
        sa.CheckConstraint(
            "hits IS NULL OR at_bats IS NULL OR hits <= at_bats",
            name="ck_career_season_facts_hits_lte_at_bats",
        ),
        *_history_owner_constraints("career_season_facts", source_record=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "snapshot_id", "player_team_season_id", "stat_type", "row_id",
            name="uq_career_season_facts_snapshot_player_type_row",
        ),
    )

    op.create_table(
        "career_aggregates",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("player_team_season_id", sa.Uuid(), nullable=False),
        sa.Column("stat_type", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("availability_reason", sa.String(length=100)),
        sa.Column("definition_version", sa.String(length=50), nullable=False),
        sa.Column("counts_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("included_row_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("excluded_rows_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "stat_type IN ('batting', 'pitching', 'fielding')",
            name="ck_career_aggregates_stat_type",
        ),
        sa.CheckConstraint(
            "status IN ('complete', 'partial', 'unavailable')",
            name="ck_career_aggregates_status",
        ),
        sa.CheckConstraint(
            "(status = 'complete' AND availability_reason IS NULL) OR "
            "(status <> 'complete' AND availability_reason IS NOT NULL)",
            name="ck_career_aggregates_reason_status",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(counts_json) = 'object'",
            name="ck_career_aggregates_counts_object",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(included_row_ids) = 'array'",
            name="ck_career_aggregates_included_array",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(excluded_rows_json) = 'object'",
            name="ck_career_aggregates_excluded_object",
        ),
        *_history_owner_constraints("career_aggregates"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "snapshot_id", "player_team_season_id", "stat_type",
            name="uq_career_aggregates_snapshot_player_type",
        ),
    )

    game_log_columns = {
        "batting_game_log_facts": (
            "at_bats", "hits", "runs", "home_runs", "walks", "strikeouts",
        ),
        "pitching_game_log_facts": (
            "innings_outs", "hits_allowed", "runs_allowed", "earned_runs",
            "walks", "strikeouts", "batters_faced",
        ),
        "fielding_game_log_facts": (
            "putouts", "assists", "errors", "total_chances", "passed_balls",
            "stolen_base_attempts", "caught_stealing_by",
        ),
    }
    for table_name, count_columns in game_log_columns.items():
        constraints = [
            sa.CheckConstraint(
                f"{column} IS NULL OR {column} >= 0",
                name=f"ck_{table_name}_{column}_nonnegative",
            )
            for column in count_columns
        ]
        if table_name == "batting_game_log_facts":
            constraints.append(sa.CheckConstraint(
                "hits IS NULL OR at_bats IS NULL OR hits <= at_bats",
                name="ck_batting_game_log_facts_hits_lte_at_bats",
            ))
        op.create_table(
            table_name,
            sa.Column("organization_id", sa.Uuid(), nullable=False),
            sa.Column("snapshot_id", sa.Uuid(), nullable=False),
            sa.Column("player_team_season_id", sa.Uuid(), nullable=False),
            sa.Column("source_record_id", sa.Uuid(), nullable=False),
            sa.Column("game_id", sa.Uuid(), nullable=False),
            sa.Column("row_id", sa.String(length=64), nullable=False),
            *(sa.Column(column, sa.Integer()) for column in count_columns),
            sa.Column("counts_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.CheckConstraint(
                "char_length(row_id) = 64", name=f"ck_{table_name}_row_id_length"
            ),
            sa.CheckConstraint(
                "jsonb_typeof(counts_json) = 'object'",
                name=f"ck_{table_name}_counts_object",
            ),
            *constraints,
            *_history_owner_constraints(table_name, source_record=True),
            sa.ForeignKeyConstraint(
                ["game_id", "snapshot_id", "organization_id"],
                ["games.id", "games.snapshot_id", "games.organization_id"],
                name=f"fk_{table_name}_game_owner",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "snapshot_id", "player_team_season_id", "game_id",
                name=f"uq_{table_name}_snapshot_player_game",
            ),
        )

    for table_name in (
        "career_season_facts", "career_aggregates",
        "batting_game_log_facts", "pitching_game_log_facts",
        "fielding_game_log_facts",
    ):
        op.execute(f"""
            CREATE TRIGGER trg_{table_name}_immutable
            BEFORE INSERT OR UPDATE OR DELETE ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION reject_published_snapshot_child_mutation()
        """)


def upgrade():
    op.drop_constraint(
        "ck_report_sections_status", "report_sections", type_="check"
    )
    op.create_check_constraint(
        "ck_report_sections_status",
        "report_sections",
        "status IN ('pending', 'complete', 'partial', 'unavailable', 'failed')",
    )

    _add_count_columns("batting_facts", BATTING_COUNT_COLUMNS)
    _add_count_columns("pitching_facts", PITCHING_COUNT_COLUMNS)

    op.create_table(
        "fielding_facts",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("player_team_season_id", sa.Uuid(), nullable=False),
        sa.Column("source_record_id", sa.Uuid(), nullable=False),
        sa.Column("games", sa.Integer()),
        sa.Column("games_started", sa.Integer()),
        sa.Column("putouts", sa.Integer()),
        sa.Column("assists", sa.Integer()),
        sa.Column("errors", sa.Integer()),
        sa.Column("total_chances", sa.Integer()),
        sa.Column("double_plays", sa.Integer()),
        sa.Column("fielding_percentage", sa.Numeric(6, 5)),
        sa.Column("passed_balls", sa.Integer()),
        sa.Column("stolen_base_attempts", sa.Integer()),
        sa.Column("caught_stealing_by", sa.Integer()),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "fielding_percentage IS NULL OR "
            "(fielding_percentage >= 0 AND fielding_percentage <= 1)",
            name="ck_fielding_facts_fielding_percentage_range",
        ),
        *(
            sa.CheckConstraint(
                f"{column} IS NULL OR {column} >= 0",
                name=f"ck_fielding_facts_{column}_nonnegative",
            )
            for column in (
                "games", "games_started", "putouts", "assists", "errors",
                "total_chances", "double_plays", "passed_balls",
                "stolen_base_attempts", "caught_stealing_by",
            )
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id", "organization_id"],
            ["report_snapshots.id", "report_snapshots.organization_id"],
            name="fk_fielding_facts_snapshot_organization",
        ),
        sa.ForeignKeyConstraint(
            ["player_team_season_id", "snapshot_id", "organization_id"],
            ["player_team_seasons.id", "player_team_seasons.snapshot_id", "player_team_seasons.organization_id"],
            name="fk_fielding_facts_player_snapshot_organization",
        ),
        sa.ForeignKeyConstraint(
            ["source_record_id", "player_team_season_id", "snapshot_id", "organization_id"],
            ["source_records.id", "source_records.player_team_season_id", "source_records.snapshot_id", "source_records.organization_id"],
            name="fk_fielding_facts_source_identity",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute(
        """
        CREATE TRIGGER trg_fielding_facts_immutable
        BEFORE INSERT OR UPDATE OR DELETE ON fielding_facts
        FOR EACH ROW EXECUTE FUNCTION reject_published_snapshot_child_mutation()
        """
    )

    op.create_table(
        "season_conference_evidence",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("source_record_id", sa.Uuid(), nullable=False),
        sa.Column("source_season_id", sa.String(length=100), nullable=False),
        sa.Column("source_team_id", sa.String(length=100), nullable=False),
        sa.Column("source_season_team_id", sa.String(length=100), nullable=False),
        sa.Column("conference_id", sa.String(length=100)),
        sa.Column("evidence_status", sa.String(length=20), nullable=False),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "evidence_status IN ('present', 'source_absent')",
            name="ck_season_conference_evidence_status",
        ),
        sa.CheckConstraint(
            "(evidence_status = 'present' AND conference_id IS NOT NULL) OR "
            "(evidence_status = 'source_absent' AND conference_id IS NULL)",
            name="ck_season_conference_evidence_value_status",
        ),
        sa.CheckConstraint(
            "char_length(payload_sha256) = 64",
            name="ck_season_conference_evidence_sha256_length",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id", "organization_id"],
            ["report_snapshots.id", "report_snapshots.organization_id"],
            name="fk_season_conference_evidence_snapshot_organization",
        ),
        sa.ForeignKeyConstraint(
            ["source_record_id", "snapshot_id", "organization_id"],
            ["source_records.id", "source_records.snapshot_id", "source_records.organization_id"],
            name="fk_season_conference_evidence_source_owner",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id", "snapshot_id", "organization_id",
            name="uq_season_conference_evidence_snapshot_owner",
        ),
        sa.UniqueConstraint(
            "snapshot_id", "source_season_id", "source_season_team_id",
            name="uq_season_conference_evidence_team_season",
        ),
    )
    op.execute(
        """
        CREATE TRIGGER trg_season_conference_evidence_immutable
        BEFORE INSERT OR UPDATE OR DELETE ON season_conference_evidence
        FOR EACH ROW EXECUTE FUNCTION reject_published_snapshot_child_mutation()
        """
    )

    op.create_table(
        "games",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("source_record_id", sa.Uuid(), nullable=False),
        sa.Column("source_season_id", sa.String(length=100), nullable=False),
        sa.Column("game_date", sa.Date(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("result_code", sa.String(length=16)),
        sa.Column("result_sha256", sa.String(length=64), nullable=False),
        sa.Column("opponent_team_id", sa.String(length=100)),
        sa.Column("opponent_season_team_id", sa.String(length=100)),
        sa.Column("report_team_conference_evidence_id", sa.Uuid()),
        sa.Column("opponent_conference_evidence_id", sa.Uuid()),
        sa.Column("doubleheader_ordinal", sa.Integer(), nullable=False),
        sa.Column("venue", sa.String(length=16), nullable=False),
        sa.Column("source_game_id", sa.String(length=100)),
        sa.Column("fixture_fallback_key", sa.String(length=300)),
        sa.Column("identity_kind", sa.String(length=24), nullable=False),
        sa.Column(
            "fixture_test_mode", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "status IN ('scheduled', 'completed', 'canceled', 'postponed', 'no_contest')",
            name="ck_games_status",
        ),
        sa.CheckConstraint(
            "venue IN ('home', 'away', 'neutral')", name="ck_games_venue"
        ),
        sa.CheckConstraint(
            "doubleheader_ordinal >= 1", name="ck_games_doubleheader_ordinal_positive"
        ),
        sa.CheckConstraint(
            "char_length(result_sha256) = 64", name="ck_games_result_sha256_length"
        ),
        sa.CheckConstraint(
            "(identity_kind = 'source' AND source_game_id IS NOT NULL "
            "AND fixture_fallback_key IS NULL AND NOT fixture_test_mode) OR "
            "(identity_kind = 'fixture_fallback' AND source_game_id IS NULL "
            "AND fixture_fallback_key IS NOT NULL AND fixture_test_mode)",
            name="ck_games_identity_contract",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id", "organization_id"],
            ["report_snapshots.id", "report_snapshots.organization_id"],
            name="fk_games_snapshot_organization",
        ),
        sa.ForeignKeyConstraint(
            ["source_record_id", "snapshot_id", "organization_id"],
            ["source_records.id", "source_records.snapshot_id", "source_records.organization_id"],
            name="fk_games_source_owner",
        ),
        sa.ForeignKeyConstraint(
            ["report_team_conference_evidence_id", "snapshot_id", "organization_id"],
            ["season_conference_evidence.id", "season_conference_evidence.snapshot_id", "season_conference_evidence.organization_id"],
            name="fk_games_report_conference_owner",
        ),
        sa.ForeignKeyConstraint(
            ["opponent_conference_evidence_id", "snapshot_id", "organization_id"],
            ["season_conference_evidence.id", "season_conference_evidence.snapshot_id", "season_conference_evidence.organization_id"],
            name="fk_games_opponent_conference_owner",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id", "snapshot_id", "organization_id", name="uq_games_snapshot_owner"
        ),
    )
    op.create_index(
        "uq_games_snapshot_source_identity", "games",
        ["organization_id", "snapshot_id", "source_game_id"], unique=True,
        postgresql_where=sa.text("source_game_id IS NOT NULL"),
    )
    op.create_index(
        "uq_games_snapshot_fixture_identity", "games",
        ["organization_id", "snapshot_id", "fixture_fallback_key"], unique=True,
        postgresql_where=sa.text(
            "source_game_id IS NULL AND identity_kind = 'fixture_fallback'"
        ),
    )
    op.execute(
        """
        CREATE TRIGGER trg_games_immutable
        BEFORE INSERT OR UPDATE OR DELETE ON games
        FOR EACH ROW EXECUTE FUNCTION reject_published_snapshot_child_mutation()
        """
    )

    _create_player_history_tables()

    op.create_table(
        "statistic_scopes",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("scope_kind", sa.String(length=32), nullable=False),
        sa.Column("scope_code", sa.String(length=64), nullable=False),
        sa.Column("stat_type", sa.String(length=16), nullable=False),
        sa.Column("display_label", sa.String(length=120), nullable=False),
        sa.Column("source_identifier", sa.String(length=100)),
        sa.Column("source_path", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text()),
        sa.Column("source_season_id", sa.String(length=100), nullable=False),
        sa.Column("source_season_team_id", sa.String(length=100), nullable=False),
        sa.Column("source_stat_category_id", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("availability_reason", sa.String(length=100)),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("parser_version", sa.String(length=64), nullable=False),
        sa.Column("schema_version", sa.String(length=64), nullable=False),
        sa.Column(
            "sample_json", postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"), nullable=False,
        ),
        sa.Column("source_payload_sha256", sa.String(length=64)),
        sa.Column("schedule_source_record_id", sa.Uuid()),
        sa.Column("start_date", sa.Date()),
        sa.Column("end_date", sa.Date()),
        sa.Column("actual_game_count", sa.Integer()),
        sa.Column("report_conference_id", sa.String(length=100)),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "scope_kind IN ('source_split', 'computed_game')",
            name="ck_statistic_scopes_kind",
        ),
        sa.CheckConstraint(
            "stat_type IN ('batting', 'pitching', 'fielding')",
            name="ck_statistic_scopes_stat_type",
        ),
        sa.CheckConstraint(
            "status IN ('complete', 'partial', 'unavailable', 'failed')",
            name="ck_statistic_scopes_status",
        ),
        sa.CheckConstraint(
            "(scope_kind = 'source_split' AND status IN ('complete', 'partial') "
            "AND source_identifier IS NOT NULL) OR "
            "(scope_kind = 'computed_game' AND source_identifier IS NULL) OR "
            "status IN ('unavailable', 'failed')",
            name="ck_statistic_scopes_source_status",
        ),
        sa.CheckConstraint(
            "(status = 'complete' AND availability_reason IS NULL) OR "
            "(status <> 'complete' AND availability_reason IS NOT NULL)",
            name="ck_statistic_scopes_reason_status",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(sample_json) = 'object'",
            name="ck_statistic_scopes_sample_object",
        ),
        sa.CheckConstraint(
            "source_payload_sha256 IS NULL OR char_length(source_payload_sha256) = 64",
            name="ck_statistic_scopes_sha256_length",
        ),
        sa.CheckConstraint(
            "actual_game_count IS NULL OR actual_game_count >= 0",
            name="ck_statistic_scopes_game_count_nonnegative",
        ),
        sa.CheckConstraint(
            "scope_kind <> 'computed_game' OR "
            "(schedule_source_record_id IS NOT NULL AND actual_game_count IS NOT NULL)",
            name="ck_statistic_scopes_computed_source",
        ),
        sa.CheckConstraint(
            "(actual_game_count IS NULL) OR "
            "(actual_game_count = 0 AND start_date IS NULL AND end_date IS NULL) OR "
            "(actual_game_count > 0 AND start_date IS NOT NULL AND end_date IS NOT NULL "
            "AND start_date <= end_date)",
            name="ck_statistic_scopes_date_range",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id", "organization_id"],
            ["report_snapshots.id", "report_snapshots.organization_id"],
            name="fk_statistic_scopes_snapshot_organization",
        ),
        sa.ForeignKeyConstraint(
            ["schedule_source_record_id", "snapshot_id", "organization_id"],
            ["source_records.id", "source_records.snapshot_id", "source_records.organization_id"],
            name="fk_statistic_scopes_schedule_source_owner",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id", "snapshot_id", "organization_id",
            name="uq_statistic_scopes_snapshot_owner",
        ),
        sa.UniqueConstraint(
            "snapshot_id", "stat_type", "scope_code",
            name="uq_statistic_scopes_snapshot_type_code",
        ),
    )
    op.execute(
        """
        CREATE TRIGGER trg_statistic_scopes_immutable
        BEFORE INSERT OR UPDATE OR DELETE ON statistic_scopes
        FOR EACH ROW EXECUTE FUNCTION reject_published_snapshot_child_mutation()
        """
    )

    op.create_table(
        "statistic_scope_games",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("statistic_scope_id", sa.Uuid(), nullable=False),
        sa.Column("game_id", sa.Uuid(), nullable=False),
        sa.Column("membership_ordinal", sa.Integer(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "membership_ordinal >= 1",
            name="ck_statistic_scope_games_ordinal_positive",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id", "organization_id"],
            ["report_snapshots.id", "report_snapshots.organization_id"],
            name="fk_statistic_scope_games_snapshot_organization",
        ),
        sa.ForeignKeyConstraint(
            ["statistic_scope_id", "snapshot_id", "organization_id"],
            ["statistic_scopes.id", "statistic_scopes.snapshot_id", "statistic_scopes.organization_id"],
            name="fk_statistic_scope_games_scope_owner",
        ),
        sa.ForeignKeyConstraint(
            ["game_id", "snapshot_id", "organization_id"],
            ["games.id", "games.snapshot_id", "games.organization_id"],
            name="fk_statistic_scope_games_game_owner",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "statistic_scope_id", "game_id",
            name="uq_statistic_scope_games_scope_game",
        ),
        sa.UniqueConstraint(
            "statistic_scope_id", "membership_ordinal",
            name="uq_statistic_scope_games_scope_ordinal",
        ),
    )
    op.execute(
        """
        CREATE TRIGGER trg_statistic_scope_games_immutable
        BEFORE INSERT OR UPDATE OR DELETE ON statistic_scope_games
        FOR EACH ROW EXECUTE FUNCTION reject_published_snapshot_child_mutation()
        """
    )

    for table_name, count_columns in (
        ("scoped_batting_facts", ("at_bats", "hits")),
        ("scoped_pitching_facts", ("opponent_at_bats", "hits_allowed")),
    ):
        op.create_table(
            table_name,
            sa.Column("organization_id", sa.Uuid(), nullable=False),
            sa.Column("snapshot_id", sa.Uuid(), nullable=False),
            sa.Column("statistic_scope_id", sa.Uuid(), nullable=False),
            sa.Column("player_team_season_id", sa.Uuid(), nullable=False),
            sa.Column("source_record_id", sa.Uuid(), nullable=False),
            *(sa.Column(column, sa.Integer()) for column in count_columns),
            sa.Column(
                "sample_json", postgresql.JSONB(astext_type=sa.Text()),
                server_default=sa.text("'{}'::jsonb"), nullable=False,
            ),
            sa.Column(
                "counts_json", postgresql.JSONB(astext_type=sa.Text()),
                server_default=sa.text("'{}'::jsonb"), nullable=False,
            ),
            sa.Column(
                "contributing_fact_ids", postgresql.JSONB(astext_type=sa.Text()),
                server_default=sa.text("'[]'::jsonb"), nullable=False,
            ),
            sa.Column("id", sa.Uuid(), nullable=False),
            *(
                sa.CheckConstraint(
                    f"{column} IS NULL OR {column} >= 0",
                    name=f"ck_{table_name}_{column}_nonnegative",
                )
                for column in count_columns
            ),
            sa.CheckConstraint(
                (
                    "hits IS NULL OR at_bats IS NULL OR hits <= at_bats"
                    if table_name == "scoped_batting_facts"
                    else "hits_allowed IS NULL OR opponent_at_bats IS NULL "
                    "OR hits_allowed <= opponent_at_bats"
                ),
                name=f"ck_{table_name}_hits_lte_at_bats",
            ),
            sa.CheckConstraint(
                "jsonb_typeof(sample_json) = 'object'",
                name=f"ck_{table_name}_sample_object",
            ),
            sa.CheckConstraint(
                "jsonb_typeof(counts_json) = 'object'",
                name=f"ck_{table_name}_counts_object",
            ),
            sa.CheckConstraint(
                "jsonb_typeof(contributing_fact_ids) = 'array'",
                name=f"ck_{table_name}_contributions_array",
            ),
            sa.ForeignKeyConstraint(
                ["snapshot_id", "organization_id"],
                ["report_snapshots.id", "report_snapshots.organization_id"],
                name=f"fk_{table_name}_snapshot_organization",
            ),
            sa.ForeignKeyConstraint(
                ["statistic_scope_id", "snapshot_id", "organization_id"],
                ["statistic_scopes.id", "statistic_scopes.snapshot_id", "statistic_scopes.organization_id"],
                name=f"fk_{table_name}_scope_snapshot_organization",
            ),
            sa.ForeignKeyConstraint(
                ["player_team_season_id", "snapshot_id", "organization_id"],
                ["player_team_seasons.id", "player_team_seasons.snapshot_id", "player_team_seasons.organization_id"],
                name=f"fk_{table_name}_player_snapshot_organization",
            ),
            sa.ForeignKeyConstraint(
                ["source_record_id", "player_team_season_id", "snapshot_id", "organization_id"],
                ["source_records.id", "source_records.player_team_season_id", "source_records.snapshot_id", "source_records.organization_id"],
                name=f"fk_{table_name}_source_identity",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "statistic_scope_id", "player_team_season_id",
                name=f"uq_{table_name}_scope_player",
            ),
        )
        op.execute(
            f"""
            CREATE TRIGGER trg_{table_name}_immutable
            BEFORE INSERT OR UPDATE OR DELETE ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION reject_published_snapshot_child_mutation()
            """
        )

    op.create_table(
        "scoped_fielding_facts",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("statistic_scope_id", sa.Uuid(), nullable=False),
        sa.Column("player_team_season_id", sa.Uuid(), nullable=False),
        sa.Column("source_record_id", sa.Uuid(), nullable=False),
        sa.Column("putouts", sa.Integer()),
        sa.Column("assists", sa.Integer()),
        sa.Column("errors", sa.Integer()),
        sa.Column("total_chances", sa.Integer()),
        sa.Column(
            "sample_json", postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"), nullable=False,
        ),
        sa.Column(
            "counts_json", postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"), nullable=False,
        ),
        sa.Column(
            "contributing_fact_ids", postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"), nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        *(
            sa.CheckConstraint(
                f"{column} IS NULL OR {column} >= 0",
                name=f"ck_scoped_fielding_facts_{column}_nonnegative",
            )
            for column in ("putouts", "assists", "errors", "total_chances")
        ),
        sa.CheckConstraint(
            "jsonb_typeof(sample_json) = 'object'",
            name="ck_scoped_fielding_facts_sample_object",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(counts_json) = 'object'",
            name="ck_scoped_fielding_facts_counts_object",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(contributing_fact_ids) = 'array'",
            name="ck_scoped_fielding_facts_contributions_array",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id", "organization_id"],
            ["report_snapshots.id", "report_snapshots.organization_id"],
            name="fk_scoped_fielding_facts_snapshot_organization",
        ),
        sa.ForeignKeyConstraint(
            ["statistic_scope_id", "snapshot_id", "organization_id"],
            ["statistic_scopes.id", "statistic_scopes.snapshot_id", "statistic_scopes.organization_id"],
            name="fk_scoped_fielding_facts_scope_snapshot_organization",
        ),
        sa.ForeignKeyConstraint(
            ["player_team_season_id", "snapshot_id", "organization_id"],
            ["player_team_seasons.id", "player_team_seasons.snapshot_id", "player_team_seasons.organization_id"],
            name="fk_scoped_fielding_facts_player_snapshot_organization",
        ),
        sa.ForeignKeyConstraint(
            ["source_record_id", "player_team_season_id", "snapshot_id", "organization_id"],
            ["source_records.id", "source_records.player_team_season_id", "source_records.snapshot_id", "source_records.organization_id"],
            name="fk_scoped_fielding_facts_source_identity",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "statistic_scope_id", "player_team_season_id",
            name="uq_scoped_fielding_facts_scope_player",
        ),
    )
    op.execute(
        """
        CREATE TRIGGER trg_scoped_fielding_facts_immutable
        BEFORE INSERT OR UPDATE OR DELETE ON scoped_fielding_facts
        FOR EACH ROW EXECUTE FUNCTION reject_published_snapshot_child_mutation()
        """
    )

    op.create_table(
        "metric_constant_sets",
        sa.Column("metric_definition_id", sa.Uuid(), nullable=False),
        sa.Column("metric_code", sa.String(length=16), nullable=False),
        sa.Column("version", sa.String(length=100), nullable=False),
        sa.Column("season_id", sa.Uuid(), nullable=False),
        sa.Column("division", sa.String(length=16), nullable=False),
        sa.Column("constant_type", sa.String(length=32), nullable=False),
        sa.Column(
            "values_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("methodology", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("source_identifier", sa.String(length=200), nullable=False),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "verification_status",
            sa.String(length=16),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_by_account_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "metric_code IN ('woba', 'fip')",
            name="ck_metric_constant_sets_metric_code",
        ),
        sa.CheckConstraint(
            "verification_status IN ('pending', 'verified', 'rejected')",
            name="ck_metric_constant_sets_verification_status",
        ),
        sa.CheckConstraint(
            "char_length(payload_sha256) = 64",
            name="ck_metric_constant_sets_sha256_length",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(values_json) = 'object'",
            name="ck_metric_constant_sets_values_object",
        ),
        sa.CheckConstraint(
            "(verification_status = 'verified' AND verified_at IS NOT NULL) OR "
            "(verification_status <> 'verified' AND verified_at IS NULL "
            "AND verified_by_account_id IS NULL)",
            name="ck_metric_constant_sets_verified_fields",
        ),
        sa.ForeignKeyConstraint(
            ["metric_definition_id"],
            ["metric_definitions.id"],
            name="fk_metric_constant_sets_definition",
        ),
        sa.ForeignKeyConstraint(
            ["season_id"],
            ["seasons.id"],
            name="fk_metric_constant_sets_season",
        ),
        sa.ForeignKeyConstraint(
            ["verified_by_account_id"],
            ["accounts.id"],
            name="fk_metric_constant_sets_verifier",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "metric_definition_id",
            "season_id",
            "division",
            "version",
            name="uq_metric_constant_sets_definition_season_division_version",
        ),
        sa.UniqueConstraint(
            "id",
            "metric_definition_id",
            name="uq_metric_constant_sets_id_definition",
        ),
    )

    op.add_column(
        "metric_values",
        sa.Column(
            "status",
            sa.String(length=32),
            server_default="available",
            nullable=False,
        ),
    )
    op.add_column(
        "metric_values", sa.Column("reason_code", sa.String(length=100))
    )
    op.add_column(
        "metric_values",
        sa.Column(
            "inputs_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "metric_values",
        sa.Column(
            "sample_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "metric_values", sa.Column("scope", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "metric_values",
        sa.Column("statistic_scope_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "metric_values", sa.Column("constant_set_id", sa.Uuid(), nullable=True)
    )
    # Existing deployments can already contain published metric rows. Suspend
    # only this table's immutable-row guard while this migration expands their
    # lineage in place, then restore the guard before constraints are applied.
    op.execute("ALTER TABLE metric_values DISABLE TRIGGER trg_metric_values_immutable")
    op.execute(
        """
        UPDATE metric_values
        SET status = CASE WHEN value IS NULL THEN 'missing_input' ELSE 'available' END,
            reason_code = CASE
                WHEN value IS NULL THEN COALESCE(NULLIF(unavailable_reason, ''), 'legacy_unavailable')
                ELSE NULL
            END,
            scope = 'full_season'
        """
    )
    op.execute("ALTER TABLE metric_values ENABLE TRIGGER trg_metric_values_immutable")
    op.drop_constraint(
        "ck_metric_values_value_or_reason", "metric_values", type_="check"
    )
    op.create_foreign_key(
        "fk_metric_values_statistic_scope_owner",
        "metric_values",
        "statistic_scopes",
        ["statistic_scope_id", "snapshot_id", "organization_id"],
        ["id", "snapshot_id", "organization_id"],
    )
    op.create_foreign_key(
        "fk_metric_values_constant_definition",
        "metric_values",
        "metric_constant_sets",
        ["constant_set_id", "metric_definition_id"],
        ["id", "metric_definition_id"],
    )
    op.create_check_constraint(
        "ck_metric_values_status",
        "metric_values",
        "status IN ('available', 'missing_input', 'undefined_denominator', "
        "'unavailable_source', 'unavailable_constant', 'incompatible_input')",
    )
    op.create_check_constraint(
        "ck_metric_values_value_status_reason",
        "metric_values",
        "(status = 'available' AND value IS NOT NULL AND reason_code IS NULL) OR "
        "(status <> 'available' AND value IS NULL AND reason_code IS NOT NULL) OR "
        "(value IS NULL AND unavailable_reason IS NOT NULL AND reason_code IS NULL)",
    )
    op.create_check_constraint(
        "ck_metric_values_inputs_object",
        "metric_values",
        "jsonb_typeof(inputs_json) = 'object'",
    )
    op.create_check_constraint(
        "ck_metric_values_sample_object",
        "metric_values",
        "jsonb_typeof(sample_json) = 'object'",
    )
    op.create_check_constraint(
        "ck_metric_values_scope_nonempty",
        "metric_values",
        "scope IS NULL OR char_length(scope) > 0",
    )
    op.create_check_constraint(
        "ck_metric_values_unavailable_constant_reference",
        "metric_values",
        "(status = 'unavailable_constant' AND constant_set_id IS NULL) "
        "OR status <> 'unavailable_constant'",
    )

    op.create_table(
        "request_ledgers",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("family", sa.String(length=50), nullable=False),
        sa.Column("source_identity", sa.String(length=200), nullable=False),
        sa.Column("request_kind", sa.String(length=16), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("safe_category", sa.String(length=100)),
        sa.Column("budget_remaining", sa.Integer(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["job_id", "organization_id"],
            ["report_jobs.id", "report_jobs.organization_id"],
            name="fk_request_ledgers_job_organization",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id", "organization_id"],
            ["report_snapshots.id", "report_snapshots.organization_id"],
            name="fk_request_ledgers_snapshot_organization",
        ),
        sa.CheckConstraint(
            "family ~ '^[a-z][a-z0-9_:]{0,49}$'",
            name="ck_request_ledgers_family_safe",
        ),
        sa.CheckConstraint(
            "source_identity ~ '^[a-z0-9][a-z0-9:_-]{0,199}$'",
            name="ck_request_ledgers_source_identity_safe",
        ),
        sa.CheckConstraint(
            "request_kind IN ('planned', 'cache', 'live')",
            name="ck_request_ledgers_request_kind",
        ),
        sa.CheckConstraint(
            "outcome IN ('planned', 'cache_hit', 'live_complete', 'live_failed', "
            "'budget_exhausted', 'capacity_exhausted')",
            name="ck_request_ledgers_outcome",
        ),
        sa.CheckConstraint(
            "safe_category IS NULL OR safe_category ~ '^[a-z][a-z0-9_]{0,99}$'",
            name="ck_request_ledgers_safe_category",
        ),
        sa.CheckConstraint(
            "budget_remaining BETWEEN 0 AND 500",
            name="ck_request_ledgers_budget_remaining",
        ),
        sa.CheckConstraint(
            "(outcome = 'planned' AND completed_at IS NULL) OR "
            "(outcome <> 'planned' AND completed_at IS NOT NULL "
            "AND completed_at >= started_at)",
            name="ck_request_ledgers_lifecycle",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "job_id", "family", "source_identity",
            name="uq_request_ledgers_job_family_source",
        ),
    )
    op.create_index(
        "ix_request_ledgers_job", "request_ledgers", ["job_id", "started_at"]
    )
    op.execute(
        """
        CREATE TRIGGER trg_request_ledgers_immutable
        BEFORE INSERT OR UPDATE OR DELETE ON request_ledgers
        FOR EACH ROW EXECUTE FUNCTION reject_published_snapshot_child_mutation()
        """
    )

    op.create_table(
        "external_refresh_diagnostics",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("report_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("safe_category", sa.String(length=100)),
        sa.Column("safe_reason_code", sa.String(length=100)),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("correlation_id", sa.String(length=120), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["report_id", "organization_id"],
            ["reports.id", "reports.organization_id"],
            name="fk_external_refresh_diagnostics_report_organization",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id", "report_id", "organization_id"],
            ["report_snapshots.id", "report_snapshots.report_id", "report_snapshots.organization_id"],
            name="fk_external_refresh_diagnostics_snapshot_lineage",
        ),
        sa.CheckConstraint(
            "provider ~ '^[a-z][a-z0-9_]{0,31}$'",
            name="ck_external_refresh_diagnostics_provider_safe",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'completed', 'failed')",
            name="ck_external_refresh_diagnostics_status",
        ),
        sa.CheckConstraint(
            "safe_category IS NULL OR safe_category ~ '^[a-z][a-z0-9_]{0,99}$'",
            name="ck_external_refresh_diagnostics_category_safe",
        ),
        sa.CheckConstraint(
            "safe_reason_code IS NULL OR safe_reason_code ~ '^[a-z][a-z0-9_]{0,99}$'",
            name="ck_external_refresh_diagnostics_reason_safe",
        ),
        sa.CheckConstraint(
            "correlation_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$'",
            name="ck_external_refresh_diagnostics_correlation_safe",
        ),
        sa.CheckConstraint(
            "attempt_number > 0",
            name="ck_external_refresh_diagnostics_attempt_positive",
        ),
        sa.CheckConstraint(
            "updated_at >= requested_at AND "
            "(started_at IS NULL OR started_at >= requested_at) AND "
            "(completed_at IS NULL OR "
            "(started_at IS NOT NULL AND completed_at >= started_at))",
            name="ck_external_refresh_diagnostics_timestamp_order",
        ),
        sa.CheckConstraint(
            "(status = 'pending' AND completed_at IS NULL) OR "
            "(status IN ('completed', 'failed') AND completed_at IS NOT NULL)",
            name="ck_external_refresh_diagnostics_lifecycle",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "snapshot_id", "provider", "attempt_number",
            name="uq_external_refresh_diagnostics_snapshot_attempt",
        ),
        sa.UniqueConstraint(
            "provider", "correlation_id",
            name="uq_external_refresh_diagnostics_provider_correlation",
        ),
    )
    op.create_index(
        "ix_external_refresh_diagnostics_snapshot",
        "external_refresh_diagnostics",
        ["organization_id", "snapshot_id", "requested_at"],
    )
    op.execute(
        """
        CREATE FUNCTION enforce_external_refresh_diagnostic_transition()
        RETURNS trigger AS $$
        BEGIN
            IF NEW.organization_id <> OLD.organization_id
               OR NEW.report_id <> OLD.report_id
               OR NEW.snapshot_id <> OLD.snapshot_id
               OR NEW.provider <> OLD.provider
               OR NEW.correlation_id <> OLD.correlation_id
               OR NEW.attempt_number <> OLD.attempt_number
               OR NEW.requested_at <> OLD.requested_at THEN
                RAISE EXCEPTION 'external refresh identity is immutable';
            END IF;
            IF OLD.status <> 'pending' OR NEW.status NOT IN ('completed', 'failed') THEN
                RAISE EXCEPTION 'external refresh transition is invalid';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_external_refresh_diagnostics_transition
        BEFORE UPDATE ON external_refresh_diagnostics
        FOR EACH ROW EXECUTE FUNCTION enforce_external_refresh_diagnostic_transition()
        """
    )

    op.execute(
        """
        CREATE FUNCTION reject_locked_metric_constant_set_mutation()
        RETURNS trigger AS $$
        BEGIN
            IF OLD.verification_status = 'verified'
               OR EXISTS (
                   SELECT 1
                   FROM metric_values
                   WHERE metric_values.constant_set_id = OLD.id
               ) THEN
                RAISE EXCEPTION 'verified or referenced metric constant sets are immutable';
            END IF;
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_metric_constant_sets_append_only
        BEFORE UPDATE OR DELETE ON metric_constant_sets
        FOR EACH ROW EXECUTE FUNCTION reject_locked_metric_constant_set_mutation()
        """
    )
    op.execute(
        """
        CREATE FUNCTION lock_metric_constant_set_reference()
        RETURNS trigger AS $$
        BEGIN
            IF NEW.constant_set_id IS NOT NULL THEN
                PERFORM 1
                FROM metric_constant_sets
                WHERE id = NEW.constant_set_id
                FOR UPDATE;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_metric_values_lock_constant_set
        BEFORE INSERT OR UPDATE OF constant_set_id ON metric_values
        FOR EACH ROW EXECUTE FUNCTION lock_metric_constant_set_reference()
        """
    )


def downgrade():
    op.execute("DROP TABLE IF EXISTS external_refresh_diagnostics")
    op.execute("DROP FUNCTION IF EXISTS enforce_external_refresh_diagnostic_transition()")
    op.execute("DROP TABLE IF EXISTS request_ledgers")

    op.execute(
        "DROP TRIGGER IF EXISTS trg_metric_values_lock_constant_set "
        "ON metric_values"
    )
    op.execute("DROP FUNCTION IF EXISTS lock_metric_constant_set_reference()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_metric_constant_sets_append_only "
        "ON metric_constant_sets"
    )
    op.execute("DROP FUNCTION IF EXISTS reject_locked_metric_constant_set_mutation()")

    # The legacy column must be restored before the detailed lineage columns
    # disappear. Published metric rows are normally immutable, so suspend only
    # that table's existing guard for this bounded migration-owned backfill.
    op.execute("ALTER TABLE metric_values DISABLE TRIGGER trg_metric_values_immutable")
    op.execute(
        """
        UPDATE metric_values
        SET unavailable_reason = COALESCE(unavailable_reason, reason_code)
        WHERE value IS NULL
        """
    )
    op.execute("ALTER TABLE metric_values ENABLE TRIGGER trg_metric_values_immutable")
    for constraint, kind in (
        ("fk_metric_values_constant_definition", "foreignkey"),
        ("fk_metric_values_statistic_scope_owner", "foreignkey"),
        ("ck_metric_values_unavailable_constant_reference", "check"),
        ("ck_metric_values_scope_nonempty", "check"),
        ("ck_metric_values_sample_object", "check"),
        ("ck_metric_values_inputs_object", "check"),
        ("ck_metric_values_value_status_reason", "check"),
        ("ck_metric_values_status", "check"),
    ):
        op.drop_constraint(constraint, "metric_values", type_=kind)
    for column in (
        "constant_set_id",
        "statistic_scope_id",
        "scope",
        "sample_json",
        "inputs_json",
        "reason_code",
        "status",
    ):
        op.drop_column("metric_values", column)
    op.create_check_constraint(
        "ck_metric_values_value_or_reason",
        "metric_values",
        "value IS NOT NULL OR unavailable_reason IS NOT NULL",
    )
    op.drop_table("metric_constant_sets")

    op.execute(
        "DROP TRIGGER IF EXISTS trg_scoped_fielding_facts_immutable "
        "ON scoped_fielding_facts"
    )
    op.execute("DROP TABLE IF EXISTS scoped_fielding_facts")
    for table_name in ("scoped_pitching_facts", "scoped_batting_facts"):
        op.execute(
            f"DROP TRIGGER IF EXISTS trg_{table_name}_immutable ON {table_name}"
        )
        op.drop_table(table_name)
    op.execute(
        "DROP TRIGGER IF EXISTS trg_statistic_scope_games_immutable "
        "ON statistic_scope_games"
    )
    op.execute("DROP TABLE IF EXISTS statistic_scope_games")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_statistic_scopes_immutable ON statistic_scopes"
    )
    op.drop_table("statistic_scopes")
    for table_name in (
        "fielding_game_log_facts", "pitching_game_log_facts",
        "batting_game_log_facts", "career_aggregates", "career_season_facts",
    ):
        op.execute(
            f"DROP TRIGGER IF EXISTS trg_{table_name}_immutable ON {table_name}"
        )
        # Phase 3 plans share this unreleased migration while it is assembled.
        # A disposable developer database may have an earlier 0025 shape.
        op.execute(f"DROP TABLE IF EXISTS {table_name}")
    op.execute("DROP TABLE IF EXISTS games")
    op.execute("DROP TABLE IF EXISTS season_conference_evidence")

    op.execute("DROP TRIGGER IF EXISTS trg_fielding_facts_immutable ON fielding_facts")
    op.drop_table("fielding_facts")

    _drop_count_columns("pitching_facts", PITCHING_COUNT_COLUMNS)
    _drop_count_columns("batting_facts", BATTING_COUNT_COLUMNS)

    op.drop_constraint(
        "ck_report_sections_status", "report_sections", type_="check"
    )
    op.create_check_constraint(
        "ck_report_sections_status",
        "report_sections",
        "status IN ('pending', 'complete', 'unavailable', 'failed')",
    )
