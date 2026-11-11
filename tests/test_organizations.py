"""Phase 2 canonical organization, invitation, and audit contracts."""

from __future__ import annotations

import logging
import unittest
import uuid
from datetime import UTC, datetime, timedelta

from alembic import command
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from tests.db_support import (
    alembic_config,
    clean_database,
    require_test_database_url,
    upgrade_database,
)


class _PostgreSQLOrganizationCase(unittest.TestCase):
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
        self.now = datetime(2026, 11, 11, 19, 0, tzinfo=UTC)

    def _account(self, email: str):
        from roster_scout.models import Account

        with self.Session.begin() as session:
            account = Account(
                normalized_email=email.casefold(),
                email_verified_at=self.now,
                status="active",
            )
            session.add(account)
            session.flush()
            return account.id

    def _organization_with_membership(self, email: str, role: str = "admin"):
        from roster_scout.models import Account, Organization, OrganizationMembership

        with self.Session.begin() as session:
            organization = Organization(name=f"Organization {uuid.uuid4()}")
            account = Account(
                normalized_email=email.casefold(),
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
            session.add(membership)
            session.flush()
            return organization.id, account.id, membership.id


class OrganizationOwnershipSchemaTests(_PostgreSQLOrganizationCase):
    def test_revision_0017_is_reversible_and_drift_free(self):
        config = alembic_config(self.database_url)
        command.downgrade(config, "0016")
        with self.engine.connect() as connection:
            tables = set(inspect(connection).get_table_names())
        self.assertNotIn("organization_invitations", tables)
        self.assertNotIn("organization_identity_connections", tables)
        self.assertNotIn("security_audit_events", tables)

        command.upgrade(config, "head")
        command.check(config)
        with self.engine.connect() as connection:
            tables = set(inspect(connection).get_table_names())
        self.assertTrue(
            {
                "organization_invitations",
                "organization_identity_connections",
                "security_audit_events",
            }.issubset(tables)
        )

    def test_invitation_constraints_and_partial_active_index_are_named(self):
        inspector = inspect(self.engine)
        foreign_keys = {
            item["name"]
            for item in inspector.get_foreign_keys("organization_invitations")
        }
        checks = {
            item["name"]
            for item in inspector.get_check_constraints("organization_invitations")
        }
        indexes = {
            item["name"]: item
            for item in inspector.get_indexes("organization_invitations")
        }
        self.assertIn("fk_organization_invitations_organization", foreign_keys)
        self.assertIn("fk_organization_invitations_inviter_membership", foreign_keys)
        self.assertIn("ck_organization_invitations_status", checks)
        active = indexes["uq_organization_invitations_active_email"]
        self.assertTrue(active["unique"])
        self.assertIn("status", str(active.get("dialect_options", {})))

    def test_security_audit_rows_are_database_append_only(self):
        from roster_scout.audit import append_security_audit_event

        organization_id, account_id, _ = self._organization_with_membership(
            "admin@example.edu"
        )
        with self.Session.begin() as session:
            event = append_security_audit_event(
                session,
                actor_account_id=account_id,
                organization_id=organization_id,
                target_type="organization",
                target_id=organization_id,
                action="organization.bootstrap",
                outcome="succeeded",
                reason="operator_provisioned",
                occurred_at=self.now,
            )
            event_id = event.id

        for statement in (
            "UPDATE security_audit_events SET outcome = 'failed' WHERE id = :id",
            "DELETE FROM security_audit_events WHERE id = :id",
        ):
            with self.subTest(statement=statement):
                with self.assertRaises(DBAPIError):
                    with self.engine.begin() as connection:
                        connection.execute(text(statement), {"id": event_id})


class InvitationCreationTests(_PostgreSQLOrganizationCase):
    def test_admin_can_invite_any_valid_address_and_member_cannot(self):
        from roster_scout.organizations import OrganizationFailure, create_invitation

        organization_id, admin_id, _ = self._organization_with_membership(
            "admin@example.edu", "admin"
        )
        _, member_id, _ = self._organization_with_membership(
            "member@example.edu", "member"
        )
        for email in (
            "coach@example.edu",
            "analyst@athletics.org",
            "vendor@sports-data.test",
            "staff@gmail.com",
        ):
            with self.subTest(email=email):
                with self.Session.begin() as session:
                    invitation = create_invitation(
                        session,
                        organization_id=organization_id,
                        actor_account_id=admin_id,
                        email=email,
                        role="member",
                        now=self.now,
                    )
                    self.assertEqual(invitation.normalized_email, email.casefold())
                    invitation.status = "revoked"
                    invitation.revoked_at = self.now

        with self.Session.begin() as session:
            with self.assertRaises(OrganizationFailure) as denied:
                create_invitation(
                    session,
                    organization_id=organization_id,
                    actor_account_id=member_id,
                    email="another@example.edu",
                    role="admin",
                    now=self.now,
                )
        self.assertEqual(denied.exception.category, "forbidden")

    def test_one_active_invitation_and_seven_day_expiry(self):
        from roster_scout.organizations import OrganizationFailure, create_invitation

        organization_id, admin_id, _ = self._organization_with_membership(
            "admin@example.edu"
        )
        with self.Session.begin() as session:
            invitation = create_invitation(
                session,
                organization_id=organization_id,
                actor_account_id=admin_id,
                email=" Invitee@Example.EDU ",
                role="admin",
                now=self.now,
            )
            self.assertEqual(invitation.expires_at, self.now + timedelta(days=7))
        with self.Session.begin() as session:
            with self.assertRaises(OrganizationFailure) as duplicate:
                create_invitation(
                    session,
                    organization_id=organization_id,
                    actor_account_id=admin_id,
                    email="invitee@example.edu",
                    role="member",
                    now=self.now + timedelta(days=1),
                )
        self.assertEqual(duplicate.exception.category, "invitation_exists")

    def test_local_invite_survives_provider_failure_and_delivery_retries(self):
        from roster_scout.identity import FakeIdentityProvider
        from roster_scout.models import OrganizationInvitation
        from roster_scout.organizations import (
            OrganizationFailure,
            create_invitation,
            deliver_invitation,
        )

        organization_id, admin_id, _ = self._organization_with_membership(
            "admin@example.edu"
        )
        with self.Session.begin() as session:
            invitation = create_invitation(
                session,
                organization_id=organization_id,
                actor_account_id=admin_id,
                email="coach@example.edu",
                role="member",
                now=self.now,
            )
            invitation_id = invitation.id

        failing = FakeIdentityProvider(
            organization_failure=RuntimeError("provider-secret"),
        )
        logger = logging.getLogger("tests.organization.redaction")
        with self.assertLogs(logger, level="WARNING") as captured:
            with self.assertRaises(OrganizationFailure) as failed:
                deliver_invitation(
                    self.Session,
                    provider=failing,
                    invitation_id=invitation_id,
                    now=self.now,
                    logger=logger,
                )
        self.assertEqual(failed.exception.category, "identity_unavailable")
        self.assertNotIn("provider-secret", " ".join(captured.output))
        with self.Session.begin() as session:
            durable = session.get(OrganizationInvitation, invitation_id)
            self.assertEqual(durable.status, "pending")
            self.assertIsNone(durable.provider_invitation_id)

        provider = FakeIdentityProvider()
        delivered = deliver_invitation(
            self.Session,
            provider=provider,
            invitation_id=invitation_id,
            now=self.now + timedelta(minutes=1),
        )
        self.assertEqual(delivered.id, invitation_id)
        self.assertEqual(len(provider.invitation_calls), 1)


class InvitationAcceptanceTests(_PostgreSQLOrganizationCase):
    def _pending_invitation(self, email="coach@example.edu", role="member"):
        from roster_scout.organizations import create_invitation

        organization_id, admin_id, _ = self._organization_with_membership(
            "admin@example.edu"
        )
        with self.Session.begin() as session:
            invitation = create_invitation(
                session,
                organization_id=organization_id,
                actor_account_id=admin_id,
                email=email,
                role=role,
                now=self.now,
            )
            return organization_id, invitation.id

    def test_exact_email_acceptance_is_single_org_and_replay_idempotent(self):
        from roster_scout.models import OrganizationMembership
        from roster_scout.organizations import accept_invitation

        organization_id, invitation_id = self._pending_invitation()
        account_id = self._account("coach@example.edu")
        with self.Session.begin() as session:
            first = accept_invitation(
                session,
                invitation_id=invitation_id,
                account_id=account_id,
                now=self.now + timedelta(minutes=1),
            )
            first_id = first.id
        with self.Session.begin() as session:
            replay = accept_invitation(
                session,
                invitation_id=invitation_id,
                account_id=account_id,
                now=self.now + timedelta(minutes=2),
            )
            self.assertEqual(replay.id, first_id)
            memberships = list(
                session.scalars(
                    select(OrganizationMembership).where(
                        OrganizationMembership.account_id == account_id
                    )
                )
            )
            self.assertEqual(len(memberships), 1)
            self.assertEqual(memberships[0].organization_id, organization_id)

    def test_same_domain_substitution_fails_without_consuming(self):
        from roster_scout.models import OrganizationInvitation
        from roster_scout.organizations import OrganizationFailure, accept_invitation

        _, invitation_id = self._pending_invitation("coach@example.edu")
        wrong_account_id = self._account("other@example.edu")
        with self.Session.begin() as session:
            with self.assertRaises(OrganizationFailure) as mismatch:
                accept_invitation(
                    session,
                    invitation_id=invitation_id,
                    account_id=wrong_account_id,
                    now=self.now + timedelta(minutes=1),
                )
        self.assertEqual(mismatch.exception.category, "invitation_email_mismatch")
        with self.Session.begin() as session:
            invitation = session.get(OrganizationInvitation, invitation_id)
            self.assertEqual(invitation.status, "pending")
            self.assertIsNone(invitation.accepted_at)

    def test_expired_invitation_does_not_grant_membership(self):
        from roster_scout.models import OrganizationMembership
        from roster_scout.organizations import OrganizationFailure, accept_invitation

        _, invitation_id = self._pending_invitation()
        account_id = self._account("coach@example.edu")
        with self.Session.begin() as session:
            with self.assertRaises(OrganizationFailure) as expired:
                accept_invitation(
                    session,
                    invitation_id=invitation_id,
                    account_id=account_id,
                    now=self.now + timedelta(days=8),
                )
        self.assertEqual(expired.exception.category, "invitation_unavailable")
        with self.Session.begin() as session:
            self.assertIsNone(
                session.scalar(
                    select(OrganizationMembership).where(
                        OrganizationMembership.account_id == account_id
                    )
                )
            )


class RoleAuthorizationTests(_PostgreSQLOrganizationCase):
    def test_only_active_admin_can_create_invitation(self):
        from roster_scout.organizations import OrganizationFailure, create_invitation

        organization_id, member_id, _ = self._organization_with_membership(
            "member@example.edu", "member"
        )
        with self.Session.begin() as session:
            with self.assertRaises(OrganizationFailure) as denied:
                create_invitation(
                    session,
                    organization_id=organization_id,
                    actor_account_id=member_id,
                    email="coach@example.edu",
                    role="member",
                    now=self.now,
                )
        self.assertEqual(denied.exception.category, "forbidden")


class NoDomainAutoJoinTests(_PostgreSQLOrganizationCase):
    def test_provider_domain_and_organization_claims_create_no_membership(self):
        from roster_scout.accounts import link_verified_identity
        from roster_scout.identity import VerifiedIdentity
        from roster_scout.models import OrganizationMembership

        with self.Session.begin() as session:
            account = link_verified_identity(
                session,
                VerifiedIdentity(
                    issuer="https://identity.example.test",
                    subject="same-domain-subject",
                    provider_user_id="same-domain-user",
                    email="uninvited@example.edu",
                    email_verified=True,
                    linking_approved=False,
                    sealed_session="sealed-sso-session",
                ),
                now=self.now,
            )
            account_id = account.id
        with self.Session.begin() as session:
            self.assertIsNone(
                session.scalar(
                    select(OrganizationMembership).where(
                        OrganizationMembership.account_id == account_id
                    )
                )
            )

