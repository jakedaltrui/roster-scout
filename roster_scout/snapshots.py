"""Transactional draft validation and publication."""

from datetime import UTC, datetime

from sqlalchemy import String, cast, distinct, func, or_, select, text

from .models import (
    BattingFact,
    PitchingFact,
    Player,
    PlayerTeamSeason,
    ReportSection,
    ReportSnapshot,
    SourceRecord,
)


class SnapshotPublicationError(ValueError):
    pass


def publish_snapshot(session, snapshot_id):
    """Validate and publish a draft inside the caller-owned transaction."""
    session.execute(
        text("SELECT lock_snapshot_mutation(CAST(:snapshot_id AS uuid))"),
        {"snapshot_id": snapshot_id},
    )
    snapshot = session.scalar(
        select(ReportSnapshot)
        .where(ReportSnapshot.id == snapshot_id)
        .with_for_update()
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
