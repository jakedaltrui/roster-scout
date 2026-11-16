---
phase: 03-detailed-ncaa-intelligence
plan: "07"
subsystem: operations
tags: [postgresql, browserless, request-budget, diagnostics, tenant-isolation]

requires:
  - phase: 03-detailed-ncaa-intelligence
    provides: stable player history, exact game logs, schedule-derived scopes, and immutable detailed snapshots
provides:
  - validated shared Phase 3 live-page, navigation-timeout, concurrency, and wall-time budgets
  - tenant-owned immutable cache/live request ledger with safe source identities and exact remaining capacity
  - read-only sanitized job, section, scope, budget, capacity, and external-refresh diagnostics
  - provider-neutral pending/completed/failed Power BI diagnostic attempt records for Phase 5 writers
affects: [phase-4-workbooks, phase-5-power-bi, report-workers, operator-runbooks]

tech-stack:
  added: []
  patterns:
    - validate collection limits before database or browser dependency construction
    - hash source-contract material into content-free ledger identities
    - derive operator state from persisted metadata without repair or mutation

key-files:
  created:
    - scripts/inspect_report_jobs.py
    - tests/test_phase3_release.py
  modified:
    - roster_scout/models.py
    - roster_scout/jobs.py
    - roster_scout/snapshots.py
    - migrations/versions/0025_detailed_ncaa_intelligence.py
    - tests/test_player_history.py
    - tests/test_snapshot_persistence.py

key-decisions:
  - "Request-ledger source identities are SHA-256-derived safe tokens over validated source-contract fields, never URLs, player/customer values, or provider payloads."
  - "External refresh attempts remain operational after snapshot publication, but tenant/report/snapshot/provider/correlation/request identity is immutable and lifecycle may transition only pending to completed or failed."
  - "Operator diagnostics observe persisted job, dispatch, section, scope, ledger, and refresh metadata only; reconciliation remains the sole repair path."

patterns-established:
  - "One build budget: cache reuse is counted separately, every live family consumes the same bounded budget, and exhaustion remains an explicit safe partial condition."
  - "Allowlisted diagnostics: only opaque IDs, lifecycle timestamps, counts, and safe machine tokens cross the operator boundary."

requirements-completed: [HIST-04, HIST-05, OPS-03]

duration: 20min
completed: 2026-11-16
---

# Phase 3 Plan 7: Bounded Collection and Privacy-Safe Operations Summary

**Detailed NCAA fan-out now shares one validated durable request budget, while a read-only CLI diagnoses stuck work, unavailable sections, capacity exhaustion, and Power BI failures without customer or source content.**

## Performance

- **Duration:** 20 min
- **Started:** 2026-11-16T10:17:35Z
- **Completed:** 2026-11-16T10:37:26Z
- **Tasks:** 3
- **Files modified:** 8

## Accomplishments

- Added validated defaults and hard bounds for two in-session navigations, 150 live pages, 45 seconds per navigation, and 12 minutes per build, with deterministic test overrides and validation before external dependency construction.
- Added tenant-owned request-ledger rows for planned, cache, live, failed, budget-exhausted, and capacity-exhausted outcomes using content-free source-contract identities and immutable published-snapshot protection.
- Extended publication so unsettled work cannot publish and safe live/budget/capacity failures force an honest partial snapshot without invalidating valid core batting and pitching.
- Added provider-neutral `ExternalRefreshDiagnostic` persistence with exact tenant/report/snapshot ownership, safe token constraints, unique attempts/correlations, and controlled pending-to-completed/failed transitions.
- Added deterministic sorted JSON diagnostics for stuck jobs, unavailable sections/scopes, budget/cache/live counts, capacity pressure, retry timing, and failed `power_bi` records while excluding customer labels, players, raw rows, URLs, email, credentials, provider payloads, and exception text.
- Passed the 139-test plan regression and the authoritative Phase 2.1 release gate, including full discovery, migration lifecycle, Node/browser contracts, catalog validation, compilation, and diff hygiene.

## Task Commits

Each task was committed atomically:

1. **Task 1: Lock shared request-budget and operator-redaction behavior** - `a53b233` (test)
2. **Task 2: Enforce one durable request ledger across supplemental families** - `1f6ef1f` (feat)
3. **Task 3: Add read-only metadata diagnostics for Phase 3 jobs** - `aeb0c7d` (feat)
4. **Task 3 follow-up: Harden tenant, immutability, lifecycle, and unavailable-section counterexamples** - `8f79a14` (test)

## Files Created/Modified

- `roster_scout/models.py` - Tenant-owned request-ledger and provider-neutral external-refresh diagnostic models with bounded safe fields and composite ownership.
- `roster_scout/jobs.py` - Validated Phase 3 limits, shared in-build budget accounting, durable cache/live settlement, and capacity-safe outcomes.
- `roster_scout/snapshots.py` - Publication rejection for unsettled ledger work and partial publication for safe live/budget/capacity failures.
- `migrations/versions/0025_detailed_ncaa_intelligence.py` - Additive reversible ledger/diagnostic schema, published-child guard, and controlled refresh lifecycle trigger.
- `scripts/inspect_report_jobs.py` - Read-only validated CLI with deterministic metadata-only JSON and fixed sanitized errors.
- `tests/test_player_history.py` - RED sentinel plus limit-boundary, deterministic override, shared-budget, cache/live, and unsafe-identity contracts.
- `tests/test_phase3_release.py` - Stuck/capacity/unavailable-section diagnostics, denylist, Power BI lifecycle, tenant substitution, immutability, invalid-input, and database-failure coverage.
- `tests/test_snapshot_persistence.py` - Request ledger registration in the complete immutable snapshot-child trigger contract.

## Decisions Made

- Ledger source identity is a fixed-origin contract digest rather than a stored URL. This preserves exact same-build reuse/accounting while making player names, arbitrary query strings, tokens, and source details structurally unavailable to diagnostics.
- Cache reuse never consumes live-page capacity; successful and failed live attempts do. Budget exhaustion settles a safe terminal ledger outcome before any external request is started.
- External refresh diagnostics are intentionally not canonical snapshot content. Phase 5 may write them after publication, but immutable identity fields, tenant lineage, bounded correlation IDs, and one-way lifecycle transitions prevent provider payload or ownership drift.
- Diagnostics never call reconciliation, external providers, or source collectors. They classify persisted timestamps/configuration and leave repair to `reconcile_stale_report_jobs()`.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Corrected a stale usage-test path**
- **Found during:** Task 1 required-read gate
- **Issue:** The plan referenced `tests/test_usage_analytics.py`, which does not exist; the repository's current aggregate-usage CLI contract is `tests/test_usage_metrics.py`.
- **Fix:** Read and followed the current `UsageReportCliTests` and `SafeArgumentParser` patterns from `tests/test_usage_metrics.py` and `scripts/report_usage.py`.
- **Files modified:** None
- **Verification:** Task 1 RED failed with exactly one assertion after imports and both service preflights.
- **Committed in:** N/A (execution-path correction)

**2. [Rule 1 - Bug] Kept the shared unreleased migration downgrade-compatible with earlier 0025 test shapes**
- **Found during:** Task 2 disposable PostgreSQL lifecycle verification
- **Issue:** A disposable database already stamped at the evolving 0025 revision did not yet contain the new tables, so unconditional downgrade drops failed before a clean rebuild.
- **Fix:** Used the migration's established `DROP TABLE IF EXISTS` compatibility pattern for the newly assembled tables before rebuilding from base.
- **Files modified:** `migrations/versions/0025_detailed_ncaa_intelligence.py`
- **Verification:** Base-to-head rebuilt cleanly and all migration lifecycle regressions passed.
- **Committed in:** `1f6ef1f`

**3. [Rule 2 - Missing Critical Functionality] Registered the request ledger in the immutable-child security manifest**
- **Found during:** Task 2 publication verification
- **Issue:** The database trigger was installed, but the exhaustive immutable-child test manifest predated the new snapshot-owned ledger.
- **Fix:** Added `request_ledgers` to the shared trigger inventory.
- **Files modified:** `tests/test_snapshot_persistence.py`
- **Verification:** The trigger inventory and complete snapshot-persistence suite passed.
- **Committed in:** `1f6ef1f`

---

**Total deviations:** 3 auto-fixed (1 blocking path correction, 1 migration compatibility bug, 1 missing critical security declaration)
**Impact on plan:** All adjustments were required for safe execution, reversible disposable-database verification, and complete immutability coverage; no product or deployment scope was added.

## Issues Encountered

- The initial database preflight returned the fixed `phase3_test_database_unavailable` result because no explicit destructive-test environment was inherited. The executor supplied only the repository-documented loopback `roster_scout_test` URL with `PHASE2_DESTRUCTIVE_DATABASE=local-disposable`, after which the safe compose/bootstrap helper succeeded.
- The Browserless preflight completed through the existing configured secret boundary and returned `phase3_browserless_ready`; no token value was read, printed, exported, or persisted.
- One exploratory snapshot test selector used the wrong test class name; the exact trigger contract was rerun under `SnapshotMutationSerializationTests` and passed. This did not change product code.

## User Setup Required

None - the existing Browserless secret boundary and repository-owned disposable loopback PostgreSQL bootstrap were sufficient. No deployment or production mutation was performed.

## Known Stubs

None.

## Threat Flags

No unplanned threat surface. The new database tables and read-only operator CLI are the exact trust boundaries registered as T-03-07-02 through T-03-07-05 in the plan and are covered by tenant-isolation, safe-field, lifecycle, denylist, and immutability tests.

## Next Phase Readiness

- Phase 4 workbook consumers can rely on truthful partial/unavailable state without triggering source collection.
- Phase 5 may insert and transition `power_bi` diagnostic attempts without changing canonical snapshots or reusing provider payloads/tokens.
- Plan 03-08 can build its release gate over the bounded collection and sanitized operations contracts.
- Production launch remains blocked by the previously documented hosting, identity, storage, and Power BI capacity approvals.

## Self-Check: PASSED

- Confirmed all eight created or modified implementation/test files exist.
- Confirmed task commits `a53b233`, `1f6ef1f`, `aeb0c7d`, and `8f79a14` exist in git history.
- Confirmed the plan regression, focused lifecycle/tenant counterexamples, compilation, diff hygiene, and authoritative repository release gate pass.
- Confirmed no goal-blocking stubs or unplanned trust-boundary surfaces remain.

---
*Phase: 03-detailed-ncaa-intelligence*
*Completed: 2026-11-16*
