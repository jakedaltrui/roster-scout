"""Transactional draft validation and publication."""

from datetime import UTC, datetime

from sqlalchemy import distinct, func, select, text

from .models import (
    BattingFact,
    PitchingFact,
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
                & (SourceRecord.snapshot_id == fact_model.snapshot_id)
                & (SourceRecord.organization_id == fact_model.organization_id)
                & (SourceRecord.section == required),
            )
            .where(
                fact_model.snapshot_id == snapshot.id,
                fact_model.organization_id == snapshot.organization_id,
            )
        ).one()
        traced_fact_count, traced_source_count = traced
        if traced_fact_count != fact_count or traced_source_count != source_count:
            raise SnapshotPublicationError(
                f"{required} facts are not uniquely linked to snapshot-owned evidence"
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
