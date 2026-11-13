"""Phase 2 canonical organization, invitation, and audit contracts."""

from __future__ import annotations

import logging
import io
import importlib
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event, Lock
from unittest.mock import patch

from alembic import command
from sqlalchemy import func, inspect, select, text
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
        self.now = datetime.now(UTC).replace(microsecond=0)

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
    def test_bootstrap_and_provider_mirror_are_idempotent(self):
        from roster_scout.identity import FakeIdentityProvider
        from roster_scout.models import (
            OrganizationIdentityConnection,
            OrganizationInvitation,
            SecurityAuditEvent,
        )
        from roster_scout.organizations import (
            bootstrap_organization,
            mirror_organization,
        )

        with self.Session.begin() as session:
            organization, invitation = bootstrap_organization(
                session,
                name="Example University Baseball",
                admin_email=" Admin@Example.EDU ",
                now=self.now,
            )
            organization_id = organization.id
            invitation_id = invitation.id
        with self.Session.begin() as session:
            repeated_organization, repeated_invitation = bootstrap_organization(
                session,
                name="Example University Baseball",
                admin_email="admin@example.edu",
                now=self.now + timedelta(minutes=1),
            )
            self.assertEqual(repeated_organization.id, organization_id)
            self.assertEqual(repeated_invitation.id, invitation_id)

        provider = FakeIdentityProvider()
        first = mirror_organization(
            self.Session,
            provider=provider,
            organization_id=organization_id,
            now=self.now,
        )
        second = mirror_organization(
            self.Session,
            provider=provider,
            organization_id=organization_id,
            now=self.now + timedelta(minutes=1),
        )
        self.assertEqual(first.id, second.id)
        self.assertEqual(
            provider.organization_calls,
            [("Example University Baseball", str(organization_id))],
        )
        with self.Session.begin() as session:
            self.assertEqual(
                session.query(OrganizationIdentityConnection).count(), 1
            )
            self.assertEqual(session.query(OrganizationInvitation).count(), 1)
            actions = list(
                session.scalars(
                    select(SecurityAuditEvent.action).order_by(
                        SecurityAuditEvent.action
                    )
                )
            )
        self.assertEqual(
            actions,
            ["invitation.create", "organization.bootstrap"],
        )

    def test_concurrent_bootstrap_serializes_the_absent_organization_row(self):
        from roster_scout.database import create_session_factory, get_engine
        from roster_scout.models import Organization, OrganizationInvitation
        from roster_scout.organizations import bootstrap_organization

        ready = Barrier(2)

        def bootstrap(_index):
            engine = get_engine(self.database_url)
            factory = create_session_factory(engine)
            try:
                ready.wait(timeout=5)
                with factory.begin() as session:
                    organization, invitation = bootstrap_organization(
                        session,
                        name="Concurrent Baseball",
                        admin_email="admin@concurrent.example",
                        now=self.now,
                    )
                    return organization.id, invitation.id
            finally:
                engine.dispose()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(bootstrap, range(2)))
        self.assertEqual(len({result[0] for result in results}), 1)
        self.assertEqual(len({result[1] for result in results}), 1)
        with self.Session() as session:
            self.assertEqual(session.query(Organization).count(), 1)
            self.assertEqual(session.query(OrganizationInvitation).count(), 1)

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

    def test_provider_expiry_cannot_extend_local_authorization(self):
        from roster_scout.identity import FakeIdentityProvider, ProviderInvitation
        from roster_scout.models import OrganizationInvitation
        from roster_scout.organizations import create_invitation, deliver_invitation

        class LongerProviderInvite(FakeIdentityProvider):
            def send_invitation(self, **kwargs):
                super().send_invitation(**kwargs)
                return ProviderInvitation(
                    provider_invitation_id="provider-invite-longer",
                    expires_at=self_now + timedelta(days=30),
                )

        self_now = self.now + timedelta(hours=1)
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
            local_expires_at = invitation.expires_at

        delivered = deliver_invitation(
            self.Session,
            provider=LongerProviderInvite(),
            invitation_id=invitation_id,
            now=self.now + timedelta(hours=1),
        )
        self.assertEqual(delivered.provider_expires_at, local_expires_at)
        with self.Session.begin() as session:
            stored = session.get(OrganizationInvitation, invitation_id)
            self.assertEqual(stored.provider_expires_at, stored.expires_at)

    def test_delivery_claim_prevents_two_callers_sending_duplicates(self):
        from roster_scout.identity import FakeIdentityProvider
        from roster_scout.organizations import (
            OrganizationFailure,
            create_invitation,
            deliver_invitation,
        )

        started = Event()
        release = Event()

        class BlockingProvider(FakeIdentityProvider):
            def send_invitation(self, **kwargs):
                started.set()
                if not release.wait(timeout=5):
                    raise RuntimeError("test delivery timeout")
                return super().send_invitation(**kwargs)

        organization_id, admin_id, _ = self._organization_with_membership(
            "admin@example.edu"
        )
        with self.Session.begin() as session:
            invitation = create_invitation(
                session,
                organization_id=organization_id,
                actor_account_id=admin_id,
                email="race@example.edu",
                role="member",
                now=self.now,
            )
            invitation_id = invitation.id
        provider = BlockingProvider()
        with ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(
                deliver_invitation,
                self.Session,
                provider=provider,
                invitation_id=invitation_id,
                now=self.now,
            )
            self.assertTrue(started.wait(timeout=5))
            with self.assertRaises(OrganizationFailure) as concurrent:
                deliver_invitation(
                    self.Session,
                    provider=provider,
                    invitation_id=invitation_id,
                    now=self.now,
                )
            self.assertEqual(
                concurrent.exception.category,
                "invitation_delivery_in_progress",
            )
            release.set()
            first.result(timeout=5)
        self.assertEqual(len(provider.invitation_calls), 1)

    def test_expired_lease_uses_one_provider_idempotency_key(self):
        from roster_scout.identity import FakeIdentityProvider
        from roster_scout.organizations import create_invitation, deliver_invitation

        started = Event()
        release = Event()
        call_lock = Lock()

        class SlowFirstProvider(FakeIdentityProvider):
            def __init__(self):
                super().__init__()
                self._call_number = 0

            def send_invitation(self, **kwargs):
                with call_lock:
                    self._call_number += 1
                    call_number = self._call_number
                if call_number == 1:
                    started.set()
                    if not release.wait(timeout=5):
                        raise RuntimeError("test delivery timeout")
                return super().send_invitation(**kwargs)

        organization_id, admin_id, _ = self._organization_with_membership(
            "admin@example.edu"
        )
        with self.Session.begin() as session:
            invitation = create_invitation(
                session,
                organization_id=organization_id,
                actor_account_id=admin_id,
                email="slow@example.edu",
                role="member",
                now=self.now,
            )
            invitation_id = invitation.id

        provider = SlowFirstProvider()
        with ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(
                deliver_invitation,
                self.Session,
                provider=provider,
                invitation_id=invitation_id,
                now=self.now,
            )
            self.assertTrue(started.wait(timeout=5))
            second = deliver_invitation(
                self.Session,
                provider=provider,
                invitation_id=invitation_id,
                now=self.now + timedelta(minutes=2, seconds=1),
            )
            release.set()
            first_result = first.result(timeout=5)

        self.assertEqual(
            first_result.provider_invitation_id,
            second.provider_invitation_id,
        )
        self.assertEqual(provider.invitation_sends, 1)
        self.assertEqual(len(provider.invitation_calls), 2)
        self.assertEqual(
            len({call[3] for call in provider.invitation_calls}),
            1,
        )

    def test_provider_success_before_local_failure_reconciles_without_resend(self):
        import roster_scout.organizations as organizations_module
        from roster_scout.identity import FakeIdentityProvider
        from roster_scout.models import OrganizationInvitation
        from roster_scout.organizations import create_invitation, deliver_invitation

        organization_id, admin_id, _ = self._organization_with_membership(
            "admin@example.edu"
        )
        with self.Session.begin() as session:
            invitation = create_invitation(
                session,
                organization_id=organization_id,
                actor_account_id=admin_id,
                email="recovery@example.edu",
                role="member",
                now=self.now,
            )
            invitation_id = invitation.id

        provider = FakeIdentityProvider()
        with patch.object(
            organizations_module,
            "append_security_audit_event",
            side_effect=RuntimeError("simulated local persistence failure"),
        ):
            with self.assertRaises(RuntimeError):
                deliver_invitation(
                    self.Session,
                    provider=provider,
                    invitation_id=invitation_id,
                    now=self.now,
                )

        with self.Session() as session:
            indeterminate = session.get(OrganizationInvitation, invitation_id)
            self.assertIsNone(indeterminate.provider_invitation_id)
            self.assertEqual(indeterminate.delivery_started_at, self.now)

        recovered = deliver_invitation(
            self.Session,
            provider=provider,
            invitation_id=invitation_id,
            now=self.now + timedelta(minutes=2, seconds=1),
        )
        self.assertIsNotNone(recovered.provider_invitation_id)
        self.assertEqual(provider.invitation_sends, 1)
        self.assertEqual(len(provider.invitation_calls), 1)
        self.assertEqual(len(provider.invitation_reconciliation_calls), 1)


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

    def test_concurrent_acceptance_converges_on_one_membership(self):
        from roster_scout.models import OrganizationMembership
        from roster_scout.organizations import accept_invitation

        _, invitation_id = self._pending_invitation()
        account_id = self._account("coach@example.edu")
        rendezvous = Barrier(2)

        def accept():
            rendezvous.wait()
            with self.Session.begin() as session:
                membership = accept_invitation(
                    session,
                    invitation_id=invitation_id,
                    account_id=account_id,
                    now=self.now + timedelta(minutes=1),
                )
                return membership.id

        with ThreadPoolExecutor(max_workers=2) as executor:
            membership_ids = list(executor.map(lambda _: accept(), range(2)))
        self.assertEqual(len(set(membership_ids)), 1)
        with self.Session.begin() as session:
            memberships = list(
                session.scalars(
                    select(OrganizationMembership).where(
                        OrganizationMembership.account_id == account_id
                    )
                )
            )
        self.assertEqual(len(memberships), 1)

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

    def test_provider_email_change_rejects_old_invite_and_accepts_current_email(self):
        from roster_scout.models import OrganizationMembership

        from roster_scout.accounts import link_verified_identity
        from roster_scout.identity import VerifiedIdentity
        from roster_scout.organizations import (
            OrganizationFailure,
            accept_invitation,
            create_invitation,
        )

        organization_id, admin_id, _ = self._organization_with_membership(
            "admin@example.edu"
        )
        identity = VerifiedIdentity(
            issuer="https://identity.example.test",
            subject="moving-subject",
            provider_user_id="moving-user",
            email="old@example.edu",
            email_verified=True,
            linking_approved=False,
            sealed_session="sealed-moving",
        )
        with self.Session.begin() as session:
            account = link_verified_identity(session, identity, now=self.now)
            account_id = account.id
            old_invite = create_invitation(
                session,
                organization_id=organization_id,
                actor_account_id=admin_id,
                email="old@example.edu",
                role="member",
                now=self.now,
            )
            new_invite = create_invitation(
                session,
                organization_id=organization_id,
                actor_account_id=admin_id,
                email="new@example.edu",
                role="member",
                now=self.now,
            )
            old_id, new_id = old_invite.id, new_invite.id
        with self.Session.begin() as session:
            link_verified_identity(
                session,
                VerifiedIdentity(
                    **{**identity.__dict__, "email": "new@example.edu"}
                ),
                now=self.now + timedelta(minutes=1),
            )
        with self.Session.begin() as session:
            with self.assertRaises(OrganizationFailure) as old:
                accept_invitation(
                    session,
                    invitation_id=old_id,
                    account_id=account_id,
                    now=self.now + timedelta(minutes=2),
                )
        self.assertEqual(old.exception.category, "invitation_email_mismatch")
        with self.Session.begin() as session:
            self.assertIsNone(
                session.scalar(
                    select(OrganizationMembership).where(
                        OrganizationMembership.account_id == account_id
                    )
                )
            )
        with self.Session.begin() as session:
            membership = accept_invitation(
                session,
                invitation_id=new_id,
                account_id=account_id,
                now=self.now + timedelta(minutes=2),
            )
        self.assertEqual(membership.organization_id, organization_id)
        with self.Session.begin() as session:
            self.assertEqual(
                session.scalar(
                    select(func.count(OrganizationMembership.id)).where(
                        OrganizationMembership.account_id == account_id
                    )
                ),
                1,
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

    def test_member_administration_capabilities_deny_by_default(self):
        from roster_scout.models import Account, OrganizationMembership
        from roster_scout.organizations import (
            OrganizationFailure,
            change_membership_role,
            remove_membership,
        )

        organization_id, admin_id, _ = self._organization_with_membership(
            "admin@example.edu", "admin"
        )
        with self.Session.begin() as session:
            member = Account(
                normalized_email="member@example.edu",
                email_verified_at=self.now,
                status="active",
            )
            target = Account(
                normalized_email="target@example.edu",
                email_verified_at=self.now,
                status="active",
            )
            session.add_all((member, target))
            session.flush()
            member_membership = OrganizationMembership(
                organization_id=organization_id,
                account_id=member.id,
                role="member",
                status="active",
            )
            target_membership = OrganizationMembership(
                organization_id=organization_id,
                account_id=target.id,
                role="member",
                status="active",
            )
            session.add_all((member_membership, target_membership))
            session.flush()
            member_id = member.id
            target_membership_id = target_membership.id

        for operation in ("role", "remove"):
            with self.subTest(operation=operation):
                with self.Session.begin() as session:
                    with self.assertRaises(OrganizationFailure) as denied:
                        if operation == "role":
                            change_membership_role(
                                session,
                                organization_id=organization_id,
                                actor_account_id=member_id,
                                membership_id=target_membership_id,
                                role="admin",
                                now=self.now,
                            )
                        else:
                            remove_membership(
                                session,
                                organization_id=organization_id,
                                actor_account_id=member_id,
                                membership_id=target_membership_id,
                                now=self.now,
                            )
                self.assertEqual(denied.exception.category, "forbidden")

        with self.Session.begin() as session:
            changed = change_membership_role(
                session,
                organization_id=organization_id,
                actor_account_id=admin_id,
                membership_id=target_membership_id,
                role="admin",
                now=self.now,
            )
            self.assertEqual(changed.role, "admin")


class MembershipAdministrationTests(_PostgreSQLOrganizationCase):
    def setUp(self):
        super().setUp()
        import app as app_module

        self.app_module = app_module
        self.original_config = dict(app_module.app.config)
        self.original_factory = app_module.app.extensions.get(
            "roster_scout_auth_session_factory"
        )
        app_module.app.config.update(
            TESTING=True,
            ROSTER_SCOUT_TEST_AUTH_ENFORCEMENT=True,
            WTF_CSRF_ENABLED=True,
            WTF_CSRF_SSL_STRICT=False,
        )
        app_module.app.extensions["roster_scout_auth_session_factory"] = self.Session
        self.client = app_module.app.test_client()

    def tearDown(self):
        self.app_module.app.config.clear()
        self.app_module.app.config.update(self.original_config)
        self.app_module.app.extensions["roster_scout_auth_session_factory"] = (
            self.original_factory
        )
        super().tearDown()

    def _csrf_token(self, credential):
        from itsdangerous import URLSafeTimedSerializer

        return URLSafeTimedSerializer(
            self.app_module.app.secret_key,
            salt="wtf-csrf-token",
        ).dumps(credential.csrf_secret)

    def _multi_organization_session(self):
        from roster_scout.models import Account, Organization, OrganizationMembership
        from roster_scout.sessions import create_account_session

        with self.Session.begin() as session:
            account = Account(
                normalized_email="coach@example.edu",
                email_verified_at=self.now,
                status="active",
            )
            first = Organization(name="Friday Opponent Prep")
            second = Organization(name="Saturday Opponent Prep")
            session.add_all((account, first, second))
            session.flush()
            first_membership = OrganizationMembership(
                organization_id=first.id,
                account_id=account.id,
                role="admin",
                status="active",
            )
            second_membership = OrganizationMembership(
                organization_id=second.id,
                account_id=account.id,
                role="member",
                status="active",
            )
            session.add_all((first_membership, second_membership))
            session.flush()
            credential = create_account_session(
                session,
                account_id=account.id,
                active_organization_id=first.id,
                sealed_provider_state="sealed-multi-org-session",
                now=self.now,
            )
            return (
                credential,
                account.id,
                first.id,
                second.id,
                second_membership.id,
            )

    def test_selection_lists_only_current_memberships_and_switch_rotates(self):
        from roster_scout.models import AccountSession
        from roster_scout.sessions import SESSION_COOKIE_NAME

        credential, _, first_id, second_id, second_membership_id = (
            self._multi_organization_session()
        )
        self.client.set_cookie(SESSION_COOKIE_NAME, credential.cookie_value)
        chooser = self.client.get("/organizations/select")
        chooser_body = chooser.get_data(as_text=True)
        self.assertEqual(chooser.status_code, 200)
        self.assertIn("Friday Opponent Prep", chooser_body)
        self.assertIn("Saturday Opponent Prep", chooser_body)
        self.assertNotIn(str(first_id), chooser_body)
        self.assertNotIn(str(second_id), chooser_body)

        response = self.client.post(
            "/organizations/switch",
            data={
                "csrf_token": self._csrf_token(credential),
                "membership_id": str(second_membership_id),
                "organization_id": str(first_id),
                "role": "admin",
                "report_id": str(uuid.uuid4()),
                "next": "/organization/members",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/reports")
        self.assertIn(f"{SESSION_COOKIE_NAME}=", response.headers["Set-Cookie"])
        with self.Session.begin() as session:
            rows = list(session.scalars(
                select(AccountSession).order_by(AccountSession.created_at)
            ))
            self.assertEqual(len(rows), 2)
            self.assertIsNotNone(rows[0].revoked_at)
            self.assertEqual(rows[1].active_organization_id, second_id)

    def test_removed_active_membership_is_cleared_before_next_context(self):
        from roster_scout.authorization import AuthorizationFailure, require_context
        from roster_scout.models import Account, AccountSession, OrganizationMembership
        from roster_scout.organizations import remove_membership

        credential, account_id, first_id, _, _ = self._multi_organization_session()
        with self.Session.begin() as session:
            remaining_admin = Account(
                normalized_email="remaining-admin@example.edu",
                email_verified_at=self.now,
                status="active",
            )
            session.add(remaining_admin)
            session.flush()
            session.add(OrganizationMembership(
                organization_id=first_id,
                account_id=remaining_admin.id,
                role="admin",
                status="active",
            ))
            session.flush()
            membership = session.scalar(select(OrganizationMembership).where(
                OrganizationMembership.organization_id == first_id,
                OrganizationMembership.account_id == account_id,
            ))
            remove_membership(
                session,
                organization_id=first_id,
                actor_account_id=remaining_admin.id,
                membership_id=membership.id,
                now=self.now,
            )
        with self.Session.begin() as session:
            stored = session.scalar(select(AccountSession))
            self.assertIsNone(stored.active_organization_id)
            with self.assertRaises(AuthorizationFailure) as denied:
                require_context(
                    session,
                    cookie_value=credential.cookie_value,
                    now=self.now + timedelta(seconds=1),
                )
        self.assertEqual(denied.exception.category, "membership_required")

    def test_concurrent_admin_mutations_preserve_one_active_admin(self):
        from roster_scout.database import create_session_factory, get_engine
        from roster_scout.models import Account, OrganizationMembership
        from roster_scout.organizations import (
            OrganizationFailure,
            change_membership_role,
            remove_membership,
        )

        organization_id, first_account_id, first_membership_id = (
            self._organization_with_membership("first-admin@example.edu", "admin")
        )
        with self.Session.begin() as session:
            second = Account(
                normalized_email="second-admin@example.edu",
                email_verified_at=self.now,
                status="active",
            )
            session.add(second)
            session.flush()
            second_membership = OrganizationMembership(
                organization_id=organization_id,
                account_id=second.id,
                role="admin",
                status="active",
            )
            session.add(second_membership)
            session.flush()
            second_account_id = second.id
            second_membership_id = second_membership.id

        ready = Barrier(2)

        def mutate(kind):
            engine = get_engine(self.database_url)
            Session = create_session_factory(engine)
            try:
                ready.wait(timeout=5)
                with Session.begin() as session:
                    if kind == "demote":
                        change_membership_role(
                            session,
                            organization_id=organization_id,
                            actor_account_id=second_account_id,
                            membership_id=first_membership_id,
                            role="member",
                            now=self.now,
                        )
                    else:
                        remove_membership(
                            session,
                            organization_id=organization_id,
                            actor_account_id=first_account_id,
                            membership_id=second_membership_id,
                            now=self.now,
                        )
                return "succeeded"
            except OrganizationFailure as exc:
                return exc.category
            finally:
                engine.dispose()

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(mutate, ("demote", "remove")))
        self.assertEqual(outcomes.count("succeeded"), 1)
        self.assertTrue(set(outcomes).issubset({"succeeded", "forbidden", "last_admin"}))
        with self.Session.begin() as session:
            active_admins = session.scalar(
                select(func.count()).select_from(OrganizationMembership).where(
                    OrganizationMembership.organization_id == organization_id,
                    OrganizationMembership.status == "active",
                    OrganizationMembership.role == "admin",
                )
            )
        self.assertEqual(active_admins, 1)


class NoDomainAutoJoinTests(_PostgreSQLOrganizationCase):
    def test_provider_organization_context_has_no_acceptance_api(self):
        from roster_scout import organizations

        self.assertFalse(
            hasattr(organizations, "accept_provider_invitation_handoff")
        )

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


class OrganizationBootstrapCliTests(_PostgreSQLOrganizationCase):
    def test_cli_bootstrap_is_idempotent_and_prints_only_fixed_output(self):
        from roster_scout.identity import FakeIdentityProvider
        from roster_scout.models import Organization, OrganizationInvitation

        provision = importlib.import_module("scripts.provision_organization")
        provider = FakeIdentityProvider()
        for _ in range(2):
            stdout = io.StringIO()
            stderr = io.StringIO()
            result = provision.main(
                [
                    "--name",
                    "Example University Baseball",
                    "--admin-email",
                    "Admin@Example.EDU",
                ],
                session_factory=self.Session,
                provider=provider,
                stdout=stdout,
                stderr=stderr,
            )
            self.assertEqual(result, 0)
            self.assertEqual(stdout.getvalue(), "organization provisioned\n")
            self.assertEqual(stderr.getvalue(), "")
            self.assertNotIn("admin@example.edu", stdout.getvalue().casefold())
            self.assertNotIn("provider", stdout.getvalue().casefold())

        with self.Session.begin() as session:
            self.assertEqual(session.query(Organization).count(), 1)
            self.assertEqual(session.query(OrganizationInvitation).count(), 1)
        self.assertEqual(len(provider.organization_calls), 1)
        self.assertEqual(len(provider.invitation_calls), 1)

    def test_cli_errors_are_fixed_and_do_not_echo_operator_input(self):
        provision = importlib.import_module("scripts.provision_organization")
        secret = "private-admin@example.edu"
        stderr = io.StringIO()
        result = provision.main(
            ["--name", "", "--admin-email", secret],
            session_factory=self.Session,
            stdout=io.StringIO(),
            stderr=stderr,
        )
        self.assertEqual(result, 2)
        self.assertEqual(stderr.getvalue(), "organization provisioning invalid arguments\n")
        self.assertNotIn(secret, stderr.getvalue())


class OrganizationInvitationRouteTests(_PostgreSQLOrganizationCase):
    def setUp(self):
        super().setUp()
        import app as app_module
        from roster_scout.identity import FakeIdentityProvider

        self.app_module = app_module
        self.original_config = dict(app_module.app.config)
        self.original_provider = app_module.app.extensions.get(
            "roster_scout_identity_provider"
        )
        self.original_factory = app_module.app.extensions.get(
            "roster_scout_auth_session_factory"
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
        self.client = app_module.app.test_client()

    def tearDown(self):
        self.app_module.app.config.clear()
        self.app_module.app.config.update(self.original_config)
        self.app_module.app.extensions["roster_scout_identity_provider"] = (
            self.original_provider
        )
        self.app_module.app.extensions["roster_scout_auth_session_factory"] = (
            self.original_factory
        )
        super().tearDown()

    def _credential(self, *, email, role="admin", organization_name="Example Baseball"):
        from roster_scout.models import Account, Organization, OrganizationMembership
        from roster_scout.sessions import create_account_session

        with self.Session.begin() as session:
            organization = Organization(name=organization_name)
            account = Account(
                normalized_email=email,
                email_verified_at=self.now,
                status="active",
            )
            session.add_all((organization, account))
            session.flush()
            session.add(OrganizationMembership(
                organization_id=organization.id,
                account_id=account.id,
                role=role,
                status="active",
            ))
            session.flush()
            credential = create_account_session(
                session,
                account_id=account.id,
                active_organization_id=organization.id,
                sealed_provider_state="sealed-organization-session",
                now=self.now,
            )
            return credential, organization.id, account.id

    def _set_cookie(self, credential):
        from roster_scout.sessions import SESSION_COOKIE_NAME

        self.client.set_cookie(SESSION_COOKIE_NAME, credential.cookie_value)

    def _csrf_token(self, credential):
        from itsdangerous import URLSafeTimedSerializer

        return URLSafeTimedSerializer(
            self.app_module.app.secret_key,
            salt="wtf-csrf-token",
        ).dumps(credential.csrf_secret)

    def test_admin_members_page_and_invitation_forms_are_accessible(self):
        credential, _, account_id = self._credential(email="admin@example.edu")
        self._set_cookie(credential)
        response = self.client.get("/organization/members")
        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Manage who can access Example Baseball", body)
        self.assertIn("Any valid email address can be invited", body)
        self.assertIn('name="email"', body)
        self.assertIn('name="role"', body)
        self.assertIn("Active members (1)", body)
        self.assertIn("Pending invitations (0)", body)
        self.assertIn("<table", body)
        self.assertIn('class="member-cards"', body)
        self.assertIn("Save role", body)
        self.assertIn("Last admin", body)
        self.assertIn("Add another admin before changing this role.", body)
        self.assertIn("disabled", body)
        self.assertIn("Remove member", body)
        self.assertNotIn(
            "They’ll immediately lose access to this organization’s reports.",
            body,
        )
        self.assertNotIn(str(account_id), body)

    def test_role_and_remove_routes_require_csrf_and_apply_scoped_changes(self):
        from roster_scout.models import Account, OrganizationMembership

        credential, organization_id, _ = self._credential(
            email="admin@example.edu"
        )
        with self.Session.begin() as session:
            target = Account(
                normalized_email="target@example.edu",
                email_verified_at=self.now,
                status="active",
            )
            session.add(target)
            session.flush()
            membership = OrganizationMembership(
                organization_id=organization_id,
                account_id=target.id,
                role="member",
                status="active",
            )
            session.add(membership)
            session.flush()
            membership_id = membership.id

        self._set_cookie(credential)
        for path, data in (
            (
                "/organization/members/role",
                {"membership_id": str(membership_id), "role": "admin"},
            ),
            (
                "/organization/members/remove",
                {"membership_id": str(membership_id)},
            ),
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.post(path, data=data).status_code, 400)

        changed = self.client.post(
            "/organization/members/role",
            data={
                "csrf_token": self._csrf_token(credential),
                "membership_id": str(membership_id),
                "role": "admin",
            },
        )
        self.assertEqual(changed.status_code, 200)
        self.assertIn("Member role updated.", changed.get_data(as_text=True))

        removed = self.client.post(
            "/organization/members/remove",
            data={
                "csrf_token": self._csrf_token(credential),
                "membership_id": str(membership_id),
            },
        )
        self.assertEqual(removed.status_code, 200)
        self.assertIn("Member removed.", removed.get_data(as_text=True))
        with self.Session.begin() as session:
            stored = session.get(OrganizationMembership, membership_id)
            self.assertEqual(stored.status, "inactive")
            self.assertIsNotNone(stored.deactivated_at)

    def test_member_actions_use_labelled_dialogs_and_safe_initial_focus(self):
        from roster_scout.models import Account, OrganizationMembership

        credential, organization_id, _ = self._credential(
            email="admin@example.edu"
        )
        with self.Session.begin() as session:
            second_admin = Account(
                normalized_email="second-admin@example.edu",
                email_verified_at=self.now,
                status="active",
            )
            session.add(second_admin)
            session.flush()
            session.add(OrganizationMembership(
                organization_id=organization_id,
                account_id=second_admin.id,
                role="admin",
                status="active",
            ))

        self._set_cookie(credential)
        body = self.client.get("/organization/members").get_data(as_text=True)

        self.assertIn("data-modal", body)
        self.assertIn('aria-labelledby="role-dialog-', body)
        self.assertIn('aria-describedby="role-dialog-', body)
        self.assertIn("Change to Member", body)
        self.assertIn("Keep admin", body)
        self.assertIn("Keep member", body)
        self.assertIn("autofocus", body)
        self.assertNotIn('<details class="confirmation">', body)

    def test_member_direct_request_gets_fixed_permission_page_without_data(self):
        credential, organization_id, _ = self._credential(
            email="member@example.edu", role="member"
        )
        with self.Session.begin() as session:
            from roster_scout.models import OrganizationInvitation

            invitation = OrganizationInvitation(
                organization_id=organization_id,
                normalized_email="private-invite@example.edu",
                role="member",
                status="pending",
                expires_at=self.now + timedelta(days=7),
            )
            session.add(invitation)
        self._set_cookie(credential)
        response = self.client.get("/organization/members")
        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 403)
        self.assertIn("You don’t have permission to manage members.", body)
        self.assertNotIn("private-invite@example.edu", body)
        self.assertNotIn(str(organization_id), body)

    def test_invite_and_revoke_require_csrf_and_server_derived_admin(self):
        credential, _, _ = self._credential(email="admin@example.edu")
        self._set_cookie(credential)
        missing_csrf = self.client.post(
            "/organization/invitations",
            data={"email": "coach@example.edu", "role": "member"},
        )
        self.assertEqual(missing_csrf.status_code, 400)

        created = self.client.post(
            "/organization/invitations",
            data={
                "csrf_token": self._csrf_token(credential),
                "email": "coach@example.edu",
                "role": "member",
            },
        )
        self.assertEqual(created.status_code, 201)
        body = created.get_data(as_text=True)
        self.assertIn("Invitation sent to coach@example.edu", body)
        self.assertIn("Invited by", body)
        self.assertIn("Revoke the invitation to coach@example.edu?", body)
        self.assertIn("Keep invitation", body)
        self.assertNotIn("provider-invite", body)

        from roster_scout.models import OrganizationInvitation

        with self.Session.begin() as session:
            invitation = session.scalar(select(OrganizationInvitation))
            invitation_id = invitation.id
        revoked = self.client.post(
            f"/organization/invitations/{invitation_id}/revoke",
            data={"csrf_token": self._csrf_token(credential)},
        )
        self.assertEqual(revoked.status_code, 200)
        self.assertIn("Invitation revoked.", revoked.get_data(as_text=True))

    def test_invalid_form_focuses_fixed_summary_without_echoing_secrets(self):
        credential, _, _ = self._credential(email="admin@example.edu")
        self._set_cookie(credential)
        secret = "provider-token-do-not-log"
        response = self.client.post(
            "/organization/invitations",
            data={
                "csrf_token": self._csrf_token(credential),
                "email": secret,
                "role": "owner",
            },
        )
        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 400)
        self.assertIn('id="form-errors"', body)
        self.assertIn('tabindex="-1"', body)
        self.assertIn("Check the invitation details and try again.", body)
        self.assertNotIn(secret, body)

    def test_acceptance_uses_account_context_and_fixed_outcomes(self):
        from roster_scout.models import OrganizationInvitation, OrganizationMembership
        from roster_scout.organizations import create_invitation

        admin_credential, organization_id, admin_id = self._credential(
            email="admin@example.edu"
        )
        invitee_credential, _, invitee_id = self._credential(
            email="coach@example.edu",
            role="member",
            organization_name="Unrelated Baseball",
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

        wrong_credential, _, _ = self._credential(
            email="other@example.edu",
            organization_name="Other Baseball",
        )
        self._set_cookie(wrong_credential)
        mismatch = self.client.post(
            f"/invitations/{invitation_id}/accept",
            data={"csrf_token": self._csrf_token(wrong_credential)},
        )
        mismatch_body = mismatch.get_data(as_text=True)
        self.assertEqual(mismatch.status_code, 409)
        self.assertIn("This invitation can’t be accepted", mismatch_body)
        self.assertIn("exact email address", mismatch_body)
        self.assertNotIn("coach@example.edu", mismatch_body)
        self.assertNotIn("Example Baseball", mismatch_body)

        self.client = self.app_module.app.test_client()
        self._set_cookie(invitee_credential)
        accepted = self.client.post(
            f"/invitations/{invitation_id}/accept",
            data={"csrf_token": self._csrf_token(invitee_credential)},
        )
        body = accepted.get_data(as_text=True)
        self.assertEqual(accepted.status_code, 200)
        self.assertIn("You joined Example Baseball", body)
        self.assertIn("Open reports", body)
        with self.Session.begin() as session:
            self.assertIsNotNone(session.scalar(select(OrganizationMembership).where(
                OrganizationMembership.organization_id == organization_id,
                OrganizationMembership.account_id == invitee_id,
            )))

        self.client = self.app_module.app.test_client()
        self._set_cookie(wrong_credential)
        unavailable = self.client.post(
            f"/invitations/{invitation_id}/accept",
            data={"csrf_token": self._csrf_token(wrong_credential)},
        )
        unavailable_body = unavailable.get_data(as_text=True)
        self.assertEqual(unavailable.status_code, 409)
        self.assertIn("This invitation is no longer available", unavailable_body)
        self.assertNotIn("coach@example.edu", unavailable_body)
        self.assertNotIn("Example Baseball", unavailable_body)

    def test_provider_failure_keeps_pending_invite_and_returns_fixed_copy(self):
        from roster_scout.identity import FakeIdentityProvider
        from roster_scout.models import OrganizationInvitation

        credential, _, _ = self._credential(email="admin@example.edu")
        self.app_module.app.extensions["roster_scout_identity_provider"] = (
            FakeIdentityProvider(invitation_failure=RuntimeError("provider-secret"))
        )
        self._set_cookie(credential)
        response = self.client.post(
            "/organization/invitations",
            data={
                "csrf_token": self._csrf_token(credential),
                "email": "coach@example.edu",
                "role": "member",
            },
        )
        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 503)
        self.assertIn("Invitation sign-in is temporarily unavailable", body)
        self.assertIn("The invitation has not been used.", body)
        self.assertNotIn("provider-secret", body)
        with self.Session.begin() as session:
            invitation = session.scalar(select(OrganizationInvitation))
            self.assertEqual(invitation.status, "pending")
            self.assertIsNone(invitation.provider_invitation_id)

        provider = FakeIdentityProvider()
        self.app_module.app.extensions["roster_scout_identity_provider"] = provider
        retry = self.client.post(
            "/organization/invitations",
            data={
                "csrf_token": self._csrf_token(credential),
                "email": "coach@example.edu",
                "role": "member",
            },
        )
        self.assertEqual(retry.status_code, 201)
        self.assertEqual(len(provider.invitation_calls), 1)
        with self.Session() as session:
            invitations = list(session.scalars(select(OrganizationInvitation)))
        self.assertEqual(len(invitations), 1)
        self.assertIsNotNone(invitations[0].provider_invitation_id)

    def test_no_public_organization_create_route_exists(self):
        routes = {rule.rule for rule in self.app_module.app.url_map.iter_rules()}
        self.assertNotIn("/organizations", routes)
        self.assertNotIn("/organization/create", routes)
