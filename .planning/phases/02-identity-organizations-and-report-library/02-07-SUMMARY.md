---
phase: 02-identity-organizations-and-report-library
plan: 07
subsystem: ui
tags: [flask, postgresql, jinja, polling, accessibility, multi-tenant]

requires:
  - phase: 02-identity-organizations-and-report-library
    provides: organization-scoped report library, exact report creation, durable snapshots and jobs
provides:
  - server-rendered report detail with the latest published snapshot kept primary
  - tenant-scoped sanitized report status endpoint
  - bounded visibility-aware progressive polling with accessible state announcements
  - append-only snapshot access audit events
affects: [02-08-report-history, 02-09-report-downloads, report-detail, snapshot-access]

tech-stack:
  added: []
  patterns: [saved-snapshot-first rendering, complete safe status projections, bounded progressive polling]

key-files:
  created: [templates/report_detail.html, static/report_status.js, tests/js/report_status.test.mjs]
  modified: [roster_scout/repositories.py, roster_scout/reports.py, roster_scout/report_routes.py, tests/test_report_library.py, tests/test_identity.py]

key-decisions:
  - "The newest published snapshot remains the dominant report content while a refresh job runs."
  - "Status JSON is a complete allowlisted view model; database identifiers and raw failure details never cross the route boundary."
  - "Automatic status polling pauses in hidden tabs, backs off from 3 to 5 to 10 seconds, and stops after terminal state or 15 minutes."

patterns-established:
  - "Tenant-scoped detail projection: every report, snapshot, job, and section query includes the active organization."
  - "Durable UI preservation: transient polling failures update only a fixed status note and never replace saved report content."

requirements-completed: [ORG-06, SNAP-06, SECU-01, SECU-02, SECU-05]

duration: 11min
completed: 2026-11-12
---

# Phase 2 Plan 7: Report Detail and Progressive Status Summary

**Saved-snapshot-first report detail with tenant-safe status JSON, bounded polling, and audited snapshot access**

## Performance

- **Duration:** 11 min
- **Started:** 2026-11-12T16:02:22Z
- **Completed:** 2026-11-12T16:13:22Z
- **Tasks:** 2
- **Files modified:** 8

## Accomplishments

- Added an organization-scoped report detail page that keeps the newest published snapshot visible and dominant while a refresh is queued, collecting, or saving.
- Added a complete sanitized status projection with fixed failure copy and identical absent/foreign not-found behavior.
- Added progressive enhancement that polls at 3/5/10-second intervals, pauses in hidden tabs, stops on terminal states or timeout, and announces transitions without noisy repeats.
- Appended a fixed `snapshot.access` audit event whenever an authenticated member opens the latest saved snapshot.

## Task Commits

Each task was committed atomically:

1. **Task 1: Specify report detail, status, and polling contracts (RED)** - `712ff57` (test)
2. **Task 2: Implement saved-first detail and bounded status polling (GREEN)** - `1c45c5f` (feat)

## Files Created/Modified

- `roster_scout/repositories.py` - Loads one bounded, fully tenant-scoped report detail projection.
- `roster_scout/reports.py` - Builds safe detail, history, section, and status view models.
- `roster_scout/report_routes.py` - Serves protected report detail and status routes and records snapshot access.
- `templates/report_detail.html` - Renders latest snapshot, active refresh, and bounded snapshot history responsively.
- `static/report_status.js` - Provides visibility-aware bounded polling and transition-only live announcements.
- `tests/test_report_library.py` - Verifies saved-snapshot dominance, safe JSON, audit events, and tenant isolation.
- `tests/js/report_status.test.mjs` - Verifies cadence, pause/resume, terminal handling, timeout, and network failures.
- `tests/test_identity.py` - Keeps the exhaustive route-protection inventory current.

## Decisions Made

- The latest published snapshot is rendered before active build state so a refresh never displaces durable scouting data.
- Failure categories are translated to a small fixed copy allowlist; raw worker messages are not exposed.
- A status response always replaces the full transient status model, while network errors only surface a fixed note after two consecutive failures.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Route inventory] Extended the exhaustive protected-route contract**
- **Found during:** Task 2 (Implement report detail and status routes)
- **Issue:** Adding two Flask routes made the repository's exhaustive route inventory stale.
- **Fix:** Added both routes to the explicit inventory so future unclassified endpoints still fail closed in tests.
- **Files modified:** `tests/test_identity.py`
- **Verification:** `AuthGateRouteMatrixTests.test_every_current_route_is_explicitly_public_account_only_or_protected` passes.
- **Committed in:** `1c45c5f`

---

**Total deviations:** 1 auto-fixed (1 correctness)
**Impact on plan:** The adjustment preserves the existing fail-closed route security contract with no feature scope expansion.

## Issues Encountered

- The initial snapshot-section fixture attempted to add child rows after publication and correctly hit the database immutability trigger. The fixture now creates sections while the snapshot is a draft and publishes only after the rows are complete.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Report history can build on the bounded newest-first snapshot projection.
- Download delivery can attach artifact actions without changing the saved-first detail or polling contracts.
- No blockers remain for subsequent Phase 2 plans.

## Self-Check: PASSED

- Created files exist: `templates/report_detail.html`, `static/report_status.js`, `tests/js/report_status.test.mjs`.
- Task commits exist: `712ff57`, `1c45c5f`.
- Focused Python contracts, full Python suite, Node tests, compile checks, NCAA catalog validation, and `git diff --check` pass.

---
*Phase: 02-identity-organizations-and-report-library*
*Completed: 2026-11-12*
