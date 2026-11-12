---
phase: 02-identity-organizations-and-report-library
plan: 08
subsystem: reports
tags: [flask, postgresql, jinja, snapshots, idempotency, multi-tenant]

requires:
  - phase: 02-identity-organizations-and-report-library
    provides: saved-snapshot-first report detail, protected report acceptance, durable job/outbox authority
provides:
  - explicit CSRF-protected live-refresh confirmation and acceptance
  - atomic organization-scoped refresh convergence with usage and audit evidence
  - predecessor-linked immutable successor snapshots with cross-build cache isolation
  - newest-first 25-item snapshot history pages with freshness, lineage, artifact state, and failed attempts
affects: [02-09-report-downloads, report-detail, snapshot-history, refresh-jobs]

tech-stack:
  added: []
  patterns: [report-row refresh serialization, explicit successor acceptance, independently paged immutable history]

key-files:
  created: [.planning/phases/02-identity-organizations-and-report-library/02-08-SUMMARY.md]
  modified: [roster_scout/repositories.py, roster_scout/reports.py, roster_scout/report_routes.py, templates/report_detail.html, tests/test_report_library.py, tests/test_identity.py]

key-decisions:
  - "Explicit refresh serializes on the organization-scoped report row and converges every active click on one durable job, usage event, and audit event."
  - "A refresh always receives a new accepted job identity after the prior attempt is terminal; Phase 1 same-job retry evidence remains reusable but evidence from an older build never is."
  - "Latest snapshot, active build, failed attempts, and the requested history page are independent projections so transient work never replaces or mutates published data."

patterns-established:
  - "Refresh acceptance: active membership lock -> tenant report lock -> active-job convergence/new job -> usage/audit -> commit -> outbox drain."
  - "Immutable history: published snapshots page newest-first while latest snapshot remains a separate dominant projection."

requirements-completed: [SNAP-05, SNAP-06, SNAP-07, ORG-06, SECU-01, SECU-02, SECU-04]

duration: 14min
completed: 2026-11-12
---

# Phase 2 Plan 8: Explicit Refresh and Immutable History Summary

**Tenant-serialized live refresh with predecessor-linked immutable snapshots, duplicate convergence, and responsive dated history pages**

## Performance

- **Duration:** 14 min
- **Started:** 2026-11-12T16:15:49Z
- **Completed:** 2026-11-12T16:30:10Z
- **Tasks:** 2
- **Files modified:** 6

## Accomplishments

- Added an explicit confirmation page and CSRF-protected POST that names the current snapshot and promises it will remain unchanged before starting live NCAA collection.
- Added a caller-owned refresh service that locks active membership and the tenant report, converges duplicate clicks, and atomically records the job/outbox, authenticated-use event, and fixed audit event.
- Proved the Phase 1 worker creates a predecessor-linked successor draft, ignores another build's cached sections, and leaves the prior snapshot, section evidence, artifact checksum, and object key unchanged.
- Added 25-item newest-first history pages with separate latest snapshot, active build, sanitized failed attempts, source freshness, predecessor dates, and truthful artifact availability on desktop and mobile.

## Task Commits

Each task was committed atomically:

1. **Task 1: Specify explicit refresh lineage and immutable history (RED)** - `21df8c3` (test)
2. **Task 2: Implement explicit refresh, successor lineage, and dated history (GREEN)** - `ddc05d9` (feat)

## Files Created/Modified

- `roster_scout/repositories.py` - Adds tenant report locking, bounded history offsets/counts, latest-snapshot independence, predecessor metadata, artifact availability, and failed-attempt reads.
- `roster_scout/reports.py` - Accepts atomic explicit refreshes and builds independently paged detail/history projections.
- `roster_scout/report_routes.py` - Serves refresh confirmation/POST, validates UUID action keys, drains accepted outbox work after commit, and parses history pages.
- `templates/report_detail.html` - Renders refresh actions/confirmation, active status replacement, lineage/freshness/artifact fields, failed attempts, and responsive pagination.
- `tests/test_report_library.py` - Verifies CSRF, identical foreign/absent denial, duplicate concurrency, immutable predecessor evidence, cache isolation, and 25-item history pages.
- `tests/test_identity.py` - Extends the fail-closed exhaustive route inventory for the protected refresh route.

## Decisions Made

- Refresh confirmation is a server-rendered accessible dialog page, so the state-changing POST remains usable without JavaScript.
- Existing active work suppresses a second confirmation and redirects to the single active status panel; the service remains the final concurrency authority for overlapping requests.
- Published history is never inferred from job order. Only Ready/Partial snapshots enter history, while failed jobs appear in a separate sanitized build-attempt region.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Route security] Extended the exhaustive protected-route inventory**
- **Found during:** Task 2 (Implement explicit refresh, successor lineage, and dated history)
- **Issue:** The new GET/POST refresh route would make the explicit route classification test stale.
- **Fix:** Added the refresh route to the protected route inventory without widening either public allowlist.
- **Files modified:** `tests/test_identity.py`
- **Verification:** `AuthGateRouteMatrixTests.test_every_current_route_is_explicitly_public_account_only_or_protected` passes.
- **Committed in:** `ddc05d9`

**2. [Rule 2 - Truthful history] Added artifact state and failed-attempt projections**
- **Found during:** Task 2 (Implement explicit refresh, successor lineage, and dated history)
- **Issue:** A snapshot-only page would omit required artifact availability and could incorrectly present failed jobs as snapshots.
- **Fix:** Queried ready workbook artifacts separately and rendered failed jobs in a sanitized build-attempt region.
- **Files modified:** `roster_scout/repositories.py`, `roster_scout/reports.py`, `templates/report_detail.html`
- **Verification:** Focused refresh/history suite and full report-job suite pass.
- **Committed in:** `ddc05d9`

---

**Total deviations:** 2 auto-fixed (2 missing critical/correctness)
**Impact on plan:** Both changes preserve fail-closed routing and truthful history without expanding the product feature boundary.

## Issues Encountered

- Full discovery ran 513 tests with 11 unrelated identity/organization route failures. Those fixtures create sessions at the fixed time `2026-11-11T19:00:00Z`; on 2026-11-12 they exceed the app's 12-hour idle limit and correctly redirect to login. The focused 17-test Plan 02-08 contract is green, and the time-dependent fixture debt is recorded in `deferred-items.md`.
- Running the report-job migration suite before identity route tests produces the same pre-existing expired-session redirects; all report-job behavior relevant to refresh, live recollection, same-job evidence reuse, and publication passed.

## Verification

- Plan 02-08 focused Python contract: **17 passed**.
- Plan 02-08 plus Phase 1 report-job behavior: all refresh/job cases passed; one unrelated fixed-clock logout fixture failed when combined after migration tests.
- Full Python discovery: **513 run, 502 passed, 11 unrelated fixed-clock identity/organization failures**.
- Node browser suites: **23 passed**.
- Python compile checks: passed.
- NCAA catalog validation: passed, 942 selectable teams.
- Required templates present and `git diff --check`: passed.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Plan 02-09 can bind exact historical artifact downloads to the new history rows without changing refresh or lineage authority.
- Explicit refresh, immutable history, duplicate convergence, and tenant denial contracts are complete.
- The time-dependent identity fixture issue is unrelated to this plan and recorded for a later test-maintenance pass.

## Self-Check: PASSED

- Created summary exists: `.planning/phases/02-identity-organizations-and-report-library/02-08-SUMMARY.md`.
- Task commits exist: `21df8c3`, `ddc05d9`.
- Focused refresh/history/idempotency tests, Node tests, compile checks, catalog validation, templates, and diff hygiene pass.

---
*Phase: 02-identity-organizations-and-report-library*
*Completed: 2026-11-12*
