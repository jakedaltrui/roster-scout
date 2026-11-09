"""Harden published snapshot and canonical count integrity.

Revision ID: 0003
Revises: 0002
"""

from alembic import op


revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


COUNT_CHECKS = {
    "batting_facts": {
        "ck_batting_facts_walks_nonnegative": "walks IS NULL OR walks >= 0",
        "ck_batting_facts_strikeouts_nonnegative": (
            "strikeouts IS NULL OR strikeouts >= 0"
        ),
    },
    "pitching_facts": {
        "ck_pitching_facts_hits_allowed_nonnegative": (
            "hits_allowed IS NULL OR hits_allowed >= 0"
        ),
        "ck_pitching_facts_earned_runs_nonnegative": (
            "earned_runs IS NULL OR earned_runs >= 0"
        ),
        "ck_pitching_facts_walks_nonnegative": "walks IS NULL OR walks >= 0",
        "ck_pitching_facts_strikeouts_nonnegative": (
            "strikeouts IS NULL OR strikeouts >= 0"
        ),
    },
}


def upgrade():
    for table_name, checks in COUNT_CHECKS.items():
        for constraint_name, condition in checks.items():
            op.create_check_constraint(constraint_name, table_name, condition)

    op.execute("""
        CREATE OR REPLACE FUNCTION reject_published_snapshot_child_mutation()
        RETURNS trigger AS $$
        DECLARE old_status varchar;
        DECLARE new_status varchar;
        BEGIN
            IF TG_OP IN ('UPDATE', 'DELETE') THEN
                SELECT status INTO old_status
                FROM report_snapshots
                WHERE id = OLD.snapshot_id;
                IF old_status IN ('ready', 'partial') THEN
                    RAISE EXCEPTION 'published snapshot rows are immutable';
                END IF;
            END IF;

            IF TG_OP IN ('INSERT', 'UPDATE') THEN
                SELECT status INTO new_status
                FROM report_snapshots
                WHERE id = NEW.snapshot_id;
                IF new_status IN ('ready', 'partial') THEN
                    RAISE EXCEPTION 'published snapshot rows are immutable';
                END IF;
            END IF;

            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$ LANGUAGE plpgsql
    """)

    op.execute("""
        CREATE FUNCTION reject_published_metric_definition_mutation()
        RETURNS trigger AS $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM metric_values
                JOIN report_snapshots
                  ON report_snapshots.id = metric_values.snapshot_id
                 AND report_snapshots.organization_id = metric_values.organization_id
                WHERE metric_values.metric_definition_id = OLD.id
                  AND report_snapshots.status IN ('ready', 'partial')
            ) THEN
                RAISE EXCEPTION 'published metric definitions are append-only';
            END IF;
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER trg_metric_definitions_published_immutable
        BEFORE UPDATE OR DELETE ON metric_definitions
        FOR EACH ROW EXECUTE FUNCTION reject_published_metric_definition_mutation()
    """)


def downgrade():
    op.execute(
        "DROP TRIGGER IF EXISTS trg_metric_definitions_published_immutable "
        "ON metric_definitions"
    )
    op.execute("DROP FUNCTION IF EXISTS reject_published_metric_definition_mutation()")

    op.execute("""
        CREATE OR REPLACE FUNCTION reject_published_snapshot_child_mutation()
        RETURNS trigger AS $$
        DECLARE target_snapshot uuid;
        DECLARE target_status varchar;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                target_snapshot := OLD.snapshot_id;
            ELSE
                target_snapshot := NEW.snapshot_id;
            END IF;
            SELECT status INTO target_status
            FROM report_snapshots
            WHERE id = target_snapshot;
            IF target_status IN ('ready', 'partial') THEN
                RAISE EXCEPTION 'published snapshot rows are immutable';
            END IF;
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$ LANGUAGE plpgsql
    """)

    for table_name, checks in reversed(tuple(COUNT_CHECKS.items())):
        for constraint_name in reversed(tuple(checks)):
            op.drop_constraint(constraint_name, table_name, type_="check")
