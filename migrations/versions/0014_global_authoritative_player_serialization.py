"""Serialize authoritative identity writes by global Player UUID.

Revision ID: 0014
Revises: 0013
"""

import importlib.util
from pathlib import Path

from alembic import op


revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def _drop_authoritative_triggers():
    op.execute(
        "DROP TRIGGER IF EXISTS "
        "trg_source_records_authoritative_source_player_insert ON source_records"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS "
        "trg_source_records_authoritative_source_player ON source_records"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_players_authoritative_source_player ON players"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS "
        "trg_player_team_seasons_authoritative_source_player_insert "
        "ON player_team_seasons"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS "
        "trg_player_team_seasons_authoritative_source_player "
        "ON player_team_seasons"
    )


def _drop_enforcement_functions():
    op.execute(
        "DROP FUNCTION IF EXISTS "
        "enforce_player_team_season_authoritative_source_player()"
    )
    op.execute("DROP FUNCTION IF EXISTS enforce_player_authoritative_source_player()")
    op.execute(
        "DROP FUNCTION IF EXISTS enforce_source_record_authoritative_source_player()"
    )


def _drop_global_helpers():
    op.execute("DROP FUNCTION IF EXISTS lock_authoritative_global_players(uuid[])")
    op.execute(
        "DROP FUNCTION IF EXISTS lock_authoritative_global_player_mutation(uuid)"
    )


def _restore_exact_0013_contract():
    previous_path = Path(__file__).with_name(
        "0013_authoritative_identity_serialization.py"
    )
    spec = importlib.util.spec_from_file_location(
        "roster_scout_alembic_revision_0013",
        previous_path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load revision 0013")
    previous = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(previous)
    previous.upgrade()


def upgrade():
    _drop_authoritative_triggers()
    _drop_enforcement_functions()
    _drop_global_helpers()

    op.execute(
        """
        CREATE FUNCTION lock_authoritative_global_player_mutation(
            player_uuid uuid
        ) RETURNS void AS $$
        BEGIN
            PERFORM pg_advisory_xact_lock(
                hashtextextended(
                    'roster-scout:authoritative-global-player:'
                    || player_uuid::text,
                    31032026
                )
            );
        END;
        $$ LANGUAGE plpgsql STRICT
        """
    )
    op.execute(
        """
        CREATE FUNCTION lock_authoritative_global_players(player_uuids uuid[])
        RETURNS void AS $$
        DECLARE target_player_id uuid;
        BEGIN
            FOR target_player_id IN
                SELECT player_id
                FROM (
                    SELECT DISTINCT player_id
                    FROM unnest(player_uuids) AS ids(player_id)
                    WHERE player_id IS NOT NULL
                ) AS distinct_players
                ORDER BY player_id::text
            LOOP
                PERFORM lock_authoritative_global_player_mutation(
                    target_player_id
                );
            END LOOP;
        END;
        $$ LANGUAGE plpgsql
        """
    )

    op.execute(
        """
        CREATE FUNCTION enforce_source_record_authoritative_source_player()
        RETURNS trigger AS $$
        DECLARE snapshot_ids uuid[];
        DECLARE organization_ids uuid[];
        DECLARE player_ids uuid[];
        DECLARE global_player_ids uuid[];
        DECLARE identity_ids uuid[];
        BEGIN
            IF TG_OP = 'INSERT' THEN
                SELECT
                    array_agg(affected.snapshot_id ORDER BY affected.snapshot_id::text),
                    array_agg(affected.organization_id ORDER BY
                        affected.organization_id::text, affected.player_id::text),
                    array_agg(affected.player_id ORDER BY
                        affected.organization_id::text, affected.player_id::text),
                    array_agg(affected.player_id ORDER BY affected.player_id::text),
                    array_agg(affected.player_team_season_id ORDER BY
                        affected.player_team_season_id::text)
                INTO snapshot_ids, organization_ids, player_ids,
                    global_player_ids, identity_ids
                FROM (
                    SELECT DISTINCT source.snapshot_id,
                        source.organization_id,
                        identity.player_id,
                        source.player_team_season_id
                    FROM new_source_rows AS source
                    JOIN player_team_seasons AS identity
                      ON identity.id = source.player_team_season_id
                     AND identity.snapshot_id = source.snapshot_id
                     AND identity.organization_id = source.organization_id
                    WHERE source.player_team_season_id IS NOT NULL
                ) AS affected;
            ELSE
                SELECT
                    array_agg(affected.snapshot_id ORDER BY affected.snapshot_id::text),
                    array_agg(affected.organization_id ORDER BY
                        affected.organization_id::text, affected.player_id::text),
                    array_agg(affected.player_id ORDER BY
                        affected.organization_id::text, affected.player_id::text),
                    array_agg(affected.player_id ORDER BY affected.player_id::text),
                    array_agg(affected.player_team_season_id ORDER BY
                        affected.player_team_season_id::text)
                INTO snapshot_ids, organization_ids, player_ids,
                    global_player_ids, identity_ids
                FROM (
                    SELECT DISTINCT source.snapshot_id,
                        source.organization_id,
                        identity.player_id,
                        source.player_team_season_id
                    FROM (
                        SELECT snapshot_id, organization_id,
                            player_team_season_id
                        FROM old_source_rows
                        UNION
                        SELECT snapshot_id, organization_id,
                            player_team_season_id
                        FROM new_source_rows
                    ) AS source
                    JOIN player_team_seasons AS identity
                      ON identity.id = source.player_team_season_id
                     AND identity.snapshot_id = source.snapshot_id
                     AND identity.organization_id = source.organization_id
                    WHERE source.player_team_season_id IS NOT NULL
                ) AS affected;
            END IF;

            PERFORM lock_authoritative_snapshots(snapshot_ids);
            PERFORM lock_authoritative_global_players(global_player_ids);
            PERFORM lock_authoritative_players(organization_ids, player_ids);
            PERFORM lock_authoritative_player_team_seasons(identity_ids);

            IF EXISTS (
                SELECT 1
                FROM new_source_rows AS source
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
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql
        """
    )

    op.execute(
        """
        CREATE FUNCTION enforce_player_authoritative_source_player()
        RETURNS trigger AS $$
        DECLARE snapshot_ids uuid[];
        DECLARE organization_ids uuid[];
        DECLARE player_ids uuid[];
        DECLARE global_player_ids uuid[];
        DECLARE identity_ids uuid[];
        BEGIN
            SELECT array_agg(DISTINCT identity.snapshot_id
                             ORDER BY identity.snapshot_id)
            INTO snapshot_ids
            FROM player_team_seasons AS identity
            JOIN old_player_rows AS old_player ON old_player.id = identity.player_id
            JOIN new_player_rows AS new_player ON new_player.id = old_player.id
            WHERE old_player.source IS DISTINCT FROM new_player.source
               OR old_player.source_player_id IS DISTINCT FROM
                  new_player.source_player_id;

            PERFORM lock_authoritative_snapshots(snapshot_ids);

            SELECT array_agg(changed.id ORDER BY changed.id::text)
            INTO global_player_ids
            FROM (
                SELECT DISTINCT new_player.id
                FROM new_player_rows AS new_player
                JOIN old_player_rows AS old_player
                  ON old_player.id = new_player.id
                WHERE old_player.source IS DISTINCT FROM new_player.source
                   OR old_player.source_player_id IS DISTINCT FROM
                      new_player.source_player_id
            ) AS changed;

            PERFORM lock_authoritative_global_players(global_player_ids);

            SELECT
                array_agg(keys.organization_id ORDER BY
                    keys.organization_id::text, keys.player_id::text),
                array_agg(keys.player_id ORDER BY
                    keys.organization_id::text, keys.player_id::text)
            INTO organization_ids, player_ids
            FROM (
                SELECT DISTINCT organization.id AS organization_id,
                    new_player.id AS player_id
                FROM organizations AS organization
                CROSS JOIN new_player_rows AS new_player
                JOIN old_player_rows AS old_player
                  ON old_player.id = new_player.id
                WHERE old_player.source IS DISTINCT FROM new_player.source
                   OR old_player.source_player_id IS DISTINCT FROM
                      new_player.source_player_id
            ) AS keys;

            PERFORM lock_authoritative_players(organization_ids, player_ids);

            SELECT array_agg(DISTINCT identity.id ORDER BY identity.id)
            INTO identity_ids
            FROM player_team_seasons AS identity
            JOIN new_player_rows AS player ON player.id = identity.player_id;

            PERFORM lock_authoritative_player_team_seasons(identity_ids);

            IF EXISTS (
                SELECT 1
                FROM new_player_rows AS player
                JOIN old_player_rows AS old_player ON old_player.id = player.id
                JOIN player_team_seasons AS identity
                  ON identity.player_id = player.id
                JOIN source_records AS source
                  ON source.player_team_season_id = identity.id
                 AND source.snapshot_id = identity.snapshot_id
                 AND source.organization_id = identity.organization_id
                WHERE (
                    old_player.source IS DISTINCT FROM player.source
                    OR old_player.source_player_id IS DISTINCT FROM
                       player.source_player_id
                )
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
                    CONSTRAINT = 'ck_players_authoritative_source_player';
            END IF;
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql
        """
    )

    op.execute(
        """
        CREATE FUNCTION enforce_player_team_season_authoritative_source_player()
        RETURNS trigger AS $$
        DECLARE snapshot_ids uuid[];
        DECLARE organization_ids uuid[];
        DECLARE player_ids uuid[];
        DECLARE global_player_ids uuid[];
        DECLARE identity_ids uuid[];
        BEGIN
            SELECT
                array_agg(affected.snapshot_id ORDER BY affected.snapshot_id::text),
                array_agg(affected.organization_id ORDER BY
                    affected.organization_id::text, affected.player_id::text),
                array_agg(affected.player_id ORDER BY
                    affected.organization_id::text, affected.player_id::text),
                array_agg(affected.player_id ORDER BY affected.player_id::text),
                array_agg(affected.id ORDER BY affected.id::text)
            INTO snapshot_ids, organization_ids, player_ids,
                global_player_ids, identity_ids
            FROM (
                SELECT DISTINCT identity.snapshot_id,
                    identity.organization_id,
                    identity.player_id,
                    identity.id
                FROM (
                    SELECT id, snapshot_id, organization_id, player_id
                    FROM old_identity_rows
                    UNION
                    SELECT id, snapshot_id, organization_id, player_id
                    FROM new_identity_rows
                ) AS identity
            ) AS affected;

            PERFORM lock_authoritative_snapshots(snapshot_ids);
            PERFORM lock_authoritative_global_players(global_player_ids);
            PERFORM lock_authoritative_players(organization_ids, player_ids);
            PERFORM lock_authoritative_player_team_seasons(identity_ids);

            IF EXISTS (
                SELECT 1
                FROM new_identity_rows AS identity
                JOIN old_identity_rows AS old_identity
                  ON old_identity.id = identity.id
                JOIN source_records AS source
                  ON source.player_team_season_id = identity.id
                 AND source.snapshot_id = identity.snapshot_id
                 AND source.organization_id = identity.organization_id
                JOIN players AS player ON player.id = identity.player_id
                WHERE (
                    old_identity.player_id IS DISTINCT FROM identity.player_id
                    OR old_identity.snapshot_id IS DISTINCT FROM
                       identity.snapshot_id
                    OR old_identity.organization_id IS DISTINCT FROM
                       identity.organization_id
                )
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
                    CONSTRAINT =
                        'ck_player_team_seasons_authoritative_source_player';
            END IF;
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql
        """
    )

    op.execute(
        """
        CREATE TRIGGER trg_source_records_authoritative_source_player_insert
        AFTER INSERT ON source_records
        REFERENCING NEW TABLE AS new_source_rows
        FOR EACH STATEMENT
        EXECUTE FUNCTION enforce_source_record_authoritative_source_player()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_source_records_authoritative_source_player
        AFTER UPDATE ON source_records
        REFERENCING OLD TABLE AS old_source_rows NEW TABLE AS new_source_rows
        FOR EACH STATEMENT
        EXECUTE FUNCTION enforce_source_record_authoritative_source_player()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_players_authoritative_source_player
        AFTER UPDATE ON players
        REFERENCING OLD TABLE AS old_player_rows NEW TABLE AS new_player_rows
        FOR EACH STATEMENT
        EXECUTE FUNCTION enforce_player_authoritative_source_player()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_player_team_seasons_authoritative_source_player
        AFTER UPDATE ON player_team_seasons
        REFERENCING OLD TABLE AS old_identity_rows NEW TABLE AS new_identity_rows
        FOR EACH STATEMENT
        EXECUTE FUNCTION enforce_player_team_season_authoritative_source_player()
        """
    )


def downgrade():
    _drop_authoritative_triggers()
    _drop_enforcement_functions()
    _drop_global_helpers()
    _restore_exact_0013_contract()
