"""Bind authoritative source IDs to the canonical Player identity.

Revision ID: 0010
Revises: 0009
"""

from alembic import op


revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM source_records AS source
                JOIN player_team_seasons AS identity
                  ON identity.id = source.player_team_season_id
                 AND identity.snapshot_id = source.snapshot_id
                 AND identity.organization_id = source.organization_id
                JOIN players AS player ON player.id = identity.player_id
                WHERE source.player_team_season_id IS NOT NULL
                  AND source.source_player_id IS NOT NULL
                  AND (
                      player.source <> 'ncaa'
                      OR player.source_player_id IS NULL
                      OR player.source_player_id <> source.source_player_id
                  )
            ) THEN
                RAISE EXCEPTION USING
                    MESSAGE = 'authoritative source player mismatch',
                    ERRCODE = '23514',
                    CONSTRAINT = 'ck_source_records_authoritative_source_player';
            END IF;
        END;
        $$
        """
    )

    op.execute(
        """
        CREATE FUNCTION enforce_source_record_authoritative_source_player()
        RETURNS trigger AS $$
        DECLARE
            canonical_source varchar;
            canonical_source_player_id varchar;
        BEGIN
            IF NEW.player_team_season_id IS NULL
               OR NEW.source_player_id IS NULL THEN
                RETURN NEW;
            END IF;

            SELECT player.source, player.source_player_id
            INTO canonical_source, canonical_source_player_id
            FROM player_team_seasons AS identity
            JOIN players AS player ON player.id = identity.player_id
            WHERE identity.id = NEW.player_team_season_id
              AND identity.snapshot_id = NEW.snapshot_id
              AND identity.organization_id = NEW.organization_id;

            IF NOT FOUND
               OR canonical_source <> 'ncaa'
               OR canonical_source_player_id IS NULL
               OR canonical_source_player_id <> NEW.source_player_id THEN
                RAISE EXCEPTION USING
                    MESSAGE = 'authoritative source player mismatch',
                    ERRCODE = '23514',
                    CONSTRAINT = 'ck_source_records_authoritative_source_player';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_source_records_authoritative_source_player
        BEFORE INSERT OR UPDATE OF
            source_player_id,
            player_team_season_id,
            snapshot_id,
            organization_id
        ON source_records
        FOR EACH ROW
        EXECUTE FUNCTION enforce_source_record_authoritative_source_player()
        """
    )

    op.execute(
        """
        CREATE FUNCTION enforce_player_authoritative_source_player()
        RETURNS trigger AS $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM player_team_seasons AS identity
                JOIN source_records AS source
                  ON source.player_team_season_id = identity.id
                 AND source.snapshot_id = identity.snapshot_id
                 AND source.organization_id = identity.organization_id
                WHERE identity.player_id = OLD.id
                  AND source.source_player_id IS NOT NULL
                  AND (
                      NEW.source <> 'ncaa'
                      OR NEW.source_player_id IS NULL
                      OR NEW.source_player_id <> source.source_player_id
                  )
            ) THEN
                RAISE EXCEPTION USING
                    MESSAGE = 'authoritative source player mismatch',
                    ERRCODE = '23514',
                    CONSTRAINT = 'ck_players_authoritative_source_player';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_players_authoritative_source_player
        BEFORE UPDATE OF source, source_player_id
        ON players
        FOR EACH ROW
        EXECUTE FUNCTION enforce_player_authoritative_source_player()
        """
    )

    op.execute(
        """
        CREATE FUNCTION enforce_player_team_season_authoritative_source_player()
        RETURNS trigger AS $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM source_records AS source
                LEFT JOIN players AS player ON player.id = NEW.player_id
                WHERE source.player_team_season_id = OLD.id
                  AND source.snapshot_id = NEW.snapshot_id
                  AND source.organization_id = NEW.organization_id
                  AND source.source_player_id IS NOT NULL
                  AND (
                      player.id IS NULL
                      OR player.source <> 'ncaa'
                      OR player.source_player_id IS NULL
                      OR player.source_player_id <> source.source_player_id
                  )
            ) THEN
                RAISE EXCEPTION USING
                    MESSAGE = 'authoritative source player mismatch',
                    ERRCODE = '23514',
                    CONSTRAINT = 'ck_player_team_seasons_authoritative_source_player';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_player_team_seasons_authoritative_source_player
        BEFORE UPDATE OF player_id
        ON player_team_seasons
        FOR EACH ROW
        EXECUTE FUNCTION enforce_player_team_season_authoritative_source_player()
        """
    )


def downgrade():
    op.execute(
        "DROP TRIGGER IF EXISTS "
        "trg_player_team_seasons_authoritative_source_player "
        "ON player_team_seasons"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_players_authoritative_source_player ON players"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS "
        "trg_source_records_authoritative_source_player ON source_records"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS "
        "enforce_player_team_season_authoritative_source_player()"
    )
    op.execute("DROP FUNCTION IF EXISTS enforce_player_authoritative_source_player()")
    op.execute(
        "DROP FUNCTION IF EXISTS enforce_source_record_authoritative_source_player()"
    )
