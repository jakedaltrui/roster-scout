---
phase: 02-identity-organizations-and-report-library
reviewed: 2026-11-13T09:18:50Z
depth: deep
files_reviewed: 31
files_reviewed_list:
  - .env.example
  - DEPLOYMENT.md
  - app.py
  - fly.toml
  - migrations/versions/0019_phase2_review_safety.py
  - roster_scout/accounts.py
  - roster_scout/audit.py
  - roster_scout/auth_routes.py
  - roster_scout/database.py
  - roster_scout/identity.py
  - roster_scout/models.py
  - roster_scout/organization_routes.py
  - roster_scout/organizations.py
  - roster_scout/report_routes.py
  - roster_scout/reports.py
  - roster_scout/sessions.py
  - scripts/validate_phase2_release.py
  - static/product.css
  - static/product_shell.js
  - static/report_status.js
  - templates/auth_error.html
  - templates/product_base.html
  - templates/report_detail.html
  - templates/report_library.html
  - tests/js/download_tracking.test.mjs
  - tests/js/report_status.test.mjs
  - tests/test_app_routes.py
  - tests/test_deployment_config.py
  - tests/test_identity.py
  - tests/test_organizations.py
  - tests/test_report_library.py
findings:
  critical: 3
  warning: 1
  info: 0
  total: 4
status: issues_found
---

# Phase 02: Code Review Report

**Reviewed:** 2026-11-13T09:18:50Z
**Depth:** deep
**Files Reviewed:** 31
**Status:** issues_found

## Summary

The re-review traced commits `11c9731`, `cdf95b7`, `11fe14f`, and `d958b1d` against every original finding and the affected tests. Eleven original findings are resolved: canonical verified-email rotation, request-path provider refresh with split transactions, sign-in audit production wiring, mobile/logout/theme actions, terminal status synchronization, bootstrap serialization, artifact-I/O transaction splitting, authenticated legacy-download accounting, deployment secret documentation, no-store headers, and exact artifact download auditing all have coherent cross-file implementations. Original WR-03 is only partially resolved.

The phase is still not safe to ship. The invitation test still fabricates provider organization context instead of exercising the token-bearing email entry, so a real email-initiated flow cannot establish the database-backed callback state and ordinary organization-scoped sign-ins can accept an invitation without proving the user followed that invitation. The destructive validator also checks the URL authority host while psycopg honors a query-string `host` override, allowing a remote database through the local-only guard. In addition, public auth endpoints provide unthrottled durable write amplification, made worse by the new failure-audit insert, and the invitation delivery lease can expire while a provider send is still in flight and permit duplicate sends.

The focused Node suite passed all 26 tests, Python compilation passed, the 10 focused deployment/validator unit tests passed, and `git diff --check` passed. A direct SQLAlchemy driver-contract probe proved that `postgresql+psycopg://...@127.0.0.1/..._test?host=db.example.invalid` passes `_validate_database_url()` while the driver resolves `host=db.example.invalid`. The current shell has no `DATABASE_URL`, so the database-backed re-run skipped 21 integration classes; the existing fix evidence reports that the complete 547-test release validator passed twice on local disposable PostgreSQL. Those passes do not exercise the counterexamples below.

## Resolution of Original Findings

| Original finding | Re-review verdict |
|---|---|
| CR-01 invitation handoff | **Unresolved** — callback fallback exists, but the token-bearing email entry/state binding still does not. |
| CR-02 canonical email rotation | Resolved. |
| CR-03 request-path provider refresh | Resolved. |
| CR-04 destructive validator | **Unresolved** — authority host check is bypassed by the driver's query host override. |
| CR-05 sign-in audit | Resolved functionally; CR-03 below captures the new unbounded public write surface. |
| WR-01 mobile/logout/theme | Resolved. |
| WR-02 terminal status UI | Resolved. |
| WR-03 invitation retry/concurrency | **Partially resolved** — retry works, but the bounded lease does not prevent late/crash-window duplicates. |
| WR-04 bootstrap concurrency | Resolved. |
| WR-05 artifact I/O transaction | Resolved. |
| WR-06 protected legacy accounting | Resolved. |
| WR-07 deployment identity secrets | Resolved. |
| WR-08 private no-store | Resolved. |
| WR-09 exact artifact audit target | Resolved. |

## Narrative Findings (AI reviewer)

## Critical Issues

### CR-01: Invitation email entry is still not bound to local state

**Classification:** BLOCKER

**File:** `roster_scout/auth_routes.py:147-205`, `roster_scout/identity.py:234-246`, `tests/test_identity.py:999-1041`

**Issue:** `/auth/start` accepts no invitation token and always creates an auth transaction with `invitation_id=None`, even though the provider adapter supports `invitation_token`. A token-bearing email entry therefore cannot create the database-backed state that `/auth/callback` requires. The new test avoids that production boundary: it starts an ordinary `/auth/start`, fabricates a `VerifiedIdentity.provider_organization_id`, and then calls the callback. Consequently it proves only that any ordinary organization-scoped sign-in can consume a matching local invitation. It does not prove that clicking the invitation email reaches the callback, and it loses the invitee-choice property that WorkOS invitations preserve. This contradicts the phase research's required token resolution -> local invitation UUID -> one-time transaction -> AuthKit token flow. See the [WorkOS invitation reference](https://workos.com/docs/reference/authkit/invitation) and [authorization URL contract](https://workos.com/docs/reference/authkit/authentication/get-authorization-url).

**Fix:** Add a bounded public invitation-entry route matching the configured WorkOS user-invitation URL. Resolve the raw token through WorkOS without logging or persisting it, map the returned provider invitation/organization to exactly one pending local invitation, store only that local invitation UUID in `begin_auth_transaction(invitation_id=...)`, and pass the raw token only to `authorization_url(..., invitation_token=...)`. On callback, accept only `transaction.invitation_id`; do not auto-accept from bare `provider_organization_id`. Replace the synthetic test with an HTTP test that starts at `?invitation_token=...`, asserts the provider receives the token, follows the callback, and proves a normal organization-scoped sign-in cannot consume the pending invite.

### CR-02: Query-string host override bypasses the destructive database guard

**Classification:** BLOCKER

**File:** `scripts/validate_phase2_release.py:154-179`, `tests/test_deployment_config.py:1878-1903`

**Issue:** `_validate_database_url()` checks only `url.host`, which is the authority host. SQLAlchemy's psycopg dialect merges URL query parameters afterward, and a query `host` overrides the authority value. The validator therefore accepts `postgresql+psycopg://review:placeholder@127.0.0.1/customer_test?host=db.example.invalid` as loopback, while `create_connect_args()` resolves the actual driver host to `db.example.invalid`. The manifest then runs `alembic downgrade base` against that remote target. The added tests cover a remote authority and a query host with an empty authority, but not this precedence case.

**Fix:** Reject PostgreSQL connection-routing query parameters (`host`, `hostaddr`, service/socket routing, and multi-host forms) for this destructive command, or validate the final dialect/driver connection arguments rather than `URL.host`. Require the final effective host to be exactly loopback and keep the explicit disposable marker/database suffix checks. Add counterexamples for loopback authority plus remote `host`, `hostaddr`, Unix/cloud socket, service file, and multi-host query values before any manifest command is constructed or run.

### CR-03: Public auth endpoints allow unbounded durable write amplification

**Classification:** BLOCKER

**File:** `roster_scout/auth_routes.py:123-184`, `roster_scout/identity.py:407-426`, `app.py:702-712`

**Issue:** `/auth/start` and `/auth/callback` are public and have no request throttle. Every `/auth/start` inserts an `auth_transactions` row, expired/consumed rows have no cleanup path, and every arbitrary invalid callback now inserts an append-only `security_audit_events` row through `_record_sign_in_failure()`. An unauthenticated client can grow both tables indefinitely without completing provider authentication, eventually exhausting storage or degrading the authentication database. This violates locked decision D-05's request-throttling requirement and turns the CR-05 audit fix into an additional denial-of-service write primitive.

**Fix:** Apply a shared, deployment-safe limiter to auth start and callback attempts with a fixed sanitized 429 response, and add bounded deletion/retention for expired auth transactions. Preserve audit evidence for meaningful known-state replay/provider failures, but rate-limit or aggregate nonexistent-state noise so arbitrary bearer strings cannot force an append-only database write each time. Add route tests for start/callback limits and transaction retention.

## Warnings

### WR-01: Invitation delivery lease still permits late and crash-window duplicates

**Classification:** WARNING

**File:** `roster_scout/organizations.py:683-769`, `tests/test_organizations.py:413-467`

**Issue:** The delivery claim expires two minutes after the call's initial `now`. If the provider call is still in flight after that time, a second caller can replace the claim and send another provider invitation. The first provider call may already have succeeded, but its final transaction then sees the replaced token and cannot persist the provider invitation ID. A process crash after provider success and before the final transaction has the same retry-duplicates-send window. The concurrency test passes the identical frozen `now` to both calls, so it cannot exercise lease expiry and proves only the short-call case.

**Fix:** Make the provider side idempotent using a stable key derived from the local invitation UUID when supported, or reconcile existing provider invitations before resend. At minimum, enforce a provider timeout safely shorter than a renewable lease and persist/reconcile an indeterminate outcome rather than blindly resending after expiry. Add tests where the second caller advances beyond the lease while the first send is blocked and where provider success occurs immediately before local persistence failure.

---

_Reviewed: 2026-11-13T09:18:50Z_
_Reviewer: the agent (gsd-code-reviewer)_
_Depth: deep_
