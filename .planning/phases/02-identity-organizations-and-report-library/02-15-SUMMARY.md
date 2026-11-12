---
phase: 02-identity-organizations-and-report-library
plan: "15"
subsystem: reports
tags: [flask, postgresql, ncaa, csrf, accessibility, idempotency]

requires:
  - phase: 02-06
    provides: Atomic actor-bound report acceptance, durable job convergence, usage, audit, and dispatch outbox
  - phase: 02-12
    provides: Default-deny authentication gate, SQL-backed sessions, global CSRF, and authenticated product shell
provides:
  - Protected server-rendered report creation workflow
  - Exact current-catalog NCAA team validation before durable acceptance
  - Accessible progressive D1/D2/D3 typeahead with safe DOM rendering
  - Post-commit dispatch of accepted durable jobs
affects: [report-detail, report-status, snapshot-history, report-refresh]

tech-stack:
  added: []
  patterns:
    - Thin Flask route composition over atomic domain acceptance
    - Server re-resolution of browser selector identity
    - DOM-safe vanilla JavaScript progressive enhancement

key-files:
  created:
    - templates/report_new.html
    - static/report_form.js
  modified:
    - roster_scout/report_routes.py
    - tests/test_report_library.py
    - tests/test_identity.py

key-decisions:
  - "The browser submits only division, exact season-team identity, team label, and an opaque action UUID; account, organization, and role always come from the revalidated authorization context."
  - "The route re-resolves the submitted season-team identity against the current server catalog and rejects typed-only, stale, mismatched, or malformed selections before opening the acceptance transaction."
  - "Durable acceptance commits before outbox dispatch; a broker or dispatch-claim failure leaves the durable intent queued and does not undo the accepted request."

patterns-established:
  - "Create-report composition calls the domain acceptance service once, then drains only the accepted job's outbox intent after commit."
  - "Dynamic NCAA result content is built with createElement/textContent and clears the hidden identity after every edit or division change."

requirements-completed: [ORG-01, ORG-04, SNAP-07, SECU-02, SECU-04]

duration: 28 min
completed: 2026-11-12
---

# Phase 2 Plan 15: Exact Report Creation Summary

**Protected NCAA report creation now revalidates exact catalog identity and authenticated organization scope before atomically accepting and dispatching one durable report job.**

## Performance

- **Duration:** 28 min
- **Started:** 2026-11-12T01:15:13Z
- **Completed:** 2026-11-12T01:42:43Z
- **Tasks:** 2
- **Files modified:** 5

## Accomplishments

- Added protected `GET/POST /reports/new` composition that derives the actor and organization exclusively from `g.authorization_context`, passes a canonical DTO into Plan 02-06, and redirects accepted or converged requests to the canonical report URL.
- Added server-side exact selector validation that rejects typed-only text, stale IDs, label tampering, malformed divisions, and invalid action UUIDs without creating reports, jobs, usage events, or audit rows.
- Added an accessible 560px report form and progressive vanilla-JavaScript selector with D1/D2/D3 switching, current catalog loading, visible exact selection, Arrow/Home/End/Enter/Escape behavior, hidden-ID clearing, safe `textContent` rendering, and explicit pending state.
- Preserved the durable boundary by committing report acceptance before dispatch and leaving failed dispatch attempts queued for existing reconciliation.

## Task Commits

Each task was committed atomically:

1. **Task 1: Wave 0 — specify exact-selector route and browser composition** - `f011d8c` (test)
2. **Task 2: Implement protected exact-selector report creation** - `9f2a7d9` (feat)

## Files Created/Modified

- `roster_scout/report_routes.py` - Validates current NCAA catalog identity, composes actor-bound acceptance, and drains durable dispatch only after commit.
- `templates/report_new.html` - Renders the accessible server form, fixed validation summary, segmented division control, exact selection, and pending-status regions.
- `static/report_form.js` - Progressively loads and filters the authenticated catalog with keyboard/ARIA semantics and DOM-safe rendering.
- `tests/test_report_library.py` - Covers CSRF, typed-only/stale/tampered rejection, server-derived authority, duplicate convergence, redirect behavior, and selector accessibility contracts.
- `tests/test_identity.py` - Records `/reports/new` in the explicit protected route inventory.

## Decisions Made

- A catalog row's stable `team_id` is preferred for the canonical team identity; the exact `season_team_id` is used when the current catalog intentionally lacks a stable team ID, matching the validated selector contract.
- The catalog year is the canonical source-season identity already used by the Plan 02-06 DTO contract, while `season_team_id` remains the exact current team-season identity.
- Repeated acceptance redirects add the fixed `notice=active` signal without creating another active job, usage event, or dispatch intent.
- Dynamic team logos use only the same-origin numeric team-logo endpoint; catalog strings enter the document through `textContent`, never HTML injection.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Updated the exact application route inventory**
- **Found during:** Task 2 route registration
- **Issue:** The existing security regression asserts the complete Flask URL map, so the planned endpoint made that inventory stale.
- **Fix:** Added `/reports/new` with its `GET` and `POST` methods to the protected-by-default route matrix.
- **Files modified:** `tests/test_identity.py`
- **Verification:** `tests.test_identity.AuthGateRouteMatrixTests.test_every_current_route_is_explicitly_public_account_only_or_protected` passes.
- **Committed in:** `9f2a7d9`

**2. [Rule 2 - Missing critical functionality] Kept committed acceptance independent from transient dispatch failure**
- **Found during:** Task 2 post-commit dispatch composition
- **Issue:** A database failure while claiming the already-committed outbox intent could otherwise return an unhandled error after the request was durably accepted.
- **Fix:** Dispatch runs after commit, is scoped to the accepted job, and logs a fixed warning while leaving the durable intent queued if claiming fails.
- **Files modified:** `roster_scout/report_routes.py`
- **Verification:** Focused acceptance, dispatch/job, and full repository suites pass; broker exceptions remain handled by the existing drainer.
- **Committed in:** `9f2a7d9`

---

**Total deviations:** 2 auto-fixed (1 bug, 1 missing critical functionality)
**Impact on plan:** Both changes preserve explicit authorization coverage and the durable acceptance guarantee without expanding product scope.

## Issues Encountered

- The managed sandbox initially blocked the shared uv cache and local PostgreSQL socket. Approved local test commands used only the existing environment and disposable `roster_scout_test` database.
- The full 505-test suite is migration-heavy and took about four minutes; it completed successfully without needing to recreate the test database.
- No production database, broker, live NCAA smoke boundary, deployment resource, or external identity provider was contacted.

## User Setup Required

None - no new dependency, environment variable, external service, or deployment configuration was introduced.

## Next Phase Readiness

- The canonical redirect target is ready for the report detail/status plan to render accepted jobs, polling, snapshots, convergence notice, and later explicit refresh controls.
- Exact selector validation, actor-bound acceptance, CSRF, usage, audit, and outbox dispatch are now connected end to end with no blocker.

## Verification

- Plan-focused Python regression suite - **97 tests passed**
- Full Python discovery - **505 tests passed**
- Exact authentication route matrix - **passed**
- JavaScript suite - **16 tests passed**
- `node --check static/report_form.js` - **passed**
- Application, scraper, report-route, smoke-script, and catalog-script compilation - **passed**
- NCAA selector catalog - **942 selectable teams; valid**
- Required template existence and `git diff --check` - **passed**
- Stub scan across created/modified plan files - **no blocking stubs found**

## Security and Threat Surface

- The new protected route, catalog read, CSRF mutation, selector trust boundary, and post-commit dispatch are all covered by Plan 02-15's STRIDE register; no unplanned trust boundary was introduced.
- Browser-supplied `organization_id`, `account_id`, and `role` values are ignored, while current membership is revalidated by both the default-deny request gate and atomic acceptance service.
- Selector errors and service errors use fixed copy and do not render provider responses, exception details, tenant identifiers, or database state.

## Known Stubs

None. Empty JavaScript arrays and nullable selection values are transient selector state populated from `/api/teams`, not UI placeholders.

## Self-Check: PASSED

- Both created implementation files, all three modified files, and task commits `f011d8c` and `9f2a7d9` exist.
- Focused, full, JavaScript, compile, catalog, route-inventory, and diff checks were rerun after the implementation commit.

---
*Phase: 02-identity-organizations-and-report-library*
*Completed: 2026-11-12*
