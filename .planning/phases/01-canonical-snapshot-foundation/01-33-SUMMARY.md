---
phase: 01-canonical-snapshot-foundation
plan: "33"
subsystem: usage-analytics-and-native-workbook
tags: [flask, postgresql, sqlalchemy, xlsx, usage-metrics, observability]

requires:
  - phase: 01-canonical-snapshot-foundation
    provides: Privacy-safe usage ledger, legacy workbook tracking, and native two-sheet workbook route from Plans 01-28 through 01-30
provides:
  - Authenticated-use-only distinct-user aggregates globally and per organization
  - Database-optional native workbook delivery with explicit sanitized analytics-drop telemetry
  - Recorded/dropped persistence dispositions for legacy workbook usage writes
affects: [usage-analytics, native-workbooks, identity-organizations, download-acknowledgement]

tech-stack:
  added: []
  patterns: [event-type-qualified adoption metric, best-effort local telemetry boundary, fixed-token privacy-safe warnings]

key-files:
  created: []
  modified:
    - roster_scout/usage.py
    - app.py
    - tests/test_usage_metrics.py
    - tests/test_app_routes.py

key-decisions:
  - "Unique authenticated users are distinct canonical actor UUIDs from authenticated_use events only; actor-bearing download events remain valid audit rows but never define adoption."
  - "The legacy/native workbook route treats usage persistence as best-effort and returns recorded or dropped while durable report/job paths remain database-required."
  - "Dropped native analytics emit one fixed usage_event_dropped warning with no request, identity, database, or exception material."

patterns-established:
  - "Aggregate eligibility: event type and actor presence jointly define authenticated-user membership."
  - "Native telemetry failure: contain the failure after workbook construction, emit a fixed safe token, and preserve HTTP 200 workbook delivery."

requirements-completed: [DATA-10, OPS-05]

duration: 8 min
completed: 2026-11-11
---

# Phase 1 Plan 33: Usage Semantics and Database-Optional Native Workbook Summary

**Authenticated adoption now counts only accepted authenticated-use events, while the native two-sheet workbook remains available without PostgreSQL and reports dropped analytics through one privacy-safe warning.**

## Performance

- **Duration:** 8 min
- **Started:** 2026-11-11T05:20:15Z
- **Completed:** 2026-11-11T05:28:26Z
- **Tasks:** 2
- **Files modified:** 4

## Accomplishments

- Qualified the distinct-user aggregate by `authenticated_use`, preventing actor-bearing download rows from inflating adoption globally or within an organization.
- Preserved the shared half-open UTC window and organization predicates for both download and unique-user metrics.
- Restored the documented local `/scrape` workflow when `DATABASE_URL` is absent: the response remains a readable Hitters/Pitchers XLSX.
- Added explicit `recorded`/`dropped` persistence dispositions and one fixed `usage_event_dropped route=legacy_scrape reason=database_unavailable` warning without exception or request details.
- Kept configured PostgreSQL writes idempotent and left durable report/job production paths database-authoritative.

## Task Commits

Each task was committed atomically:

1. **Task 1 RED: Download actors incorrectly counted as authenticated users** - `287a774` (test)
2. **Task 1 GREEN: Restrict unique users to authenticated-use events** - `ccbc84d` (fix)
3. **Task 2 RED: Native workbook incorrectly coupled to analytics database** - `fa194b1` (test)
4. **Task 2 GREEN: Contain legacy analytics failures after workbook construction** - `7a55966` (fix)
5. **Task 2 validation correction: Stabilize the analytics-drop assertion** - `aba17ca` (test)

## Files Created/Modified

- `roster_scout/usage.py` - Requires `authenticated_use` plus a non-null canonical actor for the distinct-user aggregate.
- `app.py` - Makes legacy workbook usage persistence best-effort, returns a disposition, and emits a fixed sanitized warning on failure.
- `tests/test_usage_metrics.py` - Covers download-only actors, duplicate accepted uses, and matching global/organization semantics.
- `tests/test_app_routes.py` - Covers configured persistence dispositions, sanitized failures, and an unmocked no-database native XLSX response.

## Decisions Made

- Actor presence is useful for future authorization and audit, but only the accepted authenticated-use producer is eligible for the adoption metric.
- Local/native analytics loss is observable but cannot revoke an already-built workbook; production report/job durability remains a separate database-required boundary.
- The drop warning uses only fixed route and reason tokens, preventing credentials, UUIDs, team names, IPs, request metadata, exception text, and traceback disclosure.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Stabilized the analytics-drop regression across the complete route suite**
- **Found during:** Task 2 final validation
- **Issue:** The initial failure-path test depended on process-global logger capture and was order-sensitive even though the real no-database route test and focused persistence behavior were green.
- **Fix:** Exercised the persistence helper directly and asserted the exact warning call at the boundary; the separate unmocked route test continues to prove real log emission and workbook delivery.
- **Files modified:** `tests/test_app_routes.py`
- **Verification:** Complete route module passed 38 tests and repository-wide discovery passed 377 tests.
- **Committed in:** `aba17ca`

---

**Total deviations:** 1 auto-fixed (1 Rule 1 bug).
**Impact on plan:** The correction removed test-order dependence without changing the shipped persistence or workbook behavior.

## Issues Encountered

- The full suite continues to emit the pre-existing psycopg connection `ResourceWarning` already recorded in `deferred-items.md`; all 377 tests pass.

## User Setup Required

None - local native workbook generation intentionally needs no PostgreSQL setup, and configured deployments continue using the existing `DATABASE_URL` contract.

## Verification Results

- Task 1 focused PostgreSQL counterexamples: 2 passed.
- Usage service suite: 7 passed.
- No-`DATABASE_URL` native workbook suite: 1 passed with readable Hitters and Pitchers worksheets and sanitized fixed telemetry.
- Configured workbook usage plus usage service suites: 12 passed.
- Complete app-route module: 38 passed.
- Complete PostgreSQL-backed repository discovery: 377 passed.
- Python compilation, required templates, 942-team NCAA catalog validation, and `git diff --check`: passed.

## Known Stubs

None. The `dropped` disposition represents an observed local analytics failure, not a fallback store or placeholder implementation.

## Threat Flags

None - event-type spoofing, native-route denial of service, log disclosure, and silent analytics repudiation are covered by T-01-33-01 through T-01-33-04.

## TDD Gate Compliance

PASSED - both behavior changes have failing RED commits followed by passing GREEN commits; the post-GREEN test-only commit corrects validation determinism discovered by the full suite.

## Self-Check: PASSED

- `app.py`, `roster_scout/usage.py`, `tests/test_app_routes.py`, `tests/test_usage_metrics.py`, and this summary exist.
- Task commits `287a774`, `ccbc84d`, `fa194b1`, `7a55966`, and `aba17ca` resolve in repository history.
- Every acceptance criterion, the exact focused suites, complete 377-test discovery, compilation, templates, catalog, and diff checks passed.
- `brag-output/` remains untouched and untracked.

## Next Phase Readiness

- Plan 01-34 can separate workbook preparation from protected browser acknowledgement without inheriting the former native PostgreSQL availability regression.
- Phase 2 can add canonical account foreign keys and emit accepted authenticated-use events without changing the aggregate definition.

---
*Phase: 01-canonical-snapshot-foundation*
*Completed: 2026-11-11*
