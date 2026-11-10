---
phase: 01-canonical-snapshot-foundation
plan: "15"
subsystem: database
tags: [postgresql, advisory-locks, alembic, concurrency, immutable-snapshots]

requires:
  - phase: 01-canonical-snapshot-foundation
    provides: Canonical snapshot schema, published-row triggers, validated publication, and metric lineage from Plans 01-04 and 01-11
provides:
  - One transaction-scoped PostgreSQL lock namespace shared by snapshot publication and every snapshot child mutation
  - Phantom-safe metric-definition mutation ordering across first-reference insertion and all referenced snapshots
  - Deterministic two-connection proof for both legal publication/mutation commit orders
affects: [canonical-publication, snapshot-immutability, metric-lineage, migration-lifecycle]

tech-stack:
  added: []
  patterns: [transaction advisory lock namespaces, definition-before-snapshot lock ordering, event-barrier concurrency tests]

key-files:
  created:
    - migrations/versions/0006_snapshot_mutation_serialization.py
  modified:
    - roster_scout/snapshots.py
    - tests/test_snapshot_persistence.py

key-decisions:
  - "Publication and all snapshot-owned child mutations use lock_snapshot_mutation(uuid) before publication-state validation."
  - "Metric-value and metric-definition mutations acquire ordered definition locks before ordered snapshot locks so a first reference cannot escape enumeration."

patterns-established:
  - "Global lock order: metric-definition UUIDs first, then snapshot UUIDs, with each set deduplicated and sorted."
  - "Concurrency proof: independent PostgreSQL engines coordinate with events, assert blocking before release, and verify the final committed state."

requirements-completed:
  - SNAP-03
  - DATA-08
  - OPS-04

duration: 11 min
completed: 2026-11-10
---

# Phase 1 Plan 15: Snapshot Mutation Serialization Summary

**Transaction-scoped PostgreSQL advisory locks now make validated snapshot publication atomic against child mutation and phantom metric-definition references.**

## Performance

- **Duration:** 11 min
- **Started:** 2026-11-10T01:30:31Z
- **Completed:** 2026-11-10T01:41:38Z
- **Tasks:** 2
- **Files modified:** 3

## Accomplishments

- Added reversible revision 0006 with distinct snapshot and metric-definition advisory namespaces, xact-scoped release, and deterministic UUID ordering.
- Wired `publish_snapshot()` into the same snapshot lock used by all six snapshot-owned child trigger paths before parent locking and validation.
- Prevented phantom metric references by making metric-value INSERT/UPDATE and metric-definition UPDATE/DELETE share definition locks before any snapshot enumeration or locking.
- Proved both legal commit orders for source-row/publication and first-metric-reference/definition races using independent PostgreSQL engines and event barriers.
- Preserved migration downgrade/re-upgrade, fresh-install behavior, model drift, all 201 repository regressions, and the existing workbook/catalog contracts.

## Task Commits

Each task was committed atomically through its TDD gates:

1. **Task 1 RED: Expose missing snapshot mutation serialization** - `13691a0` (test)
2. **Task 1 GREEN: Serialize snapshot publication and child mutation** - `f2bc2b5` (feat)
3. **Task 2 RED: Expose metric-definition publication races** - `de30692` (test)
4. **Task 2 GREEN: Serialize metric definitions with snapshot publication** - `e7b81b3` (feat)

## Files Created/Modified

- `migrations/versions/0006_snapshot_mutation_serialization.py` - Reversible snapshot/definition advisory helpers and ordered trigger protocols.
- `roster_scout/snapshots.py` - Publisher acquisition of the shared snapshot mutation lock before its parent row lock.
- `tests/test_snapshot_persistence.py` - Migration assertions and both-direction independent-connection concurrency barriers.

## Decisions Made

- Used fixed, documented `hashtextextended` seeds for separate snapshot and definition namespaces; collisions may add safe contention but cannot weaken serialization.
- Kept every advisory lock transaction-scoped so commit, rollback, connection loss, and process loss release ownership automatically.
- Ordered distinct definition UUIDs before distinct snapshot UUIDs on both metric-value and metric-definition paths, preventing deadlocks and phantom-reference escape.
- Re-read ready/partial state only after all affecting locks are held.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Made edited-revision downgrade tolerant of the not-yet-created definition helper**
- **Found during:** Task 2 GREEN migration reapplication
- **Issue:** The disposable database had Task 1's earlier 0006 body applied, so Task 2's expanded downgrade attempted to drop a metric helper that that in-progress database revision had never created.
- **Fix:** Used `DROP FUNCTION IF EXISTS` for the new definition helper while retaining a strict drop for the always-present snapshot helper.
- **Files modified:** `migrations/versions/0006_snapshot_mutation_serialization.py`
- **Verification:** Downgrade 0006→0005, re-upgrade, base→head fresh install, full migration suite, and `alembic check` all pass.
- **Committed in:** `e7b81b3`

---

**Total deviations:** 1 auto-fixed (1 Rule 3 blocking issue).
**Impact on plan:** The change only improves reversible migration execution while developing the same new revision; no schema or runtime scope was added.

## Issues Encountered

- The managed sandbox blocks localhost PostgreSQL sockets and Git index writes. Scoped approved execution was used for the disposable `roster_scout_test` database and normal hooked commits.

## User Setup Required

None - no external service configuration required.

## Verification Results

- Narrow four-test race suite: passed three consecutive runs.
- Full snapshot persistence and canonical publication suites: 60 tests passed.
- Full repository discovery with PostgreSQL enabled: 201 tests passed.
- Migration lifecycle: 0005→0006, 0006→0005→0006, base→head, and historical downgrade/re-upgrade tests passed.
- Alembic model drift: `No new upgrade operations detected.`
- NCAA catalog: 942 selectable teams (308 D1, 253 D2, 381 D3), zero errors.
- Required Python compilation, template existence checks, and `git diff --check`: passed.

## Known Stubs

None - modified production paths contain no placeholder or unwired data flow.

## TDD Gate Compliance

PASSED - Task 1 has RED commit `13691a0` followed by GREEN commit `f2bc2b5`; Task 2 has RED commit `de30692` followed by GREEN commit `e7b81b3`.

## Self-Check: PASSED

- All three created or modified implementation/test files exist.
- Task commits `13691a0`, `f2bc2b5`, `de30692`, and `e7b81b3` resolve in repository history.
- Every task acceptance criterion and plan-level verification command passed.
- `brag-output/` remains untouched and untracked.

## Next Phase Readiness

- Verification gap 1 is closed for snapshot child and metric-definition concurrency.
- Plan 01-16 can proceed; existing managed-service and uninterrupted-hosting production prerequisites remain unchanged.

---
*Phase: 01-canonical-snapshot-foundation*
*Completed: 2026-11-10*
