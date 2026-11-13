---
phase: 02
fixed_at: 2026-11-13T09:10:26Z
review_path: .planning/phases/02-identity-organizations-and-report-library/02-REVIEW.md
iteration: 1
findings_in_scope: 14
fixed: 14
skipped: 0
status: all_fixed
---

# Phase 02: Code Review Fix Report

**Fixed at:** 2026-11-13T09:10:26Z
**Source review:** `.planning/phases/02-identity-organizations-and-report-library/02-REVIEW.md`
**Iteration:** 1

**Summary:**

- Findings in scope: 14
- Fixed: 14
- Skipped: 0
- Verification: the complete Phase 2 deterministic release validator passed twice against an explicitly local disposable PostgreSQL database.
- Release evidence: `code_verified=true`, `production_auth_verified=false`, `production_launch_ready=false`.

## Fixed Issues

### CR-01: Provider invitations have no reachable local acceptance handoff

**Files modified:** `roster_scout/auth_routes.py`, `roster_scout/identity.py`, `roster_scout/organizations.py`, `tests/test_identity.py`
**Commit:** 11c9731
**Applied fix:** Routed provider organization context only to an exact-email pending local invitation; provider claims never grant membership authority.

### CR-02: A verified provider email change leaves stale invitation authority

**Files modified:** `roster_scout/accounts.py`, `tests/test_identity.py`, `tests/test_organizations.py`
**Commit:** 11c9731
**Applied fix:** Atomically updates the canonical verified account email for an established provider subject and fails closed on collisions.

### CR-03: Terminal provider revocation never reaches the Flask authorization path

**Files modified:** `app.py`, `roster_scout/models.py`, `roster_scout/sessions.py`, `migrations/versions/0019_phase2_review_safety.py`, `tests/test_identity.py`
**Commit:** 11c9731
**Applied fix:** Added bounded provider revalidation to the production authorization path with provider I/O outside database transactions, transient preservation, and terminal local revocation.

### CR-04: The release validator can destructively downgrade an arbitrary remote database

**Files modified:** `scripts/validate_phase2_release.py`, `DEPLOYMENT.md`, `tests/test_deployment_config.py`
**Commit:** 11fe14f
**Applied fix:** Requires an explicit local-disposable marker, an `_test` database, PostgreSQL, and a loopback host before any destructive lifecycle command.

### CR-05: Required sign-in outcome audit events are never written

**Files modified:** `roster_scout/auth_routes.py`, `roster_scout/audit.py`, `tests/test_identity.py`
**Commit:** 11c9731
**Applied fix:** Added sanitized append-only success and failure sign-in audit events without callback secrets or identity tuples.

### WR-01: Responsive and no-membership shells remove required user actions

**Files modified:** `static/product.css`, `static/product_shell.js`, `templates/auth_error.html`, `templates/product_base.html`, `templates/report_library.html`, `tests/test_identity.py`
**Commit:** cdf95b7
**Applied fix:** Restored mobile reports, members, create, theme, and CSRF-protected sign-out actions, including truthful no-membership recovery.

### WR-02: Terminal polling leaves the page showing the wrong snapshot and actions

**Files modified:** `static/report_status.js`, `tests/js/report_status.test.mjs`
**Commit:** cdf95b7
**Applied fix:** Terminal polling now renders its terminal state and reloads the server-rendered page to synchronize snapshot panels and actions.

### WR-03: A transient invitation delivery failure is not retryable and concurrent delivery can send duplicates

**Files modified:** `roster_scout/models.py`, `roster_scout/organizations.py`, `roster_scout/organization_routes.py`, `migrations/versions/0019_phase2_review_safety.py`, `tests/test_organizations.py`
**Commit:** 11c9731
**Applied fix:** Added retry of the same pending invitation plus a bounded transactional delivery claim that prevents duplicate provider sends.

### WR-04: Concurrent organization bootstrap is not actually idempotent

**Files modified:** `roster_scout/organizations.py`, `tests/test_organizations.py`
**Commit:** 11c9731
**Applied fix:** Serialized absent-row bootstrap with a transaction-scoped PostgreSQL advisory lock.

### WR-05: Artifact storage I/O runs while a database transaction is held

**Files modified:** `roster_scout/report_routes.py`, `tests/test_report_library.py`
**Commit:** 11c9731
**Applied fix:** Split metadata resolution, external artifact I/O, and locked revalidation/audit into separate phases while preserving caller-owned transaction semantics.

### WR-06: Protected legacy workbook downloads are still recorded as anonymous

**Files modified:** `app.py`, `tests/test_app_routes.py`
**Commit:** 11c9731
**Applied fix:** Bound protected legacy workbook receipt proofs and successful acknowledgements to the authenticated account and organization.

### WR-07: Fly deployment instructions omit every managed-identity secret

**Files modified:** `DEPLOYMENT.md`, `tests/test_deployment_config.py`
**Commit:** 11fe14f
**Applied fix:** Documented every identity secret name and name-only readiness checks without placing credential values in source or shell history.

### WR-08: Authenticated tenant pages and status JSON lack an explicit no-store policy

**Files modified:** `app.py`, `tests/test_identity.py`
**Commit:** 11c9731
**Applied fix:** Added private no-store and pragma headers to sensitive dynamic responses while preserving public static and health endpoints.

### WR-09: Successful download audits discard the artifact identity

**Files modified:** `app.py`, `tests/test_app_routes.py`, `tests/test_report_library.py`
**Commit:** 11c9731
**Applied fix:** Carries the exact signed artifact identifier into the successful append-only download audit without exposing other tenant identifiers in the proof.

## Additional Verification Fix

**Commit:** d958b1d
**Applied fix:** Made the non-enumerating artifact-response counterexample compare stable response content while ignoring only the independently signed per-response CSRF nonce.

---

_Fixed: 2026-11-13T09:10:26Z_
_Fixer: the agent (gsd-code-fixer)_
_Iteration: 1_
