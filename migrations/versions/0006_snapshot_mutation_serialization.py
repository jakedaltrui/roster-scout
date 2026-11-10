"""Serialize snapshot publication with affecting canonical mutations.

Revision ID: 0006
Revises: 0005
"""

from alembic import op


revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE FUNCTION lock_snapshot_mutation(snapshot_uuid uuid)
        RETURNS void AS $$
        BEGIN
            -- Namespace seed 15062026 is reserved for snapshot publication locks.
            -- Hash collisions cause safe extra contention, never unsafe concurrency.
            PERFORM pg_advisory_xact_lock(
                hashtextextended(snapshot_uuid::text, 15062026)
            );
        END;
        $$ LANGUAGE plpgsql STRICT
    """)

    op.execute("""
        CREATE FUNCTION lock_metric_definition_mutation(metric_definition_uuid uuid)
        RETURNS void AS $$
        BEGIN
            -- Namespace seed 16062026 is reserved for metric-definition locks.
            -- It is deliberately distinct from the snapshot-lock namespace.
            PERFORM pg_advisory_xact_lock(
                hashtextextended(metric_definition_uuid::text, 16062026)
            );
        END;
        $$ LANGUAGE plpgsql STRICT
    """)

    op.execute("""
        CREATE OR REPLACE FUNCTION reject_published_snapshot_child_mutation()
        RETURNS trigger AS $$
        DECLARE old_snapshot_id uuid;
        DECLARE new_snapshot_id uuid;
        DECLARE old_metric_definition_id uuid;
        DECLARE new_metric_definition_id uuid;
        DECLARE target_metric_definition_id uuid;
        DECLARE target_snapshot_id uuid;
        BEGIN
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
                        ARRAY[
                            old_metric_definition_id,
                            new_metric_definition_id
                        ]
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
    """)

    op.execute("""
        CREATE OR REPLACE FUNCTION reject_published_metric_definition_mutation()
        RETURNS trigger AS $$
        DECLARE old_metric_definition_id uuid;
        DECLARE new_metric_definition_id uuid;
        DECLARE target_metric_definition_id uuid;
        DECLARE target_snapshot_id uuid;
        BEGIN
            IF TG_OP IN ('UPDATE', 'DELETE') THEN
                old_metric_definition_id := OLD.id;
            END IF;
            IF TG_OP = 'UPDATE' THEN
                new_metric_definition_id := NEW.id;
            END IF;

            FOR target_metric_definition_id IN
                SELECT DISTINCT metric_definition_id
                FROM unnest(
                    ARRAY[
                        old_metric_definition_id,
                        new_metric_definition_id
                    ]
                ) AS ids(metric_definition_id)
                WHERE metric_definition_id IS NOT NULL
                ORDER BY metric_definition_id
            LOOP
                PERFORM lock_metric_definition_mutation(
                    target_metric_definition_id
                );
            END LOOP;

            FOR target_snapshot_id IN
                SELECT DISTINCT metric_values.snapshot_id AS snapshot_id
                FROM metric_values
                WHERE metric_values.metric_definition_id IN (
                    old_metric_definition_id,
                    new_metric_definition_id
                )
                ORDER BY snapshot_id
            LOOP
                PERFORM lock_snapshot_mutation(target_snapshot_id);
            END LOOP;

            IF EXISTS (
                SELECT 1
                FROM metric_values
                JOIN report_snapshots
                  ON report_snapshots.id = metric_values.snapshot_id
                 AND report_snapshots.organization_id =
                     metric_values.organization_id
                WHERE metric_values.metric_definition_id IN (
                    old_metric_definition_id,
                    new_metric_definition_id
                )
                  AND report_snapshots.status IN ('ready', 'partial')
            ) THEN
                RAISE EXCEPTION 'published metric definitions are append-only';
            END IF;

            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$ LANGUAGE plpgsql
    """)


def downgrade():
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
        CREATE OR REPLACE FUNCTION reject_published_metric_definition_mutation()
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
    op.execute("DROP FUNCTION IF EXISTS lock_metric_definition_mutation(uuid)")
    op.execute("DROP FUNCTION lock_snapshot_mutation(uuid)")
