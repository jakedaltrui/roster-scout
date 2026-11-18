"""Read-only, same-snapshot projection for one authorized NCAA player."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
import uuid

from sqlalchemy import and_, select

from .models import (
    BattingFact,
    BattingGameLogFact,
    CareerAggregate,
    CareerSeasonFact,
    FieldingFact,
    FieldingGameLogFact,
    Game,
    MetricDefinition,
    MetricValue,
    PitchingFact,
    PitchingGameLogFact,
    Player,
    PlayerTeamSeason,
    Report,
    ReportSection,
    ReportSnapshot,
    ScopedBattingFact,
    ScopedFieldingFact,
    ScopedPitchingFact,
    SourceRecord,
    StatisticScope,
)


PLAYER_DETAIL_VIEWS = frozenset({"season", "splits", "career", "games", "all"})

_SEASON_FACTS = {
    "batting": (
        BattingFact,
        (
            "games",
            "games_started",
            "plate_appearances",
            "at_bats",
            "runs",
            "hits",
            "doubles",
            "triples",
            "home_runs",
            "runs_batted_in",
            "walks",
            "hit_by_pitch",
            "strikeouts",
            "sacrifice_flies",
            "sacrifice_hits",
            "stolen_bases",
            "caught_stealing",
            "total_bases",
        ),
    ),
    "pitching": (
        PitchingFact,
        (
            "appearances",
            "games_started",
            "innings_outs",
            "hits_allowed",
            "runs_allowed",
            "earned_runs",
            "walks",
            "strikeouts",
            "home_runs_allowed",
            "hit_batters",
            "batters_faced",
            "opponent_at_bats",
            "opponent_sacrifice_flies",
        ),
    ),
    "fielding": (
        FieldingFact,
        (
            "games",
            "games_started",
            "putouts",
            "assists",
            "errors",
            "total_chances",
            "double_plays",
            "fielding_percentage",
            "passed_balls",
            "stolen_base_attempts",
            "caught_stealing_by",
        ),
    ),
}

_SCOPED_FACTS = {
    "batting": ScopedBattingFact,
    "pitching": ScopedPitchingFact,
    "fielding": ScopedFieldingFact,
}

_GAME_LOG_FACTS = {
    "batting": BattingGameLogFact,
    "pitching": PitchingGameLogFact,
    "fielding": FieldingGameLogFact,
}


class PlayerDetailFailure(RuntimeError):
    """Fixed lookup failure that does not reveal which identifier missed."""

    def __init__(self, category: str = "not_found"):
        self.category = category
        super().__init__(category)


def _canonical_uuid(value, *, field):
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        raise ValueError(f"{field} must be a UUID") from None


def _json_value(value):
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def _source_projection(session, source_record_id, organization_id, snapshot_id):
    if source_record_id is None:
        return None
    source = session.scalar(
        select(SourceRecord).where(
            SourceRecord.id == source_record_id,
            SourceRecord.organization_id == organization_id,
            SourceRecord.snapshot_id == snapshot_id,
        )
    )
    if source is None:
        raise PlayerDetailFailure()
    return {
        "id": str(source.id),
        "section": source.section,
        "source_team_id": source.source_team_id,
        "source_season_id": source.source_season_id,
        "source_season_team_id": source.source_season_team_id,
        "source_stat_category_id": source.source_stat_category_id,
        "source_player_id": source.source_player_id,
        "collected_at": source.collected_at.isoformat(),
        "parser_version": source.parser_version,
        "schema_version": source.schema_version,
        "payload_sha256": source.payload_sha256,
    }


def _metric_projections(
    session,
    *,
    organization_id,
    snapshot_id,
    player_team_season_id,
    statistic_scope_id=None,
):
    query = (
        select(MetricValue, MetricDefinition)
        .join(
            MetricDefinition,
            MetricDefinition.id == MetricValue.metric_definition_id,
        )
        .where(
            MetricValue.organization_id == organization_id,
            MetricValue.snapshot_id == snapshot_id,
            MetricValue.player_team_season_id == player_team_season_id,
        )
    )
    if statistic_scope_id is None:
        query = query.where(MetricValue.statistic_scope_id.is_(None))
    else:
        query = query.where(MetricValue.statistic_scope_id == statistic_scope_id)
    rows = session.execute(
        query.order_by(MetricDefinition.code, MetricDefinition.version)
    ).all()
    return [
        {
            "code": definition.code,
            "definition_version": definition.version,
            "status": metric.status,
            "reason_code": metric.reason_code,
            "value": _json_value(metric.value),
            "inputs": metric.inputs_json,
            "sample": metric.sample_json,
            "scope": metric.scope,
        }
        for metric, definition in rows
    ]


def _season_projection(
    session, *, organization_id, snapshot_id, player_team_season_id
):
    result = {}
    for stat_type, (model, columns) in _SEASON_FACTS.items():
        fact = session.scalar(
            select(model).where(
                model.organization_id == organization_id,
                model.snapshot_id == snapshot_id,
                model.player_team_season_id == player_team_season_id,
            )
        )
        if fact is None:
            result[stat_type] = None
            continue
        result[stat_type] = {
            "counts": {
                column: _json_value(getattr(fact, column)) for column in columns
            },
            "source": _source_projection(
                session,
                fact.source_record_id,
                organization_id,
                snapshot_id,
            ),
        }
    result["metrics"] = _metric_projections(
        session,
        organization_id=organization_id,
        snapshot_id=snapshot_id,
        player_team_season_id=player_team_season_id,
    )
    return result


def _scoped_fact_projection(
    session,
    *,
    stat_type,
    scope_id,
    organization_id,
    snapshot_id,
    player_team_season_id,
):
    model = _SCOPED_FACTS[stat_type]
    fact = session.scalar(
        select(model).where(
            model.organization_id == organization_id,
            model.snapshot_id == snapshot_id,
            model.statistic_scope_id == scope_id,
            model.player_team_season_id == player_team_season_id,
        )
    )
    if fact is None:
        return None
    return {
        "counts": fact.counts_json,
        "sample": fact.sample_json,
        "source": _source_projection(
            session,
            fact.source_record_id,
            organization_id,
            snapshot_id,
        ),
    }


def _splits_projection(
    session, *, organization_id, snapshot_id, player_team_season_id
):
    scopes = tuple(session.scalars(
        select(StatisticScope)
        .where(
            StatisticScope.organization_id == organization_id,
            StatisticScope.snapshot_id == snapshot_id,
            StatisticScope.scope_kind == "source_split",
        )
        .order_by(
            StatisticScope.stat_type,
            StatisticScope.scope_code,
            StatisticScope.id,
        )
    ))
    return [
        {
            "id": str(scope.id),
            "scope_code": scope.scope_code,
            "stat_type": scope.stat_type,
            "display_label": scope.display_label,
            "status": scope.status,
            "availability_reason": scope.availability_reason,
            "sample": scope.sample_json,
            "source": {
                "identifier": scope.source_identifier,
                "path": scope.source_path,
                "source_season_id": scope.source_season_id,
                "source_season_team_id": scope.source_season_team_id,
                "source_stat_category_id": scope.source_stat_category_id,
                "collected_at": scope.collected_at.isoformat(),
                "parser_version": scope.parser_version,
                "schema_version": scope.schema_version,
                "payload_sha256": scope.source_payload_sha256,
            },
            "fact": _scoped_fact_projection(
                session,
                stat_type=scope.stat_type,
                scope_id=scope.id,
                organization_id=organization_id,
                snapshot_id=snapshot_id,
                player_team_season_id=player_team_season_id,
            ),
            "metrics": _metric_projections(
                session,
                organization_id=organization_id,
                snapshot_id=snapshot_id,
                player_team_season_id=player_team_season_id,
                statistic_scope_id=scope.id,
            ),
        }
        for scope in scopes
    ]


def _career_projection(
    session, *, organization_id, snapshot_id, player_team_season_id
):
    seasons = tuple(session.scalars(
        select(CareerSeasonFact)
        .where(
            CareerSeasonFact.organization_id == organization_id,
            CareerSeasonFact.snapshot_id == snapshot_id,
            CareerSeasonFact.player_team_season_id == player_team_season_id,
        )
        .order_by(
            CareerSeasonFact.stat_type,
            CareerSeasonFact.season_label,
            CareerSeasonFact.row_id,
        )
    ))
    aggregates = tuple(session.scalars(
        select(CareerAggregate)
        .where(
            CareerAggregate.organization_id == organization_id,
            CareerAggregate.snapshot_id == snapshot_id,
            CareerAggregate.player_team_season_id == player_team_season_id,
        )
        .order_by(CareerAggregate.stat_type)
    ))
    return {
        "seasons": [
            {
                "row_id": row.row_id,
                "stat_type": row.stat_type,
                "season_label": row.season_label,
                "source_season_id": row.source_season_id,
                "school_name": row.school_name,
                "source_season_team_id": row.source_season_team_id,
                "division": row.division,
                "source_stat_category_id": row.source_stat_category_id,
                "counts": row.counts_json,
                "rates": row.rates_json,
                "source": _source_projection(
                    session,
                    row.source_record_id,
                    organization_id,
                    snapshot_id,
                ),
            }
            for row in seasons
        ],
        "aggregate": [
            {
                "stat_type": row.stat_type,
                "status": row.status,
                "availability_reason": row.availability_reason,
                "definition_version": row.definition_version,
                "counts": row.counts_json,
                "included_row_ids": row.included_row_ids,
                "excluded_rows": row.excluded_rows_json,
            }
            for row in aggregates
        ],
    }


def _game_projection(game, session, organization_id, snapshot_id):
    return {
        "id": str(game.id),
        "game_date": game.game_date.isoformat(),
        "completed_at": _json_value(game.completed_at),
        "status": game.status,
        "result_code": game.result_code,
        "opponent_team_id": game.opponent_team_id,
        "opponent_season_team_id": game.opponent_season_team_id,
        "doubleheader_ordinal": game.doubleheader_ordinal,
        "venue": game.venue,
        "source_game_id": game.source_game_id,
        "source": _source_projection(
            session,
            game.source_record_id,
            organization_id,
            snapshot_id,
        ),
    }


def _game_fact_projection(fact, session, organization_id, snapshot_id):
    return {
        "row_id": fact.row_id,
        "counts": fact.counts_json,
        "source": _source_projection(
            session,
            fact.source_record_id,
            organization_id,
            snapshot_id,
        ),
    }


def _games_projection(
    session, *, organization_id, snapshot_id, player_team_season_id
):
    by_game = {}
    for stat_type, model in _GAME_LOG_FACTS.items():
        rows = session.execute(
            select(model, Game)
            .join(
                Game,
                and_(
                    Game.id == model.game_id,
                    Game.organization_id == model.organization_id,
                    Game.snapshot_id == model.snapshot_id,
                ),
            )
            .where(
                model.organization_id == organization_id,
                model.snapshot_id == snapshot_id,
                model.player_team_season_id == player_team_season_id,
            )
        ).all()
        for fact, game in rows:
            item = by_game.setdefault(
                game.id,
                {
                    "game": _game_projection(
                        game, session, organization_id, snapshot_id
                    ),
                    "facts": {},
                },
            )
            item["facts"][stat_type] = _game_fact_projection(
                fact, session, organization_id, snapshot_id
            )
    return sorted(
        by_game.values(),
        key=lambda item: (
            item["game"]["game_date"],
            item["game"]["doubleheader_ordinal"],
            item["game"]["id"],
        ),
    )


def _completeness_projection(session, organization_id, snapshot_id, status):
    sections = tuple(session.scalars(
        select(ReportSection)
        .where(
            ReportSection.organization_id == organization_id,
            ReportSection.snapshot_id == snapshot_id,
        )
        .order_by(ReportSection.section)
    ))
    return {
        "status": status,
        "sections": [
            {
                "name": section.section,
                "status": section.status,
                "row_count": section.row_count,
                "verified_empty": section.verified_empty,
                "failure_category": section.failure_category,
                "collected_at": _json_value(section.collected_at),
            }
            for section in sections
        ],
    }


def load_snapshot_player_detail(
    session,
    organization_id,
    report_id,
    snapshot_id,
    player_id,
    view="all",
):
    """Return one fixed projection from an exact published snapshot/player graph."""
    organization_id = _canonical_uuid(organization_id, field="organization_id")
    report_id = _canonical_uuid(report_id, field="report_id")
    snapshot_id = _canonical_uuid(snapshot_id, field="snapshot_id")
    player_id = _canonical_uuid(player_id, field="player_id")
    if view not in PLAYER_DETAIL_VIEWS:
        raise ValueError("view must be a supported player detail view")

    snapshot = session.scalar(
        select(ReportSnapshot)
        .join(
            Report,
            and_(
                Report.id == ReportSnapshot.report_id,
                Report.organization_id == ReportSnapshot.organization_id,
            ),
        )
        .where(
            ReportSnapshot.id == snapshot_id,
            ReportSnapshot.report_id == report_id,
            ReportSnapshot.organization_id == organization_id,
            ReportSnapshot.status.in_(("ready", "partial")),
            Report.id == report_id,
            Report.organization_id == organization_id,
        )
    )
    if snapshot is None:
        raise PlayerDetailFailure()

    players = session.execute(
        select(PlayerTeamSeason, Player)
        .join(Player, Player.id == PlayerTeamSeason.player_id)
        .join(
            Report,
            and_(
                Report.id == report_id,
                Report.organization_id == organization_id,
                Report.team_season_id == PlayerTeamSeason.team_season_id,
            ),
        )
        .where(
            PlayerTeamSeason.organization_id == organization_id,
            PlayerTeamSeason.snapshot_id == snapshot_id,
            PlayerTeamSeason.player_id == player_id,
            Player.id == player_id,
        )
    ).all()
    if len(players) != 1:
        raise PlayerDetailFailure()
    player_team_season, player = players[0]

    base_path = (
        f"/reports/{report_id}/snapshots/{snapshot_id}/players/{player_id}"
    )
    links = {
        code: f"{base_path}?view={code}"
        for code in ("season", "splits", "career", "games", "all")
    }
    loaders = {
        "season": _season_projection,
        "splits": _splits_projection,
        "career": _career_projection,
        "games": _games_projection,
    }
    selected = loaders if view == "all" else {view: loaders[view]}
    data = {
        name: loader(
            session,
            organization_id=organization_id,
            snapshot_id=snapshot_id,
            player_team_season_id=player_team_season.id,
        )
        for name, loader in selected.items()
    }
    return {
        "snapshot": {
            "id": str(snapshot.id),
            "report_id": str(snapshot.report_id),
            "status": snapshot.status,
            "schema_version": snapshot.schema_version,
            "source_collected_at": _json_value(snapshot.source_collected_at),
            "published_at": snapshot.published_at.isoformat(),
        },
        "player": {
            "id": str(player.id),
            "source": player.source,
            "source_player_id": player.source_player_id,
            "display_name": player_team_season.display_name,
            "jersey_number": player_team_season.jersey_number,
            "position": player_team_season.position,
            "identity_status": player_team_season.identity_status,
            "identity_key": player_team_season.identity_key,
        },
        "completeness": _completeness_projection(
            session, organization_id, snapshot_id, snapshot.status
        ),
        "view": view,
        "links": links,
        "data": data,
    }
