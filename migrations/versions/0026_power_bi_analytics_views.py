"""Add tenant-bound Power BI analytics views.

Revision ID: 0026
Revises: 0025
"""

from alembic import op


revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


VIEW_NAMES = (
    "pbi_sources", "pbi_data_quality", "pbi_game_logs",
    "pbi_career_seasons", "pbi_scoped_fielding", "pbi_scoped_pitching",
    "pbi_scoped_batting", "pbi_scopes", "pbi_metrics", "pbi_fielding",
    "pbi_pitching", "pbi_batting", "pbi_players", "pbi_snapshots",
)


VIEW_SQL = {
    "pbi_snapshots": """
        SELECT rs.organization_id, rs.id AS snapshot_id,
               rs.organization_id::text || '|' || rs.id::text AS security_key,
               rs.report_id, r.name AS report_name, t.name AS opponent_name,
               t.source_team_id, ts.source_season_team_id, ts.division,
               s.year AS season_year, rs.status AS snapshot_status,
               rs.schema_version, rs.collection_contract_version,
               rs.source_collected_at, rs.published_at
        FROM report_snapshots rs
        JOIN reports r ON r.id = rs.report_id AND r.organization_id = rs.organization_id
        JOIN team_seasons ts ON ts.id = r.team_season_id
        JOIN teams t ON t.id = ts.team_id
        JOIN seasons s ON s.id = ts.season_id
        WHERE rs.status IN ('ready', 'partial') AND rs.published_at IS NOT NULL
    """,
    "pbi_players": """
        SELECT pts.organization_id, pts.snapshot_id,
               pts.organization_id::text || '|' || pts.snapshot_id::text AS security_key,
               pts.id AS player_team_season_id, pts.player_id,
               p.source_player_id, pts.display_name, pts.jersey_number,
               pts.position, pts.identity_status,
               COALESCE(src.source_url, '') AS source_url,
               COALESCE(src.class_year, '') AS class_year
        FROM player_team_seasons pts
        JOIN pbi_snapshots snap ON snap.organization_id = pts.organization_id AND snap.snapshot_id = pts.snapshot_id
        JOIN players p ON p.id = pts.player_id
        LEFT JOIN LATERAL (
            SELECT sr.source_url,
                   COALESCE(sr.raw_values->>'Yr', sr.raw_values->>'Class', '') AS class_year
            FROM source_records sr
            WHERE sr.organization_id = pts.organization_id AND sr.snapshot_id = pts.snapshot_id
              AND sr.player_team_season_id = pts.id
            ORDER BY sr.row_ordinal, sr.id LIMIT 1
        ) src ON TRUE
    """,
    "pbi_batting": """
        SELECT bf.organization_id, bf.snapshot_id,
               bf.organization_id::text || '|' || bf.snapshot_id::text AS security_key,
               bf.player_team_season_id, bf.source_record_id,
               bf.games, bf.games_started, bf.plate_appearances, bf.at_bats,
               bf.runs, bf.hits, bf.doubles, bf.triples, bf.home_runs,
               bf.runs_batted_in, bf.walks, bf.hit_by_pitch, bf.strikeouts,
               bf.sacrifice_flies, bf.sacrifice_hits, bf.stolen_bases,
               bf.caught_stealing, bf.total_bases
        FROM batting_facts bf JOIN pbi_snapshots snap
          ON snap.organization_id = bf.organization_id AND snap.snapshot_id = bf.snapshot_id
    """,
    "pbi_pitching": """
        SELECT pf.organization_id, pf.snapshot_id,
               pf.organization_id::text || '|' || pf.snapshot_id::text AS security_key,
               pf.player_team_season_id, pf.source_record_id,
               pf.appearances, pf.games_started, pf.innings_outs,
               pf.hits_allowed, pf.runs_allowed, pf.earned_runs, pf.walks,
               pf.strikeouts, pf.home_runs_allowed, pf.hit_batters,
               pf.batters_faced, pf.opponent_at_bats, pf.opponent_sacrifice_flies
        FROM pitching_facts pf JOIN pbi_snapshots snap
          ON snap.organization_id = pf.organization_id AND snap.snapshot_id = pf.snapshot_id
    """,
    "pbi_fielding": """
        SELECT ff.organization_id, ff.snapshot_id,
               ff.organization_id::text || '|' || ff.snapshot_id::text AS security_key,
               ff.player_team_season_id, ff.source_record_id,
               ff.games, ff.games_started, ff.putouts, ff.assists, ff.errors,
               ff.total_chances, ff.double_plays, ff.fielding_percentage,
               ff.passed_balls, ff.stolen_base_attempts, ff.caught_stealing_by
        FROM fielding_facts ff JOIN pbi_snapshots snap
          ON snap.organization_id = ff.organization_id AND snap.snapshot_id = ff.snapshot_id
    """,
    "pbi_metrics": """
        SELECT mv.organization_id, mv.snapshot_id,
               mv.organization_id::text || '|' || mv.snapshot_id::text AS security_key,
               mv.player_team_season_id, mv.statistic_scope_id,
               md.code AS metric_code, md.version AS metric_version,
               md.formula, mv.scope, mv.status, mv.value,
               mv.reason_code, mv.unavailable_reason, mv.sample_json, mv.inputs_json
        FROM metric_values mv JOIN pbi_snapshots snap
          ON snap.organization_id = mv.organization_id AND snap.snapshot_id = mv.snapshot_id
        JOIN metric_definitions md ON md.id = mv.metric_definition_id
    """,
    "pbi_scopes": """
        SELECT ss.organization_id, ss.snapshot_id,
               ss.organization_id::text || '|' || ss.snapshot_id::text AS security_key,
               ss.id AS statistic_scope_id, ss.scope_kind, ss.scope_code,
               ss.stat_type, ss.display_label, ss.status, ss.availability_reason,
               ss.source_identifier, ss.source_url, ss.start_date, ss.end_date,
               ss.actual_game_count, ss.report_conference_id, ss.collected_at,
               ss.parser_version, ss.schema_version
        FROM statistic_scopes ss JOIN pbi_snapshots snap
          ON snap.organization_id = ss.organization_id AND snap.snapshot_id = ss.snapshot_id
    """,
    "pbi_scoped_batting": """
        SELECT sf.organization_id, sf.snapshot_id,
               sf.organization_id::text || '|' || sf.snapshot_id::text AS security_key,
               sf.statistic_scope_id, sf.player_team_season_id,
               sf.at_bats, sf.hits,
               NULLIF(sf.counts_json->>'plate_appearances', '')::integer AS plate_appearances,
               NULLIF(sf.counts_json->>'walks', '')::integer AS walks,
               NULLIF(sf.counts_json->>'hit_by_pitch', '')::integer AS hit_by_pitch,
               NULLIF(sf.counts_json->>'strikeouts', '')::integer AS strikeouts,
               NULLIF(sf.counts_json->>'sacrifice_flies', '')::integer AS sacrifice_flies,
               NULLIF(sf.counts_json->>'home_runs', '')::integer AS home_runs,
               NULLIF(sf.counts_json->>'total_bases', '')::integer AS total_bases,
               sf.counts_json, sf.sample_json
        FROM scoped_batting_facts sf JOIN pbi_snapshots snap
          ON snap.organization_id = sf.organization_id AND snap.snapshot_id = sf.snapshot_id
    """,
    "pbi_scoped_pitching": """
        SELECT sf.organization_id, sf.snapshot_id,
               sf.organization_id::text || '|' || sf.snapshot_id::text AS security_key,
               sf.statistic_scope_id, sf.player_team_season_id,
               sf.opponent_at_bats, sf.hits_allowed,
               NULLIF(sf.counts_json->>'innings_outs', '')::integer AS innings_outs,
               NULLIF(sf.counts_json->>'earned_runs', '')::integer AS earned_runs,
               NULLIF(sf.counts_json->>'walks', '')::integer AS walks,
               NULLIF(sf.counts_json->>'strikeouts', '')::integer AS strikeouts,
               NULLIF(sf.counts_json->>'home_runs_allowed', '')::integer AS home_runs_allowed,
               NULLIF(sf.counts_json->>'batters_faced', '')::integer AS batters_faced,
               sf.counts_json, sf.sample_json
        FROM scoped_pitching_facts sf JOIN pbi_snapshots snap
          ON snap.organization_id = sf.organization_id AND snap.snapshot_id = sf.snapshot_id
    """,
    "pbi_scoped_fielding": """
        SELECT sf.organization_id, sf.snapshot_id,
               sf.organization_id::text || '|' || sf.snapshot_id::text AS security_key,
               sf.statistic_scope_id, sf.player_team_season_id,
               sf.putouts, sf.assists, sf.errors, sf.total_chances,
               sf.counts_json, sf.sample_json
        FROM scoped_fielding_facts sf JOIN pbi_snapshots snap
          ON snap.organization_id = sf.organization_id AND snap.snapshot_id = sf.snapshot_id
    """,
    "pbi_career_seasons": """
        SELECT cf.organization_id, cf.snapshot_id,
               cf.organization_id::text || '|' || cf.snapshot_id::text AS security_key,
               cf.player_team_season_id, cf.stat_type, cf.season_label,
               cf.school_name, cf.division, cf.source_season_id,
               cf.source_season_team_id, cf.counts_json, cf.rates_json,
               cf.at_bats, cf.hits, cf.innings_outs, cf.earned_runs,
               cf.putouts, cf.assists, cf.errors, cf.total_chances
        FROM career_season_facts cf JOIN pbi_snapshots snap
          ON snap.organization_id = cf.organization_id AND snap.snapshot_id = cf.snapshot_id
    """,
    "pbi_game_logs": """
        SELECT gl.organization_id, gl.snapshot_id,
               gl.organization_id::text || '|' || gl.snapshot_id::text AS security_key,
               gl.player_team_season_id, gl.game_id, 'batting'::text AS stat_type,
               g.game_date, g.completed_at, g.venue, g.result_code,
               g.opponent_team_id, g.opponent_season_team_id, gl.counts_json
        FROM batting_game_log_facts gl JOIN pbi_snapshots snap
          ON snap.organization_id = gl.organization_id AND snap.snapshot_id = gl.snapshot_id
        JOIN games g ON g.id = gl.game_id AND g.organization_id = gl.organization_id AND g.snapshot_id = gl.snapshot_id
        UNION ALL
        SELECT gl.organization_id, gl.snapshot_id,
               gl.organization_id::text || '|' || gl.snapshot_id::text,
               gl.player_team_season_id, gl.game_id, 'pitching'::text,
               g.game_date, g.completed_at, g.venue, g.result_code,
               g.opponent_team_id, g.opponent_season_team_id, gl.counts_json
        FROM pitching_game_log_facts gl JOIN pbi_snapshots snap
          ON snap.organization_id = gl.organization_id AND snap.snapshot_id = gl.snapshot_id
        JOIN games g ON g.id = gl.game_id AND g.organization_id = gl.organization_id AND g.snapshot_id = gl.snapshot_id
        UNION ALL
        SELECT gl.organization_id, gl.snapshot_id,
               gl.organization_id::text || '|' || gl.snapshot_id::text,
               gl.player_team_season_id, gl.game_id, 'fielding'::text,
               g.game_date, g.completed_at, g.venue, g.result_code,
               g.opponent_team_id, g.opponent_season_team_id, gl.counts_json
        FROM fielding_game_log_facts gl JOIN pbi_snapshots snap
          ON snap.organization_id = gl.organization_id AND snap.snapshot_id = gl.snapshot_id
        JOIN games g ON g.id = gl.game_id AND g.organization_id = gl.organization_id AND g.snapshot_id = gl.snapshot_id
    """,
    "pbi_data_quality": """
        SELECT sec.organization_id, sec.snapshot_id,
               sec.organization_id::text || '|' || sec.snapshot_id::text AS security_key,
               sec.section, sec.status, sec.row_count, sec.verified_empty,
               sec.collected_at, sec.failure_category, sec.source_url,
               sec.schema_version, sec.parser_version
        FROM report_sections sec JOIN pbi_snapshots snap
          ON snap.organization_id = sec.organization_id AND snap.snapshot_id = sec.snapshot_id
    """,
    "pbi_sources": """
        SELECT sr.organization_id, sr.snapshot_id,
               sr.organization_id::text || '|' || sr.snapshot_id::text AS security_key,
               sr.player_team_season_id, sr.section, sr.row_ordinal,
               sr.source_url, sr.source_team_id, sr.source_season_id,
               sr.source_season_team_id, sr.source_stat_category_id,
               sr.source_player_id, sr.collected_at, sr.parser_version, sr.schema_version
        FROM source_records sr JOIN pbi_snapshots snap
          ON snap.organization_id = sr.organization_id AND snap.snapshot_id = sr.snapshot_id
    """,
}


def _replace_audit_constraints(dashboard_enabled):
    op.drop_constraint("ck_security_audit_events_target_type", "security_audit_events", type_="check")
    op.drop_constraint("ck_security_audit_events_action", "security_audit_events", type_="check")
    targets = "'account','organization','invitation','membership','report','job','snapshot','artifact','session'"
    actions = "'identity.sign_in','organization.bootstrap','organization.switch','invitation.create','invitation.deliver','invitation.accept','invitation.revoke','membership.create','membership.remove','membership.role_change','report.create','report.refresh','report.retry','snapshot.access','artifact.download','authorization.deny'"
    if dashboard_enabled:
        targets += ",'dashboard'"
        actions += ",'dashboard.embed_token'"
    op.create_check_constraint("ck_security_audit_events_target_type", "security_audit_events", f"target_type IN ({targets})")
    op.create_check_constraint("ck_security_audit_events_action", "security_audit_events", f"action IN ({actions})")


def upgrade():
    _replace_audit_constraints(True)
    for name in reversed(VIEW_NAMES):
        op.execute(f"CREATE VIEW {name} AS {VIEW_SQL[name]}")
        op.execute(f"REVOKE ALL ON {name} FROM PUBLIC")


def downgrade():
    for name in VIEW_NAMES:
        op.execute(f"DROP VIEW IF EXISTS {name}")
    _replace_audit_constraints(False)
