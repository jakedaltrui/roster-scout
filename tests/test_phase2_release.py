"""Final deterministic Phase 2 release story and failure-state contracts."""

from __future__ import annotations

import logging
import uuid
import unittest
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from unittest.mock import Mock, patch

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from tests.db_support import clean_database, require_test_database_url, upgrade_database


class _Phase2ReleaseCase(unittest.TestCase):
    """Shared disposable-PostgreSQL and authenticated-client release fixture."""

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
        self.now = datetime.now(UTC)
        self.original_config = dict(app_module.app.config)
        self.original_extensions = {
            name: app_module.app.extensions.get(name)
            for name in (
                "roster_scout_auth_session_factory",
                "roster_scout_report_session_factory",
                "roster_scout_artifact_reader",
            )
        }
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
        for name, original in self.original_extensions.items():
            if original is None:
                self.app_module.app.extensions.pop(name, None)
            else:
                self.app_module.app.extensions[name] = original

    def _provider_account(self, email, label):
        from roster_scout.accounts import link_verified_identity
        from roster_scout.identity import (
            FakeIdentityProvider,
            VerifiedIdentity,
            exchange_authorization_code,
        )

        code = f"code-{label}"
        identity = VerifiedIdentity(
            issuer="https://identity.example.test",
            subject=f"subject-{label}",
            provider_user_id=f"provider-user-{label}",
            email=email,
            email_verified=True,
            linking_approved=False,
            sealed_session=f"sealed-{label}",
        )
        verified = exchange_authorization_code(
            FakeIdentityProvider(identities={code: identity}),
            code=code,
        )
        with self.Session.begin() as session:
            account = link_verified_identity(session, verified, now=self.now)
            return account.id

    def _bootstrap_organization(self, name, admin_email, label):
        from roster_scout.organizations import accept_invitation, bootstrap_organization

        with self.Session.begin() as session:
            organization, invitation = bootstrap_organization(
                session,
                name=name,
                admin_email=admin_email,
                now=self.now,
            )
            organization_id = organization.id
            invitation_id = invitation.id
        account_id = self._provider_account(admin_email, label)
        with self.Session.begin() as session:
            membership = accept_invitation(
                session,
                invitation_id=invitation_id,
                account_id=account_id,
                now=self.now,
            )
            return organization_id, account_id, membership.id

    def _invite_member(self, organization_id, admin_id, email, label):
        from roster_scout.organizations import accept_invitation, create_invitation

        with self.Session.begin() as session:
            invitation = create_invitation(
                session,
                organization_id=organization_id,
                actor_account_id=admin_id,
                email=email,
                role="member",
                now=self.now,
            )
            invitation_id = invitation.id
        account_id = self._provider_account(email, label)
        with self.Session.begin() as session:
            membership = accept_invitation(
                session,
                invitation_id=invitation_id,
                account_id=account_id,
                now=self.now,
            )
            return account_id, membership.id

    def _client(self, account_id, organization_id):
        from roster_scout.sessions import SESSION_COOKIE_NAME, create_account_session

        with self.Session.begin() as session:
            credential = create_account_session(
                session,
                account_id=account_id,
                active_organization_id=organization_id,
                sealed_provider_state="sealed-release-session",
                now=self.now,
            )
        client = self.app_module.app.test_client()
        client.set_cookie(SESSION_COOKIE_NAME, credential.cookie_value)
        return client, credential

    def _csrf(self, credential):
        from itsdangerous import URLSafeTimedSerializer

        return URLSafeTimedSerializer(
            self.app_module.app.secret_key,
            salt="wtf-csrf-token",
        ).dumps(credential.csrf_secret)

    @staticmethod
    def _report_request():
        from roster_scout.reports import CanonicalReportRequest

        return CanonicalReportRequest(
            source_team_id="12345",
            team_name="Release State University",
            source_season_id="2026",
            season_year=2026,
            source_season_team_id="67890",
            division="d1",
        )

    def _accept_report(self, organization_id, account_id):
        from roster_scout.reports import accept_authorized_report_request

        with self.Session.begin() as session:
            return accept_authorized_report_request(
                session,
                actor_account_id=account_id,
                organization_id=organization_id,
                request=self._report_request(),
                idempotency_key=str(uuid.uuid4()),
            )

    def _publish_partial_snapshot(self, organization_id, account_id, accepted, payload):
        from roster_scout.models import (
            ReportArtifact,
            ReportJob,
            ReportSection,
            ReportSnapshot,
        )

        with self.Session.begin() as session:
            snapshot = ReportSnapshot(
                organization_id=organization_id,
                report_id=accepted.report_id,
                status="draft",
                schema_version="2026.1",
                source_collected_at=self.now - timedelta(minutes=5),
                created_by_account_id=account_id,
            )
            session.add(snapshot)
            session.flush()
            session.add_all((
                ReportSection(
                    organization_id=organization_id,
                    snapshot_id=snapshot.id,
                    job_id=accepted.job_id,
                    section="batting",
                    critical=True,
                    status="complete",
                    row_count=1,
                    verified_empty=False,
                    collected_at=self.now,
                ),
                ReportSection(
                    organization_id=organization_id,
                    snapshot_id=snapshot.id,
                    job_id=accepted.job_id,
                    section="pitching",
                    critical=True,
                    status="unavailable",
                    row_count=0,
                    verified_empty=False,
                    failure_category="source_unavailable",
                ),
            ))
            artifact = ReportArtifact(
                organization_id=organization_id,
                snapshot_id=snapshot.id,
                artifact_type="xlsx",
                status="ready",
                schema_version="2026.1",
                sha256=sha256(payload).hexdigest(),
                object_key="release/partial.xlsx",
            )
            session.add(artifact)
            session.flush()
            snapshot.status = "partial"
            snapshot.published_at = self.now
            job = session.get(ReportJob, accepted.job_id)
            job.status = "ready"
            job.result_snapshot_id = snapshot.id
            job.finished_at = self.now
            session.flush()
            return snapshot.id, artifact.id

    def _ack(self, client, credential, receipt, proof):
        return client.post(
            "/api/downloads/ack",
            json={
                "download_event_id": receipt,
                "acknowledgement_proof": proof,
            },
            headers={
                "Origin": "http://localhost",
                "Sec-Fetch-Site": "same-origin",
                "X-CSRFToken": self._csrf(credential),
            },
        )


class Phase2ReleaseStoryTests(_Phase2ReleaseCase):
    """One invited-staff story proves the complete public Phase 2 outcome."""

    def test_invited_staff_share_immutable_reports_without_cross_org_access(self):
        from roster_scout.models import (
            OrganizationMembership,
            ProductUsageEvent,
            ReportJob,
            ReportJobDispatch,
            ReportSnapshot,
            SecurityAuditEvent,
        )
        from roster_scout.organizations import remove_membership
        from roster_scout.reports import (
            accept_authorized_report_refresh,
            accept_authorized_report_retry,
        )
        from roster_scout.usage import (
            query_usage_metrics,
            record_workbook_download_served,
        )

        organization_id, admin_id, _ = self._bootstrap_organization(
            "Release Baseball", "admin@release.example", "release-admin"
        )
        member_id, member_membership_id = self._invite_member(
            organization_id,
            admin_id,
            "member@release.example",
            "release-member",
        )
        foreign_org_id, foreign_admin_id, _ = self._bootstrap_organization(
            "Foreign Baseball", "admin@foreign.example", "foreign-admin"
        )
        admin_client, _ = self._client(admin_id, organization_id)
        member_client, member_credential = self._client(member_id, organization_id)
        foreign_client, foreign_credential = self._client(
            foreign_admin_id, foreign_org_id
        )

        accepted = self._accept_report(organization_id, member_id)
        with self.Session() as session:
            self.assertEqual(session.scalar(select(func.count(ReportJob.id))), 1)
            self.assertEqual(session.scalar(select(func.count(ReportJobDispatch.id))), 1)
            self.assertEqual(
                session.scalar(select(func.count(ProductUsageEvent.id)).where(
                    ProductUsageEvent.event_type == "authenticated_use"
                )),
                1,
            )
            create_audits = tuple(session.scalars(select(SecurityAuditEvent).where(
                SecurityAuditEvent.action == "report.create"
            )))
        self.assertEqual(len(create_audits), 1)

        for client in (admin_client, member_client):
            library = client.get("/reports")
            self.assertEqual(library.status_code, 200)
            self.assertIn(b"Release State University", library.data)
        foreign = foreign_client.get(f"/reports/{accepted.report_id}")
        absent = foreign_client.get(f"/reports/{uuid.uuid4()}")
        self.assertEqual(foreign.status_code, 404)
        self.assertEqual(foreign.get_data(), absent.get_data())

        payload = b"PK\x03\x04phase-two-release-workbook"
        snapshot_id, artifact_id = self._publish_partial_snapshot(
            organization_id, member_id, accepted, payload
        )
        detail = member_client.get(f"/reports/{accepted.report_id}")
        self.assertEqual(detail.status_code, 200)
        for expected in (b"Partial", b"Batting", b"Pitching", b"NCAA source unavailable"):
            self.assertIn(expected, detail.data)

        with self.Session.begin() as session:
            first_refresh = accept_authorized_report_refresh(
                session,
                actor_account_id=member_id,
                organization_id=organization_id,
                report_id=accepted.report_id,
                idempotency_key=str(uuid.uuid4()),
            )
        with self.Session.begin() as session:
            duplicate_refresh = accept_authorized_report_refresh(
                session,
                actor_account_id=member_id,
                organization_id=organization_id,
                report_id=accepted.report_id,
                idempotency_key=str(uuid.uuid4()),
            )
        self.assertEqual(first_refresh.job_id, duplicate_refresh.job_id)
        active_detail = member_client.get(f"/reports/{accepted.report_id}")
        self.assertIn(b"Latest snapshot", active_detail.data)
        self.assertIn(b"Queued", active_detail.data)

        with self.Session.begin() as session:
            failed_job = session.get(ReportJob, first_refresh.job_id)
            failed_job.status = "failed"
            failed_job.failure_category = "source_unavailable"
            failed_job.failure_message = "secret Browserless token and exception"
            failed_job.finished_at = self.now + timedelta(minutes=1)
        failed_detail = member_client.get(f"/reports/{accepted.report_id}")
        self.assertIn(b"NCAA source unavailable", failed_detail.data)
        self.assertIn(b"Latest snapshot", failed_detail.data)
        with self.Session.begin() as session:
            retry = accept_authorized_report_retry(
                session,
                actor_account_id=member_id,
                organization_id=organization_id,
                report_id=accepted.report_id,
                failed_job_id=first_refresh.job_id,
                idempotency_key=str(uuid.uuid4()),
            )
        self.assertNotEqual(retry.job_id, first_refresh.job_id)
        with self.Session() as session:
            preserved = session.get(ReportSnapshot, snapshot_id)
            failed_job = session.get(ReportJob, first_refresh.job_id)
        self.assertEqual(preserved.status, "partial")
        self.assertEqual(preserved.published_at, self.now)
        self.assertEqual(failed_job.status, "failed")

        self.app_module.app.extensions["roster_scout_artifact_reader"] = Mock(
            return_value=payload
        )
        prepared = member_client.post(
            f"/reports/{accepted.report_id}/snapshots/{snapshot_id}"
            f"/artifacts/{artifact_id}/download",
            data={"csrf_token": self._csrf(member_credential)},
        )
        self.assertEqual(prepared.status_code, 200)
        self.assertEqual(prepared.data, payload)
        receipt = prepared.headers["X-Roster-Scout-Download-Event-ID"]
        proof = prepared.headers["X-Roster-Scout-Download-Ack-Proof"]
        missing_proof = self._ack(member_client, member_credential, receipt, "")
        unprepared = self._ack(
            member_client,
            member_credential,
            receipt,
            self.app_module._DOWNLOAD_ACK_SERIALIZER.dumps({
                "purpose": self.app_module._AUTHENTICATED_DOWNLOAD_ACK_PURPOSE,
                "download_event_id": receipt,
            }),
        )
        foreign_ack = self._ack(foreign_client, foreign_credential, receipt, proof)
        self.assertEqual(missing_proof.status_code, 400)
        self.assertEqual(unprepared.status_code, 400)
        self.assertEqual(foreign_ack.status_code, 404)

        first_ack = self._ack(member_client, member_credential, receipt, proof)
        ambiguous_retry = self._ack(member_client, member_credential, receipt, proof)
        self.assertEqual(first_ack.status_code, 200)
        self.assertEqual(first_ack.get_json(), ambiguous_retry.get_json())

        expired_receipt = str(uuid.uuid4())
        scope = {
            "account_id": member_id,
            "organization_id": organization_id,
            "report_id": accepted.report_id,
            "snapshot_id": snapshot_id,
            "artifact_id": artifact_id,
        }
        issued_at = 2_000_000_000
        with patch("itsdangerous.timed.time.time", return_value=issued_at):
            expired_proof = self.app_module._issue_authenticated_download_ack_proof(
                expired_receipt, **scope
            )
        with patch("itsdangerous.timed.time.time", return_value=issued_at + 901):
            expired = self._ack(
                member_client,
                member_credential,
                expired_receipt,
                expired_proof,
            )
        self.assertEqual(expired.status_code, 400)

        with self.Session.begin() as session:
            record_workbook_download_served(
                session,
                event_key=str(uuid.uuid4()),
                occurred_at=self.now,
            )
        with self.Session() as session:
            global_metrics = query_usage_metrics(
                session,
                start=self.now - timedelta(minutes=1),
                end=self.now + timedelta(minutes=2),
            )
            scoped_metrics = query_usage_metrics(
                session,
                start=self.now - timedelta(minutes=1),
                end=self.now + timedelta(minutes=2),
                organization_id=organization_id,
            )
            canonical_downloads = session.scalar(
                select(func.count(ProductUsageEvent.id)).where(
                    ProductUsageEvent.event_type == "workbook_download_served",
                    ProductUsageEvent.actor_account_id == member_id,
                    ProductUsageEvent.organization_id == organization_id,
                )
            )
        self.assertEqual(canonical_downloads, 1)
        self.assertEqual(global_metrics.successful_workbook_downloads, 2)
        self.assertEqual(scoped_metrics.successful_workbook_downloads, 1)
        self.assertEqual(scoped_metrics.unique_authenticated_users, 1)

        with self.Session.begin() as session:
            remove_membership(
                session,
                organization_id=organization_id,
                actor_account_id=admin_id,
                membership_id=member_membership_id,
                now=self.now + timedelta(minutes=2),
            )
        removed = member_client.get(f"/reports/{accepted.report_id}")
        self.assertNotEqual(removed.status_code, 200)
        with self.Session() as session:
            membership = session.get(OrganizationMembership, member_membership_id)
        self.assertEqual(membership.status, "inactive")

    def test_acceptance_rolls_back_job_usage_audit_and_outbox_together(self):
        from roster_scout.models import ProductUsageEvent, Report, ReportJob, ReportJobDispatch
        from roster_scout.reports import accept_authorized_report_request

        organization_id, admin_id, _ = self._bootstrap_organization(
            "Rollback Baseball", "admin@rollback.example", "rollback-admin"
        )
        with self.assertRaises(RuntimeError):
            with self.Session.begin() as session:
                with patch(
                    "roster_scout.reports.append_security_audit_event",
                    side_effect=RuntimeError("forced release rollback"),
                ):
                    accept_authorized_report_request(
                        session,
                        actor_account_id=admin_id,
                        organization_id=organization_id,
                        request=self._report_request(),
                        idempotency_key=str(uuid.uuid4()),
                    )
        with self.Session() as session:
            for model in (Report, ReportJob, ReportJobDispatch, ProductUsageEvent):
                self.assertEqual(session.scalar(select(func.count(model.id))), 0)


class ExternalFailureStateTests(_Phase2ReleaseCase):
    """External boundaries retain distinct fixed customer-safe states."""

    def test_external_failures_are_distinct_sanitized_and_phase_bounded(self):
        from roster_scout.identity import FakeIdentityProvider, IdentityFailure, exchange_authorization_code
        from roster_scout.models import ReportJob
        from roster_scout.reports import load_report_status

        identity_secret = "identity-secret-token"
        logger = logging.getLogger("tests.phase2.release.identity")
        with self.assertLogs(logger, level="WARNING") as captured:
            with self.assertRaises(IdentityFailure) as identity_error:
                exchange_authorization_code(
                    FakeIdentityProvider(failure=RuntimeError(identity_secret)),
                    code="authorization-code-secret",
                    logger=logger,
                )
        self.assertEqual(identity_error.exception.category, "identity_unavailable")
        self.assertEqual(
            captured.output,
            ["WARNING:tests.phase2.release.identity:identity_provider_exchange_failed"],
        )
        self.assertNotIn(identity_secret, " ".join(captured.output))

        organization_id, admin_id, _ = self._bootstrap_organization(
            "Failure Taxonomy Baseball",
            "admin@failures.example",
            "failure-admin",
        )
        client, credential = self._client(admin_id, organization_id)

        class _BrokenFactory:
            def begin(self):
                raise RuntimeError("database-password-and-host")

        self.app_module.app.extensions["roster_scout_report_session_factory"] = _BrokenFactory()
        database = client.get("/reports")
        self.assertEqual(database.status_code, 503)
        self.assertIn(b"We couldn\xe2\x80\x99t load reports", database.data)
        self.assertNotIn(b"database-password", database.data)
        self.app_module.app.extensions["roster_scout_report_session_factory"] = self.Session

        accepted = self._accept_report(organization_id, admin_id)
        for category, expected in {
            "queue": "Build service unavailable",
            "source_unavailable": "NCAA source unavailable",
        }.items():
            with self.Session.begin() as session:
                job = session.get(ReportJob, accepted.job_id)
                job.status = "failed"
                job.failure_category = category
                job.failure_message = f"secret-{category}-exception"
                job.finished_at = self.now
            with self.Session() as session:
                status = load_report_status(session, organization_id, accepted.report_id)
            self.assertEqual(status.message, expected)
            self.assertNotIn("secret", status.message)

        payload = b"PK\x03\x04expected-artifact"
        snapshot_id, artifact_id = self._publish_partial_snapshot(
            organization_id, admin_id, accepted, payload
        )
        self.app_module.app.extensions["roster_scout_artifact_reader"] = Mock(
            side_effect=RuntimeError("secret-object-storage-key")
        )
        artifact = client.post(
            f"/reports/{accepted.report_id}/snapshots/{snapshot_id}"
            f"/artifacts/{artifact_id}/download",
            data={"csrf_token": self._csrf(credential)},
        )
        self.assertEqual(artifact.status_code, 503)
        self.assertEqual(artifact.get_json()["error"], "artifact_unavailable")
        self.assertEqual(
            artifact.get_json()["message"],
            "The saved workbook is temporarily unavailable. Try again.",
        )
        self.assertNotIn("secret-object", artifact.get_data(as_text=True))

        rules = {rule.rule for rule in self.app_module.app.url_map.iter_rules()}
        self.assertFalse(any("power" in rule.lower() for rule in rules))
        self.assertFalse(any("embed" in rule.lower() for rule in rules))
        self.assertFalse(any("split" in rule.lower() for rule in rules))
        self.assertNotIn("/organizations/create", rules)


if __name__ == "__main__":
    unittest.main()
