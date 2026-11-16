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
        "metric_values", sa.Column("constant_set_id", sa.Uuid(), nullable=True)
    )
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
    op.drop_constraint(
        "ck_metric_values_value_or_reason", "metric_values", type_="check"
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


def downgrade():
    op.execute(
        "DROP TRIGGER IF EXISTS trg_metric_constant_sets_append_only "
        "ON metric_constant_sets"
    )
    op.execute("DROP FUNCTION IF EXISTS reject_locked_metric_constant_set_mutation()")

    op.execute(
        """
        UPDATE metric_values
        SET unavailable_reason = COALESCE(unavailable_reason, reason_code)
        WHERE value IS NULL
        """
    )
    for constraint, kind in (
        ("fk_metric_values_constant_definition", "foreignkey"),
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
