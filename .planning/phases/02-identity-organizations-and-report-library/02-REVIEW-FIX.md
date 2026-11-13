---
phase: 02
fixed_at: 2026-11-13T11:48:59Z
review_path: .planning/phases/02-identity-organizations-and-report-library/02-REVIEW.md
iteration: 2
findings_in_scope: 4
fixed: 4
skipped: 0
status: all_fixed
---

# Phase 02: Code Review Fix Report

**Fixed at:** 2026-11-13T11:48:59Z
**Source review:** `.planning/phases/02-identity-organizations-and-report-library/02-REVIEW.md`
**Iteration:** 2

**Summary:**

- Findings in scope: 4
- Fixed: 4
- Skipped: 0
- Verification: the complete Phase 2 deterministic release validator passed twice against an explicitly local disposable PostgreSQL database.
- Release evidence: `code_verified=true`, `production_auth_verified=false`, `production_launch_ready=false`.
- Review state: all reported fixes were applied, but `02-REVIEW.md` remains unchanged pending an independent reviewer re-check.

## Fixed Issues

### CR-01: Provider invitation acceptance still has no real token-to-local-state handoff

**Files modified:** `app.py`, `roster_scout/auth_routes.py`, `roster_scout/identity.py`, `roster_scout/organizations.py`, `tests/test_identity.py`
**Commit:** 53bf8a5
**Applied fix:** Resolved the real provider invitation token before authorization, matched its sanitized provider identity to exactly one pending exact-email local invitation, persisted only that local invitation UUID in the signed authorization transaction, and removed provider organization context as callback authority. Counterexample tests exercise the production HTTP token path and prove ordinary provider-organization sign-in cannot consume an invitation.

### CR-02: The destructive database guard trusts the URL authority instead of the effective psycopg target

**Files modified:** `scripts/validate_phase2_release.py`, `tests/test_deployment_config.py`
**Commit:** f5f810e
**Applied fix:** Resolved the effective SQLAlchemy/psycopg connection arguments, required the exact PostgreSQL psycopg driver plus an explicit loopback `_test` database, and rejected all query-parameter, socket, service-file, alternate-host, and multi-host target overrides before destructive commands run.

### CR-03: Public authentication endpoints permit unbounded durable write amplification

**Status:** fixed: requires human verification
**Files modified:** `migrations/versions/0020_auth_request_limits.py`, `roster_scout/auth_routes.py`, `roster_scout/identity.py`, `roster_scout/models.py`, `scripts/validate_phase2_release.py`, `tests/test_deployment_config.py`, `tests/test_identity.py`
**Commit:** d3014bd
**Applied fix:** Added shared PostgreSQL-backed, privacy-preserving request limits for `/auth/start` and invalid callback attempts, fixed error responses with `Retry-After`, bounded stale transaction and limiter cleanup, and prevented unknown callback states from appending audit rows while retaining audit evidence for known replay and expiry failures.

### WR-01: Invitation delivery lease expiry can resend after a slow or crashed provider call

**Status:** fixed: requires human verification
**Files modified:** `app.py`, `migrations/versions/0021_invitation_delivery_recovery.py`, `roster_scout/identity.py`, `roster_scout/models.py`, `roster_scout/organizations.py`, `scripts/validate_phase2_release.py`, `tests/test_deployment_config.py`, `tests/test_identity.py`, `tests/test_organizations.py`
**Commit:** 40a75b0
**Applied fix:** Added a stable local-invitation-derived WorkOS idempotency key, a provider timeout shorter than the delivery lease, durable delivery start tracking, and reconciliation of pending provider invitations before retry. Counterexample tests cover a slow first send beyond the lease and provider success immediately before local persistence failure; both recover to one provider delivery.

---

_Fixed: 2026-11-13T11:48:59Z_
_Fixer: the agent (gsd-code-fixer)_
_Iteration: 2_
