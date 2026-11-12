---
phase: 02-identity-organizations-and-report-library
plan: "05"
subsystem: report-library
tags: [flask, postgresql, sqlalchemy, authorization, accessibility, responsive-ui]

requires:
  - phase: 02-04
    provides: Revalidated active-organization context and organization switching
  - phase: 02-12
    provides: Default-deny route gate, global CSRF, and authenticated product shell
provides:
  - Active-organization-scoped persistent opponent report library
  - Bounded newest-activity pagination with independent snapshot and build state
  - Equivalent accessible desktop-table and mobile-card report views
affects: [02-06, 02-07, 02-15, report-detail, report-creation, customer-portal]

tech-stack:
  added: []
  patterns:
    - Server-derived tenant context on every report, snapshot, job, and section query
    - Fixed 25-row server-rendered pagination ordered by newest report activity
    - Truthful page-level database failure distinct from a valid empty library

key-files:
  created:
    - roster_scout/reports.py
    - roster_scout/report_routes.py
    - templates/report_library.html
    - tests/test_report_library.py
  modified:
    - roster_scout/repositories.py
    - app.py
    - templates/product_base.html
    - tests/test_identity.py

key-decisions:
  - "Library queries accept the active organization only as an explicit server argument; browser fields and URLs never select tenant authority."
  - "Latest published snapshot and latest active or failed build are loaded independently so an in-progress refresh never hides the last usable report."
  - "Database failures render a fixed 503 retry state rather than a misleading empty library."

patterns-established:
  - "All report-library list, count, child, and summary queries carry the same organization predicate."
  - "Desktop rows and mobile cards render the same report fields from one immutable page view model."

requirements-completed: [ORG-01, ORG-06, AUTH-01, SECU-01, SECU-02]

duration: 11 min
completed: 2026-11-11
---

# Phase 2 Plan 05: Organization-Scoped Report Library Summary

**Persistent opponent reports now open in a bounded, tenant-filtered library with independent published-snapshot and current-build status across accessible desktop and mobile views.**

## Performance

- **Duration:** 11 min
- **Started:** 2026-11-12T00:34:59Z
- **Completed:** 2026-11-12T00:46:09Z
- **Tasks:** 2
- **Files modified:** 8

## Accomplishments

- Added organization-scoped repository queries that page at exactly 25 reports, order by newest report/snapshot/build activity, and separately select the latest published snapshot, current or failed build, published snapshot count, and unavailable section count.
- Added a protected `/reports` blueprint that derives its organization only from `g.authorization_context`, redirects authenticated `/` requests to the library, keeps unauthenticated requests behind the existing sign-in gate, and renders fixed 404/503 contracts without leaking database or tenant detail.
- Added an accessible responsive report library: a semantic desktop table, field-equivalent mobile cards, active navigation, ready/partial/no-snapshot states, independent build status, pagination, empty-state CTA, and a retryable database-failure state.
- Added PostgreSQL integration coverage for admin/member sharing, foreign-tenant isolation, bounded pagination, newest-activity ordering, partial/no-snapshot truthfulness, database failure behavior, and server-rendered desktop/mobile parity.

## Task Commits

Each task was committed atomically:

1. **Task 1: Specify shared, scoped report-library behavior** - `b58a5b9` (test)
2. **Task 2: Implement organization-scoped report library and responsive UI** - `fdc20a0` (feat)

## Files Created/Modified

- `roster_scout/repositories.py` - Adds bounded tenant-scoped report, snapshot, job, section, and count queries plus a non-enumerating scoped report lookup.
- `roster_scout/reports.py` - Converts persistence records into immutable report-library page and row view models with truthful status labels.
- `roster_scout/report_routes.py` - Composes the protected library route over server-derived authorization context and sanitized error contracts.
- `app.py` - Registers report routes and sends authenticated root traffic to the report library while preserving connection-free test/legacy behavior outside enforced auth.
- `templates/product_base.html` - Marks the Reports navigation item as current on the library route.
- `templates/report_library.html` - Renders equivalent desktop/mobile report data, empty/error states, and server pagination.
- `tests/test_report_library.py` - Proves tenant isolation, sharing, ordering, page bounds, report-state truthfulness, and responsive field parity.
- `tests/test_identity.py` - Keeps the exact default-deny Flask route inventory current with `/reports`.

## Decisions Made

- The library query boundary requires `organization_id` on every repository call and repeats the predicate on every child-table query. This keeps tenant isolation visible in code and prevents an unscoped join or batch lookup from becoming an enumeration path.
- Published snapshot state and build state remain independent. A report can truthfully show its last Ready or Partial snapshot while also showing Queued, Collecting, Saving, or Failed for a newer build.
- The repository uses PostgreSQL `DISTINCT ON` through SQLAlchemy 2.1's dialect extension for deterministic latest-per-report selection without deprecated APIs.
- The protected route treats a persistence outage as a sanitized 503 with retry affordance. It never converts an exception into the normal no-reports empty state.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Corrected RED fixtures to honor snapshot immutability and session lifetime**
- **Found during:** Task 1 RED verification
- **Issue:** The partial-snapshot fixture inserted a child after marking its parent published, and its fixed account-session timestamp had expired relative to the test clock.
- **Fix:** Inserted the unavailable section while the snapshot was draft, published it afterward in the same transaction, and based session time on the current UTC clock.
- **Files modified:** `tests/test_report_library.py`
- **Committed in:** `b58a5b9`

**2. [Rule 1 - Bug] Updated the exact protected-route inventory**
- **Found during:** Task 2 auth-boundary verification
- **Issue:** Registering the planned `/reports` route made the security regression's exhaustive Flask route set stale.
- **Fix:** Added `/reports` to the expected protected route inventory without adding it to either public allowlist.
- **Files modified:** `tests/test_identity.py`
- **Committed in:** `fdc20a0`

---

**Total deviations:** 2 auto-fixed bugs
**Impact on plan:** Both fixes preserve existing immutability and default-deny security contracts; no product scope was added.

## Issues Encountered

- Local PostgreSQL verification required approved localhost access to the disposable `roster_scout_test` database. No production database, identity workspace, deployment target, or live NCAA endpoint was contacted.
- The installed SQLAlchemy 2.1 runtime warned that expression-based `Select.distinct()` is deprecated; the implementation was updated to its documented PostgreSQL `distinct_on()` extension before final verification.

## User Setup Required

None - no dependency, schema, identity-provider, or hosted configuration changes were introduced.

## Next Phase Readiness

- Plan 02-06 can accept report/job creation against the same server-derived organization and have accepted work appear in this library without changing the page contract.
- Plan 02-07 can bind the reserved report-detail links to scoped report pages while reusing the library's non-enumerating organization/report lookup.
- No blockers remain for the next Phase 2 plan.

## Verification

- `SharedLibraryTests` plus `AuthGateRouteMatrixTests` - **10 tests passed**
- Clean full `unittest discover` against disposable PostgreSQL - **496 tests passed**
- `node --test tests/js/*.test.mjs` - **16 tests passed**
- Application, scraper, report modules, test, and catalog-script compilation - **passed**
- NCAA selector catalog - **942 selectable teams; valid**
- `git diff --check` - **passed**
- Stub and threat-surface scan across all created/modified plan files - **no blocking implementation stubs or unplanned threat surface found**

## Security and Threat Surface

- The new `/reports` endpoint is protected by the existing default-deny gate and never reads organization authority from a query string, form, route parameter, or JSON body.
- Report, snapshot, job, section, count, and updated-today queries all repeat the active organization predicate. Foreign and absent report lookups both return `None` with no tenant detail.
- The new endpoint and read patterns are covered by Plan 02-05's threat register; no unplanned network, file, schema, authentication, or mutation surface was introduced.

## Known Stubs

- `templates/report_library.html` links each Open report action to the reserved `/reports/<report_id>` destination. Plan 02-07 owns and explicitly supplies that scoped detail/status route; the library data, authorization, and rendering delivered here are complete independently.

## Self-Check: PASSED

- All four created files, all four modified files, and both task commits exist.
- Focused, full Python, JavaScript, compilation, catalog, stub, threat, and diff claims were rerun after implementation.

---
*Phase: 02-identity-organizations-and-report-library*
*Completed: 2026-11-11*
