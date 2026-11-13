"""Phase 2 identity, account-linking, and server-session contracts."""

import hashlib
import logging
import os
import unittest
import uuid
from base64 import urlsafe_b64encode
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from tests.db_support import clean_database, require_test_database_url, upgrade_database


class _PostgreSQLIdentityCase(unittest.TestCase):
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


class PasswordlessBoundaryTests(_PostgreSQLIdentityCase):
    @staticmethod
    def _verified_identity(**overrides):
        from roster_scout.identity import VerifiedIdentity

        values = {
            "issuer": "https://identity.example.test",
            "subject": "browser-subject-1",
            "provider_user_id": "browser-user-1",
            "email": "coach@example.edu",
            "email_verified": True,
            "linking_approved": False,
            "sealed_session": "sealed-browser-session",
        }
        values.update(overrides)
        return VerifiedIdentity(**values)

    def _auth_client(self, provider):
        import app as app_module

        app_module.app.config.update(TESTING=True)
        app_module.app.extensions["roster_scout_identity_provider"] = provider
        app_module.app.extensions["roster_scout_auth_session_factory"] = self.Session
        return app_module.app.test_client()

    def test_login_page_hands_off_without_local_credentials(self):
        from roster_scout.identity import FakeIdentityProvider

        response = self._auth_client(FakeIdentityProvider()).get("/login")

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("Sign in to Roster Scout", body)
        self.assertIn(
            "Use your work email to access your organization’s opponent reports.",
            body,
        )
        self.assertIn("Continue to sign in", body)
        self.assertIn(
            "Passwordless email, Google, Microsoft, and your organization’s SSO may be available.",
            body,
        )
        self.assertIn('href="#main-content"', body)
        self.assertNotIn('type="password"', body)
        self.assertNotIn('type="email"', body)
        self.assertNotIn('name="email"', body)

    def test_start_creates_state_and_preserves_only_allowlisted_return_path(self):
        from roster_scout.identity import FakeIdentityProvider
        from roster_scout.models import AuthTransaction

        provider = FakeIdentityProvider()
        client = self._auth_client(provider)
        response = client.get("/auth/start?return_to=/reports")

        self.assertEqual(response.status_code, 302)
        query = parse_qs(urlsplit(response.headers["Location"]).query)
        self.assertEqual(set(query), {"state"})
        state = query["state"][0]
        self.assertGreaterEqual(len(state), 32)
        with self.Session.begin() as session:
            row = session.scalar(select(AuthTransaction))
            self.assertEqual(row.return_path, "/reports")
            self.assertNotEqual(row.state_digest, state)

        unsafe = client.get("/auth/start?return_to=//attacker.example/path")
        self.assertEqual(unsafe.status_code, 400)
        unsafe_body = unsafe.get_data(as_text=True)
        self.assertIn("We couldn’t sign you in", unsafe_body)
        self.assertNotIn("attacker.example", unsafe_body)

    def test_auth_start_and_unknown_callbacks_are_shared_and_bounded(self):
        from roster_scout.identity import FakeIdentityProvider
        from roster_scout.models import (
            AuthRequestLimit,
            AuthTransaction,
            SecurityAuditEvent,
        )

        client = self._auth_client(FakeIdentityProvider())
        for _ in range(10):
            self.assertEqual(client.get("/auth/start").status_code, 302)
        limited_start = client.get("/auth/start")
        self.assertEqual(limited_start.status_code, 429)
        self.assertGreater(int(limited_start.headers["Retry-After"]), 0)
        self.assertIn("We couldn’t sign you in", limited_start.get_data(as_text=True))

        for index in range(20):
            response = client.get(
                "/auth/callback",
                query_string={"state": f"unknown-state-{index}", "code": "noise"},
            )
            self.assertEqual(response.status_code, 400)
        limited_callback = client.get(
            "/auth/callback",
            query_string={"state": "unknown-state-limited", "code": "noise"},
        )
        self.assertEqual(limited_callback.status_code, 429)
        self.assertGreater(int(limited_callback.headers["Retry-After"]), 0)

        with self.Session() as session:
            self.assertEqual(session.query(AuthTransaction).count(), 10)
            self.assertEqual(session.query(AuthRequestLimit).count(), 2)
            self.assertEqual(session.query(SecurityAuditEvent).count(), 0)

    def test_auth_start_prunes_only_a_bounded_batch_of_stale_transactions(self):
        from roster_scout.identity import FakeIdentityProvider
        from roster_scout.models import AuthTransaction

        stale_created = self.now - timedelta(hours=3)
        stale_expires = self.now - timedelta(hours=2)
        with self.Session.begin() as session:
            session.add_all([
                AuthTransaction(
                    state_digest=hashlib.sha256(
                        f"stale-state-{index}".encode("ascii")
                    ).hexdigest(),
                    return_path="/reports",
                    created_at=stale_created,
                    expires_at=stale_expires,
                )
                for index in range(105)
            ])

        client = self._auth_client(FakeIdentityProvider())
        self.assertEqual(client.get("/auth/start").status_code, 302)
        with self.Session() as session:
            stale_count = session.query(AuthTransaction).filter(
                AuthTransaction.expires_at == stale_expires
            ).count()
            total_count = session.query(AuthTransaction).count()
        self.assertEqual(stale_count, 5)
        self.assertEqual(total_count, 6)

    def test_callback_links_account_sets_secure_cookie_and_replay_is_fixed(self):
        from roster_scout.identity import FakeIdentityProvider
        from roster_scout.models import Account, AccountSession, AuthTransaction
        from roster_scout.sessions import SESSION_COOKIE_NAME

        provider = FakeIdentityProvider(
            identities={"browser-code-secret": self._verified_identity()}
        )
        client = self._auth_client(provider)
        started = client.get("/auth/start")
        state = parse_qs(urlsplit(started.headers["Location"]).query)["state"][0]

        callback = client.get(
            "/auth/callback",
            query_string={"state": state, "code": "browser-code-secret"},
        )

        self.assertEqual(callback.status_code, 302)
        self.assertEqual(
            urlsplit(callback.headers["Location"]).path,
            "/organizations/none",
        )
        cookie = callback.headers["Set-Cookie"]
        self.assertIn(f"{SESSION_COOKIE_NAME}=", cookie)
        self.assertIn("Secure", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)
        self.assertIn("Path=/", cookie)
        self.assertNotIn("Domain=", cookie)
        with self.Session.begin() as session:
            self.assertEqual(session.query(Account).count(), 1)
            self.assertEqual(session.query(AccountSession).count(), 1)
            transaction = session.scalar(select(AuthTransaction))
            self.assertIsNotNone(transaction.consumed_at)

        replay = client.get(
            "/auth/callback",
            query_string={"state": state, "code": "browser-code-secret"},
        )
        self.assertEqual(replay.status_code, 400)
        replay_body = replay.get_data(as_text=True)
        self.assertIn("We couldn’t sign you in", replay_body)
        self.assertIn("Return to sign in", replay_body)
        self.assertNotIn(state, replay_body)
        self.assertNotIn("browser-code-secret", replay_body)

    def test_provider_failure_consumes_state_and_redacts_callback_material(self):
        from roster_scout.identity import FakeIdentityProvider

        provider_secret = "provider-exception-secret"
        callback_secret = "callback-code-secret"
        client = self._auth_client(
            FakeIdentityProvider(failure=RuntimeError(provider_secret))
        )
        started = client.get("/auth/start")
        state = parse_qs(urlsplit(started.headers["Location"]).query)["state"][0]

        app_logger = logging.getLogger("app")
        logger_was_disabled = app_logger.disabled
        app_logger.disabled = False
        try:
            with self.assertLogs(app_logger, level="WARNING") as captured:
                failed = client.get(
                    "/auth/callback",
                    query_string={"state": state, "code": callback_secret},
                )
        finally:
            app_logger.disabled = logger_was_disabled

        self.assertEqual(failed.status_code, 503)
        rendered = failed.get_data(as_text=True) + " ".join(captured.output)
        self.assertIn("We couldn’t sign you in", rendered)
        self.assertNotIn(provider_secret, rendered)
        self.assertNotIn(callback_secret, rendered)
        self.assertNotIn(state, rendered)
        self.assertEqual(
            captured.output,
            [
                "WARNING:app:identity_provider_exchange_failed",
                "WARNING:app:identity_callback_failed",
            ],
        )

        replay = client.get(
            "/auth/callback",
            query_string={"state": state, "code": callback_secret},
        )
        self.assertEqual(replay.status_code, 400)

    def test_authorization_state_is_hashed_bounded_and_consumed_once(self):
        from roster_scout.identity import (
            IdentityFailure,
            begin_auth_transaction,
            consume_auth_transaction,
        )
        from roster_scout.models import AuthTransaction

        with self.Session.begin() as session:
            started = begin_auth_transaction(
                session,
                return_path="/reports",
                now=self.now,
            )
            self.assertEqual(started.return_path, "/reports")
            self.assertEqual(started.expires_at, self.now + timedelta(minutes=10))
            row = session.scalar(select(AuthTransaction))
            self.assertIsNotNone(row)
            self.assertNotEqual(row.state_digest, started.state)
            self.assertEqual(
                row.state_digest,
                hashlib.sha256(started.state.encode("ascii")).hexdigest(),
            )

        with self.Session.begin() as session:
            consumed = consume_auth_transaction(
                session,
                state=started.state,
                now=self.now + timedelta(minutes=1),
            )
            self.assertEqual(consumed.return_path, "/reports")

        for state in (started.state, "not-the-original-state"):
            with self.subTest(state=state):
                with self.Session.begin() as session:
                    with self.assertRaises(IdentityFailure) as raised:
                        consume_auth_transaction(
                            session,
                            state=state,
                            now=self.now + timedelta(minutes=2),
                        )
                self.assertEqual(raised.exception.category, "invalid_auth_state")
                self.assertEqual(str(raised.exception), "invalid_auth_state")

    def test_expired_state_and_provider_failure_are_fixed_and_redacted(self):
        from roster_scout.identity import (
            FakeIdentityProvider,
            IdentityFailure,
            begin_auth_transaction,
            exchange_authorization_code,
            consume_auth_transaction,
        )

        with self.Session.begin() as session:
            started = begin_auth_transaction(session, now=self.now)
        with self.Session.begin() as session:
            with self.assertRaises(IdentityFailure) as expired:
                consume_auth_transaction(
                    session,
                    state=started.state,
                    now=self.now + timedelta(minutes=11),
                )
        self.assertEqual(expired.exception.category, "invalid_auth_state")

        provider_secret = "provider-exception-secret"
        code_secret = "authorization-code-secret"
        provider = FakeIdentityProvider(failure=RuntimeError(provider_secret))
        logger = logging.getLogger("tests.identity.redaction")
        with self.assertLogs(logger, level="WARNING") as captured:
            with self.assertRaises(IdentityFailure) as unavailable:
                exchange_authorization_code(
                    provider,
                    code=code_secret,
                    logger=logger,
                )
        self.assertEqual(unavailable.exception.category, "identity_unavailable")
        rendered = " ".join(captured.output) + " " + str(unavailable.exception)
        self.assertNotIn(provider_secret, rendered)
        self.assertNotIn(code_secret, rendered)
        self.assertEqual(captured.output, [
            "WARNING:tests.identity.redaction:identity_provider_exchange_failed"
        ])

    def test_fake_provider_is_deterministic_and_protocol_neutral(self):
        from roster_scout.identity import (
            FakeIdentityProvider,
            IdentityProvider,
            VerifiedIdentity,
            exchange_authorization_code,
        )

        identity = VerifiedIdentity(
            issuer="https://identity.example.test",
            subject="subject-1",
            provider_user_id="user-1",
            email="Coach@Example.EDU",
            email_verified=True,
            linking_approved=False,
            sealed_session="sealed-provider-session",
        )
        provider = FakeIdentityProvider(
            identities={"one-time-code": identity},
            authorization_base_url="https://identity.example.test/authorize",
        )
        self.assertIsInstance(provider, IdentityProvider)
        self.assertEqual(
            provider.authorization_url(state="opaque-state"),
            "https://identity.example.test/authorize?state=opaque-state",
        )
        self.assertEqual(
            exchange_authorization_code(provider, code="one-time-code"),
            identity,
        )


class ExternalIdentityLinkingTests(_PostgreSQLIdentityCase):
    @staticmethod
    def _identity(**overrides):
        from roster_scout.identity import VerifiedIdentity

        values = {
            "issuer": "https://identity.example.test",
            "subject": "subject-1",
            "provider_user_id": "user-1",
            "email": " Coach@Example.EDU ",
            "email_verified": True,
            "linking_approved": False,
            "sealed_session": "sealed-session",
        }
        values.update(overrides)
        return VerifiedIdentity(**values)

    def test_verified_issuer_subject_is_idempotently_linked(self):
        from roster_scout.accounts import link_verified_identity
        from roster_scout.models import Account, ExternalIdentity

        with self.Session.begin() as session:
            first = link_verified_identity(session, self._identity(), now=self.now)
            first_id = first.id
        with self.Session.begin() as session:
            second = link_verified_identity(session, self._identity(), now=self.now)
            self.assertEqual(second.id, first_id)
            self.assertEqual(second.normalized_email, "coach@example.edu")
            self.assertEqual(session.query(Account).count(), 1)
            self.assertEqual(session.query(ExternalIdentity).count(), 1)

    def test_unverified_email_cannot_create_or_claim_an_account(self):
        from roster_scout.accounts import AccountLinkFailure, link_verified_identity
        from roster_scout.models import Account

        with self.Session.begin() as session:
            session.add(Account(
                normalized_email="coach@example.edu",
                email_verified_at=self.now,
                status="active",
            ))

        with self.Session.begin() as session:
            with self.assertRaises(AccountLinkFailure) as raised:
                link_verified_identity(
                    session,
                    self._identity(email_verified=False),
                    now=self.now,
                )
        self.assertEqual(raised.exception.category, "identity_not_verified")
        self.assertNotIn("coach@example.edu", str(raised.exception))

    def test_email_match_requires_provider_approved_linking(self):
        from roster_scout.accounts import AccountLinkFailure, link_verified_identity
        from roster_scout.models import Account, ExternalIdentity

        with self.Session.begin() as session:
            account = Account(
                normalized_email="coach@example.edu",
                email_verified_at=self.now,
                status="active",
            )
            session.add(account)
            session.flush()
            account_id = account.id

        with self.Session.begin() as session:
            with self.assertRaises(AccountLinkFailure) as raised:
                link_verified_identity(session, self._identity(), now=self.now)
        self.assertEqual(raised.exception.category, "identity_link_required")

        with self.Session.begin() as session:
            linked = link_verified_identity(
                session,
                self._identity(linking_approved=True),
                now=self.now,
            )
            self.assertEqual(linked.id, account_id)
            self.assertEqual(session.query(ExternalIdentity).count(), 1)

    def test_subject_and_provider_user_identifiers_cannot_cross_accounts(self):
        from roster_scout.accounts import AccountLinkFailure, link_verified_identity

        with self.Session.begin() as session:
            original = link_verified_identity(session, self._identity(), now=self.now)
            original_id = original.id

        with self.Session.begin() as session:
            with self.assertRaises(AccountLinkFailure) as raised:
                link_verified_identity(
                    session,
                    self._identity(
                        subject="subject-2",
                        provider_user_id="user-1",
                        email="different@example.edu",
                    ),
                    now=self.now,
                )
        self.assertEqual(raised.exception.category, "identity_conflict")

        with self.Session.begin() as session:
            resolved = link_verified_identity(session, self._identity(), now=self.now)
            self.assertEqual(resolved.id, original_id)

    def test_established_subject_moves_canonical_verified_email_atomically(self):
        from roster_scout.accounts import AccountLinkFailure, link_verified_identity
        from roster_scout.models import Account, ExternalIdentity

        with self.Session.begin() as session:
            original = link_verified_identity(session, self._identity(), now=self.now)
            account_id = original.id
        changed_at = self.now + timedelta(minutes=1)
        with self.Session.begin() as session:
            changed = link_verified_identity(
                session,
                self._identity(email="new-coach@example.edu"),
                now=changed_at,
            )
            self.assertEqual(changed.id, account_id)
            self.assertEqual(changed.normalized_email, "new-coach@example.edu")
            self.assertEqual(changed.email_verified_at, changed_at)
            external = session.scalar(select(ExternalIdentity))
            self.assertEqual(external.last_verified_email, "new-coach@example.edu")

        with self.Session.begin() as session:
            session.add(Account(
                normalized_email="collision@example.edu",
                email_verified_at=self.now,
                status="active",
            ))
        with self.Session.begin() as session:
            with self.assertRaises(AccountLinkFailure) as collision:
                link_verified_identity(
                    session,
                    self._identity(email="collision@example.edu"),
                    now=changed_at + timedelta(minutes=1),
                )
        self.assertEqual(collision.exception.category, "identity_link_required")


class ServerSessionLifecycleTests(_PostgreSQLIdentityCase):
    def _account_id(self):
        from roster_scout.models import Account

        with self.Session.begin() as session:
            account = Account(
                normalized_email="coach@example.edu",
                email_verified_at=self.now,
                status="active",
            )
            session.add(account)
            session.flush()
            return account.id

    def test_session_stores_only_digest_and_exact_expiries(self):
        from roster_scout.models import AccountSession
        from roster_scout.sessions import create_account_session, load_account_session

        account_id = self._account_id()
        with self.Session.begin() as session:
            credential = create_account_session(
                session,
                account_id=account_id,
                sealed_provider_state="sealed-session-secret",
                now=self.now,
            )
            row = session.scalar(select(AccountSession))
            lookup, verifier = credential.cookie_value.split(".", 1)
            self.assertEqual(str(row.lookup_id), lookup)
            self.assertNotIn(verifier, row.verifier_digest)
            self.assertEqual(
                row.verifier_digest,
                hashlib.sha256(verifier.encode("ascii")).hexdigest(),
            )
            self.assertEqual(row.idle_expires_at, self.now + timedelta(hours=12))
            self.assertEqual(row.absolute_expires_at, self.now + timedelta(days=7))
            self.assertEqual(row.sealed_provider_state, "sealed-session-secret")
            self.assertNotIn(verifier, row.sealed_provider_state)

        with self.Session.begin() as session:
            loaded = load_account_session(
                session,
                cookie_value=credential.cookie_value,
                now=self.now + timedelta(minutes=5),
            )
            self.assertEqual(loaded.account_id, account_id)

    def test_callback_rotation_revokes_pre_auth_session(self):
        from roster_scout.models import AccountSession
        from roster_scout.sessions import create_account_session, load_account_session

        account_id = self._account_id()
        with self.Session.begin() as session:
            pre_auth = create_account_session(
                session,
                account_id=account_id,
                sealed_provider_state="pre-auth-sealed",
                now=self.now,
            )
        with self.Session.begin() as session:
            authenticated = create_account_session(
                session,
                account_id=account_id,
                sealed_provider_state="authenticated-sealed",
                rotate_cookie_value=pre_auth.cookie_value,
                now=self.now + timedelta(minutes=1),
            )
            rows = list(session.scalars(select(AccountSession)))
            self.assertEqual(len(rows), 2)
            self.assertEqual(sum(row.revoked_at is not None for row in rows), 1)

        with self.Session.begin() as session:
            self.assertIsNone(load_account_session(
                session,
                cookie_value=pre_auth.cookie_value,
                now=self.now + timedelta(minutes=2),
            ))
            self.assertIsNotNone(load_account_session(
                session,
                cookie_value=authenticated.cookie_value,
                now=self.now + timedelta(minutes=2),
            ))

    def test_idle_absolute_and_invalid_verifier_fail_closed(self):
        from roster_scout.sessions import create_account_session, load_account_session

        account_id = self._account_id()
        with self.Session.begin() as session:
            credential = create_account_session(
                session,
                account_id=account_id,
                sealed_provider_state="sealed",
                now=self.now,
            )

        lookup, _ = credential.cookie_value.split(".", 1)
        for cookie, now in (
            (f"{lookup}.wrong-verifier", self.now + timedelta(minutes=1)),
            (credential.cookie_value, self.now + timedelta(hours=12, seconds=1)),
            (credential.cookie_value, self.now + timedelta(days=7, seconds=1)),
            ("malformed-cookie", self.now + timedelta(minutes=1)),
        ):
            with self.subTest(cookie=cookie, now=now):
                with self.Session.begin() as session:
                    self.assertIsNone(load_account_session(
                        session,
                        cookie_value=cookie,
                        now=now,
                    ))

    def test_cookie_contract_is_host_only_secure_and_http_only(self):
        from roster_scout.sessions import SESSION_COOKIE_NAME, session_cookie_options

        self.assertEqual(SESSION_COOKIE_NAME, "__Host-rs_session")
        self.assertEqual(
            session_cookie_options(),
            {
                "secure": True,
                "httponly": True,
                "samesite": "Lax",
                "path": "/",
            },
        )
        self.assertNotIn("domain", session_cookie_options())

    def test_raw_secrets_are_absent_from_service_results_and_logs(self):
        from roster_scout.sessions import create_account_session

        account_id = self._account_id()
        sealed = "sealed-provider-secret"
        logger = logging.getLogger("tests.sessions.redaction")
        with self.assertNoLogs(logger):
            with self.Session.begin() as session:
                credential = create_account_session(
                    session,
                    account_id=account_id,
                    sealed_provider_state=sealed,
                    now=self.now,
                )
        rendered = repr(credential)
        self.assertNotIn(sealed, rendered)
        self.assertNotIn("coach@example.edu", rendered)

    def test_refresh_success_rotates_provider_state_and_transient_failure_preserves_it(self):
        from roster_scout.identity import FakeIdentityProvider, RefreshResult
        from roster_scout.models import AccountSession
        from roster_scout.sessions import create_account_session, refresh_account_session

        account_id = self._account_id()
        with self.Session.begin() as session:
            credential = create_account_session(
                session,
                account_id=account_id,
                sealed_provider_state="sealed-original-secret",
                now=self.now,
            )

        refreshed_provider = FakeIdentityProvider(
            refresh_result=RefreshResult.refreshed("sealed-rotated-secret")
        )
        with self.Session.begin() as session:
            outcome = refresh_account_session(
                session,
                provider=refreshed_provider,
                cookie_value=credential.cookie_value,
                now=self.now + timedelta(minutes=5),
            )
            self.assertEqual(outcome.status, "refreshed")
            row = session.scalar(select(AccountSession))
            self.assertEqual(row.sealed_provider_state, "sealed-rotated-secret")

        transient_provider = FakeIdentityProvider(
            refresh_result=RefreshResult.retryable_failure()
        )
        with self.Session.begin() as session:
            outcome = refresh_account_session(
                session,
                provider=transient_provider,
                cookie_value=credential.cookie_value,
                now=self.now + timedelta(minutes=10),
            )
            self.assertEqual(outcome.status, "preserved")
            row = session.scalar(select(AccountSession))
            self.assertIsNone(row.revoked_at)
            self.assertEqual(row.sealed_provider_state, "sealed-rotated-secret")

    def test_terminal_refresh_revokes_and_logout_is_local_first(self):
        from roster_scout.identity import FakeIdentityProvider, RefreshResult
        from roster_scout.models import AccountSession
        from roster_scout.sessions import (
            complete_provider_logout,
            create_account_session,
            prepare_account_session_logout,
            refresh_account_session,
        )

        account_id = self._account_id()
        with self.Session.begin() as session:
            terminal_credential = create_account_session(
                session,
                account_id=account_id,
                sealed_provider_state="sealed-terminal-secret",
                now=self.now,
            )
        with self.Session.begin() as session:
            outcome = refresh_account_session(
                session,
                provider=FakeIdentityProvider(
                    refresh_result=RefreshResult.terminal_failure()
                ),
                cookie_value=terminal_credential.cookie_value,
                now=self.now + timedelta(minutes=1),
            )
            self.assertEqual(outcome.status, "revoked")
            self.assertIsNotNone(session.scalar(select(AccountSession)).revoked_at)

        with self.Session.begin() as session:
            logout_credential = create_account_session(
                session,
                account_id=account_id,
                sealed_provider_state="sealed-logout-secret",
                now=self.now + timedelta(minutes=2),
            )
        provider = FakeIdentityProvider(logout_url="https://identity.example.test/logout")
        with self.Session.begin() as session:
            prepared = prepare_account_session_logout(
                session,
                cookie_value=logout_credential.cookie_value,
                now=self.now + timedelta(minutes=3),
            )
            self.assertEqual(provider.logout_calls, [])
            row = session.scalar(
                select(AccountSession).where(
                    AccountSession.lookup_id
                    == uuid.UUID(logout_credential.cookie_value.split(".", 1)[0])
                )
            )
            self.assertIsNotNone(row.revoked_at)

        self.assertEqual(
            complete_provider_logout(provider, prepared),
            "https://identity.example.test/logout",
        )
        self.assertEqual(provider.logout_calls, ["sealed-logout-secret"])

    def test_request_refresh_uses_two_transactions_and_terminal_transient_semantics(self):
        from roster_scout.identity import FakeIdentityProvider, RefreshResult
        from roster_scout.models import AccountSession
        from roster_scout.sessions import (
            create_account_session,
            refresh_account_session_for_request,
        )

        account_id = self._account_id()
        with self.Session.begin() as session:
            preserved_credential = create_account_session(
                session,
                account_id=account_id,
                sealed_provider_state="sealed-preserved",
                now=self.now,
            )
        transient = FakeIdentityProvider(
            refresh_result=RefreshResult.retryable_failure()
        )
        preserved = refresh_account_session_for_request(
            self.Session,
            provider=transient,
            cookie_value=preserved_credential.cookie_value,
            now=self.now + timedelta(minutes=6),
        )
        self.assertEqual(preserved.status, "preserved")
        self.assertIsNotNone(preserved.session)

        with self.Session.begin() as session:
            terminal_credential = create_account_session(
                session,
                account_id=account_id,
                sealed_provider_state="sealed-terminal",
                now=self.now,
            )
        terminal = refresh_account_session_for_request(
            self.Session,
            provider=FakeIdentityProvider(
                refresh_result=RefreshResult.terminal_failure()
            ),
            cookie_value=terminal_credential.cookie_value,
            now=self.now + timedelta(minutes=6),
        )
        self.assertEqual(terminal.status, "revoked")
        self.assertIsNone(terminal.session)
        with self.Session() as session:
            row = session.scalar(select(AccountSession).where(
                AccountSession.lookup_id
                == uuid.UUID(terminal_credential.cookie_value.split(".", 1)[0])
            ))
            self.assertIsNotNone(row.revoked_at)


class AuthGateRouteMatrixTests(_PostgreSQLIdentityCase):
    """Complete Flask composition contract for the Phase 2 auth boundary."""

    @staticmethod
    def _verified_identity(email="coach@example.edu"):
        from roster_scout.identity import VerifiedIdentity

        return VerifiedIdentity(
            issuer="https://identity.example.test",
            subject=f"subject-{email}",
            provider_user_id=f"user-{email}",
            email=email,
            email_verified=True,
            linking_approved=True,
            sealed_session="sealed-browser-session",
        )

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

    def _account_with_memberships(self, count):
        from roster_scout.models import Account, Organization, OrganizationMembership

        with self.Session.begin() as session:
            account = Account(
                normalized_email="coach@example.edu",
                email_verified_at=self.now,
                status="active",
            )
            session.add(account)
            session.flush()
            organizations = []
            for index in range(count):
                organization = Organization(name=f"University {index + 1}")
                session.add(organization)
                session.flush()
                session.add(OrganizationMembership(
                    organization_id=organization.id,
                    account_id=account.id,
                    role="admin" if index == 0 else "member",
                    status="active",
                ))
                organizations.append(organization.id)
            session.flush()
            return account.id, organizations

    def _credential(self):
        from roster_scout.sessions import create_account_session

        account_id, organizations = self._account_with_memberships(1)
        with self.Session.begin() as session:
            credential = create_account_session(
                session,
                account_id=account_id,
                active_organization_id=organizations[0],
                sealed_provider_state="sealed-local-session",
                now=self.now,
            )
        return credential

    def _set_cookie(self, credential):
        from roster_scout.sessions import SESSION_COOKIE_NAME

        self.client.set_cookie(SESSION_COOKIE_NAME, credential.cookie_value)

    def _csrf_token(self, credential):
        from itsdangerous import URLSafeTimedSerializer

        return URLSafeTimedSerializer(
            self.app_module.app.secret_key,
            salt="wtf-csrf-token",
        ).dumps(credential.csrf_secret)

    def test_every_current_route_is_explicitly_public_account_only_or_protected(self):
        routes = {
            (rule.rule, tuple(sorted(rule.methods - {"HEAD", "OPTIONS"})))
            for rule in self.app_module.app.url_map.iter_rules()
        }
        self.assertEqual(
            routes,
            {
                ("/", ("GET",)),
                ("/api/downloads/ack", ("POST",)),
                ("/api/team-logo/<team_id>", ("GET",)),
                ("/api/teams", ("GET",)),
                ("/auth/callback", ("GET",)),
                ("/auth/error", ("GET",)),
                ("/auth/start", ("GET",)),
                ("/demo", ("GET",)),
                ("/healthz", ("GET",)),
                ("/login", ("GET",)),
                ("/logout", ("POST",)),
                ("/invitations/<uuid:invitation_id>/accept", ("POST",)),
                ("/organization/invitations", ("POST",)),
                ("/organization/invitations/<uuid:invitation_id>/revoke", ("POST",)),
                ("/organization/members", ("GET",)),
                ("/organization/members/remove", ("POST",)),
                ("/organization/members/role", ("POST",)),
                ("/organizations/none", ("GET",)),
                ("/organizations/select", ("GET",)),
                ("/organizations/switch", ("POST",)),
                ("/readyz", ("GET",)),
                ("/reports", ("GET",)),
                ("/reports/<uuid:report_id>", ("GET",)),
                (
                    "/reports/<uuid:report_id>/builds/"
                    "<uuid:failed_job_id>/retry",
                    ("POST",),
                ),
                ("/reports/<uuid:report_id>/refresh", ("GET", "POST")),
                (
                    "/reports/<uuid:report_id>/snapshots/<uuid:snapshot_id>/"
                    "artifacts/<uuid:artifact_id>/download",
                    ("POST",),
                ),
                ("/reports/<uuid:report_id>/status", ("GET",)),
                ("/reports/new", ("GET", "POST")),
                ("/scrape", ("POST",)),
                ("/static/<path:filename>", ("GET",)),
            },
        )
        self.assertEqual(
            self.app_module._PUBLIC_ENDPOINTS,
            frozenset({
                "static",
                "healthz",
                "readyz",
                "demo",
                "acknowledge_workbook_download",
                "auth.login",
                "auth.start",
                "auth.callback",
                "auth.error",
            }),
        )
        self.assertEqual(
            self.app_module._ACCOUNT_ONLY_ENDPOINTS,
            frozenset({
                "auth.no_membership",
                "auth.logout",
                "organization.accept",
                "organization.select_organization",
                "organization.switch_organization",
            }),
        )

    def test_unauthenticated_product_and_legacy_routes_fail_before_view_execution(self):
        html = self.client.get("/")
        self.assertEqual(html.status_code, 302)
        self.assertEqual(urlsplit(html.headers["Location"]).path, "/login")

        for method, path, kwargs in (
            ("get", "/api/teams?division=d1", {}),
            ("get", "/api/team-logo/1", {}),
            ("post", "/scrape", {"json": {"team": "Tampa"}}),
        ):
            with self.subTest(method=method, path=path):
                response = getattr(self.client, method)(path, **kwargs)
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.get_json(), {"error": "authentication_required"})

        acknowledgement = self.client.post(
            "/api/downloads/ack",
            json={},
        )
        self.assertEqual(acknowledgement.status_code, 403)
        self.assertNotEqual(
            acknowledgement.get_json(),
            {"error": "authentication_required"},
        )

        for path in ("/login", "/auth/error", "/demo", "/healthz"):
            with self.subTest(path=path):
                self.assertNotEqual(self.client.get(path).status_code, 302)

    def test_callback_selects_only_current_local_memberships(self):
        from roster_scout.identity import FakeIdentityProvider
        from roster_scout.models import AccountSession

        for membership_count, expected_path in (
            (0, "/organizations/none"),
            (1, "/reports"),
            (2, "/organizations/select"),
        ):
            with self.subTest(membership_count=membership_count):
                clean_database(self.database_url)
                if membership_count:
                    self._account_with_memberships(membership_count)
                provider = FakeIdentityProvider(
                    identities={"browser-code": self._verified_identity()}
                )
                self.app_module.app.extensions[
                    "roster_scout_identity_provider"
                ] = provider
                client = self.app_module.app.test_client()
                started = client.get("/auth/start")
                state = parse_qs(
                    urlsplit(started.headers["Location"]).query
                )["state"][0]
                response = client.get(
                    "/auth/callback",
                    query_string={"state": state, "code": "browser-code"},
                )
                self.assertEqual(response.status_code, 302)
                self.assertEqual(
                    urlsplit(response.headers["Location"]).path,
                    expected_path,
                )
                with self.Session.begin() as session:
                    row = session.scalar(select(AccountSession))
                    self.assertEqual(
                        row.active_organization_id is not None,
                        membership_count == 1,
                    )

    def test_invitation_token_entry_binds_local_invite_before_callback(self):
        from roster_scout.identity import (
            FakeIdentityProvider,
            ProviderInvitationReference,
        )
        from roster_scout.models import (
            AuthTransaction,
            OrganizationIdentityConnection,
            OrganizationInvitation,
            OrganizationMembership,
        )
        from roster_scout.organizations import (
            bootstrap_organization,
            deliver_invitation,
            mirror_organization,
        )

        delivery_provider = FakeIdentityProvider()
        with self.Session.begin() as session:
            organization, invitation = bootstrap_organization(
                session,
                name="Invited Baseball",
                admin_email="invited@example.edu",
                now=self.now,
            )
            organization_id = organization.id
            invitation_id = invitation.id
        connection = mirror_organization(
            self.Session,
            provider=delivery_provider,
            organization_id=organization_id,
            now=self.now,
        )
        deliver_invitation(
            self.Session,
            provider=delivery_provider,
            invitation_id=invitation_id,
            now=self.now,
        )
        with self.Session() as session:
            invitation = session.get(OrganizationInvitation, invitation_id)
            provider_invitation_id = invitation.provider_invitation_id
            local_expires_at = invitation.expires_at

        invitation_token = "signed-provider-invitation-token"
        provider = FakeIdentityProvider(
            identities={
                "invite-code": self._verified_identity("invited@example.edu")
            },
            invitation_references={
                invitation_token: ProviderInvitationReference(
                    provider_invitation_id=provider_invitation_id,
                    provider_organization_id=connection.provider_organization_id,
                    email="invited@example.edu",
                    state="pending",
                    expires_at=local_expires_at,
                )
            },
        )
        self.app_module.app.extensions["roster_scout_identity_provider"] = provider
        started = self.client.get(
            "/auth/start",
            query_string={"invitation_token": invitation_token},
        )
        authorization_query = parse_qs(
            urlsplit(started.headers["Location"]).query
        )
        self.assertEqual(
            authorization_query["invitation_token"],
            [invitation_token],
        )
        self.assertEqual(provider.invitation_resolution_calls, [invitation_token])
        state = authorization_query["state"][0]
        with self.Session() as session:
            transaction = session.scalar(select(AuthTransaction))
            self.assertEqual(transaction.invitation_id, invitation_id)
            self.assertFalse(hasattr(transaction, "invitation_token"))
            self.assertEqual(
                session.scalar(select(OrganizationIdentityConnection)).id,
                connection.id,
            )
        response = self.client.get(
            "/auth/callback",
            query_string={"state": state, "code": "invite-code"},
        )
        self.assertEqual(urlsplit(response.headers["Location"]).path, "/reports")
        with self.Session() as session:
            membership = session.scalar(select(OrganizationMembership).where(
                OrganizationMembership.organization_id == organization_id
            ))
        self.assertIsNotNone(membership)
        self.assertEqual(membership.role, "admin")

    def test_ordinary_provider_org_sign_in_cannot_consume_pending_invite(self):
        from roster_scout.identity import FakeIdentityProvider
        from roster_scout.models import OrganizationInvitation, OrganizationMembership
        from roster_scout.organizations import bootstrap_organization, mirror_organization

        mirror_provider = FakeIdentityProvider()
        with self.Session.begin() as session:
            organization, invitation = bootstrap_organization(
                session,
                name="Pending Invitation Baseball",
                admin_email="pending@example.edu",
                now=self.now,
            )
            organization_id = organization.id
            invitation_id = invitation.id
        connection = mirror_organization(
            self.Session,
            provider=mirror_provider,
            organization_id=organization_id,
            now=self.now,
        )
        identity = self._verified_identity("pending@example.edu")
        identity = type(identity)(
            **{
                **identity.__dict__,
                "provider_organization_id": connection.provider_organization_id,
            }
        )
        self.app_module.app.extensions["roster_scout_identity_provider"] = (
            FakeIdentityProvider(identities={"ordinary-code": identity})
        )
        started = self.client.get("/auth/start")
        state = parse_qs(urlsplit(started.headers["Location"]).query)["state"][0]
        response = self.client.get(
            "/auth/callback",
            query_string={"state": state, "code": "ordinary-code"},
        )
        self.assertEqual(
            urlsplit(response.headers["Location"]).path,
            "/organizations/none",
        )
        with self.Session() as session:
            invitation = session.get(OrganizationInvitation, invitation_id)
            memberships = list(session.scalars(select(OrganizationMembership)))
        self.assertEqual(invitation.status, "pending")
        self.assertEqual(memberships, [])

    def test_request_authorization_refreshes_provider_and_audits_sign_in_outcomes(self):
        from roster_scout.identity import FakeIdentityProvider, RefreshResult
        from roster_scout.models import AccountSession, SecurityAuditEvent

        credential = self._credential()
        with self.Session.begin() as session:
            row = session.scalar(select(AccountSession))
            row.provider_refreshed_at = self.now - timedelta(minutes=10)
        self._set_cookie(credential)
        self.app_module.app.extensions["roster_scout_identity_provider"] = (
            FakeIdentityProvider(refresh_result=RefreshResult.retryable_failure())
        )
        self.assertEqual(self.client.get("/organizations/select").status_code, 200)

        with self.Session.begin() as session:
            row = session.scalar(select(AccountSession))
            row.provider_refreshed_at = self.now - timedelta(minutes=10)
        self.app_module.app.extensions["roster_scout_identity_provider"] = (
            FakeIdentityProvider(refresh_result=RefreshResult.terminal_failure())
        )
        ended = self.client.get("/organizations/select")
        self.assertEqual(urlsplit(ended.headers["Location"]).path, "/login")

        client = self.app_module.app.test_client()
        provider = FakeIdentityProvider(
            identities={"audit-code": self._verified_identity("audit@example.edu")}
        )
        self.app_module.app.extensions["roster_scout_identity_provider"] = provider
        started = client.get("/auth/start")
        state = parse_qs(urlsplit(started.headers["Location"]).query)["state"][0]
        self.assertEqual(client.get(
            "/auth/callback",
            query_string={"state": state, "code": "audit-code"},
        ).status_code, 302)
        self.assertEqual(client.get(
            "/auth/callback",
            query_string={"state": state, "code": "audit-code"},
        ).status_code, 400)
        with self.Session() as session:
            audits = list(session.scalars(select(SecurityAuditEvent).where(
                SecurityAuditEvent.action == "identity.sign_in"
            )))
        self.assertEqual([event.outcome for event in audits], ["succeeded", "failed"])
        self.assertEqual(audits[-1].reason, "invalid_auth_state")

    def test_logout_requires_csrf_and_revokes_locally_when_provider_fails(self):
        from roster_scout.identity import FakeIdentityProvider
        from roster_scout.models import AccountSession

        credential = self._credential()
        provider = FakeIdentityProvider(
            logout_failure=RuntimeError("provider-logout-secret")
        )
        self.app_module.app.extensions["roster_scout_identity_provider"] = provider
        self._set_cookie(credential)

        missing = self.client.post("/logout")
        self.assertEqual(missing.status_code, 400)
        with self.Session.begin() as session:
            self.assertIsNone(session.scalar(select(AccountSession)).revoked_at)

        app_logger = logging.getLogger("app")
        logger_was_disabled = app_logger.disabled
        app_logger.disabled = False
        try:
            with self.assertLogs(app_logger, level="WARNING") as captured:
                response = self.client.post(
                    "/logout",
                    headers={"X-CSRFToken": self._csrf_token(credential)},
                )
        finally:
            app_logger.disabled = logger_was_disabled
        self.assertEqual(response.status_code, 302)
        self.assertEqual(urlsplit(response.headers["Location"]).path, "/login")
        self.assertNotIn("provider-logout-secret", " ".join(captured.output))
        with self.Session.begin() as session:
            self.assertIsNotNone(session.scalar(select(AccountSession)).revoked_at)

    def test_shared_shell_is_accessible_and_contains_one_csrf_logout(self):
        source = os.path.join(
            os.path.dirname(__file__),
            "..",
            "templates",
            "product_base.html",
        )
        with open(source, encoding="utf-8") as handle:
            template = handle.read()
        self.assertIn('href="#main-content"', template)
        self.assertEqual(template.count("<main"), 1)
        self.assertIn("{% block page_title %}", template)
        self.assertIn("csrf_token()", template)
        self.assertIn('action="{{ url_for(\'auth.logout\') }}"', template)
        self.assertNotIn('type="password"', template)
        self.assertNotIn('type="email"', template)
        self.assertIn("static', filename='product.css'", template)
        self.assertIn("mobile-account-nav", template)
        self.assertIn("Create report", template)
        self.assertIn("data-theme-control", template)
        self.assertIn("static', filename='product_shell.js'", template)

        auth_error = os.path.join(
            os.path.dirname(__file__), "..", "templates", "auth_error.html"
        )
        with open(auth_error, encoding="utf-8") as handle:
            error_template = handle.read()
        self.assertIn('action="{{ url_for(\'auth.logout\') }}"', error_template)
        self.assertIn("csrf_token()", error_template)

    def test_sensitive_pages_and_auth_transitions_are_private_no_store(self):
        credential = self._credential()
        self._set_cookie(credential)
        for path in ("/organizations/select", "/organizations/none"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.headers["Cache-Control"], "private, no-store")
                self.assertEqual(response.headers["Pragma"], "no-cache")
        login = self.app_module.app.test_client().get("/login")
        self.assertEqual(login.headers["Cache-Control"], "private, no-store")


class EnterpriseSsoAuthorizationTests(_PostgreSQLIdentityCase):
    def _account(self, email="sso-coach@example.edu"):
        from roster_scout.models import Account

        with self.Session.begin() as session:
            account = Account(
                normalized_email=email,
                email_verified_at=self.now,
                status="active",
            )
            session.add(account)
            session.flush()
            return account.id

    def test_provider_organization_and_domain_do_not_grant_local_context(self):
        from roster_scout.authorization import AuthorizationFailure, require_context
        from roster_scout.models import Organization
        from roster_scout.sessions import create_account_session

        account_id = self._account()
        with self.Session.begin() as session:
            organization = Organization(name="Provider Claimed University")
            session.add(organization)
            session.flush()
            organization_id = organization.id
            credential = create_account_session(
                session,
                account_id=account_id,
                sealed_provider_state="sealed-sso-session",
                now=self.now,
            )

        with self.Session.begin() as session:
            with self.assertRaises(AuthorizationFailure) as raised:
                require_context(
                    session,
                    cookie_value=credential.cookie_value,
                    now=self.now + timedelta(minutes=1),
                )
        self.assertEqual(raised.exception.category, "membership_required")
        self.assertNotIn(str(organization_id), str(raised.exception))

    def test_only_active_local_membership_grants_context(self):
        from roster_scout.authorization import require_context
        from roster_scout.models import Organization, OrganizationMembership
        from roster_scout.sessions import create_account_session

        account_id = self._account("invited-coach@example.edu")
        with self.Session.begin() as session:
            organization = Organization(name="Inviting University")
            session.add(organization)
            session.flush()
            session.add(OrganizationMembership(
                organization_id=organization.id,
                account_id=account_id,
                role="member",
                status="active",
            ))
            session.flush()
            credential = create_account_session(
                session,
                account_id=account_id,
                active_organization_id=organization.id,
                sealed_provider_state="sealed-invited-session",
                now=self.now,
            )
            organization_id = organization.id

        with self.Session.begin() as session:
            context = require_context(
                session,
                cookie_value=credential.cookie_value,
                now=self.now + timedelta(minutes=1),
            )
        self.assertEqual(context.account_id, account_id)
        self.assertEqual(context.organization_id, organization_id)
        self.assertEqual(context.role, "member")

    def test_workos_adapter_ignores_provider_organization_authority(self):
        from roster_scout.identity import WorkOSIdentityProvider

        user = SimpleNamespace(
            id="user_synthetic_sso",
            email="synthetic-sso@example.test",
            email_verified=True,
            to_dict=lambda: {
                "id": "user_synthetic_sso",
                "email": "synthetic-sso@example.test",
                "email_verified": True,
            },
        )
        response = SimpleNamespace(
            user=user,
            access_token="synthetic-access-token",
            refresh_token="synthetic-refresh-token",
            impersonator=None,
            organization_id="org_provider_claim_only",
        )

        class _UserManagement:
            def authenticate_with_code(self, **kwargs):
                self.exchange_kwargs = kwargs
                return response

            def get_authorization_url(self, **kwargs):
                self.authorization_kwargs = kwargs
                return "https://identity.example.test/authorize"

            def find_invitation_by_token(self, token):
                self.resolved_token = token
                return SimpleNamespace(
                    id="invitation_provider_1",
                    organization_id="org_provider_1",
                    email="invited@example.edu",
                    state=SimpleNamespace(value="pending"),
                    expires_at=self_now + timedelta(days=1),
                )

        client = SimpleNamespace(user_management=_UserManagement())
        provider = WorkOSIdentityProvider(
            api_key="synthetic-api-key",
            client_id="synthetic-client-id",
            redirect_uri="https://app.example.test/auth/callback",
            cookie_password=urlsafe_b64encode(b"0" * 32).decode("ascii"),
            client=client,
        )
        identity = provider.exchange_code(code="synthetic-code")
        self_now = self.now
        reference = provider.resolve_invitation_token(
            invitation_token="signed-provider-token"
        )

        self.assertEqual(identity.provider_user_id, "user_synthetic_sso")
        self.assertFalse(hasattr(identity, "organization_id"))
        self.assertEqual(identity.provider_organization_id, "org_provider_claim_only")
        self.assertEqual(reference.provider_invitation_id, "invitation_provider_1")
        self.assertEqual(reference.provider_organization_id, "org_provider_1")
        self.assertEqual(client.user_management.resolved_token, "signed-provider-token")


class SecretRedactionTests(_PostgreSQLIdentityCase):
    def test_provider_exchange_and_refresh_failures_are_fixed_and_redacted(self):
        from roster_scout.identity import FakeIdentityProvider
        from roster_scout.sessions import create_account_session, refresh_account_session

        account = EnterpriseSsoAuthorizationTests._account(self, "redaction@example.edu")
        with self.Session.begin() as session:
            credential = create_account_session(
                session,
                account_id=account,
                sealed_provider_state="sealed-provider-state-secret",
                now=self.now,
            )

        logger = logging.getLogger("tests.identity.refresh.redaction")
        provider_secret = "provider-refresh-exception-secret"
        with self.assertLogs(logger, level="WARNING") as captured:
            with self.Session.begin() as session:
                outcome = refresh_account_session(
                    session,
                    provider=FakeIdentityProvider(
                        refresh_failure=RuntimeError(provider_secret)
                    ),
                    cookie_value=credential.cookie_value,
                    now=self.now + timedelta(minutes=1),
                    logger=logger,
                )
        rendered = " ".join(captured.output) + repr(outcome)
        self.assertEqual(outcome.status, "preserved")
        self.assertNotIn(provider_secret, rendered)
        self.assertNotIn("sealed-provider-state-secret", rendered)
        self.assertEqual(
            captured.output,
            ["WARNING:tests.identity.refresh.redaction:identity_provider_refresh_failed"],
        )


class IdentitySchemaConstraintTests(_PostgreSQLIdentityCase):
    def test_identity_schema_has_named_constraints_and_no_raw_verifier_column(self):
        from sqlalchemy import inspect

        inspector = inspect(self.engine)
        self.assertTrue({
            "accounts",
            "external_identities",
            "auth_transactions",
            "account_sessions",
        }.issubset(inspector.get_table_names()))
        session_columns = {
            column["name"] for column in inspector.get_columns("account_sessions")
        }
        self.assertIn("verifier_digest", session_columns)
        self.assertNotIn("verifier", session_columns)
        identity_uniques = {
            constraint["name"]
            for constraint in inspector.get_unique_constraints("external_identities")
        }
        self.assertIn("uq_external_identities_issuer_subject", identity_uniques)
        self.assertIn("uq_external_identities_provider_user", identity_uniques)

    def test_database_rejects_duplicate_external_identity(self):
        from roster_scout.models import Account, ExternalIdentity

        with self.Session.begin() as session:
            first = Account(
                normalized_email="first@example.edu",
                email_verified_at=self.now,
                status="active",
            )
            second = Account(
                normalized_email="second@example.edu",
                email_verified_at=self.now,
                status="active",
            )
            session.add_all((first, second))
            session.flush()
            session.add(ExternalIdentity(
                account_id=first.id,
                issuer="https://identity.example.test",
                subject="same-subject",
                provider_user_id="same-provider-user",
                last_verified_email="first@example.edu",
                verified_at=self.now,
            ))

        with self.assertRaises(IntegrityError):
            with self.Session.begin() as session:
                second = session.scalar(
                    select(Account).where(
                        Account.normalized_email == "second@example.edu"
                    )
                )
                session.add(ExternalIdentity(
                    account_id=second.id,
                    issuer="https://identity.example.test",
                    subject="same-subject",
                    provider_user_id="another-provider-user",
                    last_verified_email="second@example.edu",
                    verified_at=self.now,
                ))


if __name__ == "__main__":
    unittest.main()
