"""Phase 3 operator diagnostics and external-refresh safety contracts."""

from __future__ import annotations

import json
import uuid
import unittest
from datetime import UTC, datetime, timedelta
from io import StringIO
from unittest.mock import patch

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from tests.db_support import clean_database, require_test_database_url, upgrade_database


class Phase3PostgreSQLTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database_url = require_test_database_url()
        upgrade_database(cls.database_url)
        from roster_scout.database import create_session_factory, get_engine

        cls.engine = get_engine(cls.database_url)
        cls.Session = create_session_factory(cls.engine)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        clean_database(self.database_url)

    def fixture(self, *, name="Diagnostic Organization"):
        from roster_scout.jobs import accept_report_job
        from roster_scout.models import Organization, Report, Season, Team, TeamSeason

        with self.Session.begin() as session:
            organization = Organization(name=name)
            team = Team(source="ncaa", source_team_id=str(uuid.uuid4()), name="Team")
            season = Season(source="ncaa", source_season_id=str(uuid.uuid4()), year=2026)
            session.add_all((organization, team, season))
            session.flush()
            team_season = TeamSeason(
                team_id=team.id,
                season_id=season.id,
                source_season_team_id=str(uuid.uuid4()),
                division="2",
                batting_stat_category_id="batting",
                pitching_stat_category_id="pitching",
            )
            session.add(team_season)
            session.flush()
            report = Report(
                organization_id=organization.id,
                team_season_id=team_season.id,
                name="Customer Report Name",
            )
            session.add(report)
            session.flush()
            organization_id, report_id = organization.id, report.id
        job = accept_report_job(
            self.Session,
            organization_id,
            report_id,
            f"diagnostic-{uuid.uuid4()}",
            dispatch=None,
        )
        return organization_id, report_id, job.id


class ExternalRefreshDiagnosticTests(Phase3PostgreSQLTestCase):
    def test_power_bi_lifecycle_is_tenant_owned_safe_and_controlled(self):
        from roster_scout.models import ExternalRefreshDiagnostic, ReportJob, ReportSnapshot

        organization_id, report_id, job_id = self.fixture()
        other_organization_id, _other_report_id, _ = self.fixture(name="Other Customer")
        now = datetime(2026, 11, 16, 10, 0, tzinfo=UTC)
        with self.Session.begin() as session:
            job = session.get(ReportJob, job_id)
            snapshot = ReportSnapshot(
                organization_id=organization_id,
                report_id=report_id,
                schema_version="schema-1",
            )
            session.add(snapshot)
            session.flush()
            job.result_snapshot_id = snapshot.id
            diagnostic = ExternalRefreshDiagnostic(
                organization_id=organization_id,
                report_id=report_id,
                snapshot_id=snapshot.id,
                provider="power_bi",
                status="pending",
                attempt_number=1,
                correlation_id=f"refresh-{uuid.uuid4()}",
                requested_at=now,
                updated_at=now,
            )
            session.add(diagnostic)
            session.flush()
            diagnostic.started_at = now + timedelta(seconds=1)
            diagnostic.completed_at = now + timedelta(seconds=2)
            diagnostic.updated_at = now + timedelta(seconds=2)
            diagnostic.status = "failed"
            diagnostic.safe_category = "provider_unavailable"
            diagnostic.safe_reason_code = "capacity_unavailable"

        with self.Session() as session:
            stored = session.scalar(select(ExternalRefreshDiagnostic))
            self.assertEqual(stored.provider, "power_bi")
            self.assertEqual(stored.status, "failed")
            self.assertEqual(stored.safe_reason_code, "capacity_unavailable")
            stored.organization_id = other_organization_id
            with self.assertRaises(IntegrityError):
                session.flush()


class OperatorDiagnosticsTests(Phase3PostgreSQLTestCase):
    def _run(self, arguments, *, environ=None):
        from scripts import inspect_report_jobs

        stdout, stderr = StringIO(), StringIO()
        status = inspect_report_jobs.main(
            arguments,
            environ={"DATABASE_URL": self.database_url, **(environ or {})},
            stdout=stdout,
            stderr=stderr,
        )
        return status, stdout.getvalue(), stderr.getvalue()

    def test_invalid_inputs_and_database_failure_are_fixed_and_sanitized(self):
        status, stdout, stderr = self._run(["--job-id", "player@example.edu"])
        self.assertEqual((status, stdout, stderr), (2, "", "report job diagnostics invalid arguments\n"))

        from scripts import inspect_report_jobs

        with patch.object(
            inspect_report_jobs,
            "_runtime_dependencies",
            side_effect=RuntimeError(
                "postgresql://private-user:secret@remote/customer?token=unsafe"
            ),
        ):
            status, stdout, stderr = self._run(["--older-than-seconds", "60"])
        self.assertEqual(status, 1)
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "report job diagnostics unavailable\n")
        self.assertNotIn("private-user", stderr)
        self.assertNotIn("secret", stderr)

    def test_sorted_metadata_only_output_identifies_stuck_and_failed_power_bi(self):
        from roster_scout.models import (
            ExternalRefreshDiagnostic,
            ReportJob,
            ReportSnapshot,
            RequestLedger,
        )

        organization_id, report_id, job_id = self.fixture()
        now = datetime.now(UTC)
        correlation_id = f"opaque-{uuid.uuid4()}"
        with self.Session.begin() as session:
            job = session.get(ReportJob, job_id)
            job.status = "running"
            job.started_at = now - timedelta(hours=1)
            snapshot = ReportSnapshot(
                organization_id=organization_id,
                report_id=report_id,
                schema_version="schema-1",
            )
            session.add(snapshot)
            session.flush()
            job.result_snapshot_id = snapshot.id
            session.add(RequestLedger(
                organization_id=organization_id,
                job_id=job.id,
                snapshot_id=snapshot.id,
                family="player_history",
                source_identity="ncaa:player:123:batting",
                request_kind="live",
                outcome="capacity_exhausted",
                started_at=now - timedelta(minutes=5),
                completed_at=now - timedelta(minutes=4),
                safe_category="browser_capacity",
                budget_remaining=0,
            ))
            session.add(ExternalRefreshDiagnostic(
                organization_id=organization_id,
                report_id=report_id,
                snapshot_id=snapshot.id,
                provider="power_bi",
                status="failed",
                safe_category="provider_unavailable",
                safe_reason_code="capacity_unavailable",
                attempt_number=1,
                correlation_id=correlation_id,
                requested_at=now - timedelta(minutes=3),
                started_at=now - timedelta(minutes=2),
                completed_at=now - timedelta(minutes=1),
                updated_at=now - timedelta(minutes=1),
            ))

        before = None
        with self.Session() as session:
            before = session.scalar(select(func.count()).select_from(RequestLedger))
        status, stdout, stderr = self._run(["--job-id", str(job_id)])
        self.assertEqual((status, stderr), (0, ""))
        self.assertEqual(stdout, json.dumps(json.loads(stdout), sort_keys=True) + "\n")
        payload = json.loads(stdout)
        self.assertEqual(payload["jobs"][0]["state_category"], "running_stuck")
        self.assertEqual(payload["jobs"][0]["capacity_category"], "browser_capacity")
        self.assertEqual(payload["jobs"][0]["request_budget"]["remaining"], 0)
        self.assertEqual(payload["jobs"][0]["external_refreshes"][0]["provider"], "power_bi")
        self.assertEqual(payload["jobs"][0]["external_refreshes"][0]["correlation_id"], correlation_id)
        lowered = stdout.casefold()
        for denied in (
            "customer report name", "diagnostic organization", "player",
            "@example", "token=", "private-user", "secret", "stats.ncaa.org",
        ):
            self.assertNotIn(denied, lowered)
        with self.Session() as session:
            self.assertEqual(
                session.scalar(select(func.count()).select_from(RequestLedger)), before
            )


if __name__ == "__main__":
    unittest.main()
