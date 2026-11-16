"""Transactional draft validation and publication."""

from datetime import UTC, datetime

from sqlalchemy import String, cast, distinct, func, or_, select, text

from .models import (
    BattingFact,
    FieldingFact,
    MetricConstantSet,
    MetricDefinition,
    MetricValue,
    PitchingFact,
    Player,
    PlayerTeamSeason,
    Report,
    ReportSection,
    ReportSnapshot,
    SourceRecord,
    TeamSeason,
)
from .metrics import DEFINITION_VERSION


_BATTING_METRIC_CODES = frozenset({
    "pa",
    "avg",
    "obp",
    "slg",
    "ops",
    "iso",
    "batting_bb_pct",
    "batting_k_pct",
    "babip",
    "singles",
    "total_bases",
    "woba",
})
_PITCHING_METRIC_CODES = frozenset({
    "era",
    "whip",
    "k_per_9",
    "bb_per_9",
    "h_per_9",
    "hr_per_9",
    "k_bb",
    "pitching_k_pct",
    "pitching_bb_pct",
    "babip_allowed",
    "fip",
})
_CONSTANT_METRIC_CODES = frozenset({"woba", "fip"})


class SnapshotPublicationError(ValueError):
    pass


def _validate_detailed_metric_lineage(session, snapshot, sections):
    """Require complete tagged lineage for durable detailed-season builds."""
    durable_build = any(
        section.section in {"batting", "pitching"}
        and section.schema_version is not None
        for section in sections
    )
    if not durable_build:
        return

    batting_players = set(session.scalars(
        select(BattingFact.player_team_season_id).where(
            BattingFact.snapshot_id == snapshot.id,
            BattingFact.organization_id == snapshot.organization_id,
        )
    ))
    pitching_players = set(session.scalars(
        select(PitchingFact.player_team_season_id).where(
            PitchingFact.snapshot_id == snapshot.id,
            PitchingFact.organization_id == snapshot.organization_id,
        )
    ))
    expected = {
        (player_id, code)
        for player_id in batting_players
        for code in _BATTING_METRIC_CODES
    } | {
        (player_id, code)
        for player_id in pitching_players
        for code in _PITCHING_METRIC_CODES
    }
    rows = session.execute(
        select(MetricValue, MetricDefinition, MetricConstantSet)
        .join(
            MetricDefinition,
            MetricDefinition.id == MetricValue.metric_definition_id,
        )
        .outerjoin(
            MetricConstantSet,
            MetricConstantSet.id == MetricValue.constant_set_id,
        )
        .where(
            MetricValue.snapshot_id == snapshot.id,
            MetricValue.organization_id == snapshot.organization_id,
            MetricValue.scope == "full_season",
            MetricDefinition.version == DEFINITION_VERSION,
        )
    ).all()
    actual = [
        (metric.player_team_season_id, definition.code)
        for metric, definition, _constant in rows
    ]
    if len(actual) != len(set(actual)) or set(actual) != expected:
        raise SnapshotPublicationError(
            "full-season detailed metric definitions are incomplete"
        )

    report = session.get(Report, snapshot.report_id)
    team_season = session.get(TeamSeason, report.team_season_id)
    for metric, definition, constant in rows:
        if not definition.formula.strip():
            raise SnapshotPublicationError("metric definition formula is missing")
        if not isinstance(metric.inputs_json, dict) or not metric.inputs_json:
            raise SnapshotPublicationError("metric inputs are missing")
        if not isinstance(metric.sample_json, dict) or set(metric.sample_json) != {
            "pa",
            "ab",
            "bf",
            "outs",
        }:
            raise SnapshotPublicationError("metric sample lineage is missing")
        if metric.status == "available":
            if metric.value is None or metric.reason_code is not None:
                raise SnapshotPublicationError("available metric lineage is invalid")
        elif metric.value is not None or not metric.reason_code:
            raise SnapshotPublicationError("unavailable metric lineage is invalid")

        if constant is not None:
            if (
                definition.code not in _CONSTANT_METRIC_CODES
                or constant.metric_definition_id != definition.id
                or constant.metric_code != definition.code
                or constant.season_id != team_season.season_id
                or constant.division != team_season.division
                or constant.verification_status != "verified"
                or constant.verified_at is None
            ):
                raise SnapshotPublicationError(
                    "metric constant set does not match definition, season, or division"
                )
        if definition.code in _CONSTANT_METRIC_CODES:
            if metric.status == "available" and constant is None:
                raise SnapshotPublicationError(
                    "constant-dependent metric lacks a verified constant set"
                )
            if metric.status == "unavailable_constant" and constant is not None:
                raise SnapshotPublicationError(
                    "unavailable constant metric references a constant set"
                )


def publish_snapshot(session, snapshot_id):
    """Validate and publish a draft inside the caller-owned transaction."""
    session.execute(
        text("SELECT lock_snapshot_mutation(CAST(:snapshot_id AS uuid))"),
        {"snapshot_id": snapshot_id},
    )
    snapshot = session.scalar(
        select(ReportSnapshot)
        .where(ReportSnapshot.id == snapshot_id)
        .with_for_update(key_share=True)
    )
    if snapshot is None:
        raise SnapshotPublicationError("snapshot does not exist")
    if snapshot.status != "draft":
        raise SnapshotPublicationError("only draft snapshots can be published")

    linked_player_pairs = (
        select(
            PlayerTeamSeason.organization_id,
            PlayerTeamSeason.player_id,
        )
        .where(
            PlayerTeamSeason.snapshot_id == snapshot.id,
            PlayerTeamSeason.organization_id == snapshot.organization_id,
        )
        .distinct()
        .subquery()
    )
    linked_global_players = session.execute(
        select(
            linked_player_pairs.c.player_id,
            cast(linked_player_pairs.c.player_id, String).label("player_id_text"),
        )
        .distinct()
        .order_by("player_id_text")
    ).all()
    for player_id, _player_id_text in linked_global_players:
        session.execute(
            text(
                "SELECT lock_authoritative_global_player_mutation("
                "CAST(:player_id AS uuid))"
            ),
            {"player_id": player_id},
        )

    linked_players = session.execute(
        select(
            linked_player_pairs.c.organization_id,
            linked_player_pairs.c.player_id,
        )
        .order_by(
            cast(linked_player_pairs.c.organization_id, String),
            cast(linked_player_pairs.c.player_id, String),
        )
    ).all()
    for organization_id, player_id in linked_players:
        session.execute(
            text(
                "SELECT lock_authoritative_player_mutation("
                "CAST(:organization_id AS uuid), CAST(:player_id AS uuid))"
            ),
            {
                "organization_id": organization_id,
                "player_id": player_id,
            },
        )

    linked_identity_ids = session.scalars(
        select(PlayerTeamSeason.id)
        .where(
            PlayerTeamSeason.snapshot_id == snapshot.id,
            PlayerTeamSeason.organization_id == snapshot.organization_id,
        )
        .order_by(cast(PlayerTeamSeason.id, String))
    )
    for player_team_season_id in linked_identity_ids:
        session.execute(
            text(
                "SELECT lock_authoritative_player_team_season_mutation("
                "CAST(:player_team_season_id AS uuid))"
            ),
            {"player_team_season_id": player_team_season_id},
        )

    sections = list(session.scalars(
        select(ReportSection).where(ReportSection.snapshot_id == snapshot.id)
    ))
    by_name = {section.section: section for section in sections}
    for required in ("batting", "pitching"):
        section = by_name.get(required)
        if section is None or not section.critical or section.status != "complete":
            raise SnapshotPublicationError(f"{required} is not complete")
        if section.row_count == 0 and not section.verified_empty:
            raise SnapshotPublicationError(f"{required} has no verified-empty evidence")

        fact_model = BattingFact if required == "batting" else PitchingFact
        source_count = session.scalar(
            select(func.count())
            .select_from(SourceRecord)
            .where(
                SourceRecord.snapshot_id == snapshot.id,
                SourceRecord.organization_id == snapshot.organization_id,
                SourceRecord.section == required,
            )
        )
        fact_count = session.scalar(
            select(func.count())
            .select_from(fact_model)
            .where(
                fact_model.snapshot_id == snapshot.id,
                fact_model.organization_id == snapshot.organization_id,
            )
        )
        if section.row_count != source_count:
            raise SnapshotPublicationError(
                f"{required} row count does not match source evidence"
            )
        if section.row_count != fact_count:
            raise SnapshotPublicationError(
                f"{required} row count does not match typed facts"
            )

        traced = session.execute(
            select(
                func.count(distinct(fact_model.id)),
                func.count(distinct(fact_model.source_record_id)),
            )
            .select_from(fact_model)
            .join(
                PlayerTeamSeason,
                (PlayerTeamSeason.id == fact_model.player_team_season_id)
                & (PlayerTeamSeason.snapshot_id == fact_model.snapshot_id)
                & (PlayerTeamSeason.organization_id == fact_model.organization_id),
            )
            .join(
                SourceRecord,
                (SourceRecord.id == fact_model.source_record_id)
                & (
                    SourceRecord.player_team_season_id
                    == fact_model.player_team_season_id
                )
                & (SourceRecord.snapshot_id == fact_model.snapshot_id)
                & (SourceRecord.organization_id == fact_model.organization_id)
                & (SourceRecord.section == required),
            )
            .where(
                fact_model.snapshot_id == snapshot.id,
                fact_model.organization_id == snapshot.organization_id,
                SourceRecord.player_team_season_id.is_not(None),
            )
        ).one()
        traced_fact_count, traced_source_count = traced
        if traced_fact_count != fact_count or traced_source_count != source_count:
            raise SnapshotPublicationError(
                f"{required} facts are not uniquely linked to snapshot-owned evidence"
            )

    fielding = by_name.get("fielding")
    if fielding is not None:
        fielding_source_count = session.scalar(
            select(func.count())
            .select_from(SourceRecord)
            .where(
                SourceRecord.snapshot_id == snapshot.id,
                SourceRecord.organization_id == snapshot.organization_id,
                SourceRecord.section == "fielding",
            )
        )
        fielding_fact_count = session.scalar(
            select(func.count())
            .select_from(FieldingFact)
            .where(
                FieldingFact.snapshot_id == snapshot.id,
                FieldingFact.organization_id == snapshot.organization_id,
            )
        )
        if fielding.status == "complete":
            if fielding.row_count == 0 and not fielding.verified_empty:
                raise SnapshotPublicationError(
                    "fielding has no verified-empty evidence"
                )
            if fielding.row_count != fielding_source_count:
                raise SnapshotPublicationError(
                    "fielding row count does not match source evidence"
                )
            if fielding.row_count != fielding_fact_count:
                raise SnapshotPublicationError(
                    "fielding row count does not match typed facts"
                )
            traced_fielding = session.execute(
                select(
                    func.count(distinct(FieldingFact.id)),
                    func.count(distinct(FieldingFact.source_record_id)),
                )
                .select_from(FieldingFact)
                .join(
                    PlayerTeamSeason,
                    (PlayerTeamSeason.id == FieldingFact.player_team_season_id)
                    & (PlayerTeamSeason.snapshot_id == FieldingFact.snapshot_id)
                    & (
                        PlayerTeamSeason.organization_id
                        == FieldingFact.organization_id
                    ),
                )
                .join(
                    SourceRecord,
                    (SourceRecord.id == FieldingFact.source_record_id)
                    & (
                        SourceRecord.player_team_season_id
                        == FieldingFact.player_team_season_id
                    )
                    & (SourceRecord.snapshot_id == FieldingFact.snapshot_id)
                    & (
                        SourceRecord.organization_id
                        == FieldingFact.organization_id
                    )
                    & (SourceRecord.section == "fielding"),
                )
                .where(
                    FieldingFact.snapshot_id == snapshot.id,
                    FieldingFact.organization_id == snapshot.organization_id,
                    SourceRecord.player_team_season_id.is_not(None),
                )
            ).one()
            if traced_fielding != (fielding_fact_count, fielding_source_count):
                raise SnapshotPublicationError(
                    "fielding facts are not uniquely linked to snapshot-owned evidence"
                )
        elif (
            fielding.row_count != 0
            or fielding.verified_empty
            or fielding_source_count != 0
            or fielding_fact_count != 0
            or not fielding.failure_category
            or fielding.collected_at is None
        ):
            raise SnapshotPublicationError(
                "incomplete fielding status contains contradictory evidence"
            )

    _validate_detailed_metric_lineage(session, snapshot, sections)

    authoritative_mismatch_count = session.scalar(
        select(func.count())
        .select_from(SourceRecord)
        .join(
            PlayerTeamSeason,
            (PlayerTeamSeason.id == SourceRecord.player_team_season_id)
            & (PlayerTeamSeason.snapshot_id == SourceRecord.snapshot_id)
            & (PlayerTeamSeason.organization_id == SourceRecord.organization_id),
        )
        .join(Player, Player.id == PlayerTeamSeason.player_id)
        .where(
            SourceRecord.snapshot_id == snapshot.id,
            SourceRecord.organization_id == snapshot.organization_id,
            SourceRecord.source_player_id.is_not(None),
            or_(
                Player.source != "ncaa",
                Player.source_player_id.is_(None),
                Player.source_player_id != SourceRecord.source_player_id,
            ),
        )
    )
    if authoritative_mismatch_count:
        raise SnapshotPublicationError(
            "snapshot contains an authoritative player identity mismatch"
        )

    conflict = session.scalar(
        select(PlayerTeamSeason.id).where(
            PlayerTeamSeason.snapshot_id == snapshot.id,
            PlayerTeamSeason.identity_status == "conflict",
        ).limit(1)
    )
    if conflict is not None:
        raise SnapshotPublicationError("snapshot contains an identity conflict")

    supplemental_incomplete = any(
        not section.critical and section.status != "complete" for section in sections
    )
    snapshot.status = "partial" if supplemental_incomplete else "ready"
    snapshot.published_at = datetime.now(UTC)
    session.flush()
    return snapshot
