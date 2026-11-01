"""Create the canonical snapshot foundation.

Revision ID: 0001
Revises: None
"""

from alembic import op

from roster_scout.models import Base


revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # The metadata is manually reviewed and contains explicit, named constraints.
    Base.metadata.create_all(bind=op.get_bind(), checkfirst=False)
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


def downgrade():
    op.execute("DROP FUNCTION IF EXISTS reject_published_snapshot_child_mutation() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS reject_published_snapshot_mutation() CASCADE")
    Base.metadata.drop_all(bind=op.get_bind(), checkfirst=False)
