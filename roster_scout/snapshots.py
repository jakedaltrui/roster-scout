"""Transactional draft validation and publication."""

from datetime import UTC, datetime

from sqlalchemy import select

from .models import PlayerTeamSeason, ReportSection, ReportSnapshot


class SnapshotPublicationError(ValueError):
    pass


def publish_snapshot(session, snapshot_id):
    """Validate and publish a draft inside the caller-owned transaction."""
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
