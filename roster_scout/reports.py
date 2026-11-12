"""Connection-free report-library presentation service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from math import ceil

from .repositories import (
    REPORT_LIBRARY_PAGE_SIZE,
    count_reports_for_organization,
    count_reports_updated_since,
    list_report_library_records,
)


class ReportLibraryFailure(RuntimeError):
    def __init__(self, category):
        self.category = category
        super().__init__(category)


@dataclass(frozen=True)
class ReportLibraryItem:
    id: object
    opponent_name: str
    source_team_id: str
    division: str
    season_year: int
    latest_snapshot_at: object | None
    completeness: str
    unavailable_count: int
    build_status: str
    snapshot_count: int


@dataclass(frozen=True)
class ReportLibraryPage:
    items: tuple[ReportLibraryItem, ...]
    total_count: int
    updated_today_count: int
    page: int
    page_count: int
    previous_page: int | None
    next_page: int | None


_BUILD_LABELS = {
    "queued": "Queued",
    "running": "Collecting",
    "retrying": "Saving",
    "failed": "Failed",
    "blocked": "Failed",
}


def load_report_library(session, organization_id, *, page=1, now=None):
    """Build one truthful, fixed-size page for the active organization."""
    if not isinstance(page, int) or page < 1:
        raise ReportLibraryFailure("not_found")
    now = now or datetime.now(UTC)
    page_size = REPORT_LIBRARY_PAGE_SIZE
    total_count = count_reports_for_organization(session, organization_id)
    page_count = max(1, ceil(total_count / page_size))
    if total_count and page > page_count:
        raise ReportLibraryFailure("not_found")
    records = list_report_library_records(
        session,
        organization_id,
        offset=(page - 1) * page_size,
        limit=page_size,
    )
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    items = tuple(
        ReportLibraryItem(
            id=record.report_id,
            opponent_name=record.team_name or record.report_name,
            source_team_id=record.source_team_id,
            division=record.division,
            season_year=record.season_year,
            latest_snapshot_at=(
                record.latest_snapshot.published_at
                if record.latest_snapshot is not None
                else None
            ),
            completeness=(
                "Ready"
                if record.latest_snapshot is not None
                and record.latest_snapshot.status == "ready"
                else "Partial"
                if record.latest_snapshot is not None
                and record.latest_snapshot.status == "partial"
                else "—"
            ),
            unavailable_count=record.unavailable_section_count,
            build_status=(
                _BUILD_LABELS.get(record.latest_build.status, "No active build")
                if record.latest_build is not None
                else "No active build"
            ),
            snapshot_count=record.snapshot_count,
        )
        for record in records
    )
    return ReportLibraryPage(
        items=items,
        total_count=total_count,
        updated_today_count=count_reports_updated_since(
            session, organization_id, start_of_day
        ),
        page=page,
        page_count=page_count,
        previous_page=page - 1 if page > 1 else None,
        next_page=page + 1 if page < page_count else None,
    )
