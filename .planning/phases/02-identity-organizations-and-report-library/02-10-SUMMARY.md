---
phase: 02-identity-organizations-and-report-library
plan: "10"
subsystem: authorization
tags: [flask, postgresql, tenant-isolation, audit, idor, security]

requires:
  - phase: 02-identity-organizations-and-report-library
    provides: Plans 02-05 through 02-09 tenant-scoped report surfaces and Plan 02-16 authenticated download acknowledgement
provides:
  - Exhaustive protected-route and two-organization substitution matrix
  - Fixed append-only authorization denial evidence derived from active membership
  - Secret-free audit allowlist and database immutability verification
  - Cross-channel proof that browser and provider scope claims are inert
affects: [phase-2-release, authorization, report-library, security-audit]

tech-stack:
  added: []
  patterns:
    - Revalidated AuthorizationContext is the only producer of denial actor and organization scope
    - Foreign and absent resources share one response while appending the same fixed audit shape

key-files:
  created:
    - tests/test_phase2_tenant_isolation.py
  modified:
    - app.py
    - roster_scout/audit.py
    - roster_scout/authorization.py
    - roster_scout/report_routes.py

key-decisions:
  - "Authorization denial events use one fixed authorization.deny/denied/not_found tuple and receive actor and organization IDs only from the revalidated AuthorizationContext."
  - "Denial-audit persistence failure logs one fixed category and never changes the non-enumerating customer response."
  - "Authenticated acknowledgement records a denial only after a valid current membership exists and the signed principal binding proves foreign scope."

patterns-established:
  - "Scoped denial boundary: resource loader fails -> original transaction exits -> fixed denial event commits separately -> identical HTML or JSON 404 returns."
  - "Route inventory regression: every protected, account-only, legacy, acknowledgement, and report method must remain explicitly named by the tenant-isolation suite."

requirements-completed: [AUTH-01, ORG-01, ORG-06, SECU-01, SECU-02, SECU-03, SECU-04]

duration: 11 min
completed: 2026-11-13
---

# Phase 2 Plan 10: Tenant Isolation and Sanitized Audit Summary

**Every Phase 2 report surface now makes foreign resources indistinguishable from absence while recording one canonical, append-only, secret-free denial event from the active local membership.**

## Performance

- **Duration:** 11 minutes
- **Started:** 2026-11-13T06:33:32Z
- **Completed:** 2026-11-13T06:44:28Z
- **Tasks:** 2
- **Files modified:** 5

## Accomplishments

- Added a two-organization matrix covering report HTML, status JSON, refresh GET/POST, failed-job retry, snapshot substitution, historical artifact preparation, authenticated acknowledgement, legacy `/api/teams`, and `/scrape` behavior.
- Proved foreign and random absent identifiers produce byte-equivalent status, body, and content type while every scoped denial appends fixed canonical audit evidence.
- Proved organization, account, role, provider-organization, and domain claims remain inert across query, form, JSON, and header channels.
- Centralized denial audit production behind the revalidated `AuthorizationContext`, preserving strict action/outcome/reason allowlists and the existing database append-only trigger.
- Verified audit rows and logs exclude email, IP, user agent, tokens, assertions, cookies, report data, raw requests, and exception text.

## Task Commits

Each task was committed atomically:

1. **Task 1: Wave 0 — build the complete two-organization denial and audit matrix** - `02740d0` (test)
2. **Task 2: Centralize tenant denial and sanitized audit behavior** - `b39db71` (feat)

## Files Created/Modified

- `tests/test_phase2_tenant_isolation.py` - Complete route inventory, two-organization denial matrix, untrusted-scope matrix, allowlist checks, secret scan, and direct audit mutation rejection.
- `roster_scout/audit.py` - Fixed non-enumerating authorization-denial producer over the strict append-only audit primitive.
- `roster_scout/authorization.py` - Membership-context-only denial recording boundary.
- `roster_scout/report_routes.py` - Identical HTML/JSON not-found helpers that append scoped denial evidence without exposing existence.
- `app.py` - Authenticated acknowledgement records a fixed denial for a foreign signed principal binding.

## Decisions Made

- Denial evidence is created only when the request already has a revalidated active membership; browser and provider claims never enter the event producer.
- The candidate resource UUID may be stored as an opaque canonical target ID, but no existence result, request material, or customer data is persisted.
- Audit availability does not become an enumeration side channel: a database failure emits only `authorization_denial_audit_failed` and returns the same original 404.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

- The managed sandbox initially blocked localhost PostgreSQL and Git index access. Approved operations were limited to the disposable `roster_scout_test` database and this repository's required atomic commits.
- The full suite continues to emit pre-existing psycopg connection `ResourceWarning` messages, but all tests pass.

## Verification

- Plan security regression (`tenant_isolation`, `SecretRedactionTests`, `AuthGateRouteMatrixTests`) - **11 tests passed**.
- Adjacent report action and authenticated acknowledgement regression - **20 tests passed**.
- Full Python discovery - **530 tests passed**.
- Browser behavior suites - **26 tests passed**.
- Python compilation for application, scraper, required scripts, changed authorization/audit modules, and the new test module - **passed**.
- NCAA selector catalog - **942 selectable teams; valid**.
- Required templates and `git diff --check` - **passed**.

## Known Stubs

None. The audit and authorization paths are connected to the live report and authenticated acknowledgement routes; test-only empty collections are fixture state, not product placeholders.

## User Setup Required

None - no dependency, schema, provider, broker, storage, or deployment configuration changed.

## Next Phase Readiness

- The complete Phase 2 tenant boundary is ready for release verification and the remaining Phase 2 release plan.
- No blockers remain for Plan 02-11.

## Self-Check: PASSED

- All five implementation/test files and this summary exist.
- Task commits `02740d0` and `b39db71` are present in repository history.
- Focused, adjacent, full Python, browser, compilation, catalog, template, and diff claims were rerun after the final implementation commit.

---
*Phase: 02-identity-organizations-and-report-library*
*Completed: 2026-11-13*
