"""Active-organization report library integration contracts."""

from __future__ import annotations

import uuid
import unittest
import re
from urllib.parse import parse_qs, urlsplit
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Barrier
from unittest.mock import Mock, patch

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from tests.db_support import (
    clean_database,
    require_test_database_url,
    upgrade_database,
    without_csrf_tokens,
)


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
        self.assertIn(b"Updating", admin_html.data)
        self.assertIn(b"Previous report remains available", admin_html.data)
        self.assertIn(b"&lt;1h ago", admin_html.data)
        self.assertNotIn(b"D1 \xc2\xb7 2026", admin_html.data)
        self.assertNotIn(b"Foreign Rivals", admin_html.data)
        self.assertIn(b"Foreign Rivals", foreign_html.data)
        self.assertNotIn(b">Members<", admin_html.data)
        self.assertNotIn(b">Members<", member_html.data)
        self.assertIn(b">New report<", admin_html.data)
        self.assertIn(b">Dashboard<", member_html.data)
        self.assertIn(b"Share with North Valley Baseball staff", admin_html.data)
        self.assertIn(b'id="share-organization-dialog"', admin_html.data)
        self.assertIn(b'account-menu-action--invite', admin_html.data)
        self.assertIn(b'aria-controls="share-organization-dialog"', admin_html.data)
        self.assertNotIn(b'href="/reports?share=staff"', admin_html.data)
        self.assertIn(b'name="return_to" value="/reports"', admin_html.data)
        self.assertNotIn(b'account-menu-action--invite', member_html.data)
        self.assertNotIn(b'id="share-organization-dialog"', member_html.data)
        self.assertIn(b"Shared with North Valley Baseball", member_html.data)

    def test_library_run_age_uses_hours_then_days_then_weeks(self):
        from roster_scout.reports import _relative_run_age

        self.assertEqual(
            _relative_run_age(self.now - timedelta(hours=3), self.now),
            "3h ago",
        )
        self.assertEqual(
            _relative_run_age(self.now - timedelta(days=1), self.now),
            "1d ago",
        )
        self.assertEqual(
            _relative_run_age(self.now - timedelta(weeks=3), self.now),
            "3w ago",
        )

    def test_empty_library_uses_the_shared_premium_workbook_state(self):
        organization_id, accounts = self._organization(
            "Empty State University", ("coach@example.edu", "admin")
        )

        response = self._client(accounts[0], organization_id).get("/reports")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"No opponent reports yet", response.data)
        self.assertIn(b"Your scouting workspace", response.data)
        self.assertIn(b"Create your first report", response.data)
        self.assertIn(b"Opponent Stats MM-DD-YY.xlsx", response.data)
        self.assertIn(b"Fielding", response.data)
        self.assertNotIn(b"roster-scout-dog-outlined", response.data.split(b"<main", 1)[1])

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
        self.assertIn(b"1 section unavailable", response.data)
        self.assertIn(b"Waiting for first report", response.data)

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
            b"Ready to use",
            b"All data available",
            b"1 saved version",
            b"data-team-logo",
        ):
            self.assertGreaterEqual(response.data.count(value), 2, value)
        self.assertIn(b"Open report", response.data)
        self.assertIn(b'data-report-href="/reports/', response.data)
        self.assertIn(b'href="/reports/new">New report</a>', response.data)
        self.assertNotIn(b">Completeness<", response.data)
        self.assertNotIn(b">Snapshots<", response.data)
        self.assertIn(b"<table", response.data)
        self.assertIn(b'<time class="report-meta" datetime=', response.data)
        self.assertIn(b'<ul class="member-cards"', response.data)

    def test_library_uses_the_same_official_catalog_logo_as_the_homepage_picker(self):
        from roster_scout.report_routes import _catalog_logo_urls

        report = SimpleNamespace(source_team_id="2", division="D1")
        official_logo = (
            "https://www.ncaa.com/sites/default/files/images/logos/"
            "schools/bgl/abilene-christian.svg"
        )
        with patch(
            "roster_scout.report_routes.list_ncaa_teams",
            return_value=[{"team_id": "2", "logo_url": official_logo}],
        ):
            logo_urls = _catalog_logo_urls((report,))

        self.assertEqual(logo_urls, {"2": official_logo})

    def test_library_ignores_untrusted_catalog_logo_hosts(self):
        from roster_scout.report_routes import _catalog_logo_urls

        report = SimpleNamespace(source_team_id="2", division="D1")
        with patch(
            "roster_scout.report_routes.list_ncaa_teams",
            return_value=[{"team_id": "2", "logo_url": "https://example.com/logo.svg"}],
        ):
            logo_urls = _catalog_logo_urls((report,))

        self.assertEqual(logo_urls, {})

    def test_library_preserves_reviewed_local_catalog_logos(self):
        from roster_scout.report_routes import _catalog_logo_urls

        report = SimpleNamespace(source_team_id="14927", division="D1")
        with patch(
            "roster_scout.report_routes.list_ncaa_teams",
            return_value=[{
                "team_id": "14927",
                "logo_url": "/static/logos/texas-am-corpus-christi.svg",
            }],
        ):
            logo_urls = _catalog_logo_urls((report,))

        self.assertEqual(
            logo_urls,
            {"14927": "/static/logos/texas-am-corpus-christi.svg"},
        )


class SnapshotHistoryTests(unittest.TestCase):
    """Detail and status reads keep saved data visible and tenant scoped."""

    setUpClass = SharedLibraryTests.__dict__["setUpClass"]
    tearDownClass = SharedLibraryTests.__dict__["tearDownClass"]
    setUp = SharedLibraryTests.setUp
    tearDown = SharedLibraryTests.tearDown
    _organization = SharedLibraryTests._organization
    _client = SharedLibraryTests._client
    _report = SharedLibraryTests._report
    _snapshot = SharedLibraryTests._snapshot
    _job = SharedLibraryTests._job

    def _snapshot_with_sections(self, organization_id, report_id, *sections):
        from roster_scout.models import ReportSection, ReportSnapshot

        published_at = self.now - timedelta(hours=3)
        with self.Session.begin() as session:
            snapshot = ReportSnapshot(
                organization_id=organization_id,
                report_id=report_id,
                status="draft",
                schema_version="2026.1",
                source_collected_at=published_at - timedelta(minutes=5),
                created_by="test",
            )
            session.add(snapshot)
            session.flush()
            for name, status in sections:
                session.add(ReportSection(
                    organization_id=organization_id,
                    snapshot_id=snapshot.id,
                    section=name,
                    critical=name in {"batting", "pitching"},
                    status=status,
                    row_count=2 if status == "complete" else 0,
                    verified_empty=False,
                    collected_at=self.now if status == "complete" else None,
                    failure_category=(
                        "source_unavailable"
                        if status in {"unavailable", "failed"}
                        else None
                    ),
                ))
            session.flush()
            snapshot.status = "ready"
            snapshot.published_at = published_at
            return snapshot.id

    def test_detail_keeps_latest_snapshot_dominant_while_build_is_active(self):
        organization_id, accounts = self._organization(
            "History University", ("coach@example.edu", "admin")
        )
        report_id = self._report(organization_id, "History Hawks", 501)
        snapshot_id = self._snapshot_with_sections(
            organization_id,
            report_id,
            ("batting", "complete"),
            ("pitching", "complete"),
        )
        self._job(organization_id, report_id, "running", self.now)

        client = self._client(accounts[0], organization_id)
        response = client.get(f"/reports/{report_id}")

        self.assertEqual(response.status_code, 200)
        for value in (
            b"History Hawks",
            b"Workbook ready",
            b"Ready",
            b"Adding Last 10 and conference stats",
            b"Adding detailed stats",
            b"season workbook is ready now",
            b"data-local-time",
            b"data-team-logo",
        ):
            self.assertIn(value, response.data)
        self.assertLess(
            response.data.index(b"Adding Last 10 and conference stats"),
            response.data.index(b"Workbook ready"),
        )
        self.assertIn(b'data-report-status-url=', response.data)
        self.assertIn(b'aria-live="polite"', response.data)
        self.assertNotIn(str(organization_id).encode(), response.data)
        self.assertNotIn(b">Refresh report<", response.data)
        self.assertNotIn(b">View refresh status<", response.data)

        from roster_scout.models import SecurityAuditEvent

        with self.Session() as session:
            audit = session.scalar(select(SecurityAuditEvent).where(
                SecurityAuditEvent.action == "snapshot.access"
            ))
        self.assertIsNotNone(audit)
        self.assertEqual(audit.target_id, snapshot_id)

    def test_status_is_complete_safe_view_model_and_terminal_states_are_fixed(self):
        organization_id, accounts = self._organization(
            "Status University", ("coach@example.edu", "member")
        )
        report_id = self._report(organization_id, "Status Bears", 502)
        self._snapshot(organization_id, report_id, "ready", self.now)
        job_id = self._job(organization_id, report_id, "queued", self.now)
        client = self._client(accounts[0], organization_id)

        queued = client.get(f"/reports/{report_id}/status")
        self.assertEqual(queued.status_code, 200)
        self.assertEqual(
            set(queued.get_json()),
            {"state", "heading", "message", "updated_at", "terminal", "sections"},
        )
        self.assertEqual(queued.get_json()["state"], "queued")
        self.assertFalse(queued.get_json()["terminal"])

        from roster_scout.models import ReportJob

        with self.Session.begin() as session:
            job = session.get(ReportJob, job_id)
            job.status = "failed"
            job.failure_category = "source_unavailable"
            job.failure_message = "secret upstream exception detail"
            job.finished_at = self.now

        failed = client.get(f"/reports/{report_id}/status")
        payload = failed.get_json()
        self.assertEqual(payload["state"], "failed")
        self.assertTrue(payload["terminal"])
        serialized = failed.get_data(as_text=True)
        for forbidden in (
            str(organization_id),
            str(accounts[0]),
            str(job_id),
            "secret upstream exception detail",
        ):
            self.assertNotIn(forbidden, serialized)
        self.assertIn("NCAA source unavailable", serialized)

    def test_quick_workbook_is_terminal_while_targeted_detail_status_continues(self):
        from hashlib import sha256

        from roster_scout.models import ReportArtifact, ReportJob, ReportSnapshot

        organization_id, accounts = self._organization(
            "Two Stage University", ("coach@example.edu", "member")
        )
        report_id = self._report(organization_id, "Two Stage Tigers", 509)
        with self.Session.begin() as session:
            snapshot = ReportSnapshot(
                organization_id=organization_id,
                report_id=report_id,
                status="draft",
                schema_version="2026.1",
                collection_contract_version="quick-season-v1",
                source_collected_at=self.now - timedelta(minutes=1),
            )
            session.add(snapshot)
            session.flush()
            snapshot.status = "ready"
            snapshot.published_at = self.now
            payload = b"quick-workbook"
            session.add(ReportArtifact(
                organization_id=organization_id,
                snapshot_id=snapshot.id,
                artifact_type="xlsx",
                status="ready",
                schema_version="coach-facing-two-sheet-xlsx-v4",
                sha256=sha256(payload).hexdigest(),
                object_key="test/two-stage.xlsx",
            ))
            detail_job = ReportJob(
                organization_id=organization_id,
                report_id=report_id,
                idempotency_key=f"detailed:{uuid.uuid4()}",
                build_profile="detailed",
                status="running",
                started_at=self.now,
            )
            session.add(detail_job)
            session.flush()
            detail_job_id = detail_job.id

        client = self._client(accounts[0], organization_id)
        quick_status = client.get(f"/reports/{report_id}/status")
        detailed_status = client.get(
            f"/reports/{report_id}/status?job_id={detail_job_id}"
        )
        reused_status = client.get(
            f"/reports/{report_id}/status?job_id={detail_job_id}"
            "&prefer_current=1"
        )

        self.assertEqual(quick_status.status_code, 200)
        self.assertTrue(quick_status.get_json()["terminal"])
        self.assertEqual(
            quick_status.get_json()["heading"], "Season workbook ready"
        )
        self.assertIn(
            "continuing in the background", quick_status.get_json()["message"]
        )
        self.assertEqual(detailed_status.status_code, 200)
        self.assertFalse(detailed_status.get_json()["terminal"])
        self.assertEqual(
            detailed_status.get_json()["heading"], "Adding detailed stats"
        )
        self.assertTrue(reused_status.get_json()["terminal"])
        self.assertEqual(
            reused_status.get_json()["heading"], "Season workbook ready"
        )

        foreign_id, foreign_accounts = self._organization(
            "Foreign Two Stage University", ("other@example.edu", "member")
        )
        foreign_client = self._client(foreign_accounts[0], foreign_id)
        denied = foreign_client.get(
            f"/reports/{report_id}/status?job_id={detail_job_id}"
        )
        self.assertEqual(denied.status_code, 404)

    def test_absent_and_foreign_detail_and_status_share_one_404_contract(self):
        first_id, first_accounts = self._organization(
            "First History Org", ("first@example.edu", "admin")
        )
        second_id, _ = self._organization(
            "Second History Org", ("second@example.edu", "admin")
        )
        foreign_report_id = self._report(second_id, "Private Panthers", 503)
        missing_report_id = uuid.uuid4()
        client = self._client(first_accounts[0], first_id)

        for suffix in ("", "/status"):
            foreign = client.get(f"/reports/{foreign_report_id}{suffix}")
            absent = client.get(f"/reports/{missing_report_id}{suffix}")
            self.assertEqual(foreign.status_code, 404)
            self.assertEqual(absent.status_code, 404)
            self.assertEqual(foreign.content_type, absent.content_type)
            self.assertEqual(
                without_csrf_tokens(foreign.get_data()),
                without_csrf_tokens(absent.get_data()),
            )

    def test_snapshot_history_is_newest_first_bounded_and_paged(self):
        organization_id, accounts = self._organization(
            "Paged History University", ("coach@example.edu", "admin")
        )
        report_id = self._report(organization_id, "Paged Panthers", 504)
        for index in range(27):
            self._snapshot(
                organization_id,
                report_id,
                "ready",
                self.now - timedelta(hours=index),
            )

        client = self._client(accounts[0], organization_id)
        first = client.get(f"/reports/{report_id}")
        second = client.get(f"/reports/{report_id}?history_page=2")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.data.count(b"data-snapshot-row"), 24)
        self.assertEqual(second.data.count(b"data-snapshot-row"), 2)
        self.assertIn(b"Next", first.data)
        self.assertIn(b"Previous", second.data)
        self.assertIn(b"Previous reports", first.data)
        self.assertIn(b"26 saved", first.data)
        self.assertNotIn(b"Showing 1\xe2\x80\x9325", first.data)

    def test_partial_snapshot_keeps_ready_sections_and_names_unavailable_sections(self):
        from roster_scout.models import ReportSection, ReportSnapshot

        organization_id, accounts = self._organization(
            "Partial History University", ("coach@example.edu", "member")
        )
        report_id = self._report(
            organization_id, "Partial Pelicans", 505
        )
        published_at = self.now - timedelta(hours=2)
        with self.Session.begin() as session:
            snapshot = ReportSnapshot(
                organization_id=organization_id,
                report_id=report_id,
                status="draft",
                schema_version="2026.1",
                source_collected_at=published_at - timedelta(minutes=5),
                created_by="test",
            )
            session.add(snapshot)
            session.flush()
            session.add_all((
                ReportSection(
                    organization_id=organization_id,
                    snapshot_id=snapshot.id,
                    section="batting",
                    critical=True,
                    status="complete",
                    row_count=12,
                    verified_empty=False,
                    collected_at=published_at - timedelta(minutes=5),
                ),
                ReportSection(
                    organization_id=organization_id,
                    snapshot_id=snapshot.id,
                    section="fielding",
                    critical=False,
                    status="unavailable",
                    row_count=0,
                    verified_empty=False,
                    failure_category="source_unavailable",
                ),
            ))
            session.flush()
            snapshot.status = "partial"
            snapshot.published_at = published_at

        response = self._client(accounts[0], organization_id).get(
            f"/reports/{report_id}"
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Some data is unavailable.", response.data)
        self.assertIn(b"The saved workbook is still available. Missing:", response.data)
        self.assertIn(b"Fielding", response.data)
        self.assertNotIn(b"NCAA source unavailable", response.data)


class SnapshotPlayerDetailRouteTests(unittest.TestCase):
    """Published player detail remains immutable, bounded, and tenant scoped."""

    setUpClass = SharedLibraryTests.__dict__["setUpClass"]
    tearDownClass = SharedLibraryTests.__dict__["tearDownClass"]
    setUp = SharedLibraryTests.setUp
    tearDown = SharedLibraryTests.tearDown
    _organization = SharedLibraryTests._organization
    _client = SharedLibraryTests._client
    _report = SharedLibraryTests._report

    def _source(
        self,
        *,
        organization_id,
        snapshot_id,
        player_team_season_id,
        section,
        row_ordinal,
        source_player_id="9001",
    ):
        from roster_scout.models import SourceRecord

        return SourceRecord(
            organization_id=organization_id,
            snapshot_id=snapshot_id,
            player_team_season_id=player_team_season_id,
            section=section,
            row_ordinal=row_ordinal,
            source_url=(
                "https://stats.ncaa.org/players/9001?token=do-not-leak"
            ),
            source_team_id="team-701",
            source_season_id="2026",
            source_season_team_id="season-team-701",
            source_stat_category_id=f"category-{section}",
            source_player_id=source_player_id,
            collected_at=self.now - timedelta(minutes=10),
            parser_version="parser-test-v1",
            schema_version="schema-test-v1",
            payload_sha256=f"{row_ordinal + 1:064x}",
            raw_values={"private": "secret raw html", "section": section},
        )

    def _seed_detail_graph(self):
        from roster_scout.models import (
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
            StatisticScope,
        )

        organization_id, accounts = self._organization(
            "Detail University", ("detail@example.edu", "member")
        )
        foreign_organization_id, foreign_accounts = self._organization(
            "Foreign Detail University", ("foreign-detail@example.edu", "admin")
        )
        report_id = self._report(organization_id, "Detail Falcons", 701)
        foreign_report_id = self._report(
            foreign_organization_id, "Private Owls", 702
        )

        with self.Session.begin() as session:
            report = session.get(Report, report_id)
            snapshot = ReportSnapshot(
                organization_id=organization_id,
                report_id=report_id,
                status="draft",
                schema_version="2026.1",
                source_collected_at=self.now - timedelta(minutes=10),
                created_by_account_id=accounts[0],
            )
            player = Player(
                source="ncaa",
                source_player_id="9001",
                display_name="Safe Player",
            )
            session.add_all((snapshot, player))
            session.flush()
            player_team_season = PlayerTeamSeason(
                organization_id=organization_id,
                snapshot_id=snapshot.id,
                player_id=player.id,
                team_season_id=report.team_season_id,
                identity_status="authoritative",
                identity_key="ncaa:9001",
                display_name="Safe Player",
                jersey_number="7",
                position="OF/P",
            )
            session.add(player_team_season)
            session.flush()

            sources = {}
            for ordinal, section in enumerate((
                "batting",
                "pitching",
                "fielding",
                "split_batting",
                "career_batting",
                "schedule",
                "batting_game_log",
                "pitching_game_log",
                "fielding_game_log",
            )):
                source = self._source(
                    organization_id=organization_id,
                    snapshot_id=snapshot.id,
                    player_team_season_id=(
                        None if section == "schedule" else player_team_season.id
                    ),
                    section=section,
                    row_ordinal=ordinal,
                    source_player_id=None if section == "schedule" else "9001",
                )
                session.add(source)
                sources[section] = source
            session.flush()

            session.add_all((
                BattingFact(
                    organization_id=organization_id,
                    snapshot_id=snapshot.id,
                    player_team_season_id=player_team_season.id,
                    source_record_id=sources["batting"].id,
                    games=20,
                    games_started=19,
                    plate_appearances=82,
                    at_bats=70,
                    runs=18,
                    hits=28,
                    doubles=6,
                    triples=1,
                    home_runs=4,
                    runs_batted_in=21,
                    walks=9,
                    hit_by_pitch=2,
                    strikeouts=11,
                    sacrifice_flies=1,
                    sacrifice_hits=0,
                    stolen_bases=5,
                    caught_stealing=1,
                    total_bases=48,
                ),
                PitchingFact(
                    organization_id=organization_id,
                    snapshot_id=snapshot.id,
                    player_team_season_id=player_team_season.id,
                    source_record_id=sources["pitching"].id,
                    appearances=8,
                    games_started=4,
                    innings_outs=63,
                    hits_allowed=17,
                    runs_allowed=7,
                    earned_runs=6,
                    walks=5,
                    strikeouts=24,
                    home_runs_allowed=1,
                    hit_batters=1,
                    batters_faced=81,
                    opponent_at_bats=74,
                    opponent_sacrifice_flies=1,
                ),
                FieldingFact(
                    organization_id=organization_id,
                    snapshot_id=snapshot.id,
                    player_team_season_id=player_team_season.id,
                    source_record_id=sources["fielding"].id,
                    games=20,
                    games_started=19,
                    putouts=32,
                    assists=4,
                    errors=1,
                    total_chances=37,
                    double_plays=2,
                    fielding_percentage=Decimal("0.97297"),
                    passed_balls=0,
                    stolen_base_attempts=0,
                    caught_stealing_by=0,
                ),
            ))

            metric_definition = MetricDefinition(
                code="avg",
                version="metrics-v1",
                formula="H / AB",
            )
            session.add(metric_definition)
            session.flush()
            session.add(MetricValue(
                organization_id=organization_id,
                snapshot_id=snapshot.id,
                metric_definition_id=metric_definition.id,
                player_team_season_id=player_team_season.id,
                value=Decimal("0.40000000"),
                status="available",
                inputs_json={"hits": 28, "at_bats": 70},
                sample_json={"at_bats": 70},
                scope="season",
            ))

            split_scope = StatisticScope(
                organization_id=organization_id,
                snapshot_id=snapshot.id,
                scope_kind="source_split",
                scope_code="vs_lhp",
                stat_type="batting",
                display_label="vs LHP",
                source_identifier="split-1",
                source_path="/teams/701/stats?split=1",
                source_season_id="2026",
                source_season_team_id="season-team-701",
                source_stat_category_id="category-split_batting",
                status="complete",
                collected_at=self.now - timedelta(minutes=10),
                parser_version="parser-test-v1",
                schema_version="schema-test-v1",
                sample_json={"plate_appearances": 12},
                source_payload_sha256="a" * 64,
            )
            session.add(split_scope)
            session.flush()
            session.add(ScopedBattingFact(
                organization_id=organization_id,
                snapshot_id=snapshot.id,
                statistic_scope_id=split_scope.id,
                player_team_season_id=player_team_season.id,
                source_record_id=sources["split_batting"].id,
                at_bats=10,
                hits=4,
                sample_json={"plate_appearances": 12},
                counts_json={"at_bats": 10, "hits": 4, "walks": 2},
                contributing_fact_ids=[],
            ))
            session.add(MetricValue(
                organization_id=organization_id,
                snapshot_id=snapshot.id,
                metric_definition_id=metric_definition.id,
                player_team_season_id=player_team_season.id,
                statistic_scope_id=split_scope.id,
                value=Decimal("0.40000000"),
                status="available",
                inputs_json={"hits": 4, "at_bats": 10},
                sample_json={"at_bats": 10},
                scope="vs_lhp",
            ))

            career_row_id = "c" * 64
            session.add(CareerSeasonFact(
                organization_id=organization_id,
                snapshot_id=snapshot.id,
                player_team_season_id=player_team_season.id,
                source_record_id=sources["career_batting"].id,
                row_id=career_row_id,
                stat_type="batting",
                season_label="2025-26",
                source_season_id="2026",
                school_name="Detail University",
                source_season_team_id="season-team-701",
                division="d1",
                source_stat_category_id="category-career_batting",
                counts_json={"at_bats": 70, "hits": 28},
                rates_json={"avg": "0.400"},
                at_bats=70,
                hits=28,
            ))
            session.add(CareerAggregate(
                organization_id=organization_id,
                snapshot_id=snapshot.id,
                player_team_season_id=player_team_season.id,
                stat_type="batting",
                status="complete",
                definition_version="career-v1",
                counts_json={"at_bats": 70, "hits": 28},
                included_row_ids=[career_row_id],
                excluded_rows_json={},
            ))

            game = Game(
                organization_id=organization_id,
                snapshot_id=snapshot.id,
                source_record_id=sources["schedule"].id,
                source_season_id="2026",
                game_date=self.now.date(),
                completed_at=self.now - timedelta(hours=2),
                status="completed",
                result_code="W",
                result_sha256="b" * 64,
                opponent_team_id="opponent-1",
                opponent_season_team_id="opponent-season-1",
                doubleheader_ordinal=1,
                venue="home",
                source_game_id="game-701",
                identity_kind="source",
            )
            session.add(game)
            session.flush()
            session.add_all((
                BattingGameLogFact(
                    organization_id=organization_id,
                    snapshot_id=snapshot.id,
                    player_team_season_id=player_team_season.id,
                    source_record_id=sources["batting_game_log"].id,
                    game_id=game.id,
                    row_id="d" * 64,
                    at_bats=4,
                    hits=2,
                    runs=1,
                    home_runs=1,
                    walks=0,
                    strikeouts=1,
                    counts_json={"at_bats": 4, "hits": 2},
                ),
                PitchingGameLogFact(
                    organization_id=organization_id,
                    snapshot_id=snapshot.id,
                    player_team_season_id=player_team_season.id,
                    source_record_id=sources["pitching_game_log"].id,
                    game_id=game.id,
                    row_id="e" * 64,
                    innings_outs=9,
                    hits_allowed=2,
                    runs_allowed=1,
                    earned_runs=1,
                    walks=1,
                    strikeouts=4,
                    batters_faced=12,
                    counts_json={"innings_outs": 9, "strikeouts": 4},
                ),
                FieldingGameLogFact(
                    organization_id=organization_id,
                    snapshot_id=snapshot.id,
                    player_team_season_id=player_team_season.id,
                    source_record_id=sources["fielding_game_log"].id,
                    game_id=game.id,
                    row_id="f" * 64,
                    putouts=2,
                    assists=1,
                    errors=0,
                    total_chances=3,
                    passed_balls=0,
                    stolen_base_attempts=0,
                    caught_stealing_by=0,
                    counts_json={"putouts": 2, "assists": 1, "errors": 0},
                ),
            ))
            for section in (
                "batting",
                "pitching",
                "fielding",
                "splits",
                "player_history",
                "game_logs",
            ):
                session.add(ReportSection(
                    organization_id=organization_id,
                    snapshot_id=snapshot.id,
                    section=section,
                    critical=section in {"batting", "pitching"},
                    status="complete",
                    row_count=1,
                    verified_empty=False,
                    collected_at=self.now - timedelta(minutes=10),
                ))
            session.flush()
            snapshot.status = "ready"
            snapshot.published_at = self.now - timedelta(hours=1)

            foreign_report = session.get(Report, foreign_report_id)
            foreign_snapshot = ReportSnapshot(
                organization_id=foreign_organization_id,
                report_id=foreign_report_id,
                status="draft",
                schema_version="2026.1",
                source_collected_at=self.now - timedelta(minutes=5),
                created_by_account_id=foreign_accounts[0],
            )
            foreign_player = Player(
                source="ncaa",
                source_player_id="9002",
                display_name="Foreign Player",
            )
            session.add_all((foreign_snapshot, foreign_player))
            session.flush()
            session.add(PlayerTeamSeason(
                organization_id=foreign_organization_id,
                snapshot_id=foreign_snapshot.id,
                player_id=foreign_player.id,
                team_season_id=foreign_report.team_season_id,
                identity_status="authoritative",
                identity_key="ncaa:9002",
                display_name="Foreign Player",
            ))
            session.flush()
            foreign_snapshot.status = "ready"
            foreign_snapshot.published_at = self.now

            return {
                "organization_id": organization_id,
                "account_id": accounts[0],
                "report_id": report_id,
                "snapshot_id": snapshot.id,
                "player_id": player.id,
                "player_team_season_id": player_team_season.id,
                "game_id": game.id,
                "foreign_organization_id": foreign_organization_id,
                "foreign_report_id": foreign_report_id,
                "foreign_snapshot_id": foreign_snapshot.id,
                "foreign_player_id": foreign_player.id,
            }

    @staticmethod
    def _detail_path(ids, *, report_id=None, snapshot_id=None, player_id=None):
        return (
            f"/reports/{report_id or ids['report_id']}"
            f"/snapshots/{snapshot_id or ids['snapshot_id']}"
            f"/players/{player_id or ids['player_id']}"
        )

    def _add_successor(self, ids):
        from roster_scout.models import (
            BattingFact,
            PlayerTeamSeason,
            Report,
            ReportSnapshot,
        )

        with self.Session.begin() as session:
            report = session.get(Report, ids["report_id"])
            successor = ReportSnapshot(
                organization_id=ids["organization_id"],
                report_id=ids["report_id"],
                predecessor_snapshot_id=ids["snapshot_id"],
                status="draft",
                schema_version="2026.1",
                source_collected_at=self.now,
                created_by_account_id=ids["account_id"],
            )
            session.add(successor)
            session.flush()
            successor_player = PlayerTeamSeason(
                organization_id=ids["organization_id"],
                snapshot_id=successor.id,
                player_id=ids["player_id"],
                team_season_id=report.team_season_id,
                identity_status="authoritative",
                identity_key="ncaa:9001",
                display_name="Safe Player",
                jersey_number="7",
                position="OF/P",
            )
            session.add(successor_player)
            session.flush()
            source = self._source(
                organization_id=ids["organization_id"],
                snapshot_id=successor.id,
                player_team_season_id=successor_player.id,
                section="batting",
                row_ordinal=0,
            )
            session.add(source)
            session.flush()
            session.add(BattingFact(
                organization_id=ids["organization_id"],
                snapshot_id=successor.id,
                player_team_season_id=successor_player.id,
                source_record_id=source.id,
                at_bats=1,
                hits=1,
            ))
            session.flush()
            successor.status = "ready"
            successor.published_at = self.now
            return successor.id

    def test_authorized_same_snapshot_player_detail_contract(self):
        """Named RED sentinel: one missing route assertion and no setup errors."""
        ids = self._seed_detail_graph()
        response = self._client(
            ids["account_id"], ids["organization_id"]
        ).get(self._detail_path(ids))
        self.assertEqual(response.status_code, 200)

    def test_every_view_is_fixed_and_joins_only_the_exact_snapshot(self):
        ids = self._seed_detail_graph()
        client = self._client(ids["account_id"], ids["organization_id"])
        expected_data_keys = {
            "season": {"season"},
            "splits": {"splits"},
            "career": {"career"},
            "games": {"games"},
            "all": {"season", "splits", "career", "games"},
        }
        for view in (None, "season", "splits", "career", "games", "all"):
            with self.subTest(view=view or "default"):
                suffix = "" if view is None else f"?view={view}"
                response = client.get(f"{self._detail_path(ids)}{suffix}")
                self.assertEqual(response.status_code, 200)
                payload = response.get_json()
                expected_view = view or "all"
                self.assertEqual(payload["view"], expected_view)
                self.assertEqual(
                    set(payload),
                    {"snapshot", "player", "completeness", "view", "links", "data"},
                )
                self.assertEqual(set(payload["data"]), expected_data_keys[expected_view])
                self.assertEqual(
                    set(payload["links"]),
                    {"season", "splits", "career", "games", "all"},
                )

        payload = client.get(f"{self._detail_path(ids)}?view=all").get_json()
        self.assertEqual(payload["player"]["id"], str(ids["player_id"]))
        self.assertEqual(payload["snapshot"]["id"], str(ids["snapshot_id"]))
        self.assertEqual(payload["data"]["season"]["batting"]["counts"]["hits"], 28)
        self.assertEqual(payload["data"]["splits"][0]["scope_code"], "vs_lhp")
        self.assertEqual(payload["data"]["career"]["aggregate"][0]["status"], "complete")
        self.assertEqual(payload["data"]["games"][0]["game"]["id"], str(ids["game_id"]))
        self.assertEqual(
            set(payload["data"]["games"][0]["facts"]),
            {"batting", "pitching", "fielding"},
        )
        serialized = response.get_data(as_text=True).casefold()
        for forbidden in (
            "secret raw html",
            "do-not-leak",
            "token=",
            "detail@example.edu",
            "foreign player",
            str(ids["foreign_organization_id"]),
        ):
            self.assertNotIn(forbidden, serialized)

    def test_foreign_absent_and_mixed_identifiers_share_audited_404(self):
        from roster_scout.models import SecurityAuditEvent

        ids = self._seed_detail_graph()
        client = self._client(ids["account_id"], ids["organization_id"])
        paths = (
            self._detail_path(ids, report_id=ids["foreign_report_id"]),
            self._detail_path(ids, snapshot_id=ids["foreign_snapshot_id"]),
            self._detail_path(ids, player_id=ids["foreign_player_id"]),
            self._detail_path(ids, player_id=uuid.uuid4()),
        )
        responses = [client.get(path) for path in paths]
        self.assertTrue(all(response.status_code == 404 for response in responses))
        self.assertTrue(all(response.get_json() == {"error": "not_found"} for response in responses))
        with self.Session() as session:
            audits = tuple(session.scalars(
                select(SecurityAuditEvent).where(
                    SecurityAuditEvent.action == "authorization.deny"
                )
            ))
        self.assertEqual(len(audits), len(paths))
        self.assertTrue(all(item.organization_id == ids["organization_id"] for item in audits))
        self.assertTrue(all(item.outcome == "denied" and item.reason == "not_found" for item in audits))

    def test_invalid_view_and_service_failure_are_fixed(self):
        ids = self._seed_detail_graph()
        client = self._client(ids["account_id"], ids["organization_id"])
        invalid = client.get(f"{self._detail_path(ids)}?view=private")
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid.get_json(), {"error": "invalid_view"})
        with patch(
            "roster_scout.report_routes.load_snapshot_player_detail",
            side_effect=RuntimeError("secret database exception"),
        ):
            failed = client.get(self._detail_path(ids))
        self.assertEqual(failed.status_code, 503)
        self.assertEqual(
            failed.get_json(), {"error": "player_detail_service_unavailable"}
        )
        self.assertNotIn("secret database exception", failed.get_data(as_text=True))

    def test_successor_creation_cannot_change_predecessor_response(self):
        ids = self._seed_detail_graph()
        client = self._client(ids["account_id"], ids["organization_id"])
        path = f"{self._detail_path(ids)}?view=all"
        before = client.get(path)
        successor_id = self._add_successor(ids)
        after = client.get(path)
        self.assertEqual(before.status_code, 200)
        self.assertEqual(after.status_code, 200)
        self.assertEqual(before.get_json(), after.get_json())
        self.assertNotIn(str(successor_id), after.get_data(as_text=True))

    def test_route_never_fetches_ncaa_or_mutates_canonical_data(self):
        from roster_scout.models import SourceRecord

        ids = self._seed_detail_graph()
        client = self._client(ids["account_id"], ids["organization_id"])
        with self.Session() as session:
            before = session.scalar(select(func.count(SourceRecord.id)))
        with patch(
            "roster_scout.report_routes.list_ncaa_teams",
            side_effect=AssertionError("detail route must not contact NCAA"),
        ):
            response = client.get(f"{self._detail_path(ids)}?view=games")
        with self.Session() as session:
            after = session.scalar(select(func.count(SourceRecord.id)))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(before, after)


class FailedBuildRetryTests(unittest.TestCase):
    """Failed attempts stay immutable while retry and artifacts remain scoped."""

    setUpClass = SharedLibraryTests.__dict__["setUpClass"]
    tearDownClass = SharedLibraryTests.__dict__["tearDownClass"]
    _organization = SharedLibraryTests._organization
    _report = SharedLibraryTests._report
    _snapshot = SharedLibraryTests._snapshot

    def setUp(self):
        SharedLibraryTests.setUp(self)
        self.original_artifact_reader = self.app_module.app.extensions.get(
            "roster_scout_artifact_reader"
        )
        self.original_google_sheets_client = self.app_module.app.extensions.get(
            "roster_scout_google_sheets_oauth"
        )

    def tearDown(self):
        if self.original_artifact_reader is None:
            self.app_module.app.extensions.pop(
                "roster_scout_artifact_reader", None
            )
        else:
            self.app_module.app.extensions["roster_scout_artifact_reader"] = (
                self.original_artifact_reader
            )
        if self.original_google_sheets_client is None:
            self.app_module.app.extensions.pop(
                "roster_scout_google_sheets_oauth", None
            )
        else:
            self.app_module.app.extensions["roster_scout_google_sheets_oauth"] = (
                self.original_google_sheets_client
            )
        SharedLibraryTests.tearDown(self)

    def _client_with_credential(self, account_id, organization_id):
        from roster_scout.sessions import SESSION_COOKIE_NAME, create_account_session

        with self.Session.begin() as session:
            credential = create_account_session(
                session,
                account_id=account_id,
                active_organization_id=organization_id,
                sealed_provider_state="sealed-retry-session",
                now=self.now,
            )
        client = self.app_module.app.test_client()
        client.set_cookie(SESSION_COOKIE_NAME, credential.cookie_value)
        return client, credential

    def _csrf_token(self, credential):
        from itsdangerous import URLSafeTimedSerializer

        return URLSafeTimedSerializer(
            self.app_module.app.secret_key,
            salt="wtf-csrf-token",
        ).dumps(credential.csrf_secret)

    def _failed_job(self, organization_id, report_id, account_id, category):
        from roster_scout.models import ReportJob

        with self.Session.begin() as session:
            job = ReportJob(
                organization_id=organization_id,
                report_id=report_id,
                requested_by_account_id=account_id,
                idempotency_key=str(uuid.uuid4()),
                status="failed",
                failure_category=category,
                failure_message="secret upstream exception and token",
                finished_at=self.now,
            )
            session.add(job)
            session.flush()
            return job.id

    def _artifact(self, organization_id, snapshot_id, object_key, payload):
        from hashlib import sha256

        from roster_scout.models import ReportArtifact

        with self.Session.begin() as session:
            artifact = ReportArtifact(
                organization_id=organization_id,
                snapshot_id=snapshot_id,
                artifact_type="xlsx",
                status="ready",
                schema_version="2026.1",
                sha256=sha256(payload).hexdigest(),
                object_key=object_key,
            )
            session.add(artifact)
            session.flush()
            return artifact.id

    def _retry(self, organization_id, account_id, report_id, failed_job_id):
        from roster_scout.reports import accept_authorized_report_retry

        with self.Session.begin() as session:
            return accept_authorized_report_retry(
                session,
                actor_account_id=account_id,
                organization_id=organization_id,
                report_id=report_id,
                failed_job_id=failed_job_id,
                idempotency_key=str(uuid.uuid4()),
            )

    def test_retry_preserves_failed_attempt_snapshot_and_converges_duplicates(self):
        from roster_scout.models import (
            ProductUsageEvent,
            ReportJob,
            ReportJobDispatch,
            ReportSnapshot,
            SecurityAuditEvent,
        )

        organization_id, accounts = self._organization(
            "Retry University", ("coach@example.edu", "member")
        )
        report_id = self._report(organization_id, "Retry Ravens", 506)
        snapshot_id = self._snapshot(
            organization_id, report_id, "ready", self.now - timedelta(hours=1)
        )
        failed_job_id = self._failed_job(
            organization_id, report_id, accounts[0], "source_unavailable"
        )
        start = Barrier(2)

        def retry_once(_index):
            start.wait(timeout=10)
            return self._retry(
                organization_id, accounts[0], report_id, failed_job_id
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            accepted = list(pool.map(retry_once, range(2)))

        self.assertEqual(accepted[0].job_id, accepted[1].job_id)
        with self.Session() as session:
            jobs = tuple(session.scalars(
                select(ReportJob).order_by(ReportJob.created_at, ReportJob.id)
            ))
            snapshot = session.get(ReportSnapshot, snapshot_id)
            usage_count = session.scalar(select(func.count(ProductUsageEvent.id)))
            dispatch_count = session.scalar(select(func.count(ReportJobDispatch.id)))
            audits = tuple(session.scalars(select(SecurityAuditEvent)))
        self.assertEqual(len(jobs), 2)
        self.assertEqual(jobs[0].id, failed_job_id)
        self.assertEqual(jobs[0].status, "failed")
        self.assertEqual(jobs[0].failure_message, "secret upstream exception and token")
        self.assertEqual(jobs[1].status, "queued")
        self.assertTrue(jobs[1].idempotency_key.startswith(f"retry:{failed_job_id}:"))
        self.assertEqual(dispatch_count, 1)
        self.assertEqual(usage_count, 1)
        self.assertEqual(len(audits), 1)
        self.assertEqual(audits[0].action, "report.retry")
        self.assertEqual(audits[0].reason, "retry_accepted")
        self.assertEqual(snapshot.status, "ready")
        self.assertEqual(snapshot.published_at, self.now - timedelta(hours=1))

    def test_retry_route_is_csrf_protected_and_failure_copy_is_fixed(self):
        organization_id, accounts = self._organization(
            "Failure University", ("coach@example.edu", "admin")
        )
        report_id = self._report(organization_id, "Failure Foxes", 507)
        categories = ("source_unavailable", "database", "queue", "artifact")
        failed_ids = [
            self._failed_job(organization_id, report_id, accounts[0], category)
            for category in categories
        ]
        client, credential = self._client_with_credential(
            accounts[0], organization_id
        )

        detail = client.get(f"/reports/{report_id}")
        self.assertEqual(detail.status_code, 200)
        for safe_copy in (
            b"NCAA source unavailable",
            b"Report service unavailable",
            b"Storage unavailable",
        ):
            self.assertIn(safe_copy, detail.data)
        self.assertNotIn(b"secret upstream exception", detail.data)
        self.assertIn(b"Try again", detail.data)

        rejected = client.post(
            f"/reports/{report_id}/builds/{failed_ids[-1]}/retry",
            data={"idempotency_key": str(uuid.uuid4())},
        )
        self.assertEqual(rejected.status_code, 400)
        accepted = client.post(
            f"/reports/{report_id}/builds/{failed_ids[-1]}/retry",
            data={
                "csrf_token": self._csrf_token(credential),
                "idempotency_key": str(uuid.uuid4()),
            },
        )
        self.assertEqual(accepted.status_code, 303)
        self.assertEqual(
            accepted.headers["Location"],
            f"/reports/{report_id}?notice=retry_started",
        )

    def test_prepare_streams_only_exact_stored_artifact_without_usage_write(self):
        from contextlib import contextmanager
        from roster_scout.models import ProductUsageEvent, SecurityAuditEvent

        organization_id, accounts = self._organization(
            "Artifact University", ("coach@example.edu", "member")
        )
        report_id = self._report(organization_id, "Artifact Antelopes", 508)
        first_snapshot_id = self._snapshot(
            organization_id, report_id, "ready", self.now - timedelta(hours=2)
        )
        second_snapshot_id = self._snapshot(
            organization_id, report_id, "ready", self.now - timedelta(hours=1)
        )
        first_payload = b"PK\x03\x04exact historical workbook"
        second_payload = b"PK\x03\x04newer workbook"
        first_artifact_id = self._artifact(
            organization_id,
            first_snapshot_id,
            "immutable/first.xlsx",
            first_payload,
        )
        second_artifact_id = self._artifact(
            organization_id,
            second_snapshot_id,
            "immutable/second.xlsx",
            second_payload,
        )
        stored = {
            "immutable/first.xlsx": first_payload,
            "immutable/second.xlsx": second_payload,
        }
        active_transactions = [0]
        session_factory = self.Session

        class TrackingFactory:
            @contextmanager
            def begin(self):
                with session_factory.begin() as session:
                    active_transactions[0] += 1
                    try:
                        yield session
                    finally:
                        active_transactions[0] -= 1

        def read_artifact(object_key):
            self.assertEqual(active_transactions[0], 0)
            return stored[object_key]

        reader = Mock(side_effect=read_artifact)
        self.app_module.app.extensions[
            "roster_scout_report_session_factory"
        ] = TrackingFactory()
        self.app_module.app.extensions["roster_scout_artifact_reader"] = reader
        client, credential = self._client_with_credential(
            accounts[0], organization_id
        )

        response = client.post(
            f"/reports/{report_id}/snapshots/{first_snapshot_id}"
            f"/artifacts/{first_artifact_id}/download",
            data={"csrf_token": self._csrf_token(credential)},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, first_payload)
        self.assertIn(
            f"Team Stats {datetime.now(UTC).strftime('%m-%d-%y')}.xlsx",
            response.headers["Content-Disposition"],
        )
        self.assertNotIn("Artifact Antelopes", response.headers["Content-Disposition"])
        self.assertEqual(reader.call_args_list, [(("immutable/first.xlsx",), {})])
        receipt = response.headers["X-Roster-Scout-Download-Event-ID"]
        proof = response.headers["X-Roster-Scout-Download-Ack-Proof"]
        self.assertEqual(str(uuid.UUID(receipt)), receipt)
        self.assertLessEqual(len(proof), 512)
        scope = {
            "account_id": accounts[0],
            "organization_id": organization_id,
            "report_id": report_id,
            "snapshot_id": first_snapshot_id,
            "artifact_id": first_artifact_id,
        }
        self.assertTrue(
            self.app_module._verify_authenticated_download_ack_proof(
                proof, receipt, **scope
            )
        )
        self.assertFalse(
            self.app_module._verify_authenticated_download_ack_proof(
                proof,
                receipt,
                **{**scope, "artifact_id": second_artifact_id},
            )
        )
        decoded_proof = self.app_module._DOWNLOAD_ACK_SERIALIZER.loads(proof)
        self.assertEqual(decoded_proof["artifact_id"], str(first_artifact_id))
        for name, identifier in scope.items():
            if name == "artifact_id":
                continue
            self.assertNotIn(str(identifier), str(decoded_proof))
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertNotIn("immutable/first.xlsx", str(response.headers))
        self.assertNotIn(
            "secret", response.data.decode("utf-8", errors="ignore")
        )
        with self.Session() as session:
            self.assertEqual(
                session.scalar(select(func.count(ProductUsageEvent.id))), 0
            )
            audit = session.scalar(select(SecurityAuditEvent))
        self.assertEqual(audit.action, "artifact.download")
        self.assertEqual(audit.reason, "download_prepared")
        self.assertEqual(audit.target_id, first_artifact_id)

        csrf = self._csrf_token(credential)
        substituted = client.post(
            f"/reports/{report_id}/snapshots/{first_snapshot_id}"
            f"/artifacts/{second_artifact_id}/download",
            data={"csrf_token": csrf},
        )
        absent = client.post(
            f"/reports/{report_id}/snapshots/{first_snapshot_id}"
            f"/artifacts/{uuid.uuid4()}/download",
            data={"csrf_token": csrf},
        )
        self.assertEqual(substituted.status_code, 404)
        self.assertEqual(
            without_csrf_tokens(substituted.get_data()),
            without_csrf_tokens(absent.get_data()),
        )
        self.assertEqual(reader.call_count, 1)

    def test_google_sheets_oauth_is_state_bound_and_exports_exact_artifact(self):
        from hashlib import sha256
        from roster_scout.models import SecurityAuditEvent

        payload = b"PK-exact-saved-workbook"
        organization_id, accounts = self._organization(
            "Sheets University", ("coach@example.edu", "member")
        )
        report_id = self._report(organization_id, "Sheets Sharks", 511)
        snapshot_id = self._snapshot(
            organization_id, report_id, "ready", self.now - timedelta(hours=1)
        )
        artifact_id = self._artifact(
            organization_id, snapshot_id, "immutable/sheets.xlsx", payload
        )
        self.app_module.app.extensions["roster_scout_artifact_reader"] = Mock(
            return_value=payload
        )
        oauth = Mock()
        oauth.configured = True
        oauth.authorization_url.side_effect = (
            lambda *, state: f"https://accounts.google.test/oauth?state={state}"
        )
        oauth.exchange_code.return_value = "access-token"
        oauth.import_workbook.return_value = (
            "https://docs.google.com/spreadsheets/d/sheet_id-123456789/edit"
        )
        self.app_module.app.extensions["roster_scout_google_sheets_oauth"] = oauth
        client, credential = self._client_with_credential(
            accounts[0], organization_id
        )
        export_path = (
            f"/reports/{report_id}/snapshots/{snapshot_id}"
            f"/artifacts/{artifact_id}/google-sheets"
        )

        json_started = client.post(
            export_path,
            data={"csrf_token": self._csrf_token(credential)},
            headers={"Accept": "application/json"},
        )

        self.assertEqual(json_started.status_code, 200)
        self.assertTrue(
            json_started.get_json()["authorization_url"].startswith(
                "https://accounts.google.test/oauth?state="
            )
        )

        started = client.post(
            export_path,
            data={"csrf_token": self._csrf_token(credential)},
        )

        self.assertEqual(started.status_code, 303)
        state = parse_qs(urlsplit(started.headers["Location"]).query)["state"][0]
        completed = client.get(
            "/integrations/google/callback",
            query_string={"state": state, "code": "authorization-code"},
        )
        self.assertEqual(completed.status_code, 303)
        self.assertEqual(
            completed.headers["Location"],
            "https://docs.google.com/spreadsheets/d/sheet_id-123456789/edit",
        )
        oauth.exchange_code.assert_called_once_with("authorization-code")
        oauth.import_workbook.assert_called_once()
        exported = oauth.import_workbook.call_args.kwargs
        self.assertEqual(exported["payload"], payload)
        self.assertRegex(
            exported["workbook_name"],
            r"^Team Stats \d{2}-\d{2}-\d{2}\.xlsx$",
        )
        with self.Session() as session:
            audit = session.scalar(
                select(SecurityAuditEvent).where(
                    SecurityAuditEvent.reason == "google_sheets_exported"
                )
            )
        self.assertEqual(audit.target_id, artifact_id)
        self.assertEqual(audit.outcome, "succeeded")
        self.assertNotIn("authorization-code", str(audit.__dict__))
        self.assertNotIn("access-token", str(audit.__dict__))

    def test_google_sheets_state_cannot_cross_accounts_or_organizations(self):
        payload = b"PK-private-workbook"
        first_org, first_accounts = self._organization(
            "First Sheets University", ("first@example.edu", "member")
        )
        second_org, second_accounts = self._organization(
            "Second Sheets University", ("second@example.edu", "member")
        )
        report_id = self._report(first_org, "Private Pelicans", 512)
        snapshot_id = self._snapshot(
            first_org, report_id, "ready", self.now - timedelta(hours=1)
        )
        artifact_id = self._artifact(
            first_org, snapshot_id, "immutable/private.xlsx", payload
        )
        oauth = Mock()
        oauth.configured = True
        oauth.authorization_url.side_effect = (
            lambda *, state: f"https://accounts.google.test/oauth?state={state}"
        )
        self.app_module.app.extensions["roster_scout_google_sheets_oauth"] = oauth
        first_client, first_credential = self._client_with_credential(
            first_accounts[0], first_org
        )
        started = first_client.post(
            f"/reports/{report_id}/snapshots/{snapshot_id}"
            f"/artifacts/{artifact_id}/google-sheets",
            data={"csrf_token": self._csrf_token(first_credential)},
        )
        state = parse_qs(urlsplit(started.headers["Location"]).query)["state"][0]
        second_client, _ = self._client_with_credential(
            second_accounts[0], second_org
        )

        rejected = second_client.get(
            "/integrations/google/callback",
            query_string={"state": state, "code": "foreign-code"},
        )

        self.assertEqual(rejected.status_code, 303)
        self.assertEqual(rejected.headers["Location"], "/reports?notice=google_failed")
        oauth.exchange_code.assert_not_called()
        oauth.import_workbook.assert_not_called()

    def test_foreign_or_missing_artifact_is_identical_and_never_read_or_rebuilt(self):
        first_id, first_accounts = self._organization(
            "First Artifact Org", ("first@example.edu", "admin")
        )
        second_id, _ = self._organization(
            "Second Artifact Org", ("second@example.edu", "admin")
        )
        first_report_id = self._report(first_id, "First Falcons", 509)
        first_snapshot_id = self._snapshot(first_id, first_report_id, "ready", self.now)
        second_report_id = self._report(second_id, "Hidden Hawks", 510)
        second_snapshot_id = self._snapshot(second_id, second_report_id, "ready", self.now)
        foreign_artifact_id = self._artifact(
            second_id,
            second_snapshot_id,
            "private/foreign.xlsx",
            b"PK\x03\x04private",
        )
        reader = Mock(side_effect=AssertionError("foreign bytes must not be read"))
        self.app_module.app.extensions["roster_scout_artifact_reader"] = reader
        client, credential = self._client_with_credential(
            first_accounts[0], first_id
        )
        csrf = self._csrf_token(credential)

        foreign = client.post(
            f"/reports/{first_report_id}/snapshots/{first_snapshot_id}"
            f"/artifacts/{foreign_artifact_id}/download",
            data={"csrf_token": csrf},
        )
        missing = client.post(
            f"/reports/{first_report_id}/snapshots/{first_snapshot_id}"
            f"/artifacts/{uuid.uuid4()}/download",
            data={"csrf_token": csrf},
        )
        self.assertEqual(foreign.status_code, 404)
        def without_csrf_nonce(body):
            body = re.sub(
                rb'(<meta name="csrf-token" content=")[^"]+("[^>]*>)',
                rb'\1<csrf>\2',
                body,
            )
            return re.sub(
                rb'(name="csrf_token" value=")[^"]+("[^>]*>)',
                rb'\1<csrf>\2',
                body,
            )

        self.assertEqual(
            without_csrf_nonce(foreign.get_data()),
            without_csrf_nonce(missing.get_data()),
        )
        reader.assert_not_called()
        detail = client.get(f"/reports/{first_report_id}")
        self.assertIn(b"Download unavailable", detail.data)
        self.assertIn(b"will not be replaced with newer data", detail.data)

    def test_storage_failure_is_sanitized_and_issues_no_receipt_or_audit(self):
        from roster_scout.models import ProductUsageEvent, SecurityAuditEvent

        organization_id, accounts = self._organization(
            "Storage Failure Org", ("coach@example.edu", "member")
        )
        report_id = self._report(organization_id, "Storage Stags", 511)
        snapshot_id = self._snapshot(
            organization_id, report_id, "ready", self.now
        )
        artifact_id = self._artifact(
            organization_id,
            snapshot_id,
            "immutable/unavailable.xlsx",
            b"PK\x03\x04expected",
        )
        self.app_module.app.extensions["roster_scout_artifact_reader"] = Mock(
            side_effect=RuntimeError("secret storage token and exception")
        )
        client, credential = self._client_with_credential(
            accounts[0], organization_id
        )

        response = client.post(
            f"/reports/{report_id}/snapshots/{snapshot_id}"
            f"/artifacts/{artifact_id}/download",
            data={"csrf_token": self._csrf_token(credential)},
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()["error"], "artifact_unavailable")
        self.assertNotIn("secret", response.get_data(as_text=True))
        self.assertNotIn(
            "X-Roster-Scout-Download-Event-ID", response.headers
        )
        self.assertNotIn(
            "X-Roster-Scout-Download-Ack-Proof", response.headers
        )
        with self.Session() as session:
            self.assertEqual(
                session.scalar(select(func.count(ProductUsageEvent.id))), 0
            )
            self.assertEqual(
                session.scalar(select(func.count(SecurityAuditEvent.id))), 0
            )


class RefreshLineageTests(unittest.TestCase):
    """Explicit refreshes preserve published lineage and converge atomically."""

    setUpClass = SharedLibraryTests.__dict__["setUpClass"]
    tearDownClass = SharedLibraryTests.__dict__["tearDownClass"]
    setUp = SharedLibraryTests.setUp
    tearDown = SharedLibraryTests.tearDown
    _organization = SharedLibraryTests._organization
    _client = SharedLibraryTests._client
    _report = SharedLibraryTests._report
    _snapshot = SharedLibraryTests._snapshot

    def _csrf_token(self, credential):
        from itsdangerous import URLSafeTimedSerializer

        return URLSafeTimedSerializer(
            self.app_module.app.secret_key,
            salt="wtf-csrf-token",
        ).dumps(credential.csrf_secret)

    def _client_with_credential(self, account_id, organization_id):
        from roster_scout.sessions import SESSION_COOKIE_NAME, create_account_session

        with self.Session.begin() as session:
            credential = create_account_session(
                session,
                account_id=account_id,
                active_organization_id=organization_id,
                sealed_provider_state="sealed-refresh-session",
                now=self.now,
            )
        client = self.app_module.app.test_client()
        client.set_cookie(SESSION_COOKIE_NAME, credential.cookie_value)
        return client, credential

    def _refresh(self, organization_id, account_id, report_id, action_id):
        from roster_scout.reports import accept_authorized_report_refresh

        with self.Session.begin() as session:
            return accept_authorized_report_refresh(
                session,
                actor_account_id=account_id,
                organization_id=organization_id,
                report_id=report_id,
                idempotency_key=action_id,
            )

    def test_refresh_confirmation_names_latest_snapshot_and_requires_post(self):
        organization_id, accounts = self._organization(
            "Refresh University", ("coach@example.edu", "admin")
        )
        report_id = self._report(organization_id, "Refresh Ravens", 601)
        self._snapshot(organization_id, report_id, "ready", self.now)
        client, credential = self._client_with_credential(
            accounts[0], organization_id
        )

        confirmation = client.get(f"/reports/{report_id}/refresh")
        self.assertEqual(confirmation.status_code, 200)
        for value in (
            b"Refresh Refresh Ravens?",
            b"collect current NCAA data and prepare a new workbook",
            b"will remain available",
            b"Start refresh",
            b"Keep current report",
        ):
            self.assertIn(value, confirmation.data)

        rejected = client.post(
            f"/reports/{report_id}/refresh",
            data={"idempotency_key": str(uuid.uuid4())},
        )
        self.assertEqual(rejected.status_code, 400)

        accepted = client.post(
            f"/reports/{report_id}/refresh",
            data={
                "csrf_token": self._csrf_token(credential),
                "idempotency_key": str(uuid.uuid4()),
            },
        )
        self.assertEqual(accepted.status_code, 303)
        self.assertEqual(accepted.headers["Location"], f"/reports/{report_id}?notice=refresh_started")

    def test_concurrent_refresh_clicks_create_one_live_job_usage_and_audit(self):
        from roster_scout.models import (
            ProductUsageEvent,
            ReportJob,
            ReportSnapshot,
            SecurityAuditEvent,
        )

        organization_id, accounts = self._organization(
            "Converged Refresh University", ("coach@example.edu", "member")
        )
        report_id = self._report(organization_id, "Converged Cougars", 602)
        snapshot_id = self._snapshot(
            organization_id, report_id, "ready", self.now
        )
        start = Barrier(2)

        def refresh_once(_index):
            start.wait(timeout=10)
            return self._refresh(
                organization_id,
                accounts[0],
                report_id,
                str(uuid.uuid4()),
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            accepted = list(pool.map(refresh_once, range(2)))

        self.assertEqual(accepted[0].job_id, accepted[1].job_id)
        with self.Session() as session:
            jobs = tuple(session.scalars(select(ReportJob)))
            usage_count = session.scalar(select(func.count(ProductUsageEvent.id)))
            audits = tuple(session.scalars(select(SecurityAuditEvent)))
            preserved = session.get(ReportSnapshot, snapshot_id)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].status, "queued")
        self.assertEqual(usage_count, 1)
        self.assertEqual(len(audits), 1)
        self.assertEqual(audits[0].action, "report.refresh")
        self.assertEqual(preserved.status, "ready")
        self.assertEqual(preserved.published_at, self.now)

    def test_new_build_links_predecessor_without_reusing_or_mutating_old_evidence(self):
        from roster_scout.jobs import (
            PARSER_VERSION,
            SCHEMA_VERSION,
            _load_cached_sections,
            _new_or_existing_snapshot,
        )
        from roster_scout.models import (
            ReportArtifact,
            ReportJob,
            ReportSection,
            ReportSnapshot,
        )

        organization_id, accounts = self._organization(
            "Lineage University", ("coach@example.edu", "member")
        )
        report_id = self._report(organization_id, "Lineage Lions", 604)
        with self.Session.begin() as session:
            predecessor = ReportSnapshot(
                organization_id=organization_id,
                report_id=report_id,
                status="draft",
                schema_version=SCHEMA_VERSION,
                source_collected_at=self.now - timedelta(minutes=5),
                created_by_account_id=accounts[0],
            )
            session.add(predecessor)
            session.flush()
            snapshot_id = predecessor.id
            old_job = ReportJob(
                organization_id=organization_id,
                report_id=report_id,
                idempotency_key=str(uuid.uuid4()),
                status="ready",
                result_snapshot_id=snapshot_id,
                requested_by_account_id=accounts[0],
            )
            session.add(old_job)
            session.flush()
            session.add(ReportSection(
                organization_id=organization_id,
                snapshot_id=snapshot_id,
                job_id=old_job.id,
                section="batting",
                critical=True,
                status="complete",
                row_count=1,
                verified_empty=False,
                collected_at=self.now,
                source_url="https://stats.ncaa.org/old-build",
                schema_version=SCHEMA_VERSION,
                parser_version=PARSER_VERSION,
            ))
            session.add(ReportArtifact(
                organization_id=organization_id,
                snapshot_id=snapshot_id,
                artifact_type="xlsx",
                status="ready",
                schema_version=SCHEMA_VERSION,
                sha256="a" * 64,
                object_key="immutable/old.xlsx",
            ))
            session.flush()
            predecessor.status = "ready"
            predecessor.published_at = self.now

        accepted = self._refresh(
            organization_id,
            accounts[0],
            report_id,
            str(uuid.uuid4()),
        )
        with self.Session.begin() as session:
            new_job = session.get(ReportJob, accepted.job_id)
            successor = _new_or_existing_snapshot(session, new_job)
            cached = _load_cached_sections(session, new_job, successor)
            successor_id = successor.id

        with self.Session() as session:
            predecessor = session.get(ReportSnapshot, snapshot_id)
            successor = session.get(ReportSnapshot, successor_id)
            artifact = session.scalar(select(ReportArtifact).where(
                ReportArtifact.snapshot_id == snapshot_id
            ))
            old_section = session.scalar(select(ReportSection).where(
                ReportSection.snapshot_id == snapshot_id
            ))
        self.assertEqual(successor.predecessor_snapshot_id, snapshot_id)
        self.assertEqual(successor.status, "draft")
        self.assertIsNone(successor.source_collected_at)
        self.assertEqual(cached, {})
        self.assertEqual(predecessor.status, "ready")
        self.assertEqual(predecessor.published_at, self.now)
        self.assertEqual(artifact.sha256, "a" * 64)
        self.assertEqual(artifact.object_key, "immutable/old.xlsx")
        self.assertEqual(old_section.source_url, "https://stats.ncaa.org/old-build")
        self.assertEqual(old_section.job_id, old_job.id)

    def test_foreign_refresh_matches_absent_without_writes(self):
        first_id, first_accounts = self._organization(
            "First Refresh Org", ("first@example.edu", "admin")
        )
        second_id, _ = self._organization(
            "Second Refresh Org", ("second@example.edu", "admin")
        )
        foreign_report_id = self._report(second_id, "Private Pumas", 603)
        client, credential = self._client_with_credential(first_accounts[0], first_id)
        form = {
            "csrf_token": self._csrf_token(credential),
            "idempotency_key": str(uuid.uuid4()),
        }

        foreign_get = client.get(f"/reports/{foreign_report_id}/refresh")
        absent_get = client.get(f"/reports/{uuid.uuid4()}/refresh")
        foreign_post = client.post(f"/reports/{foreign_report_id}/refresh", data=form)
        form["idempotency_key"] = str(uuid.uuid4())
        absent_post = client.post(f"/reports/{uuid.uuid4()}/refresh", data=form)

        self.assertEqual(foreign_get.status_code, 404)
        self.assertEqual(
            without_csrf_tokens(foreign_get.get_data()),
            without_csrf_tokens(absent_get.get_data()),
        )
        self.assertEqual(foreign_post.status_code, 404)
        self.assertEqual(
            without_csrf_tokens(foreign_post.get_data()),
            without_csrf_tokens(absent_post.get_data()),
        )
        from roster_scout.models import ProductUsageEvent, ReportJob
        with self.Session() as session:
            self.assertEqual(session.scalar(select(func.count(ReportJob.id))), 0)
            self.assertEqual(
                session.scalar(select(func.count(ProductUsageEvent.id))), 0
            )


class ReportAcceptanceIdempotencyTests(unittest.TestCase):
    """Authorized acceptance is actor-bound, atomic, and duplicate safe."""

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
        self.now = datetime.now(UTC)
        import app as app_module

        self.app_module = app_module
        self.original_config = dict(app_module.app.config)
        self.original_auth_factory = app_module.app.extensions.get(
            "roster_scout_auth_session_factory"
        )
        self.original_report_factory = app_module.app.extensions.get(
            "roster_scout_report_session_factory"
        )
        self.original_dispatch = app_module.app.extensions.get(
            "roster_scout_report_dispatch"
        )
        app_module.app.config.update(
            TESTING=True,
            ROSTER_SCOUT_TEST_AUTH_ENFORCEMENT=True,
            WTF_CSRF_ENABLED=True,
            WTF_CSRF_SSL_STRICT=False,
        )
        app_module.app.extensions["roster_scout_auth_session_factory"] = self.Session
        app_module.app.extensions["roster_scout_report_session_factory"] = self.Session

    def tearDown(self):
        self.app_module.app.config.clear()
        self.app_module.app.config.update(self.original_config)
        if self.original_auth_factory is None:
            self.app_module.app.extensions.pop(
                "roster_scout_auth_session_factory", None
            )
        else:
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
        if self.original_dispatch is None:
            self.app_module.app.extensions.pop("roster_scout_report_dispatch", None)
        else:
            self.app_module.app.extensions["roster_scout_report_dispatch"] = (
                self.original_dispatch
            )

    def _member(self, *, organization_name="Acceptance University"):
        from roster_scout.models import (
            Account,
            Organization,
            OrganizationMembership,
        )

        with self.Session.begin() as session:
            organization = Organization(name=organization_name)
            account = Account(
                normalized_email=f"{uuid.uuid4()}@example.edu",
                email_verified_at=self.now,
                status="active",
            )
            session.add_all((organization, account))
            session.flush()
            membership = OrganizationMembership(
                organization_id=organization.id,
                account_id=account.id,
                role="member",
                status="active",
            )
            session.add(membership)
            session.flush()
            return organization.id, account.id, membership.id

    @staticmethod
    def _request(**overrides):
        from roster_scout.reports import CanonicalReportRequest

        values = {
            "source_team_id": "12345",
            "team_name": "State University",
            "source_season_id": "2026",
            "season_year": 2026,
            "source_season_team_id": "67890",
            "division": "d1",
            "batting_stat_category_id": "111",
            "pitching_stat_category_id": "222",
        }
        values.update(overrides)
        return CanonicalReportRequest(**values)

    def _accept(self, organization_id, account_id, request, action_id):
        from roster_scout.reports import accept_authorized_report_request

        with self.Session.begin() as session:
            return accept_authorized_report_request(
                session,
                actor_account_id=account_id,
                organization_id=organization_id,
                request=request,
                idempotency_key=action_id,
            )

    def _route_client(self, organization_id, account_id):
        from roster_scout.sessions import SESSION_COOKIE_NAME, create_account_session

        with self.Session.begin() as session:
            credential = create_account_session(
                session,
                account_id=account_id,
                active_organization_id=organization_id,
                sealed_provider_state="sealed-report-form-session",
                now=self.now,
            )
        client = self.app_module.app.test_client()
        client.set_cookie(SESSION_COOKIE_NAME, credential.cookie_value)
        return client, credential

    def _csrf_token(self, credential):
        from itsdangerous import URLSafeTimedSerializer

        return URLSafeTimedSerializer(
            self.app_module.app.secret_key,
            salt="wtf-csrf-token",
        ).dumps(credential.csrf_secret)

    @staticmethod
    def _catalog():
        return [{
            "name": "State University",
            "display_name": "State University",
            "team_id": "12345",
            "season_team_id": "67890",
            "division": "d1",
            "year": 2026,
        }]

    def _counts(self):
        from roster_scout.models import (
            ProductUsageEvent,
            Report,
            ReportJob,
            ReportJobDispatch,
            ReportSnapshot,
            SecurityAuditEvent,
        )

        with self.Session() as session:
            return {
                "reports": session.scalar(select(func.count(Report.id))),
                "jobs": session.scalar(select(func.count(ReportJob.id))),
                "outbox": session.scalar(select(func.count(ReportJobDispatch.id))),
                "snapshots": session.scalar(select(func.count(ReportSnapshot.id))),
                "usage": session.scalar(select(func.count(ProductUsageEvent.id))),
                "audit": session.scalar(select(func.count(SecurityAuditEvent.id))),
            }

    def test_valid_request_commits_one_actor_bound_acceptance_graph(self):
        from roster_scout.models import ProductUsageEvent, ReportJob, SecurityAuditEvent
        from roster_scout.usage import query_usage_metrics

        organization_id, account_id, _membership_id = self._member()
        accepted = self._accept(
            organization_id,
            account_id,
            self._request(),
            str(uuid.uuid4()),
        )

        self.assertEqual(
            self._counts(),
            {
                "reports": 1,
                "jobs": 1,
                "outbox": 1,
                "snapshots": 0,
                "usage": 1,
                "audit": 1,
            },
        )
        with self.Session() as session:
            job = session.get(ReportJob, accepted.job_id)
            usage = session.scalar(select(ProductUsageEvent))
            audit = session.scalar(select(SecurityAuditEvent))
            metrics = query_usage_metrics(
                session,
                start=self.now - timedelta(minutes=1),
                end=self.now + timedelta(minutes=1),
            )
        self.assertEqual(job.requested_by_account_id, account_id)
        self.assertEqual(job.build_profile, "quick")
        self.assertEqual(usage.actor_account_id, account_id)
        self.assertEqual(usage.organization_id, organization_id)
        self.assertEqual(usage.event_key, str(job.id))
        self.assertEqual(audit.actor_account_id, account_id)
        self.assertEqual(audit.organization_id, organization_id)
        self.assertEqual(audit.target_id, job.id)
        self.assertEqual(audit.action, "report.create")
        self.assertEqual(metrics.unique_authenticated_users, 1)

    def test_validation_membership_and_rollback_failures_record_no_usage(self):
        from roster_scout.models import OrganizationMembership
        from roster_scout.reports import (
            CanonicalReportRequest,
            ReportAcceptanceFailure,
            accept_authorized_report_request,
        )

        organization_id, account_id, membership_id = self._member()
        with self.assertRaises(ValueError):
            CanonicalReportRequest(
                source_team_id="not-an-id",
                team_name="State University",
                source_season_id="2026",
                season_year=2026,
                source_season_team_id="67890",
                division="d1",
            )
        self.assertEqual(self._counts()["usage"], 0)

        with self.Session.begin() as session:
            membership = session.get(OrganizationMembership, membership_id)
            membership.status = "inactive"
            membership.deactivated_at = self.now
        with self.assertRaises(ReportAcceptanceFailure) as rejected:
            self._accept(
                organization_id,
                account_id,
                self._request(),
                str(uuid.uuid4()),
            )
        self.assertEqual(rejected.exception.category, "membership_required")
        self.assertEqual(self._counts()["usage"], 0)

        with self.Session.begin() as session:
            membership = session.get(OrganizationMembership, membership_id)
            membership.status = "active"
            membership.deactivated_at = None
        with self.assertRaises(RuntimeError):
            with self.Session.begin() as session:
                with patch(
                    "roster_scout.reports.append_security_audit_event",
                    side_effect=RuntimeError("forced rollback"),
                ):
                    accept_authorized_report_request(
                        session,
                        actor_account_id=account_id,
                        organization_id=organization_id,
                        request=self._request(),
                        idempotency_key=str(uuid.uuid4()),
                    )
        self.assertEqual(
            self._counts(),
            {
                "reports": 0,
                "jobs": 0,
                "outbox": 0,
                "snapshots": 0,
                "usage": 0,
                "audit": 0,
            },
        )

    def test_same_action_and_concurrent_double_clicks_converge(self):
        organization_id, account_id, _membership_id = self._member()
        request = self._request()
        action_id = str(uuid.uuid4())
        start = Barrier(2)

        def accept_once(_index):
            start.wait(timeout=10)
            return self._accept(
                organization_id,
                account_id,
                request,
                action_id,
            ).job_id

        with ThreadPoolExecutor(max_workers=2) as pool:
            job_ids = list(pool.map(accept_once, range(2)))
        self.assertEqual(job_ids[0], job_ids[1])
        self.assertEqual(self._counts()["reports"], 1)
        self.assertEqual(self._counts()["jobs"], 1)
        self.assertEqual(self._counts()["outbox"], 1)
        self.assertEqual(self._counts()["usage"], 1)
        self.assertEqual(self._counts()["audit"], 1)

    def test_active_job_converges_across_different_browser_action_ids(self):
        organization_id, account_id, _membership_id = self._member()
        request = self._request()
        first = self._accept(
            organization_id,
            account_id,
            request,
            str(uuid.uuid4()),
        )
        second = self._accept(
            organization_id,
            account_id,
            request,
            str(uuid.uuid4()),
        )

        self.assertEqual(first.report_id, second.report_id)
        self.assertEqual(first.job_id, second.job_id)
        self.assertEqual(self._counts()["jobs"], 1)
        self.assertEqual(self._counts()["outbox"], 1)
        self.assertEqual(self._counts()["usage"], 1)
        self.assertEqual(self._counts()["audit"], 1)

    def test_database_rejects_unknown_and_cross_organization_actor_pairing(self):
        from roster_scout.models import ProductUsageEvent, ReportJob, ReportSnapshot

        organization_id, account_id, _membership_id = self._member()
        other_organization_id, other_account_id, _other_membership_id = self._member(
            organization_name="Other University"
        )
        accepted = self._accept(
            organization_id,
            account_id,
            self._request(),
            str(uuid.uuid4()),
        )

        with self.assertRaises(DBAPIError):
            with self.Session.begin() as session:
                session.add(ProductUsageEvent(
                    event_type="authenticated_use",
                    event_key=str(uuid.uuid4()),
                    actor_account_id=uuid.uuid4(),
                    organization_id=organization_id,
                ))
                session.flush()
        with self.assertRaises(DBAPIError):
            with self.Session.begin() as session:
                session.add(ProductUsageEvent(
                    event_type="authenticated_use",
                    event_key=str(uuid.uuid4()),
                    actor_account_id=other_account_id,
                    organization_id=organization_id,
                ))
                session.flush()
        with self.assertRaises(DBAPIError):
            with self.Session.begin() as session:
                session.execute(
                    text(
                        "UPDATE report_jobs SET requested_by_account_id = :actor "
                        "WHERE id = :job"
                    ),
                    {"actor": other_account_id, "job": accepted.job_id},
                )
        with self.assertRaises(DBAPIError):
            with self.Session.begin() as session:
                session.add(ReportSnapshot(
                    organization_id=organization_id,
                    report_id=accepted.report_id,
                    status="draft",
                    schema_version="schema-test",
                    created_by_account_id=other_account_id,
                ))
                session.flush()
        with self.Session() as session:
            job = session.get(ReportJob, accepted.job_id)
        self.assertEqual(job.organization_id, organization_id)
        self.assertEqual(job.requested_by_account_id, account_id)
        self.assertNotEqual(organization_id, other_organization_id)

    def test_create_report_get_renders_exact_accessible_selector_contract(self):
        organization_id, account_id, _membership_id = self._member()
        client, _credential = self._route_client(organization_id, account_id)

        response = client.get("/reports/new")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Find an opponent.", body)
        self.assertIn('role="combobox"', body)
        self.assertIn('role="listbox"', body)
        self.assertIn('name="season_team_id"', body)
        self.assertIn('name="idempotency_key"', body)
        self.assertIn("Choose a division, then select a school", body)
        self.assertIn("Dashboard</a> / New report", body)
        self.assertIn("Create report", body)
        self.assertIn("report_form.js", body)
        self.assertIn("report_status.js", body)
        self.assertIn('id="report-loading"', body)
        self.assertIn('id="report-loading-meter"', body)
        self.assertIn('aria-label="Estimated report progress"', body)
        self.assertIn("Keep this page open. We’ll take you to the finished report automatically.", body)
        self.assertIn(
            '<button id="create-report-submit" class="report-submit" type="submit">',
            body,
        )
        self.assertIn('class="report-submit__download-arrow"', body)
        self.assertIn('class="report-submit__download-tray"', body)
        self.assertIn("hover .report-submit__download-arrow", body)
        self.assertIn("hover .report-submit__download-tray", body)
        self.assertNotIn(
            '<button id="create-report-submit" class="report-submit" type="submit" disabled>',
            body,
        )
        self.assertNotIn('id="exact-selection"', body)

        from pathlib import Path

        script = Path("static/report_form.js")
        source = script.read_text(encoding="utf-8")
        self.assertIn("ArrowDown", source)
        self.assertIn("Home", source)
        self.assertIn("End", source)
        self.assertIn("Escape", source)
        self.assertIn("textContent", source)
        self.assertIn('headers: { Accept: "application/json" }', source)
        self.assertIn("createReportStatusPoller", source)
        self.assertIn("createProgressController", source)
        self.assertIn("loadingProgressController?.sync(view.state)", source)
        self.assertIn("terminalReached", source)
        self.assertIn("const LOADING_STAGE_MOTION_MS = 820;", source)
        self.assertIn("transitionLoadingHeadline(loadingHeadlines[bounded], immediate);", source)
        self.assertIn('window.matchMedia("(prefers-reduced-motion: reduce)").matches', source)
        clear_selection = source.split("function clearSelection()", 1)[1].split(
            "function initials", 1
        )[0]
        self.assertNotIn("submitButton.disabled", clear_selection)
        self.assertEqual(source.count("submitButton.disabled = true;"), 1)
        self.assertNotIn("innerHTML", source)
        self.assertNotIn("exactSelection", source)

    def test_typed_only_stale_and_tampered_selector_values_write_nothing(self):
        organization_id, account_id, _membership_id = self._member()
        client, credential = self._route_client(organization_id, account_id)
        base = {
            "csrf_token": self._csrf_token(credential),
            "division": "d1",
            "team_name": "State University",
            "idempotency_key": str(uuid.uuid4()),
            "organization_id": str(uuid.uuid4()),
            "account_id": str(uuid.uuid4()),
            "role": "admin",
        }

        with patch(
            "roster_scout.report_routes.list_ncaa_teams",
            return_value=self._catalog(),
        ):
            typed_only = client.post(
                "/reports/new",
                data={**base, "season_team_id": ""},
            )
            stale = client.post(
                "/reports/new",
                data={**base, "season_team_id": "99999"},
            )
            tampered = client.post(
                "/reports/new",
                data={**base, "season_team_id": "67890", "team_name": "Other"},
            )

        for response in (typed_only, stale, tampered):
            self.assertEqual(response.status_code, 400)
            self.assertIn(b"Choose an NCAA team from the list.", response.data)
            self.assertIn(b'id="form-errors"', response.data)
            self.assertIn(b'autofocus', response.data)
        self.assertEqual(self._counts()["reports"], 0)
        self.assertEqual(self._counts()["jobs"], 0)
        self.assertEqual(self._counts()["usage"], 0)

    def test_no_javascript_team_list_accepts_one_exact_catalog_identity(self):
        organization_id, account_id, _membership_id = self._member()
        client, credential = self._route_client(organization_id, account_id)

        with patch(
            "roster_scout.report_routes.list_ncaa_teams",
            return_value=self._catalog(),
        ):
            fallback = client.get("/reports/new?nojs=1")
            accepted = client.post(
                "/reports/new?nojs=1",
                data={
                    "csrf_token": self._csrf_token(credential),
                    "division": "d1",
                    "team_name": "",
                    "season_team_id": "",
                    "fallback_team": "d1:67890",
                    "idempotency_key": str(uuid.uuid4()),
                },
            )

        self.assertEqual(fallback.status_code, 200)
        self.assertIn(b'name="fallback_team"', fallback.data)
        self.assertIn(b"D1 \xc2\xb7 State University", fallback.data)
        self.assertEqual(accepted.status_code, 303)
        self.assertEqual(self._counts()["reports"], 1)
        self.assertEqual(self._counts()["jobs"], 1)

    def test_valid_and_repeated_posts_use_server_context_and_converge(self):
        from roster_scout.models import ReportJob

        organization_id, account_id, _membership_id = self._member()
        client, credential = self._route_client(organization_id, account_id)
        action_id = str(uuid.uuid4())
        form = {
            "csrf_token": self._csrf_token(credential),
            "division": "d1",
            "team_name": "State University",
            "season_team_id": "67890",
            "idempotency_key": action_id,
            "organization_id": str(uuid.uuid4()),
            "account_id": str(uuid.uuid4()),
            "role": "admin",
        }
        dispatch = Mock()
        self.app_module.app.extensions["roster_scout_report_dispatch"] = dispatch

        with patch(
            "roster_scout.report_routes.list_ncaa_teams",
            return_value=self._catalog(),
        ):
            first = client.post("/reports/new", data=form)
            second = client.post("/reports/new", data=form)

        self.assertEqual(first.status_code, 303)
        self.assertRegex(first.headers["Location"], r"^/reports/[0-9a-f-]{36}$")
        self.assertEqual(second.status_code, 303)
        self.assertEqual(
            second.headers["Location"],
            first.headers["Location"] + "?notice=active",
        )
        self.assertEqual(self._counts()["reports"], 1)
        self.assertEqual(self._counts()["jobs"], 1)
        self.assertEqual(self._counts()["usage"], 1)
        with self.Session() as session:
            job = session.scalar(select(ReportJob))
        self.assertEqual(job.organization_id, organization_id)
        self.assertEqual(job.requested_by_account_id, account_id)
        self.assertGreaterEqual(dispatch.call_count, 1)

    def test_async_create_returns_poll_and_report_urls_without_redirecting(self):
        organization_id, account_id, _membership_id = self._member()
        client, credential = self._route_client(organization_id, account_id)
        dispatch = Mock()
        self.app_module.app.extensions["roster_scout_report_dispatch"] = dispatch

        with patch(
            "roster_scout.report_routes.list_ncaa_teams",
            return_value=self._catalog(),
        ):
            response = client.post(
                "/reports/new",
                data={
                    "csrf_token": self._csrf_token(credential),
                    "division": "d1",
                    "team_name": "State University",
                    "season_team_id": "67890",
                    "idempotency_key": str(uuid.uuid4()),
                },
                headers={"Accept": "application/json"},
            )

        self.assertEqual(response.status_code, 202)
        payload = response.get_json()
        self.assertRegex(payload["report_url"], r"^/reports/[0-9a-f-]{36}$")
        status_url = urlsplit(payload["status_url"])
        self.assertEqual(status_url.path, payload["report_url"] + "/status")
        self.assertRegex(
            parse_qs(status_url.query)["job_id"][0], r"^[0-9a-f-]{36}$"
        )
        self.assertEqual(parse_qs(status_url.query)["prefer_current"], ["1"])
        self.assertEqual(self._counts()["reports"], 1)
        self.assertEqual(self._counts()["jobs"], 1)

    def test_create_report_post_requires_csrf(self):
        organization_id, account_id, _membership_id = self._member()
        client, _credential = self._route_client(organization_id, account_id)
        response = client.post(
            "/reports/new",
            data={
                "division": "d1",
                "team_name": "State University",
                "season_team_id": "67890",
                "idempotency_key": str(uuid.uuid4()),
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self._counts()["jobs"], 0)


if __name__ == "__main__":
    unittest.main()
