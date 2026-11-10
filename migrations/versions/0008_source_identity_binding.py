"""Bind each canonical fact to evidence for the same snapshot identity.

Revision ID: 0008
Revises: 0007
"""

from alembic import op
import sqlalchemy as sa


revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


FACT_TABLES = ("batting_facts", "pitching_facts")


def upgrade():
    op.add_column(
        "source_records",
        sa.Column("player_team_season_id", sa.Uuid(), nullable=True),
    )

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT mappings.source_record_id
                FROM (
                    SELECT source_record_id, player_team_season_id
                    FROM batting_facts
                    UNION ALL
                    SELECT source_record_id, player_team_season_id
                    FROM pitching_facts
                ) AS mappings
                GROUP BY mappings.source_record_id
                HAVING COUNT(DISTINCT mappings.player_team_season_id) > 1
            ) THEN
                RAISE EXCEPTION
                    'source record maps to multiple player identities';
            END IF;
        END;
        $$
        """
    )
    op.execute(
        "ALTER TABLE source_records "
        "DISABLE TRIGGER trg_source_records_immutable"
    )
    op.execute(
        """
        WITH mappings AS (
            SELECT source_record_id, player_team_season_id
            FROM batting_facts
            UNION ALL
            SELECT source_record_id, player_team_season_id
            FROM pitching_facts
        ),
        unique_mappings AS (
            SELECT DISTINCT ON (source_record_id)
                source_record_id,
                player_team_season_id
            FROM mappings
            ORDER BY source_record_id, player_team_season_id
        )
        UPDATE source_records AS source
        SET player_team_season_id = mapping.player_team_season_id
        FROM unique_mappings AS mapping
        WHERE source.id = mapping.source_record_id
        """
    )
    op.execute(
        "ALTER TABLE source_records "
        "ENABLE TRIGGER trg_source_records_immutable"
    )

    op.create_foreign_key(
        "fk_source_records_player_snapshot_organization",
        "source_records",
        "player_team_seasons",
        ["player_team_season_id", "snapshot_id", "organization_id"],
        ["id", "snapshot_id", "organization_id"],
    )
    op.create_unique_constraint(
        "uq_source_records_identity_owner",
        "source_records",
        ["id", "player_team_season_id", "snapshot_id", "organization_id"],
    )

    for table_name in FACT_TABLES:
        op.drop_constraint(
            f"fk_{table_name}_source_snapshot_organization",
            table_name,
            type_="foreignkey",
        )
        op.create_foreign_key(
            f"fk_{table_name}_source_identity",
            table_name,
            "source_records",
            [
                "source_record_id",
                "player_team_season_id",
                "snapshot_id",
                "organization_id",
            ],
            [
                "id",
                "player_team_season_id",
                "snapshot_id",
                "organization_id",
            ],
        )


def downgrade():
    for table_name in reversed(FACT_TABLES):
        op.drop_constraint(
            f"fk_{table_name}_source_identity",
            table_name,
            type_="foreignkey",
        )
        op.create_foreign_key(
            f"fk_{table_name}_source_snapshot_organization",
            table_name,
            "source_records",
            ["source_record_id", "snapshot_id", "organization_id"],
            ["id", "snapshot_id", "organization_id"],
        )

    op.drop_constraint(
        "uq_source_records_identity_owner",
        "source_records",
        type_="unique",
    )
    op.drop_constraint(
        "fk_source_records_player_snapshot_organization",
        "source_records",
        type_="foreignkey",
    )
    op.drop_column("source_records", "player_team_season_id")
