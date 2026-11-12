"""Active-organization report library integration contracts."""

from __future__ import annotations

import uuid
import unittest
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from tests.db_support import clean_database, require_test_database_url, upgrade_database


class SharedLibraryTests(unittest.TestCase):
    """The shared library is bounded, truthful, and tenant scoped."""

    @classmethod
    def setUpClass(cls):
        cls.database_url = require_test_database_url()
        upgrade_database(cls.database_url)
        cls.engine = create_engine(cls.database_url)
        cls.Session = sessionmaker(bind=cls.engine, expire_on_commit=False)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        clean_database(self.database_url)
        import app as app_module

        self.app_module = app_module
        self.original_config = dict(app_module.app.config)
        self.original_auth_factory = app_module.app.extensions.get(
            "roster_scout_auth_session_factory"
        )
        self.original_report_factory = app_module.app.extensions.get(
            "roster_scout_report_session_factory"
        )
        app_module.app.config.update(
            TESTING=True,
            ROSTER_SCOUT_TEST_AUTH_ENFORCEMENT=True,
            WTF_CSRF_ENABLED=True,
            WTF_CSRF_SSL_STRICT=False,
        )
        app_module.app.extensions["roster_scout_auth_session_factory"] = self.Session
        app_module.app.extensions["roster_scout_report_session_factory"] = self.Session
        self.now = datetime.now(UTC)

    def tearDown(self):
        self.app_module.app.config.clear()
        self.app_module.app.config.update(self.original_config)
        self.app_module.app.extensions["roster_scout_auth_session_factory"] = (
            self.original_auth_factory
        )
        if self.original_report_factory is None:
            self.app_module.app.extensions.pop(
                "roster_scout_report_session_factory", None
            )
        else:
            self.app_module.app.extensions["roster_scout_report_session_factory"] = (
                self.original_report_factory
            )

    def _organization(self, name, *members):
        from roster_scout.models import Account, Organization, OrganizationMembership

        with self.Session.begin() as session:
            organization = Organization(name=name)
            session.add(organization)
            session.flush()
            accounts = []
            for email, role in members:
                account = Account(
                    normalized_email=email,
                    email_verified_at=self.now,
                    status="active",
                )
                session.add(account)
                session.flush()
                session.add(OrganizationMembership(
                    organization_id=organization.id,
                    account_id=account.id,
                    role=role,
                    status="active",
                ))
                accounts.append(account.id)
            return organization.id, accounts

    def _client(self, account_id, organization_id):
        from roster_scout.sessions import SESSION_COOKIE_NAME, create_account_session

        with self.Session.begin() as session:
            credential = create_account_session(
                session,
                account_id=account_id,
                active_organization_id=organization_id,
                sealed_provider_state="sealed-test-session",
                now=self.now,
            )
        client = self.app_module.app.test_client()
        client.set_cookie(SESSION_COOKIE_NAME, credential.cookie_value)
        return client

    def _report(self, organization_id, name, index, *, created_at=None):
        from roster_scout.models import Report, Season, Team, TeamSeason

        with self.Session.begin() as session:
            team = Team(
                source="ncaa",
                source_team_id=f"team-{index}",
                name=name,
            )
            season = Season(
                source="ncaa",
                source_season_id=f"season-{index}",
                year=2026,
            )
            session.add_all((team, season))
            session.flush()
            team_season = TeamSeason(
                team_id=team.id,
                season_id=season.id,
                source_season_team_id=f"season-team-{index}",
                division="D1",
            )
            session.add(team_season)
            session.flush()
            report = Report(
                organization_id=organization_id,
                team_season_id=team_season.id,
                name=name,
                created_at=created_at or self.now,
            )
            session.add(report)
            session.flush()
            return report.id

    def _snapshot(self, organization_id, report_id, status, published_at):
        from roster_scout.models import ReportSection, ReportSnapshot

        with self.Session.begin() as session:
            snapshot = ReportSnapshot(
                organization_id=organization_id,
                report_id=report_id,
                status="draft" if status == "partial" else status,
                schema_version="2026.1",
                source_collected_at=published_at - timedelta(minutes=5),
                published_at=None if status == "partial" else published_at,
                created_by="test",
            )
            session.add(snapshot)
            session.flush()
            if status == "partial":
                session.add(ReportSection(
                    organization_id=organization_id,
                    snapshot_id=snapshot.id,
                    section="fielding",
                    critical=False,
                    status="unavailable",
                    row_count=0,
                    verified_empty=False,
                    failure_category="source_unavailable",
                ))
                session.flush()
                snapshot.status = "partial"
                snapshot.published_at = published_at
            return snapshot.id

    def _job(self, organization_id, report_id, status, created_at):
        from roster_scout.models import ReportJob

        with self.Session.begin() as session:
            job = ReportJob(
                organization_id=organization_id,
                report_id=report_id,
                idempotency_key=f"job-{uuid.uuid4()}",
                status=status,
                created_at=created_at,
            )
            session.add(job)
            session.flush()
            return job.id

    def test_library_is_shared_and_snapshot_state_is_independent_from_active_build(self):
        organization_id, accounts = self._organization(
            "North Valley Baseball",
            ("admin@example.edu", "admin"),
            ("assistant@example.edu", "member"),
        )
        foreign_id, foreign_accounts = self._organization(
            "South Valley Baseball", ("foreign@example.edu", "admin")
        )
        report_id = self._report(organization_id, "Falcons", 1)
        self._snapshot(
            organization_id,
            report_id,
            "ready",
            self.now - timedelta(hours=2),
        )
        self._job(organization_id, report_id, "running", self.now)
        self._report(foreign_id, "Foreign Rivals", 2)

        admin_html = self._client(accounts[0], organization_id).get("/reports")
        member_html = self._client(accounts[1], organization_id).get("/reports")
        foreign_html = self._client(foreign_accounts[0], foreign_id).get("/reports")

        self.assertEqual(admin_html.status_code, 200)
        self.assertEqual(member_html.status_code, 200)
        self.assertIn(b"Falcons", admin_html.data)
        self.assertIn(b"Falcons", member_html.data)
        self.assertIn(b"Ready", admin_html.data)
        self.assertIn(b"Collecting", admin_html.data)
        self.assertNotIn(b"Foreign Rivals", admin_html.data)
        self.assertIn(b"Foreign Rivals", foreign_html.data)
        self.assertIn(b">Members<", admin_html.data)
        self.assertNotIn(b">Members<", member_html.data)

    def test_library_is_bounded_to_25_and_orders_by_newest_activity(self):
        organization_id, accounts = self._organization(
            "Pagination University", ("coach@example.edu", "admin")
        )
        for index in range(27):
            report_id = self._report(
                organization_id,
                f"Opponent {index:02d}",
                100 + index,
                created_at=self.now - timedelta(days=40 - index),
            )
            if index == 0:
                self._snapshot(organization_id, report_id, "ready", self.now)

        client = self._client(accounts[0], organization_id)
        first = client.get("/reports")
        second = client.get("/reports?page=2")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.data.count(b'data-report-row="desktop"'), 25)
        self.assertEqual(first.data.count(b'data-report-row="mobile"'), 25)
        self.assertEqual(second.data.count(b'data-report-row="desktop"'), 2)
        self.assertLess(first.data.index(b"Opponent 00"), first.data.index(b"Opponent 26"))
        self.assertIn(b"Next", first.data)
        self.assertIn(b"Previous", second.data)

    def test_partial_no_snapshot_and_database_failure_are_truthful(self):
        organization_id, accounts = self._organization(
            "Truthful State College", ("coach@example.edu", "admin")
        )
        partial_id = self._report(organization_id, "Partial Bears", 201)
        self._snapshot(organization_id, partial_id, "partial", self.now)
        self._report(organization_id, "New Wolves", 202)
        client = self._client(accounts[0], organization_id)

        response = client.get("/reports")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Partial", response.data)
        self.assertIn(b"1 unavailable", response.data)
        self.assertIn(b"No snapshot yet", response.data)

        class _BrokenFactory:
            def begin(self):
                raise RuntimeError("database detail must not escape")

        self.app_module.app.extensions["roster_scout_report_session_factory"] = (
            _BrokenFactory()
        )
        failed = client.get("/reports")
        self.assertEqual(failed.status_code, 503)
        self.assertIn(b"We couldn\xe2\x80\x99t load reports", failed.data)
        self.assertIn(b"Try again", failed.data)
        self.assertNotIn(b"No opponent reports yet", failed.data)
        self.assertNotIn(b"database detail", failed.data)

    def test_absent_and_foreign_report_use_the_same_non_enumerating_contract(self):
        from roster_scout.repositories import get_report_for_organization

        first_id, _ = self._organization(
            "First Organization", ("first@example.edu", "admin")
        )
        second_id, _ = self._organization(
            "Second Organization", ("second@example.edu", "admin")
        )
        foreign_report_id = self._report(second_id, "Hidden Hawks", 301)

        with self.Session.begin() as session:
            foreign = get_report_for_organization(
                session, first_id, foreign_report_id
            )
            absent = get_report_for_organization(
                session, first_id, uuid.uuid4()
            )
        self.assertIsNone(foreign)
        self.assertIsNone(absent)

    def test_desktop_and_mobile_render_equivalent_report_fields(self):
        organization_id, accounts = self._organization(
            "Accessible University", ("coach@example.edu", "admin")
        )
        report_id = self._report(organization_id, "Accessible Owls", 401)
        self._snapshot(organization_id, report_id, "ready", self.now)
        response = self._client(accounts[0], organization_id).get("/reports")

        self.assertEqual(response.status_code, 200)
        for value in (
            b"Accessible Owls",
            b"D1 \xc2\xb7 2026",
            b"Latest snapshot",
            b"Build status",
            b"Snapshots",
            b"Open report",
        ):
            self.assertGreaterEqual(response.data.count(value), 2, value)
        self.assertIn(b"<table", response.data)
        self.assertIn(b"<time datetime=", response.data)
        self.assertIn(b'<ul class="member-cards"', response.data)


if __name__ == "__main__":
    unittest.main()
