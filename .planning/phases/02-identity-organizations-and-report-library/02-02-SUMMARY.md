---
phase: 02-identity-organizations-and-report-library
plan: "02"
subsystem: identity-authorization
tags: [workos, authkit, memberships, sessions, authorization, postgresql]
requires:
  - phase: 02-identity-organizations-and-report-library
    provides: Plan 02-01 canonical accounts, external identities, auth state, and opaque sessions
  - phase: 02-identity-organizations-and-report-library
    provides: Plan 02-13 provider-neutral passwordless browser callback composition
provides:
  - Replaceable WorkOS AuthKit adapter with deterministic fake-provider verification
  - Canonical local organization memberships as the sole product authorization authority
  - Resilient provider-session refresh and commit-before-provider logout boundaries
  - Connection-free local membership and administrator authorization services
affects: [02-03-organizations, 02-04-report-library, 02-12-authenticated-shell]
tech-stack:
  added: [workos==10.8.0, Flask-WTF==1.3.0]
  patterns: [provider-adapter, local-membership-authority, opaque-server-sessions, caller-owned-transactions]
key-files:
  created:
    - migrations/versions/0016_phase2_memberships.py
    - roster_scout/authorization.py
    - tests/fixtures/workos-emulate.config.yaml
  modified:
    - requirements.txt
    - roster_scout/models.py
    - roster_scout/identity.py
    - roster_scout/sessions.py
    - tests/test_identity.py
key-decisions:
  - "Only an active canonical local membership grants product access; provider organization and domain claims never create authorization."
  - "Transient provider refresh failures preserve the local session, while explicit terminal provider failures revoke it."
  - "Logout revokes and commits the local session before a separate best-effort provider logout call."
requirements-completed: [AUTH-01, AUTH-02, AUTH-03, AUTH-04, AUTH-05, ORG-01, ORG-04, ORG-07, SECU-03, SECU-05]
duration: 12 min
completed: 2026-11-11
---

# Phase 2 Plan 02: Managed Identity and Local Authorization Summary

**WorkOS AuthKit now sits behind a replaceable adapter while canonical local memberships and resilient opaque sessions remain the only authority for tenant access.**

## Performance

- **Duration:** 12 min
- **Started:** 2026-11-11T22:48:00Z
- **Completed:** 2026-11-11T22:59:43Z
- **Tasks:** 2
- **Files modified:** 8

## Accomplishments

- Added an official WorkOS SDK adapter for authorization URL generation, callback exchange, sealed-session refresh, and provider logout without exposing provider details to Flask routes.
- Added canonical local organization memberships with active/inactive lifecycle, administrator/member roles, and a composite database constraint that prevents a session from selecting an organization without a matching account membership.
- Added connection-free authorization helpers that reload the local session and membership on each decision, clear stale organization selection, and enforce administrator access locally.
- Added resilient session refresh behavior: successful refresh rotates sealed provider state, transient provider failure preserves the local session, and terminal failure revokes it.
- Split logout into a transaction-owned local revocation step and a separate best-effort provider step so an identity-provider outage cannot prevent local logout.

## Task Commits

Each task was committed atomically:

1. **Task 1: Define provider, membership, refresh, and redaction contracts** - `cd2397e` (test)
2. **Task 2: Add the managed identity adapter and local authorization backend** - `addf5f6` (feat)

## Files Created/Modified

- `requirements.txt` - Pins the audited WorkOS and Flask-WTF dependencies.
- `roster_scout/models.py` - Defines canonical organization memberships and membership-bound active session selection.
- `migrations/versions/0016_phase2_memberships.py` - Creates membership constraints and replaces direct organization selection with a composite membership foreign key.
- `roster_scout/identity.py` - Implements the fake and WorkOS provider adapters plus redacted refresh classification.
- `roster_scout/sessions.py` - Implements resilient refresh and commit-before-provider logout boundaries.
- `roster_scout/authorization.py` - Provides local membership lookup, organization context, and administrator enforcement.
- `tests/test_identity.py` - Covers provider-claim isolation, membership authorization, session refresh/revocation, logout ordering, and secret redaction.
- `tests/fixtures/workos-emulate.config.yaml` - Supplies synthetic provider-emulator identity data for deterministic verification.

## Decisions Made

- Product authorization comes only from an active `organization_memberships` row. A verified identity can authenticate an account, but provider organization, domain, or JIT claims cannot grant tenant access.
- Session refresh distinguishes terminal invalid/revoked provider state from throttling, transport failure, and provider outage. Only terminal state revokes the local session.
- Local logout and provider logout are deliberately separate operations. The caller commits local revocation before attempting provider logout so the local security boundary remains available during provider outages.
- Service helpers remain connection-free and transaction-neutral. Callers inject the SQLAlchemy session and retain commit/rollback ownership.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

- Full database-backed verification required the approved local PostgreSQL test service. No production provider, database, email, or deployment resources were contacted.

## User Setup Required

None for deterministic development and test execution. Production WorkOS credentials, redirect URLs, organization configuration, and deployment remain a later explicit deployment checkpoint.

## Next Phase Readiness

- Organization invitation and chooser plans can create and select canonical memberships without trusting provider-side tenant claims.
- The authenticated shell can authorize every request from the opaque local session and current local membership.
- Report-library routes can enforce organization and administrator access without importing WorkOS or opening their own database connections.

## Verification

- Focused identity, membership, session, and redaction contracts - **11 tests passed**
- `DATABASE_URL=... .venv/bin/python -m unittest tests.test_identity -v` - **24 tests passed**
- Migration downgrade to `0015`, upgrade to head, and `alembic check` - **passed**
- `DATABASE_URL=... .venv/bin/python -m unittest discover -s tests -v` - **453 tests passed**
- Python compilation for required application, scraper, operational scripts, and identity/authorization modules - **passed**
- Required `templates/index.html` and `templates/demo.html` checks - **passed**
- `scripts/validate_ncaa_catalog.py` - **942 selectable teams; valid**
- `node --test tests/js/*.test.mjs` - **13 browser tests passed**
- `git diff --check` - **passed**
- Stub scan across all plan-created and plan-modified files - **no blocking stubs found**

## Security and Threat Surface

- The new provider network boundary is contained inside `WorkOSIdentityProvider`; tests use only the fake adapter and synthetic emulator fixture.
- Provider exceptions, sealed session values, callback codes, identity payloads, and logout URLs are excluded from application logs and browser-facing errors.
- No route, public endpoint, credential file, production provider resource, or deployment configuration changed in this plan.

## Self-Check: PASSED

- Created files exist: `migrations/versions/0016_phase2_memberships.py`, `roster_scout/authorization.py`, and `tests/fixtures/workos-emulate.config.yaml`.
- Task commits exist: `cd2397e` and `addf5f6`.
- Required focused, migration, full-repository, catalog, browser, compilation, and diff verification completed successfully.
- User-owned untracked planning copies and `brag-output/` remain untouched.

---
*Phase: 02-identity-organizations-and-report-library*
*Completed: 2026-11-11*
