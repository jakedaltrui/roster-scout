---
phase: 02-identity-organizations-and-report-library
reviewed: 2026-11-13T07:57:36Z
depth: deep
files_reviewed: 49
files_reviewed_list:
  - .env.example
  - DEPLOYMENT.md
  - app.py
  - fly.toml
  - migrations/versions/0015_phase2_identity_accounts.py
  - migrations/versions/0016_phase2_memberships.py
  - migrations/versions/0017_phase2_invitations_audit.py
  - migrations/versions/0018_phase2_report_actors.py
  - requirements.txt
  - roster_scout/accounts.py
  - roster_scout/audit.py
  - roster_scout/auth_routes.py
  - roster_scout/authorization.py
  - roster_scout/identity.py
  - roster_scout/jobs.py
  - roster_scout/models.py
  - roster_scout/organization_routes.py
  - roster_scout/organizations.py
  - roster_scout/report_routes.py
  - roster_scout/reports.py
  - roster_scout/repositories.py
  - roster_scout/sessions.py
  - roster_scout/usage.py
  - scripts/provision_organization.py
  - scripts/validate_phase2_release.py
  - static/download_tracking.js
  - static/product.css
  - static/report_form.js
  - static/report_status.js
  - templates/auth_error.html
  - templates/invitation_outcome.html
  - templates/organization_members.html
  - templates/organization_select.html
  - templates/product_base.html
  - templates/report_detail.html
  - templates/report_library.html
  - templates/report_new.html
  - templates/sign_in.html
  - tests/fixtures/workos-emulate.config.yaml
  - tests/js/download_tracking.test.mjs
  - tests/js/report_status.test.mjs
  - tests/test_app_routes.py
  - tests/test_deployment_config.py
  - tests/test_identity.py
  - tests/test_organizations.py
  - tests/test_phase2_release.py
  - tests/test_phase2_tenant_isolation.py
  - tests/test_report_library.py
  - tests/test_usage_metrics.py
findings:
  critical: 5
  warning: 9
  info: 0
  total: 14
status: issues_found
---

# Phase 02: Code Review Report

**Reviewed:** 2026-11-13T07:57:36Z
**Depth:** deep
**Files Reviewed:** 49
**Status:** issues_found

## Summary

The Phase 2 implementation has strong tenant predicates and database ownership constraints, but it is not ready to ship. The production invitation journey has no browser handoff to local acceptance, established identities retain stale invitation-authority email addresses, provider revocation is never consulted by request handling, and the release validator can erase an arbitrary remote database whose name merely ends in `_test`. Required sign-in audit evidence is also absent. Nine additional findings affect mobile workflows, polling truthfulness, invitation/provisioning concurrency, transaction boundaries, usage attribution, deployment instructions, caching, and download audit fidelity.

The focused Node suite (26 tests), Python compilation, and available deployment/passwordless unit tests passed. PostgreSQL integration tests could not run because `DATABASE_URL` was not configured; the available suite skipped those cases. The critical invitation and refresh defects are nevertheless established directly by the call graph: the relevant service functions and fields have no production caller.

## Narrative Findings (AI reviewer)

## Critical Issues

### CR-01: Provider invitations have no reachable local acceptance handoff

**Classification:** BLOCKER

**File:** `roster_scout/auth_routes.py:126-209`

**Issue:** The application sends a WorkOS organization invitation and persists its provider ID, but no browser flow carries the corresponding local invitation into authentication or acceptance. `/auth/start` never accepts an invitation token or local invitation ID, `begin_auth_transaction(..., invitation_id=...)` is never called with an invitation, and the callback ignores `ConsumedAuthTransaction.invitation_id`. After the invited address authenticates, the callback sees zero local memberships and redirects to `/organizations/none`. The only local acceptance surface is a CSRF-protected POST containing the local invitation UUID (`/invitations/<uuid>/accept`), but neither the provider email nor any app page gives the invitee that UUID. This also makes the operator-provisioned first administrator unable to enter a fresh production organization. The supposed release-story test bypasses the missing browser composition by calling `accept_invitation()` directly at `tests/test_phase2_release.py:100-134`, so it cannot catch the failure.

**Fix:** Add a complete provider-to-local handoff. For example, carry an opaque signed local invitation reference through the provider flow, or return provider invitation/organization context from the adapter and resolve it to exactly one pending local invitation whose normalized email matches the verified identity. After callback, render or perform the local acceptance outcome without trusting provider membership as authorization. Add an HTTP integration test that starts with the admin invite route, follows the fake/emulated provider callback, accepts locally, and reaches the organization library without the test knowing the local invitation UUID out of band.

### CR-02: A verified provider email change leaves stale invitation authority

**Classification:** BLOCKER

**File:** `roster_scout/accounts.py:58-75`

**Issue:** When an established `(issuer, subject)` signs in with a newly verified email, the code updates only `ExternalIdentity.last_verified_email`; it never updates or invalidates `Account.normalized_email` or `Account.email_verified_at`. Invitation acceptance later compares the invite against the stale account email (`roster_scout/organizations.py:427-431`). A user whose provider identity moved away from an old school address can therefore still accept an invitation addressed to that old email, while an invitation addressed to the currently verified email is rejected. This violates the exact-current-verified-email authorization rule and can grant access after control of the old mailbox has changed.

**Fix:** Define an atomic email-change policy for an established subject. Lock the account and conflicting normalized-email row, update the canonical verified email/timestamp when safe, and fail into an explicit account-link/merge flow on collision. Alternatively, bind the current verified identity/email to the local session and require that current value during invitation acceptance. Add regressions proving that an old-address invitation is rejected after a provider email change and a new-address invitation is accepted.

### CR-03: Terminal provider revocation never reaches the Flask authorization path

**Classification:** BLOCKER

**File:** `app.py:663-698`

**Issue:** Every request authorizes by calling `load_account_session()`/`require_context()`, which checks only local expiry, local revocation, account status, and membership. `refresh_account_session()` is implemented at `roster_scout/sessions.py:305-352`, including terminal provider revocation handling, but has no non-test caller anywhere in the repository. A WorkOS session or user that becomes terminally invalid therefore continues to access Roster Scout until the local 12-hour idle or seven-day absolute timeout, contrary to the phase's immediate terminal-revocation contract. The unit tests exercise the orphan service directly and do not prove request integration.

**Fix:** Add a bounded provider-revalidation policy to request/session loading (with an explicit last-verified/next-refresh timestamp so it need not call WorkOS on every request). Do provider I/O outside the database transaction, then re-lock and compare-and-swap the same unrevoked, unrotated session before applying refreshed state or terminal revocation. Preserve local access only for classified transient failures. Add route tests showing a terminal fake-provider result redirects to sign-in and a transient result retains access with the fixed safe state.

### CR-04: The release validator can destructively downgrade an arbitrary remote database

**Classification:** BLOCKER

**File:** `scripts/validate_phase2_release.py:153-161`

**Issue:** The only destructive-target guard is that the PostgreSQL database name ends with `_test`. The manifest then unconditionally runs `alembic downgrade base` at lines 256-258. A managed, shared, staging, or mistakenly named production database such as `customer_test` on any remote host passes validation and has its entire application schema dropped. A naming suffix is not sufficient evidence that the target is disposable.

**Fix:** Default destructive lifecycle checks to loopback/container-local hosts and require a second explicit opt-in for any remote host. Prefer a database marker created specifically for disposable test databases (and verify current database/user/host before downgrade), or create and drop an isolated temporary database inside the validator. Print the resolved redacted target and abort before any Alembic command when the marker or explicit destructive authorization is absent. Add tests for remote `_test` URLs, cloud socket URLs, and ambiguous hosts.

### CR-05: Required sign-in outcome audit events are never written

**Classification:** BLOCKER

**File:** `roster_scout/auth_routes.py:146-209`

**Issue:** `identity.sign_in` is allowlisted in both the model and audit producer, but there is no call site for that action. Neither successful callback/account/session creation nor any sanitized callback failure appends a sign-in outcome. The required security ledger therefore cannot establish who signed in, whether the outcome succeeded or failed, or when suspicious replay/provider failures occurred.

**Fix:** Append a successful `identity.sign_in` event in the same transaction that links the account and creates the local session. Append fixed-category failure events where persistence is available, using null actor/organization when no canonical account has been established and never storing callback parameters or exception text. Add route tests for success, invalid/replayed state, unverified identity, provider outage, and database failure behavior.

## Warnings

### WR-01: Responsive and no-membership shells remove required user actions

**Classification:** WARNING

**File:** `static/product.css:190-200`

**Issue:** At mobile width the CSS hides the entire primary navigation and the header's `Create report` action. The account menu contains only email, theme, and logout (`templates/product_base.html:34-43`), so `Members` is unreachable on mobile and a non-empty report library also hides its page-level Create action (`templates/report_library.html:18-21`). Separately, the no-membership page tells the user to sign out but exposes only “Return to sign in” (`templates/auth_error.html:30-37`), trapping a user who authenticated with the wrong account. The visible theme button also has no script or form behavior.

**Fix:** Implement the specified mobile menu with Reports, authorized Members, Create report, theme, account email, and CSRF-protected sign-out actions. Keep at least one visible Create action on every library state. Add a CSRF-protected logout form to the authenticated no-membership state, and either implement the theme control or remove the inert button. Add rendered mobile-shell contract tests.

### WR-02: Terminal polling leaves the page showing the wrong snapshot and actions

**Classification:** WARNING

**File:** `static/report_status.js:64-75`

**Issue:** On Ready/Partial/Failed the poller updates only heading, message, and section text, then stops. It never refreshes or replaces the latest-snapshot panel, history, download action, failed-build retry action, or Refresh action. After a successful first build the page can still say “No saved snapshot is available yet” with no download/history until the user manually reloads; after refresh it can continue presenting the predecessor as latest. This violates the “complete safe view model/update actions together” UI contract.

**Fix:** On a terminal state, either perform a same-page reload to the tenant-scoped detail route or return/render all changed panels and actions atomically. Preserve focus/announcement behavior and add DOM tests proving terminal Ready exposes the new snapshot/download while Failed exposes retry without hiding the previous snapshot.

### WR-03: A transient invitation delivery failure is not retryable and concurrent delivery can send duplicates

**Classification:** WARNING

**File:** `roster_scout/organization_routes.py:391-440`

**Issue:** The route commits a pending local invitation before provider delivery. If delivery fails it tells the admin to try again, but submitting the same form calls `create_invitation()` first and returns `invitation_exists`; it never retries the undelivered row. The only workaround is manual revoke/recreate. In addition, `deliver_invitation()` reads `provider_invitation_id is None`, releases the transaction, and performs the provider call without a claim/lease; concurrent callers can send multiple provider invitations before one ID is persisted.

**Fix:** Treat an existing unexpired pending invitation with no provider ID as a resumable delivery, and introduce a database claim/lease or durable delivery outbox so only one caller contacts the provider. Make retries reselect the same local invitation and test transient failure followed by successful retry plus a two-caller delivery race.

### WR-04: Concurrent organization bootstrap is not actually idempotent

**Classification:** WARNING

**File:** `roster_scout/organizations.py:341-354`

**Issue:** Bootstrap selects organizations by display name with `FOR UPDATE`, but there is no row to lock when the organization is new and `organizations.name` has no uniqueness constraint. Two concurrent operator invocations can both create the same organization name and first-admin invitation; future runs then fail as `organization_ambiguous`. The function's documented idempotency is therefore false at the exact retry/race boundary where provisioning needs it.

**Fix:** Provision by a stable operator-supplied idempotency/external key with a unique constraint and `INSERT ... ON CONFLICT`, or acquire a transaction-scoped advisory lock on the canonical provisioning key before selecting/creating. Add a real PostgreSQL barrier test for two concurrent bootstrap commands.

### WR-05: Artifact storage I/O runs while a database transaction is held

**Classification:** WARNING

**File:** `roster_scout/report_routes.py:487-533`

**Issue:** The download route opens `_session_factory().begin()`, resolves metadata, then calls the injected artifact reader and hashes up to 64 MiB while the transaction and pooled connection remain open. A slow or failing object store can occupy all web database connections and delay unrelated authorization/logout/report requests. This also violates the project's caller-owned short-transaction/external-I/O boundary.

**Fix:** Resolve immutable scoped metadata in a short transaction, close it, read and validate storage bytes outside any database session, then open a second transaction to revalidate the exact immutable artifact scope and append the pending audit before returning the file. If immutability is not guaranteed by the database/storage contract, use an explicit version/etag comparison during the second check.

### WR-06: Protected legacy workbook downloads are still recorded as anonymous

**Classification:** WARNING

**File:** `app.py:849-854`

**Issue:** `/scrape` is now membership-protected, but it still issues the legacy identity-free proof (`app.py:1033-1036`). The acknowledgement endpoint validates that proof before loading an authenticated context and records actor/organization as null. New authenticated workbook downloads therefore inflate only the global download count and disappear from organization-scoped download metrics, despite canonical account and organization context being available.

**Fix:** When `/scrape` runs with `g.authorization_context`, issue the authenticated principal-bound proof and acknowledge it through the canonical actor/organization path. Retain legacy anonymous proofs only for an explicitly supported unauthenticated local compatibility mode. Add metric tests proving a protected scrape acknowledgement appears in its organization window and remains replay-idempotent.

### WR-07: Fly deployment instructions omit every managed-identity secret

**Classification:** WARNING

**File:** `DEPLOYMENT.md:301-307`

**Issue:** The documented `fly secrets set` command installs Browserless, database, broker, and acknowledgement values, but omits `ROSTER_SCOUT_SESSION_SECRET`, `WORKOS_API_KEY`, `WORKOS_CLIENT_ID`, `WORKOS_REDIRECT_URI`, and `WORKOS_COOKIE_PASSWORD`. Following the concrete Fly procedure produces a hosted app whose identity configuration deliberately fails closed, so no customer can sign in. The environment-variable reference table also omits these settings.

**Fix:** Add a secret-store checklist/command covering all required identity names (without real values), include them in the environment reference, and add a predeploy readiness command that verifies names/configuration without printing values. Keep the recommendation to avoid command history and prefer the Fly dashboard or secure input.

### WR-08: Authenticated tenant pages and status JSON lack an explicit no-store policy

**Classification:** WARNING

**File:** `app.py:539-568`

**Issue:** The global security-header hook does not mark protected HTML or JSON as `private, no-store`. Report pages include organization names, verified emails, report identifiers, snapshot state, and membership data; the status endpoint likewise has no cache directive. Browser history or an introduced/shared intermediary cache can retain or replay tenant data after logout or membership removal.

**Fix:** Apply `Cache-Control: private, no-store` (and an appropriate `Pragma` fallback if legacy clients matter) to authenticated/account-only HTML and JSON responses, without overwriting the stronger download headers or public static/logo caching. Add route tests for report, status, organization, invitation outcome, and auth transition responses.

### WR-09: Successful download audits discard the artifact identity

**Classification:** WARNING

**File:** `app.py:877-893`

**Issue:** Preparation records the real artifact ID, but acknowledgement emits the success audit with `target_type="artifact"` and `target_id=None`. The stateless proof exposes only hashes, and the audit table stores no receipt/event correlation, so operators cannot determine which prepared artifact a success belongs to or join the pending and succeeded actions. This fails the required target-ID audit contract even though usage counting is idempotent.

**Fix:** Preserve a verifiable artifact reference through acknowledgement—using a short-lived server receipt, an authenticated/encrypted proof field, or another privacy-reviewed correlation that can resolve the canonical artifact ID—then write the success audit with that ID only when the usage insert is new. Add a test asserting one preparation and one success event reference the same artifact across acknowledgement replay.

---

_Reviewed: 2026-11-13T07:57:36Z_
_Reviewer: the agent (gsd-code-reviewer)_
_Depth: deep_
