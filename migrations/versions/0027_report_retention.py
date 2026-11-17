"""Permit bounded application-owned retention deletion.

Revision ID: 0027
Revises: 0026
"""

from alembic import op


revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


_CHILD_FUNCTION = r"""
    CREATE OR REPLACE FUNCTION reject_published_snapshot_child_mutation()
    RETURNS trigger AS $$
    DECLARE old_snapshot_id uuid;
    DECLARE new_snapshot_id uuid;
    DECLARE old_metric_definition_id uuid;
    DECLARE new_metric_definition_id uuid;
    DECLARE target_metric_definition_id uuid;
    DECLARE target_snapshot_id uuid;
    BEGIN
        IF current_setting('roster_scout.retention_purge', true) = 'on' THEN
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END IF;

        IF TG_OP IN ('UPDATE', 'DELETE') THEN
            old_snapshot_id := OLD.snapshot_id;
        END IF;
        IF TG_OP IN ('INSERT', 'UPDATE') THEN
            new_snapshot_id := NEW.snapshot_id;
        END IF;

        IF TG_TABLE_NAME = 'metric_values' THEN
            IF TG_OP IN ('UPDATE', 'DELETE') THEN
                old_metric_definition_id := OLD.metric_definition_id;
            END IF;
            IF TG_OP IN ('INSERT', 'UPDATE') THEN
                new_metric_definition_id := NEW.metric_definition_id;
            END IF;

            FOR target_metric_definition_id IN
                SELECT DISTINCT metric_definition_id
                FROM unnest(
                    ARRAY[old_metric_definition_id, new_metric_definition_id]
                ) AS ids(metric_definition_id)
                WHERE metric_definition_id IS NOT NULL
                ORDER BY metric_definition_id
            LOOP
                PERFORM lock_metric_definition_mutation(
                    target_metric_definition_id
                );
            END LOOP;
        END IF;

        FOR target_snapshot_id IN
            SELECT DISTINCT snapshot_id
            FROM unnest(ARRAY[old_snapshot_id, new_snapshot_id]) AS ids(snapshot_id)
            WHERE snapshot_id IS NOT NULL
            ORDER BY snapshot_id
        LOOP
            PERFORM lock_snapshot_mutation(target_snapshot_id);
        END LOOP;

        IF EXISTS (
            SELECT 1
            FROM report_snapshots
            WHERE id IN (old_snapshot_id, new_snapshot_id)
              AND status IN ('ready', 'partial')
        ) THEN
            RAISE EXCEPTION 'published snapshot rows are immutable';
        END IF;

        RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
    END;
    $$ LANGUAGE plpgsql
"""


_CHILD_FUNCTION_WITHOUT_RETENTION = _CHILD_FUNCTION.replace(
    """        IF current_setting('roster_scout.retention_purge', true) = 'on' THEN
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END IF;

""",
    "",
)


def upgrade():
    op.execute(r"""
        CREATE OR REPLACE FUNCTION reject_published_snapshot_mutation()
        RETURNS trigger AS $$
        BEGIN
            IF current_setting('roster_scout.retention_purge', true) = 'on' THEN
                RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
            END IF;
            IF OLD.status IN ('ready', 'partial') THEN
                RAISE EXCEPTION 'published snapshots are immutable';
            END IF;
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute(_CHILD_FUNCTION)


def downgrade():
    op.execute(r"""
        CREATE OR REPLACE FUNCTION reject_published_snapshot_mutation()
        RETURNS trigger AS $$
        BEGIN
            IF OLD.status IN ('ready', 'partial') THEN
                RAISE EXCEPTION 'published snapshots are immutable';
            END IF;
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute(_CHILD_FUNCTION_WITHOUT_RETENTION)
