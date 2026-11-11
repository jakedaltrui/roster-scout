---
phase: 02-identity-organizations-and-report-library
plan: "13"
subsystem: authentication
tags: [flask, passwordless, callback, opaque-sessions, provider-neutral]
requires:
  - phase: 02-identity-organizations-and-report-library
    provides: Plan 02-01 canonical accounts, one-time auth state, and opaque sessions
provides:
  - Branded credential-free sign-in handoff
  - Replay-safe provider-neutral callback composition
  - Fixed redacted callback failures and secure session-cookie issuance
  - Initial no-membership browser state
affects: [02-02-identity-backend, 02-12-authenticated-shell, organizations]
tech-stack:
  added: []
  patterns: [thin-flask-blueprint, consume-before-exchange, injected-identity-boundary]
key-files:
  created:
    - roster_scout/auth_routes.py
    - templates/sign_in.html
    - templates/auth_error.html
  modified:
    - app.py
    - tests/test_identity.py
key-decisions:
  - "Callback state commits as consumed before provider code exchange, so failed exchanges cannot replay the bearer state."
  - "Only exact local return paths are accepted; the default successful callback lands on the no-membership state."
  - "Routes orchestrate injected provider, account, and session services and contain no identity SQL."
requirements-completed: [AUTH-01, AUTH-02, AUTH-03, AUTH-05, SECU-03]
duration: 14 min
completed: 2026-11-11
---

# Phase 2 Plan 13: Passwordless Browser Callback Composition Summary

**A credential-free Flask sign-in handoff now composes one-time callback state, canonical account linking, opaque sessions, secure cookies, and redacted failure states behind a provider-neutral boundary.**

## Performance

- **Duration:** 14 min
- **Started:** 2026-11-11T22:32:26Z
- **Completed:** 2026-11-11T22:46:43Z
- **Tasks:** 2
- **Files modified:** 5

## Accomplishments

- Added browser-level contracts for the branded sign-in handoff, safe return-path handling, callback replay prevention, account linking, session creation, and failure redaction.
- Added a thin authentication blueprint that consumes callback state before any provider exchange and composes the existing provider-neutral identity, account, and session services without route-layer SQL.
- Added accessible branded sign-in, no-membership, and fixed error states with no local password or email credential controls.
- Issued the opaque session token only through a `Secure`, `HttpOnly`, `SameSite=Lax`, host-only cookie and kept provider failures out of browser responses and logs.

## Task Commits

Each task was committed atomically:

1. **Task 1: Specify the browser sign-in and callback contract** - `ead99e7` (test)
2. **Task 2: Compose the provider-neutral sign-in and callback routes** - `e776b50` (feat)
3. **Verification fix: Isolate callback log assertion** - `e5f4634` (fix)

## Files Created/Modified

- `roster_scout/auth_routes.py` - Thin sign-in, callback, fixed-error, and no-membership route composition.
- `templates/sign_in.html` - Branded passwordless/SSO provider handoff with no credential collection.
- `templates/auth_error.html` - Fixed, sanitized authentication failure presentation.
- `app.py` - Authentication blueprint registration and injected provider/session dependencies.
- `tests/test_identity.py` - Browser boundary, callback replay, cookie, return-path, and redaction contracts.

## Decisions Made

- Callback state is committed as consumed before exchanging the provider authorization code. This intentionally makes the callback non-replayable even when the provider exchange fails.
- Callback return targets use an exact local allowlist (`/organizations/none`, `/organizations/select`, `/reports`); missing or invalid targets resolve to the no-membership state.
- The blueprint remains provider-neutral and receives provider, account, and session boundaries through app configuration, leaving the WorkOS production adapter to Plan 02-02.
- Browser and log failures use fixed messages and stable error codes rather than provider exception text or identity payloads.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Isolated callback log assertion from prior test logger state**

- **Found during:** Full repository verification
- **Issue:** Earlier route tests could leave the named `app` logger disabled, making the callback redaction assertion dependent on test order.
- **Fix:** Temporarily re-enabled the named logger for the scoped assertion and restored its previous state afterward.
- **Files modified:** `tests/test_identity.py`
- **Commit:** `e5f4634`

## Issues Encountered

- Full database-backed verification required the approved local PostgreSQL test service; no production services or credentials were used.

## User Setup Required

None for this plan. The app uses the existing injected fake provider boundary in local/test execution. The production WorkOS adapter and its secret configuration remain owned by Plan 02-02 and a later deployment checkpoint.

## Next Phase Readiness

- Plan 02-02 can bind WorkOS authorization and callback exchange to the provider-neutral route boundary without changing browser contracts.
- The organization chooser and authenticated shell can consume the no-membership and exact return-path states.
- Production configuration and deployment were deliberately untouched.

## Verification

- `DATABASE_URL=... .venv/bin/python -m unittest discover -s tests -v` - **447 tests passed**
- Python compilation for `app.py`, `roster_scraper.py`, the live smoke/catalog scripts, and `roster_scout/auth_routes.py` - **passed**
- Required `templates/index.html` and `templates/demo.html` checks - **passed**
- `scripts/validate_ncaa_catalog.py` - **942 selectable teams; valid**
- `git diff --check` - **passed**
- Stub scan across all plan-created and plan-modified files - **no blocking stubs found**

## Self-Check: PASSED

- Created files exist: `roster_scout/auth_routes.py`, `templates/sign_in.html`, `templates/auth_error.html`.
- Task commits exist: `ead99e7`, `e776b50`, `e5f4634`.
- Required repository verification completed successfully.
- User-owned untracked planning copies and `brag-output/` remain untouched.

---
*Phase: 02-identity-organizations-and-report-library*
*Completed: 2026-11-11*
