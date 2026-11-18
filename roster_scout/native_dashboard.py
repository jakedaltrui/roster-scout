"""Lightweight, tenant-scoped dashboard projection from one saved snapshot."""

from __future__ import annotations

from decimal import Decimal
import uuid

from sqlalchemy import func, select

from .metrics import aggregate_compatible_counts, calculate_metric
from .models import (
    BattingFact,
    BattingGameLogFact,
    FieldingFact,
    Game,
    PitchingFact,
    PitchingGameLogFact,
    PlayerTeamSeason,
    ReportJob,
    ReportSection,
    ReportSnapshot,
    ScopedBattingFact,
    ScopedFieldingFact,
    ScopedPitchingFact,
    StatisticScope,
)


_SEASON_FIELDS = {
    "batting": (
        "games", "games_started", "plate_appearances", "at_bats", "runs",
        "hits", "doubles", "triples", "home_runs", "runs_batted_in",
        "walks", "hit_by_pitch", "strikeouts", "sacrifice_flies",
        "sacrifice_hits", "stolen_bases", "caught_stealing",
    ),
    "pitching": (
        "appearances", "games_started", "innings_outs", "hits_allowed",
        "runs_allowed", "earned_runs", "walks", "strikeouts",
        "home_runs_allowed", "hit_batters", "batters_faced",
        "opponent_at_bats", "opponent_sacrifice_flies",
    ),
    "fielding": (
        "games", "games_started", "putouts", "assists", "errors",
        "total_chances", "double_plays", "passed_balls",
        "stolen_base_attempts", "caught_stealing_by",
    ),
}
_SEASON_MODELS = {
    "batting": BattingFact,
    "pitching": PitchingFact,
    "fielding": FieldingFact,
}
_SCOPED_MODELS = {
    "batting": ScopedBattingFact,
    "pitching": ScopedPitchingFact,
    "fielding": ScopedFieldingFact,
}
_SCOPE_LABELS = {
    "full_season": "Full season",
    "last_10": "Last 10",
    "conference": "Conference",
}
_DASHBOARD_SECTION_NAMES = (
    "batting",
    "pitching",
    "fielding",
    "schedule",
    "game_logs:batting",
    "game_logs:pitching",
    "game_logs:fielding",
    "splits:batting",
    "splits:pitching",
)


class NativeDashboardNotFound(LookupError):
    """Raised when the exact authorized snapshot is not available."""


def _json_number(value, *, places=3):
    if value is None:
        return None
    parsed = Decimal(value)
    return float(round(parsed, places))


def _metric(code, counts):
    result = calculate_metric(code, counts)
    return _json_number(result.value, places=4) if result.status == "available" else None


def _fact_counts(row, stat_type):
    return {field: getattr(row, field, None) for field in _SEASON_FIELDS[stat_type]}


def _scoped_counts(row, stat_type):
    raw = dict(row.counts_json or {})
    counts = {field: raw.get(field) for field in _SEASON_FIELDS[stat_type]}
    for field in _SEASON_FIELDS[stat_type]:
        value = getattr(row, field, None)
        if value is not None:
            counts[field] = value
    sample = dict(row.sample_json or {})
    sample_fields = {
        "batting": {"plate_appearances": "pa", "at_bats": "ab"},
        "pitching": {
            "batters_faced": "bf",
            "innings_outs": "outs",
            "opponent_at_bats": "ab",
        },
        "fielding": {"total_chances": "opportunities"},
    }[stat_type]
    for field, sample_key in sample_fields.items():
        if counts.get(field) is None and sample.get(sample_key) is not None:
            counts[field] = sample[sample_key]
    return counts


def _aggregate(rows):
    if not rows:
        return {}
    return aggregate_compatible_counts(rows)


def _fielding_percentage(counts):
    total = counts.get("total_chances")
    putouts = counts.get("putouts")
    assists = counts.get("assists")
    if total in (None, 0) or putouts is None or assists is None:
        return None
    return _json_number((Decimal(putouts) + Decimal(assists)) / Decimal(total), places=4)


def _split_card_values(stat_type, counts):
    """Expose only metrics supported by NCAA's published H/AB split sample."""
    if stat_type == "batting":
        return {
            "primaryLabel": "AVG",
            "primaryValue": _metric("avg", counts),
            "primaryFormat": "rate",
            "secondaryLabel": "AB",
            "secondaryValue": counts.get("at_bats"),
            "secondaryFormat": "count",
        }
    hits = counts.get("hits_allowed")
    at_bats = counts.get("opponent_at_bats")
    opponent_average = None
    if hits is not None and at_bats not in (None, 0):
        opponent_average = _json_number(
            Decimal(hits) / Decimal(at_bats), places=4
        )
    return {
        "primaryLabel": "Opp AVG",
        "primaryValue": opponent_average,
        "primaryFormat": "rate",
        "secondaryLabel": "OAB",
        "secondaryValue": at_bats,
        "secondaryFormat": "count",
    }


def _xbh_rate(counts):
    at_bats = counts.get("at_bats")
    values = tuple(counts.get(key) for key in ("doubles", "triples", "home_runs"))
    if at_bats in (None, 0) or any(value is None for value in values):
        return None
    return _json_number(sum(Decimal(value) for value in values) / Decimal(at_bats), places=4)


def _dashboard_readiness(build_profile, sections):
    """Describe dashboard coverage without promoting a quick snapshot to ready."""
    statuses = {
        name: sections.get(name, {}).get("status", "unavailable")
        for name in _DASHBOARD_SECTION_NAMES
    }
    core_ready = all(statuses[name] == "complete" for name in ("batting", "pitching"))
    if not core_ready:
        state = "unavailable"
    elif build_profile == "detailed" and all(
        status == "complete" for status in statuses.values()
    ):
        state = "ready"
    else:
        state = "partial"
    return {
        "state": state,
        "buildProfile": build_profile or "unknown",
        "sections": [
            {
                "code": name,
                "status": statuses[name],
                "reason": sections.get(name, {}).get("reason"),
            }
            for name in _DASHBOARD_SECTION_NAMES
        ],
    }


def _scope_rows(
    session,
    organization_id,
    snapshot_id,
    scope_code,
    *,
    section_statuses=None,
):
    if scope_code == "full_season":
        section_statuses = section_statuses or {}
        return {
            stat_type: (
                tuple(
                    (row.player_team_season_id, _fact_counts(row, stat_type))
                    for row in session.scalars(
                        select(model).where(
                            model.organization_id == organization_id,
                            model.snapshot_id == snapshot_id,
                        )
                    )
                ),
                section_statuses.get(stat_type, "unavailable"),
                None,
            )
            for stat_type, model in _SEASON_MODELS.items()
        }

    result = {}
    for stat_type, model in _SCOPED_MODELS.items():
        scope = session.scalar(
            select(StatisticScope).where(
                StatisticScope.organization_id == organization_id,
                StatisticScope.snapshot_id == snapshot_id,
                StatisticScope.scope_kind == "computed_game",
                StatisticScope.scope_code == scope_code,
                StatisticScope.stat_type == stat_type,
            )
        )
        if scope is None:
            result[stat_type] = ((), "unavailable", None)
            continue
        rows = tuple(
            (row.player_team_season_id, _scoped_counts(row, stat_type))
            for row in session.scalars(
                select(model).where(
                    model.organization_id == organization_id,
                    model.snapshot_id == snapshot_id,
                    model.statistic_scope_id == scope.id,
                )
            )
        )
        result[stat_type] = (rows, scope.status, scope.actual_game_count)
    return result


def _hitting_leaders(rows, players):
    leaders = []
    for player_id, counts in rows:
        ops = _metric("ops", counts)
        if ops is None:
            continue
        leaders.append({
            "id": str(player_id),
            "name": players.get(player_id, {}).get("name", "Unknown player"),
            "position": players.get(player_id, {}).get("position") or "—",
            "ops": ops,
            "average": _metric("avg", counts),
            "homeRuns": counts.get("home_runs"),
            "plateAppearances": counts.get("plate_appearances"),
        })
    leaders.sort(key=lambda item: (-item["ops"], item["name"].casefold()))
    return leaders[:8]


def _pitching_leaders(rows, players):
    leaders = []
    for player_id, counts in rows:
        era = _metric("era", counts)
        if era is None:
            continue
        leaders.append({
            "id": str(player_id),
            "name": players.get(player_id, {}).get("name", "Unknown player"),
            "position": players.get(player_id, {}).get("position") or "—",
            "era": era,
            "whip": _metric("whip", counts),
            "kPer9": _metric("k_per_9", counts),
            "inningsOuts": counts.get("innings_outs"),
        })
    leaders.sort(key=lambda item: (item["era"], item["name"].casefold()))
    return leaders[:8]


def _fielding_leaders(rows, players):
    leaders = []
    for player_id, counts in rows:
        chances = counts.get("total_chances")
        if chances is None:
            continue
        leaders.append({
            "id": str(player_id),
            "name": players.get(player_id, {}).get("name", "Unknown player"),
            "position": players.get(player_id, {}).get("position") or "—",
            "games": counts.get("games"),
            "totalChances": chances,
            "errors": counts.get("errors"),
            "percentage": _fielding_percentage(counts),
        })
    leaders.sort(key=lambda item: (-item["totalChances"], item["name"].casefold()))
    return leaders[:12]


def _player_cards(scope_rows, players):
    cards = {
        player_id: {
            "id": str(player_id),
            "name": details["name"],
            "position": details.get("position") or "—",
            "batting": None,
            "pitching": None,
            "fielding": None,
        }
        for player_id, details in players.items()
    }
    for player_id, counts in scope_rows["batting"][0]:
        cards[player_id]["batting"] = {
            "plateAppearances": counts.get("plate_appearances"),
            "average": _metric("avg", counts),
            "onBase": _metric("obp", counts),
            "slugging": _metric("slg", counts),
            "ops": _metric("ops", counts),
            "iso": _metric("iso", counts),
            "walkRate": _metric("batting_bb_pct", counts),
            "strikeoutRate": _metric("batting_k_pct", counts),
            "babip": _metric("babip", counts),
        }
    for player_id, counts in scope_rows["pitching"][0]:
        cards[player_id]["pitching"] = {
            "inningsOuts": counts.get("innings_outs"),
            "era": _metric("era", counts),
            "whip": _metric("whip", counts),
            "kPer9": _metric("k_per_9", counts),
            "bbPer9": _metric("bb_per_9", counts),
            "hPer9": _metric("h_per_9", counts),
            "hrPer9": _metric("hr_per_9", counts),
            "strikeoutRate": _metric("pitching_k_pct", counts),
            "walkRate": _metric("pitching_bb_pct", counts),
            "babip": _metric("babip_allowed", counts),
        }
    for player_id, counts in scope_rows["fielding"][0]:
        cards[player_id]["fielding"] = {
            "games": counts.get("games"),
            "totalChances": counts.get("total_chances"),
            "errors": counts.get("errors"),
            "percentage": _fielding_percentage(counts),
        }
    return sorted(cards.values(), key=lambda item: item["name"].casefold())


def _scope_projection(
    session,
    organization_id,
    snapshot_id,
    scope_code,
    players,
    *,
    section_statuses=None,
):
    scope_rows = _scope_rows(
        session,
        organization_id,
        snapshot_id,
        scope_code,
        section_statuses=section_statuses,
    )
    batting_rows, batting_status, batting_games = scope_rows["batting"]
    pitching_rows, pitching_status, pitching_games = scope_rows["pitching"]
    fielding_rows, fielding_status, fielding_games = scope_rows["fielding"]
    batting = _aggregate([counts for _, counts in batting_rows])
    pitching = _aggregate([counts for _, counts in pitching_rows])
    fielding = _aggregate([counts for _, counts in fielding_rows])
    statuses = (batting_status, pitching_status, fielding_status)
    if all(status == "complete" for status in statuses):
        status = "complete"
    elif any(status in {"complete", "partial"} for status in statuses):
        status = "partial"
    else:
        status = "unavailable"
    games = next(
        (value for value in (batting_games, pitching_games, fielding_games) if value is not None),
        None,
    )
    return {
        "label": _SCOPE_LABELS[scope_code],
        "status": status,
        "games": games,
        "metrics": {
            "ops": _metric("ops", batting),
            "strikeoutRate": _metric("batting_k_pct", batting),
            "walkRate": _metric("batting_bb_pct", batting),
            "xbhRate": _xbh_rate(batting),
            "whip": _metric("whip", pitching),
            "era": _metric("era", pitching),
            "kPer9": _metric("k_per_9", pitching),
            "bbPer9": _metric("bb_per_9", pitching),
            "fielding": _fielding_percentage(fielding),
        },
        "hittingLeaders": _hitting_leaders(batting_rows, players),
        "pitchingLeaders": _pitching_leaders(pitching_rows, players),
        "fieldingLeaders": _fielding_leaders(fielding_rows, players),
        "players": _player_cards(scope_rows, players),
    }


def _recent_games(session, organization_id, snapshot_id):
    games = tuple(
        session.scalars(
            select(Game)
            .where(
                Game.organization_id == organization_id,
                Game.snapshot_id == snapshot_id,
                Game.status == "completed",
            )
            .order_by(
                Game.game_date.desc(),
                Game.doubleheader_ordinal.desc(),
                Game.source_game_id.desc().nullslast(),
            )
            .limit(10)
        )
    )
    result = []
    for game in reversed(games):
        scored = session.scalar(
            select(func.sum(BattingGameLogFact.runs)).where(
                BattingGameLogFact.organization_id == organization_id,
                BattingGameLogFact.snapshot_id == snapshot_id,
                BattingGameLogFact.game_id == game.id,
            )
        )
        allowed = session.scalar(
            select(func.sum(PitchingGameLogFact.runs_allowed)).where(
                PitchingGameLogFact.organization_id == organization_id,
                PitchingGameLogFact.snapshot_id == snapshot_id,
                PitchingGameLogFact.game_id == game.id,
            )
        )
        result.append({
            "date": game.game_date.isoformat(),
            "result": game.result_code,
            "scored": int(scored) if scored is not None else None,
            "allowed": int(allowed) if allowed is not None else None,
        })
    return result


def _split_projection(session, organization_id, snapshot_id):
    scopes = tuple(
        session.scalars(
            select(StatisticScope)
            .where(
                StatisticScope.organization_id == organization_id,
                StatisticScope.snapshot_id == snapshot_id,
                StatisticScope.scope_kind == "source_split",
            )
            .order_by(
                StatisticScope.stat_type,
                StatisticScope.display_label,
                StatisticScope.scope_code,
            )
        )
    )
    projections = []
    for scope in scopes:
        model = _SCOPED_MODELS[scope.stat_type]
        rows = tuple(
            _scoped_counts(row, scope.stat_type)
            for row in session.scalars(
                select(model).where(
                    model.organization_id == organization_id,
                    model.snapshot_id == snapshot_id,
                    model.statistic_scope_id == scope.id,
                )
            )
        )
        counts = _aggregate(rows)
        values = _split_card_values(scope.stat_type, counts)
        projections.append({
            "code": scope.scope_code,
            "label": scope.display_label,
            "statType": scope.stat_type,
            "status": scope.status,
            **values,
        })
    return projections


def load_native_dashboard(session, *, organization_id, report_id, snapshot_id):
    """Return one small JSON-safe dashboard view model for an exact snapshot."""
    organization_id = uuid.UUID(str(organization_id))
    report_id = uuid.UUID(str(report_id))
    snapshot_id = uuid.UUID(str(snapshot_id))
    snapshot = session.scalar(
        select(ReportSnapshot).where(
            ReportSnapshot.id == snapshot_id,
            ReportSnapshot.report_id == report_id,
            ReportSnapshot.organization_id == organization_id,
            ReportSnapshot.status.in_(("ready", "partial")),
            ReportSnapshot.published_at.is_not(None),
        )
    )
    if snapshot is None:
        raise NativeDashboardNotFound("snapshot dashboard unavailable")

    player_rows = tuple(
        session.scalars(
            select(PlayerTeamSeason).where(
                PlayerTeamSeason.organization_id == organization_id,
                PlayerTeamSeason.snapshot_id == snapshot_id,
            )
        )
    )
    players = {
        row.id: {
            "name": row.display_name,
            "position": row.position,
            "jersey": row.jersey_number,
        }
        for row in player_rows
    }
    job = session.scalar(
        select(ReportJob).where(
            ReportJob.organization_id == organization_id,
            ReportJob.report_id == report_id,
            ReportJob.result_snapshot_id == snapshot_id,
        )
    )
    section_rows = tuple(
        session.scalars(
            select(ReportSection).where(
                ReportSection.organization_id == organization_id,
                ReportSection.snapshot_id == snapshot_id,
            )
        )
    )
    section_details = {
        row.section: {
            "status": row.status,
            "reason": row.failure_category,
        }
        for row in section_rows
    }
    section_statuses = {
        name: details["status"] for name, details in section_details.items()
    }
    readiness = _dashboard_readiness(
        job.build_profile if job is not None else None,
        section_details,
    )
    return {
        "snapshotId": str(snapshot.id),
        "publishedAt": snapshot.published_at.isoformat(),
        "status": snapshot.status,
        "readiness": readiness,
        "roster": [
            {
                "id": str(row.id),
                "name": row.display_name,
                "number": row.jersey_number,
                "position": row.position,
            }
            for row in sorted(player_rows, key=lambda item: item.display_name.casefold())
        ],
        "scopes": {
            code: _scope_projection(
                session,
                organization_id,
                snapshot_id,
                code,
                players,
                section_statuses=section_statuses,
            )
            for code in _SCOPE_LABELS
        },
        "recentGames": _recent_games(session, organization_id, snapshot_id),
        "splits": _split_projection(session, organization_id, snapshot_id),
    }
