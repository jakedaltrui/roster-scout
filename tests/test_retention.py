"""Thirty-day saved-report retention and private artifact deletion contracts."""

from __future__ import annotations

from datetime import timedelta
import unittest
import uuid
from unittest.mock import Mock

from sqlalchemy import select

from tests import test_report_library as _report_library


class ReportRetentionTests(unittest.TestCase):
    setUpClass = _report_library.SharedLibraryTests.__dict__["setUpClass"]
    tearDownClass = _report_library.SharedLibraryTests.__dict__["tearDownClass"]
    _organization = _report_library.SharedLibraryTests._organization
    _report = _report_library.SharedLibraryTests._report
    _snapshot = _report_library.SharedLibraryTests._snapshot

    def setUp(self):
        _report_library.SharedLibraryTests.setUp(self)

    def tearDown(self):
        _report_library.SharedLibraryTests.tearDown(self)

    def test_expired_snapshot_object_and_raw_usage_are_deleted_together(self):
        from roster_scout.models import (
            ProductUsageEvent,
            Report,
            ReportArtifact,
            ReportJob,
            ReportSection,
            ReportSnapshot,
        )
        from roster_scout.retention import RetentionConfig, purge_expired_report_data

        organization_id, _accounts = self._organization(
            "Retention University", ("coach@retention.edu", "admin")
        )
        expired_report = self._report(organization_id, "Expired Owls", 9701)
        retained_report = self._report(organization_id, "Current Bears", 9702)
        expired_snapshot = self._snapshot(
            organization_id,
            expired_report,
            "partial",
            self.now - timedelta(days=31),
        )
        retained_snapshot = self._snapshot(
            organization_id,
            retained_report,
            "ready",
            self.now - timedelta(days=29),
        )
        object_key = (
            f"organizations/{organization_id}/reports/{expired_report}/"
            f"snapshots/{expired_snapshot}/workbooks/"
            "roster-scout-coach-facing-two-sheet-xlsx-v3.xlsx"
        )
        with self.Session.begin() as session:
            session.add(ReportJob(
                organization_id=organization_id,
                report_id=expired_report,
                result_snapshot_id=expired_snapshot,
                idempotency_key=f"expired-{uuid.uuid4()}",
                status="ready",
            ))
            session.add_all((
                ReportArtifact(
                    organization_id=organization_id,
                    snapshot_id=expired_snapshot,
                    artifact_type="xlsx",
                    status="ready",
                    schema_version="coach-facing-two-sheet-xlsx-v3",
                    sha256="a" * 64,
                    object_key=object_key,
                ),
                ProductUsageEvent(
                    event_type="workbook_download_served",
                    event_key=f"old-{uuid.uuid4()}",
                    occurred_at=self.now - timedelta(days=91),
                ),
                ProductUsageEvent(
                    event_type="workbook_download_served",
                    event_key=f"new-{uuid.uuid4()}",
                    occurred_at=self.now - timedelta(days=89),
                ),
            ))

        store = Mock()
        result = purge_expired_report_data(
            self.Session,
            store=store,
            config=RetentionConfig(report_days=30, usage_days=90),
            now=self.now,
        )

        self.assertEqual(result.snapshots_deleted, 1)
        self.assertEqual(result.artifacts_deleted, 1)
        self.assertEqual(result.usage_events_deleted, 1)
        self.assertEqual(result.skipped_snapshots, 0)
        store.delete.assert_called_once_with(object_key)
        with self.Session() as session:
            self.assertIsNone(session.get(ReportSnapshot, expired_snapshot))
            self.assertIsNone(session.get(Report, expired_report))
            self.assertIsNone(session.scalar(select(ReportJob).where(
                ReportJob.result_snapshot_id == expired_snapshot
            )))
            self.assertIsNone(session.scalar(select(ReportSection).where(
                ReportSection.snapshot_id == expired_snapshot
            )))
            self.assertIsNotNone(session.get(ReportSnapshot, retained_snapshot))
            self.assertIsNotNone(session.get(Report, retained_report))
            self.assertEqual(
                len(tuple(session.scalars(select(ProductUsageEvent)))),
                1,
            )

    def test_retention_configuration_is_bounded_and_defaults_to_thirty_days(self):
        from roster_scout.retention import RetentionConfig

        self.assertEqual(RetentionConfig.from_environ({}).report_days, 30)
        self.assertEqual(
            RetentionConfig.from_environ({"REPORT_RETENTION_DAYS": "14"}).report_days,
            14,
        )
        with self.assertRaisesRegex(ValueError, "REPORT_RETENTION_DAYS"):
            RetentionConfig.from_environ({"REPORT_RETENTION_DAYS": "13"})


if __name__ == "__main__":
    unittest.main()
