---
phase: 02-identity-organizations-and-report-library
reviewed: 2026-11-13T11:56:58Z
depth: deep
files_reviewed: 33
files_reviewed_list:
  - .env.example
  - DEPLOYMENT.md
  - app.py
  - fly.toml
  - migrations/versions/0019_phase2_review_safety.py
  - migrations/versions/0020_auth_request_limits.py
  - migrations/versions/0021_invitation_delivery_recovery.py
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
  critical: 1
  warning: 3
  info: 0
  total: 4
status: issues_found
---

# Phase 02: Code Review Report

**Reviewed:** 2026-11-13T11:56:58Z
**Depth:** deep
**Files Reviewed:** 33
**Status:** issues_found

## Summary

The second fix pass closes the four reported code-level counterexamples: the provider token is resolved before authorization and bound to one exact local invitation UUID; ordinary provider organization context no longer grants membership; the destructive validator rejects query/service/socket/multi-host overrides and validates psycopg's effective target; shared PostgreSQL rate limits and bounded pruning constrain public auth writes; and invitation delivery uses a real WorkOS idempotency header, a sub-lease timeout, and reconciliation after indeterminate sends. The prior verified email, request refresh, tenant accounting, transaction split, cache, audit target, concurrency, and UI fixes remain intact.

The phase is not yet clean. The new invitation entry works only when WorkOS's User Invitation URL is configured to send token-bearing email links through `/auth/start`, but the deployment contract still instructs operators to register only `/auth/callback`. Following the documented setup can therefore bypass the state-issuing route and reproduce the production invitation failure. Three warnings remain: a valid state consumed with a missing/malformed code produces no sign-in failure audit, the rejected provider-organization auto-accept primitive remains as unused callable authorization code, and the release runbook still says migration `0019` even though the validated head is `0021`.

Verification evidence is strong but does not cover these counterexamples. The supplied full release evidence records 553 Python tests plus 26 Node tests passing twice with `code_verified=true`, `production_auth_verified=false`, and `production_launch_ready=false`. In this re-review, the 26 Node tests, Python compilation, 10 focused deployment/validator tests, effective-target probes, and `git diff --check` all passed. The installed WorkOS SDK was also inspected directly: `RequestOptions.idempotency_key` is converted to the `Idempotency-Key` request header, and the configured timeout/retry envelope remains shorter than the two-minute delivery lease.

## Resolution of Prior Findings

| Prior finding | Final re-review verdict |
|---|---|
| CR-01 real invitation token/state handoff | **Partially resolved** — code path is correct; required WorkOS User Invitation URL configuration is absent from deployment instructions. |
| CR-02 effective destructive DB target | Resolved. |
| CR-03 auth throttling/pruning/audit | **Partially resolved** — writes are bounded, but a known transaction followed by malformed code is not audited. |
| WR-01 slow/crashed invitation delivery | Resolved. |
| Earlier 11 resolved findings | Remain resolved. |

## Narrative Findings (AI reviewer)

## Critical Issues

### CR-01: Deployment never routes WorkOS invitation emails through the state-issuing entry

**Classification:** BLOCKER

**File:** `DEPLOYMENT.md:129-148`, `roster_scout/auth_routes.py:197-238`

**Issue:** The implementation now correctly expects a token-bearing request to `/auth/start`, resolves that token, stores the exact local invitation UUID in `AuthTransaction`, and then redirects to AuthKit. That path is reachable from a real email only if the WorkOS application's **User Invitation URL** is configured to the public `/auth/start` URL so WorkOS appends `invitation_token`. The runbook tells staging and production operators only to register `/auth/callback`; it never names or verifies the User Invitation URL. With WorkOS's hosted invitation entry, the provider can return to `/auth/callback` without a state that exists in Roster Scout's database, so `consume_auth_transaction()` rejects the email journey before the fixed handoff runs. This is a missing deployment dependency, not the intentional `production_auth_verified=false` checkpoint.

**Fix:** Add the exact environment-specific User Invitation URL (for example, `https://roster-scout.fly.dev/auth/start`) to the WorkOS dashboard setup, staging checklist, production-auth smoke record, and deployment tests. The live invitation smoke must begin from the provider-sent `accept_invitation_url`, prove it reaches `/auth/start?invitation_token=...`, and then complete the stateful callback into the exact-email local membership. Keep `/auth/callback` registered separately as the OAuth redirect URI.

## Warnings

### WR-01: A consumed valid state with malformed code is missing its failure audit

**Classification:** WARNING

**File:** `roster_scout/auth_routes.py:259-271`, `roster_scout/identity.py:33-39`

**Issue:** The callback consumes a known transaction at line 264 and only afterward validates `code`. Missing, empty, oversized, or control-character code raises `IdentityFailure("invalid_callback")` with the default `audit_worthy=False`, so the exception path writes no `identity.sign_in` failure event. Unknown random states are correctly suppressed to prevent write amplification, but this case is different: the server has already proved and consumed a real local auth transaction. The security ledger therefore loses a bounded, meaningful callback failure and the one-time state cannot be retried.

**Fix:** Track whether a local transaction was successfully consumed and mark subsequent callback-validation failures audit-worthy, while keeping nonexistent-state noise unaudited. Add a route test that starts authentication, calls back with that valid state and a missing/malformed code, asserts one sanitized failed `identity.sign_in` event, and proves arbitrary unknown states still create none.

### WR-02: The rejected provider-organization auto-accept primitive remains callable

**Classification:** WARNING

**File:** `roster_scout/organizations.py:503-554`

**Issue:** `accept_provider_invitation_handoff()` is no longer imported or called anywhere, but it still implements the exact behavior the final token fix rejected: a bare provider organization ID plus matching account email can consume a pending local invitation without a token-bound `AuthTransaction`. Keeping a dead authorization primitive beside the correct `resolve_provider_invitation_reference()` path creates two conflicting authority models and makes a future route refactor capable of silently reintroducing the original membership bug.

**Fix:** Delete `accept_provider_invitation_handoff()` and keep token reference -> exact provider invitation ID/organization/email -> local invitation UUID as the only invitation handoff service. Add a static/import contract or focused service test that ensures provider organization context alone has no acceptance API.

### WR-03: Release instructions still identify migration 0019 as the validated head

**Classification:** WARNING

**File:** `DEPLOYMENT.md:159-162`, `scripts/validate_phase2_release.py:28`

**Issue:** The validator and migration chain correctly require head `0021`, and the supplied release runs validated that lifecycle twice. The runbook still says the deterministic gate validates revision `0019`. Although `alembic current --check-heads` still fails a stale database, the documented release contract now misstates what passed and can send operators troubleshooting migration readiness toward the wrong head; the two omitted revisions contain the auth limiter table and invitation recovery column.

**Fix:** Update the runbook to `0021` and add a deployment-contract assertion that the documented revision equals `EXPECTED_MIGRATION_HEAD`, so future security migrations cannot drift silently.

---

_Reviewed: 2026-11-13T11:56:58Z_
_Reviewer: the agent (gsd-code-reviewer)_
_Depth: deep_
