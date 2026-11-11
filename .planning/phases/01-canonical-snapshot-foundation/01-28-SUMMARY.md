---
phase: 01-canonical-snapshot-foundation
plan: "28"
subsystem: usage-analytics
tags: [postgresql, sqlalchemy, alembic, privacy, analytics]

requires:
  - phase: 01-canonical-snapshot-foundation
    provides: Canonical organizations, PostgreSQL migration lifecycle, and bounded retention infrastructure through Plan 01-26
provides:
  - Privacy-safe, idempotent product usage event ledger
  - Exact half-open-window download and distinct authenticated-user aggregates
  - Sanitized operator CLI for global and organization-scoped usage reports
  - Stable Phase 2 producer contract for authorized accepted report jobs
affects: [identity-organizations, report-library, workbook-downloads, operations]

tech-stack:
  added: []
  patterns: [caller-owned usage transactions, conflict-safe event recording, aggregate-only operational reporting]

key-files:
  created:
    - migrations/versions/0012_product_usage_events.py
    - roster_scout/usage.py
    - scripts/report_usage.py
    - tests/test_usage_metrics.py
  modified:
    - roster_scout/models.py

key-decisions:
  - "Usage event keys are opaque UUIDs: accepted ReportJob UUIDs for authenticated use and per-submission UUIDs for workbook downloads."
  - "Unique users are COUNT(DISTINCT actor_account_id) over nonnull canonical account IDs; anonymous downloads never synthesize identity."
  - "Every populated actor must carry the matching canonical organization UUID, while Phase 1 anonymous workbook events keep both fields null."
  - "The operator report exposes aggregate counts only and returns fixed sanitized error tokens."

patterns-established:
  - "Privacy-minimal event ledger: no email, IP, cookie, user-agent, fingerprint, pseudonymous ID, or freeform metadata columns."
  - "Future-auth contract: record authenticated_use only after membership authorization and durable report-job acceptance."

requirements-completed: [SNAP-02, DATA-09, OPS-04]

duration: 7 min
completed: 2026-11-10
---

# Phase 1 Plan 28: Privacy-Conscious Usage Metrics Summary

**PostgreSQL now stores deduplicated usage events without fallback identity data, and operators can query exact successful-download and authenticated-user aggregates through a sanitized CLI.**

## Performance

- **Duration:** 7 min
- **Started:** 2026-11-11T00:22:50Z
- **Completed:** 2026-11-11T00:29:53Z
- **Tasks:** 3
- **Files modified:** 5

## Accomplishments

- Added reversible revision 0012 and matching ORM metadata with named event-type, identity-pairing, authenticated-scope, tenant-FK, and idempotency constraints.
- Added conflict-safe recording APIs plus exact half-open-window aggregates: downloads count served events, while users count distinct nonnull canonical account UUIDs.
- Added a global or organization-scoped operator JSON report with fixed sanitized failures and an explicit Phase 2 accepted-job producer contract.
- Proved anonymous Phase 1 downloads count successfully while reporting zero authenticated users and never collecting identity substitutes.

## Task Commits

Each task was committed atomically:

1. **Task 1 RED: Missing privacy-safe usage schema** - `bd23643` (test)
2. **Task 1 GREEN: Privacy-safe usage schema and migration** - `fb0a13f` (feat)
3. **Task 2 RED: Missing idempotent usage service** - `6ad9a02` (test)
4. **Task 2 GREEN: Exact event recording and aggregates** - `3feca96` (feat)
5. **Task 3: Sanitized operator usage report** - `f7d2939` (feat)

## Files Created/Modified

- `migrations/versions/0012_product_usage_events.py` - Creates the minimal usage ledger, constraints, FK, uniqueness, and query indexes.
- `roster_scout/models.py` - Adds matching `ProductUsageEvent` ORM metadata.
- `roster_scout/usage.py` - Records allowlisted events idempotently and queries exact global or tenant aggregates.
- `scripts/report_usage.py` - Prints aggregate-only JSON for a validated UTC window and optional organization.
- `tests/test_usage_metrics.py` - Covers privacy, database constraints, migrations, concurrency, boundaries, scoping, CLI output, and sanitized failures.

## Decisions Made

- Restricted event keys to UUID strings so callers cannot repurpose the idempotency key as email, IP, cookie, or other identifying data.
- Kept `actor_account_id` intentionally without a Phase 1 FK because the canonical account table belongs to Phase 2; the pairing and authenticated-scope checks already prevent malformed aggregates.
- Counted distinct nonnull actors across qualifying stored usage events, while preserving exact download totals for anonymous legacy workbook traffic.
- Kept all database/session construction lazy so invalid CLI input never initializes persistence dependencies.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing Critical] Added bounded aggregate-query indexes**
- **Found during:** Task 1 (Add the privacy-conscious usage event schema)
- **Issue:** Windowed global and organization-scoped operator queries would otherwise require full-table scans as the append-only event ledger grows.
- **Fix:** Added named indexes on `occurred_at` and `(organization_id, occurred_at)` in both revision 0012 and ORM metadata.
- **Files modified:** `migrations/versions/0012_product_usage_events.py`, `roster_scout/models.py`
- **Verification:** `alembic check` reports no drift; fresh install and downgrade/re-upgrade tests pass.
- **Committed in:** `fb0a13f`

---

**Total deviations:** 1 auto-fixed (1 missing critical performance safeguard).
**Impact on plan:** The indexes preserve the planned schema and privacy boundary while making its required windowed reports operationally bounded.

## Issues Encountered

- The first direct PostgreSQL RED run required the managed environment's localhost permission; after approval it failed for the intended missing table/model and proceeded through normal RED/GREEN execution.
- Argparse wrapped one required help phrase across lines; the contract test now normalizes display whitespace before asserting its complete meaning.

## User Setup Required

None - Phase 1 creates no authentication provider or account model. Phase 2 owns canonical account binding and event production after authorization.

## Verification Results

- Usage event schema suite: 7 tests passed, including fresh install and downgrade/re-upgrade.
- Usage service suite: 5 tests passed, including concurrent duplicate recording and exact time boundaries.
- Operator CLI suite: 4 tests passed, including global/tenant output and sanitized failures.
- Complete usage suite: 16 tests passed.
- Alembic drift: `No new upgrade operations detected.`
- Python compilation and `git diff --check`: passed.

## Known Stubs

None. The nullable actor column is an intentional Phase 2 integration boundary, not a placeholder identity; the current Phase 1 download metric is fully operational.

## Threat Flags

None - identity spoofing, duplicate tampering, aggregate disclosure, and tenant scoping are the explicit T-01-28-01 through T-01-28-04 mitigations.

## TDD Gate Compliance

PASSED - Tasks 1 and 2 each have failing `test(01-28)` RED commits followed by passing `feat(01-28)` GREEN commits.

## Self-Check: PASSED

- All four created files, the modified ORM file, and this summary exist.
- Task commits `bd23643`, `fb0a13f`, `6ad9a02`, `3feca96`, and `f7d2939` resolve in repository history.
- Every task acceptance criterion, the complete usage suite, migration lifecycle, drift check, compilation, and diff check passed.
- `brag-output/` remains untouched and untracked.

## Next Phase Readiness

- Plan 01-29 can record successful legacy workbook responses with one per-submission UUID without claiming anonymous users.
- Phase 2 Plan 02-04 can FK-bind actors to canonical accounts and call the existing accepted-job producer after membership authorization without redesigning the ledger.

---
*Phase: 01-canonical-snapshot-foundation*
*Completed: 2026-11-10*
