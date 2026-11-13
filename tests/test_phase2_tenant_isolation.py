"""Complete Phase 2 tenant-isolation and security-audit contracts."""

from __future__ import annotations

import logging
import uuid
import unittest
from datetime import UTC, datetime
from hashlib import sha256
from unittest.mock import patch

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from tests.db_support import (
    clean_database,
    require_test_database_url,
    upgrade_database,
    without_csrf_tokens,
)


class _TenantIsolationCase(unittest.TestCase):
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
        from roster_scout.identity import FakeIdentityProvider

        self.app_module = app_module
        self.original_config = dict(app_module.app.config)
        self.original_provider = app_module.app.extensions.get(
            "roster_scout_identity_provider"
        )
        self.original_auth_factory = app_module.app.extensions.get(
            "roster_scout_auth_session_factory"
        )
        self.original_report_factory = app_module.app.extensions.get(
            "roster_scout_report_session_factory"
        )
        self.original_reader = app_module.app.extensions.get(
            "roster_scout_artifact_reader"
        )
        app_module.app.config.update(
            TESTING=True,
            ROSTER_SCOUT_TEST_AUTH_ENFORCEMENT=True,
            WTF_CSRF_ENABLED=True,
            WTF_CSRF_SSL_STRICT=False,
        )
        app_module.app.extensions["roster_scout_identity_provider"] = (
            FakeIdentityProvider()
        )
        app_module.app.extensions["roster_scout_auth_session_factory"] = self.Session
        app_module.app.extensions["roster_scout_report_session_factory"] = self.Session
        self.now = datetime.now(UTC).replace(microsecond=0)

    def tearDown(self):
        self.app_module.app.config.clear()
        self.app_module.app.config.update(self.original_config)
        self.app_module.app.extensions["roster_scout_identity_provider"] = (
            self.original_provider
        )
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
        if self.original_reader is None:
            self.app_module.app.extensions.pop("roster_scout_artifact_reader", None)
        else:
            self.app_module.app.extensions["roster_scout_artifact_reader"] = (
                self.original_reader
            )

    def _tenant(self, label, *, role="admin"):
        from roster_scout.models import (
            Account,
            Organization,
            OrganizationMembership,
            Report,
            ReportArtifact,
            ReportJob,
            ReportSnapshot,
            Season,
            Team,
            TeamSeason,
        )
        from roster_scout.sessions import create_account_session

        marker = str(uuid.uuid4())
        payload = f"PK tenant workbook {label}".encode()
        with self.Session.begin() as session:
            organization = Organization(name=f"{label} Baseball")
            account = Account(
                normalized_email=f"{label.lower()}-{marker}@example.edu",
                email_verified_at=self.now,
                status="active",
            )
            session.add_all((organization, account))
            session.flush()
            membership = OrganizationMembership(
                organization_id=organization.id,
                account_id=account.id,
                role=role,
                status="active",
            )
            team = Team(
                source="ncaa",
                source_team_id=f"team-{marker}",
                name=f"{label} Opponent",
            )
            season = Season(
                source="ncaa",
                source_season_id=f"season-{marker}",
                year=2026,
            )
            session.add_all((membership, team, season))
            session.flush()
            team_season = TeamSeason(
                team_id=team.id,
                season_id=season.id,
                source_season_team_id=f"season-team-{marker}",
                division="D1",
            )
            session.add(team_season)
            session.flush()
            report = Report(
                organization_id=organization.id,
                team_season_id=team_season.id,
                name=f"{label} Opponent",
            )
            session.add(report)
            session.flush()
            snapshot = ReportSnapshot(
                organization_id=organization.id,
                report_id=report.id,
                status="ready",
                schema_version="2026.1",
                source_collected_at=self.now,
                published_at=self.now,
                created_by="tenant-isolation-test",
                created_by_account_id=account.id,
            )
            failed_job = ReportJob(
                organization_id=organization.id,
                report_id=report.id,
                requested_by_account_id=account.id,
                idempotency_key=str(uuid.uuid4()),
                status="failed",
                failure_category="source_unavailable",
                finished_at=self.now,
            )
            session.add_all((snapshot, failed_job))
            session.flush()
            artifact = ReportArtifact(
                organization_id=organization.id,
                snapshot_id=snapshot.id,
                artifact_type="xlsx",
                status="ready",
                schema_version="2026.1",
                sha256=sha256(payload).hexdigest(),
                object_key=f"tenant/{marker}.xlsx",
            )
            session.add(artifact)
            session.flush()
            credential = create_account_session(
                session,
                account_id=account.id,
                active_organization_id=organization.id,
                sealed_provider_state="sealed-test-session",
                now=self.now,
            )
            return {
                "organization_id": organization.id,
                "account_id": account.id,
                "membership_id": membership.id,
                "report_id": report.id,
                "snapshot_id": snapshot.id,
                "job_id": failed_job.id,
                "artifact_id": artifact.id,
                "object_key": artifact.object_key,
                "payload": payload,
                "credential": credential,
            }

    def _client(self, tenant):
        from roster_scout.sessions import SESSION_COOKIE_NAME

        client = self.app_module.app.test_client()
        client.set_cookie(SESSION_COOKIE_NAME, tenant["credential"].cookie_value)
        return client

    def _csrf(self, tenant):
        from itsdangerous import URLSafeTimedSerializer

        return URLSafeTimedSerializer(
            self.app_module.app.secret_key,
            salt="wtf-csrf-token",
        ).dumps(tenant["credential"].csrf_secret)

    def _audit_rows(self):
        from roster_scout.models import SecurityAuditEvent

        with self.Session() as session:
            return tuple(
                session.scalars(
                    select(SecurityAuditEvent).order_by(
                        SecurityAuditEvent.occurred_at,
                        SecurityAuditEvent.id,
                    )
                )
            )

    def _assert_same_denial(self, foreign, absent):
        self.assertEqual(foreign.status_code, 404)
        self.assertEqual(absent.status_code, 404)
        self.assertEqual(
            without_csrf_tokens(foreign.get_data()),
            without_csrf_tokens(absent.get_data()),
        )
        self.assertEqual(foreign.content_type, absent.content_type)


class CrossOrganizationDenialTests(_TenantIsolationCase):
    """Foreign and absent IDs are indistinguishable on every resource surface."""

    def test_foreign_and_absent_resource_matrix_is_identical_and_audited(self):
        first = self._tenant("First")
        foreign = self._tenant("Foreign")
        client = self._client(first)
        csrf = self._csrf(first)
        absent_report = uuid.uuid4()
        absent_job = uuid.uuid4()
        absent_snapshot = uuid.uuid4()
        absent_artifact = uuid.uuid4()

        pairs = (
            (
                "report html",
                client.get(f"/reports/{foreign['report_id']}"),
                client.get(f"/reports/{absent_report}"),
            ),
            (
                "status json",
                client.get(f"/reports/{foreign['report_id']}/status"),
                client.get(f"/reports/{absent_report}/status"),
            ),
            (
                "refresh confirmation",
                client.get(f"/reports/{foreign['report_id']}/refresh"),
                client.get(f"/reports/{absent_report}/refresh"),
            ),
            (
                "refresh post",
                client.post(
                    f"/reports/{foreign['report_id']}/refresh",
                    data={
                        "csrf_token": csrf,
                        "idempotency_key": str(uuid.uuid4()),
                    },
                ),
                client.post(
                    f"/reports/{absent_report}/refresh",
                    data={
                        "csrf_token": csrf,
                        "idempotency_key": str(uuid.uuid4()),
                    },
                ),
            ),
            (
                "failed job retry",
                client.post(
                    f"/reports/{first['report_id']}/builds/"
                    f"{foreign['job_id']}/retry",
                    data={
                        "csrf_token": csrf,
                        "idempotency_key": str(uuid.uuid4()),
                    },
                ),
                client.post(
                    f"/reports/{first['report_id']}/builds/{absent_job}/retry",
                    data={
                        "csrf_token": csrf,
                        "idempotency_key": str(uuid.uuid4()),
                    },
                ),
            ),
            (
                "artifact substitution",
                client.post(
                    f"/reports/{first['report_id']}/snapshots/"
                    f"{first['snapshot_id']}/artifacts/"
                    f"{foreign['artifact_id']}/download",
                    data={"csrf_token": csrf},
                ),
                client.post(
                    f"/reports/{first['report_id']}/snapshots/"
                    f"{first['snapshot_id']}/artifacts/{absent_artifact}/download",
                    data={"csrf_token": csrf},
                ),
            ),
            (
                "snapshot substitution",
                client.post(
                    f"/reports/{first['report_id']}/snapshots/"
                    f"{foreign['snapshot_id']}/artifacts/"
                    f"{foreign['artifact_id']}/download",
                    data={"csrf_token": csrf},
                ),
                client.post(
                    f"/reports/{first['report_id']}/snapshots/"
                    f"{absent_snapshot}/artifacts/{uuid.uuid4()}/download",
                    data={"csrf_token": csrf},
                ),
            ),
        )
        for label, known_foreign, random_absent in pairs:
            with self.subTest(surface=label):
                self._assert_same_denial(known_foreign, random_absent)

        rows = self._audit_rows()
        self.assertEqual(len(rows), len(pairs) * 2)
        self.assertTrue(all(row.action == "authorization.deny" for row in rows))
        self.assertTrue(all(row.outcome == "denied" for row in rows))
        self.assertTrue(all(row.reason == "not_found" for row in rows))
        self.assertTrue(all(row.actor_account_id == first["account_id"] for row in rows))
        self.assertTrue(
            all(row.organization_id == first["organization_id"] for row in rows)
        )


class UntrustedScopeInputTests(_TenantIsolationCase):
    """Browser/provider scope claims are inert across every input channel."""

    def _claims(self, foreign):
        return {
            "organization_id": str(foreign["organization_id"]),
            "account_id": str(foreign["account_id"]),
            "role": "admin",
            "provider_organization_id": "provider-org-secret",
            "domain": "foreign.example.edu",
        }

    def _claim_headers(self, foreign):
        return {
            "X-Organization-ID": str(foreign["organization_id"]),
            "X-Account-ID": str(foreign["account_id"]),
            "X-Role": "admin",
            "X-Provider-Organization-ID": "provider-org-secret",
            "X-Email-Domain": "foreign.example.edu",
        }

    def test_protected_and_legacy_route_methods_are_explicitly_in_scope(self):
        route_methods = {
            (rule.endpoint, tuple(sorted(rule.methods - {"HEAD", "OPTIONS"})))
            for rule in self.app_module.app.url_map.iter_rules()
        }
        expected = {
            ("index", ("GET",)),
            ("teams", ("GET",)),
            ("team_logo", ("GET",)),
            ("scrape", ("POST",)),
            ("acknowledge_workbook_download", ("POST",)),
            ("auth.no_membership", ("GET",)),
            ("auth.logout", ("POST",)),
            ("organization.accept", ("POST",)),
            ("organization.select_organization", ("GET",)),
            ("organization.switch_organization", ("POST",)),
            ("organization.members", ("GET",)),
            ("organization.change_role", ("POST",)),
            ("organization.remove_member", ("POST",)),
            ("organization.invite", ("POST",)),
            ("organization.revoke", ("POST",)),
            ("reports.library", ("GET",)),
            ("reports.detail", ("GET",)),
            ("reports.detail_status", ("GET",)),
            ("reports.refresh_report", ("GET", "POST")),
            ("reports.retry_report", ("POST",)),
            ("reports.download_snapshot_artifact", ("POST",)),
            ("reports.create_report", ("GET", "POST")),
        }
        public_outside_matrix = {
            "static",
            "healthz",
            "readyz",
            "demo",
            "auth.login",
            "auth.start",
            "auth.callback",
            "auth.error",
        }
        actual = {
            item for item in route_methods if item[0] not in public_outside_matrix
        }
        self.assertEqual(actual, expected)

    def test_query_form_json_and_headers_never_replace_membership_scope(self):
        from roster_scout.models import ProductUsageEvent, Report, ReportJob

        first = self._tenant("Trusted")
        foreign = self._tenant("Untrusted")
        client = self._client(first)
        claims = self._claims(foreign)
        headers = self._claim_headers(foreign)

        library = client.get("/reports", query_string=claims, headers=headers)
        self.assertEqual(library.status_code, 200)
        self.assertIn(b"Trusted Opponent", library.data)
        self.assertNotIn(b"Untrusted Opponent", library.data)

        with patch(
            "roster_scout.report_routes.list_ncaa_teams",
            return_value=[{
                "name": "Catalog Team",
                "display_name": "Catalog Team",
                "team_id": "12345",
                "season_team_id": "67890",
                "division": "d1",
                "year": 2026,
            }],
        ), patch("roster_scout.report_routes.drain_report_dispatch_outbox"):
            created = client.post(
                "/reports/new",
                data={
                    "csrf_token": self._csrf(first),
                    "division": "d1",
                    "team_name": "Catalog Team",
                    "season_team_id": "67890",
                    "idempotency_key": str(uuid.uuid4()),
                    **claims,
                },
                headers=headers,
            )
        self.assertEqual(created.status_code, 303)
        with self.Session() as session:
            accepted_report = session.scalar(
                select(Report)
                .where(Report.name == "Catalog Team")
            )
            accepted_job = session.scalar(
                select(ReportJob).where(ReportJob.report_id == accepted_report.id)
            )
        self.assertEqual(accepted_report.organization_id, first["organization_id"])
        self.assertEqual(accepted_job.requested_by_account_id, first["account_id"])

        baseline_scrape = client.post(
            "/scrape",
            json={"team": "Example", "division": "invalid"},
        )
        claimed_scrape = client.post(
            "/scrape",
            query_string=claims,
            json={"team": "Example", "division": "invalid", **claims},
            headers=headers,
        )
        self.assertEqual(claimed_scrape.status_code, baseline_scrape.status_code)
        self.assertEqual(claimed_scrape.get_data(), baseline_scrape.get_data())

        with patch(
            "app.list_ncaa_teams",
            return_value=[{"division": "d1", "year": 2026, "name": "Safe"}],
        ):
            teams = client.get(
                "/api/teams",
                query_string={"division": "d1", **claims},
                headers=headers,
            )
        self.assertEqual(teams.status_code, 200)
        self.assertEqual(teams.get_json()["teams"][0]["name"], "Safe")

        event_id = str(uuid.uuid4())
        proof = self.app_module._issue_authenticated_download_ack_proof(
            event_id,
            account_id=first["account_id"],
            organization_id=first["organization_id"],
            report_id=first["report_id"],
            snapshot_id=first["snapshot_id"],
            artifact_id=first["artifact_id"],
        )
        acknowledged = client.post(
            "/api/downloads/ack",
            query_string=claims,
            json={
                "download_event_id": event_id,
                "acknowledgement_proof": proof,
            },
            headers={
                **headers,
                "Origin": "http://localhost",
                "Sec-Fetch-Site": "same-origin",
                "X-CSRFToken": self._csrf(first),
            },
        )
        self.assertEqual(acknowledged.status_code, 200)
        with self.Session() as session:
            usage = session.scalar(
                select(ProductUsageEvent).where(
                    ProductUsageEvent.event_key == event_id
                )
            )
        self.assertEqual(usage.actor_account_id, first["account_id"])
        self.assertEqual(usage.organization_id, first["organization_id"])


class SecurityAuditEventTests(_TenantIsolationCase):
    """Audit evidence is allowlisted, canonical, append-only, and secret-free."""

    def test_producer_rejects_free_text_and_direct_mutation(self):
        from roster_scout.audit import append_security_audit_event

        tenant = self._tenant("Audit")
        rejected = (
            {"target_type": "raw-request", "action": "authorization.deny", "outcome": "denied"},
            {"target_type": "report", "action": "user-controlled-action", "outcome": "denied"},
            {"target_type": "report", "action": "authorization.deny", "outcome": "raw-outcome"},
            {
                "target_type": "report",
                "action": "authorization.deny",
                "outcome": "denied",
                "reason": "raw exception secret",
            },
        )
        for values in rejected:
            with self.subTest(values=values), self.assertRaises(ValueError):
                with self.Session.begin() as session:
                    append_security_audit_event(
                        session,
                        actor_account_id=tenant["account_id"],
                        organization_id=tenant["organization_id"],
                        target_id=tenant["report_id"],
                        **values,
                    )

        with self.Session.begin() as session:
            event = append_security_audit_event(
                session,
                actor_account_id=tenant["account_id"],
                organization_id=tenant["organization_id"],
                target_type="report",
                target_id=tenant["report_id"],
                action="authorization.deny",
                outcome="denied",
                reason="not_found",
            )
            event_id = event.id
        for statement in (
            "UPDATE security_audit_events SET outcome = 'failed' WHERE id = :id",
            "DELETE FROM security_audit_events WHERE id = :id",
        ):
            with self.subTest(statement=statement), self.assertRaises(DBAPIError):
                with self.engine.begin() as connection:
                    connection.execute(text(statement), {"id": event_id})

    def test_denial_audit_and_logs_exclude_untrusted_request_material(self):
        tenant = self._tenant("Safe")
        foreign = self._tenant("Private")
        client = self._client(tenant)
        secrets = (
            "private@example.edu",
            "203.0.113.44",
            "secret-user-agent",
            "secret-token",
            "secret-assertion",
            "secret-cookie",
            "private report batting data",
            "raw exception secret",
        )
        logger = logging.getLogger("app")
        was_disabled = logger.disabled
        logger.disabled = False
        try:
            with self.assertLogs(logger, level="WARNING") as captured:
                response = client.get(
                    f"/reports/{foreign['report_id']}",
                    query_string={
                        "email": secrets[0],
                        "ip": secrets[1],
                        "token": secrets[3],
                        "assertion": secrets[4],
                        "report_data": secrets[6],
                        "exception": secrets[7],
                    },
                    headers={"User-Agent": secrets[2], "X-Secret": secrets[5]},
                )
                self.app_module.app.logger.warning("tenant_denial_test_complete")
        finally:
            logger.disabled = was_disabled
        self.assertEqual(response.status_code, 404)
        rows = self._audit_rows()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.actor_account_id, tenant["account_id"])
        self.assertEqual(row.organization_id, tenant["organization_id"])
        self.assertEqual(row.target_id, foreign["report_id"])
        serialized = response.get_data(as_text=True) + repr(row.__dict__) + " ".join(
            captured.output
        )
        for secret in secrets:
            self.assertNotIn(secret, serialized)

    def test_audit_allowlists_cover_required_phase_two_actions(self):
        from roster_scout.audit import (
            AUDIT_ACTIONS,
            AUDIT_OUTCOMES,
            AUDIT_REASONS,
            AUDIT_TARGET_TYPES,
        )

        self.assertTrue({
            "membership.create",
            "membership.remove",
            "membership.role_change",
            "report.create",
            "report.refresh",
            "report.retry",
            "snapshot.access",
            "artifact.download",
            "authorization.deny",
        }.issubset(AUDIT_ACTIONS))
        self.assertEqual(AUDIT_OUTCOMES, frozenset({
            "allowed", "denied", "succeeded", "failed", "pending"
        }))
        self.assertTrue({"report", "job", "snapshot", "artifact"}.issubset(
            AUDIT_TARGET_TYPES
        ))
        self.assertTrue({
            "not_found",
            "job_accepted",
            "refresh_accepted",
            "retry_accepted",
            "snapshot_opened",
            "download_prepared",
            "download_acknowledged",
        }.issubset(AUDIT_REASONS))


if __name__ == "__main__":
    unittest.main()
