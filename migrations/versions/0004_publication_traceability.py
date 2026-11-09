"""Require snapshot-owned identity and source links for canonical facts.

Revision ID: 0004
Revises: 0003
"""

from alembic import op
import sqlalchemy as sa


revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


FACT_TABLES = {
    "batting_facts": "batting",
    "pitching_facts": "pitching",
}
REQUIRED_LINKS = ("player_team_season_id", "source_record_id")


def upgrade():
    for table_name, section in FACT_TABLES.items():
        op.execute(f"""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM {table_name} AS fact
                    LEFT JOIN player_team_seasons AS identity
                      ON identity.id = fact.player_team_season_id
                     AND identity.snapshot_id = fact.snapshot_id
                     AND identity.organization_id = fact.organization_id
                    LEFT JOIN source_records AS source
                      ON source.id = fact.source_record_id
                     AND source.snapshot_id = fact.snapshot_id
                     AND source.organization_id = fact.organization_id
                     AND source.section = '{section}'
                    WHERE fact.player_team_season_id IS NULL
                       OR fact.source_record_id IS NULL
                       OR identity.id IS NULL
                       OR source.id IS NULL
                ) THEN
                    RAISE EXCEPTION '{table_name} contains untraceable canonical facts';
                END IF;
            END;
            $$
        """)
        for column_name in REQUIRED_LINKS:
            op.alter_column(
                table_name,
                column_name,
                existing_type=sa.Uuid(),
                existing_nullable=True,
                nullable=False,
            )


def downgrade():
    for table_name in reversed(tuple(FACT_TABLES)):
        for column_name in reversed(REQUIRED_LINKS):
            op.alter_column(
                table_name,
                column_name,
                existing_type=sa.Uuid(),
                existing_nullable=False,
                nullable=True,
            )
