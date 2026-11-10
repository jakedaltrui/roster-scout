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
        CREATE OR REPLACE FUNCTION reject_published_snapshot_child_mutation()
        RETURNS trigger AS $$
        DECLARE old_snapshot_id uuid;
        DECLARE new_snapshot_id uuid;
        DECLARE target_snapshot_id uuid;
        BEGIN
            IF TG_OP IN ('UPDATE', 'DELETE') THEN
                old_snapshot_id := OLD.snapshot_id;
            END IF;
            IF TG_OP IN ('INSERT', 'UPDATE') THEN
                new_snapshot_id := NEW.snapshot_id;
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
    op.execute("DROP FUNCTION lock_snapshot_mutation(uuid)")
