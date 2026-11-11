---
phase: 01-canonical-snapshot-foundation
plan: "31"
subsystem: database
tags: [postgresql, alembic, advisory-locks, concurrency, provenance]

requires:
  - phase: 01-canonical-snapshot-foundation
    provides: Snapshot mutation serialization and authoritative source-to-Player equivalence through revision 0012
provides:
  - Stable organization-scoped Player advisory locks that serialize invisible identity creation with direct Player mutation
  - One snapshot to Player to PlayerTeamSeason lock order shared by mutation triggers and publication
  - Reversible revision 0013 with two-connection race and migration lifecycle proof
affects: [canonical-publication, provenance, player-identity, excel, power-bi]

tech-stack:
  added: []
  patterns: [statement-batched transition-table triggers, tenant-scoped advisory locks, global deterministic lock order]

key-files:
  created:
    - migrations/versions/0013_authoritative_identity_serialization.py
  modified:
    - roster_scout/snapshots.py
    - tests/test_snapshot_persistence.py
    - tests/test_canonical_publish.py

key-decisions:
  - "Authoritative identity paths acquire locks in the global metric-definition (when applicable) -> snapshot -> organization-scoped Player -> PlayerTeamSeason order."
  - "Direct Player updates acquire the stable Player UUID domain across organizations so a still-invisible PlayerTeamSeason cannot escape serialization."
  - "Publication locks every linked Player and PlayerTeamSeason before re-running the authoritative mismatch proof."

patterns-established:
  - "Visibility-independent identity serialization: the organization and canonical Player UUID form the shared advisory domain even before another transaction can see its PlayerTeamSeason."
  - "Statement-wide lock collection: transition tables collect complete OLD/NEW identity sets before sorted advisory acquisition and validation."

requirements-completed: [SNAP-02, SNAP-03, DATA-01, DATA-03, DATA-07, DATA-09, OPS-04]

duration: 18 min
completed: 2026-11-11
---

# Phase 1 Plan 31: Authoritative Identity Serialization Summary

**PostgreSQL revision 0013 and the publisher now share one deterministic snapshot-to-Player-to-PlayerTeamSeason lock protocol that closes concurrent authoritative identity write skew.**

## Performance

- **Duration:** 18 min
- **Started:** 2026-11-11T04:48:31Z
- **Completed:** 2026-11-11T05:06:59Z
- **Tasks:** 2
- **Files modified:** 5

## Accomplishments

- Added stable tenant-scoped Player and PlayerTeamSeason advisory namespaces with sorted batch helpers and statement-level transition-table triggers.
- Prevented incompatible SourceRecord insertion, invisible new PlayerTeamSeason creation, Player source-ID mutation, and PlayerTeamSeason reassignment from committing together.
- Joined publication to the same lock order before authoritative equivalence validation, without reversing Plan 01-15 metric-definition-first ordering.
- Preserved the existing named SQLSTATE 23514 contracts, composite tenant/snapshot ownership errors, caller-visible retryable database failures, and exact revision-0010 downgrade behavior.
- Proved source and Player mutation races in both start orders, upgrade/downgrade/re-upgrade, base-to-head installation, and model drift.

## Task Commits

Each TDD task was committed through separate RED and GREEN gates:

1. **Task 1 RED: Expose authoritative identity write skew** - `a420956` (test)
2. **Task 1 GREEN: Serialize authoritative identity mutations** - `6f125c6` (fix)
3. **Task 2 RED: Require publication identity serialization** - `114a3ea` (test)
4. **Task 2 GREEN: Serialize publication with identity mutations** - `9e690be` (fix)

## Files Created/Modified

- `migrations/versions/0013_authoritative_identity_serialization.py` - Advisory lock helpers, statement-batched authoritative triggers, and exact 0010 downgrade restoration.
- `roster_scout/snapshots.py` - Sorted linked Player and PlayerTeamSeason locks before publication validation.
- `tests/test_snapshot_persistence.py` - Invisible-row, reassignment, opposing-start, global-order, and migration lifecycle regressions.
- `tests/test_canonical_publish.py` - Source/Player mutation versus publication tests in both start orders.
- `.planning/phases/01-canonical-snapshot-foundation/deferred-items.md` - Records one pre-existing post-suite connection cleanup warning.

## Decisions Made

- Used distinct fixed advisory namespaces and exact organization+Player / PlayerTeamSeason key derivations from the plan rather than row locks or process-local coordination.
- Let a serialized race choose either compatible transaction as the winner; tests assert the invariant and bounded completion instead of scheduling a preferred winner.
- Kept raw 40001/40P01 behavior available to caller-owned whole-transaction retry boundaries; business validation still uses the existing named 23514 errors.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Accepted either compatible serialized winner in concurrency assertions**
- **Found during:** Task 1 GREEN
- **Issue:** Initial tests assumed the Player/PlayerTeamSeason mutation must win, although advisory scheduling legally allows the source transaction to commit first and reject the incompatible mutation.
- **Fix:** Asserted exactly one commit and either valid final state while still forbidding deadlock and lock-timeout outcomes.
- **Files modified:** `tests/test_snapshot_persistence.py`
- **Verification:** All three two-connection authoritative mutation regressions pass repeatedly.
- **Committed in:** `6f125c6`

**2. [Rule 1 - Bug] Ordered distinct UUID sets through outer queries**
- **Found during:** Task 1 GREEN
- **Issue:** PostgreSQL rejects `SELECT DISTINCT uuid ORDER BY uuid::text` unless the ordering expression is projected.
- **Fix:** Deduplicated in an inner query and applied the required textual UUID order in the outer query; publication uses the same pattern for tenant/Player pairs.
- **Files modified:** `migrations/versions/0013_authoritative_identity_serialization.py`, `roster_scout/snapshots.py`
- **Verification:** Focused concurrency suites, migration lifecycle, and 369-test discovery pass.
- **Committed in:** `6f125c6`, `9e690be`

---

**Total deviations:** 2 auto-fixed Rule 1 bugs.
**Impact on plan:** Both fixes were required for correct deterministic concurrency behavior; no feature scope was added.

## Issues Encountered

- Localhost PostgreSQL required the managed sandbox's approved execution path; the supported test commands were unchanged.
- Running the catalog check with system Python lacked project dependencies, so the required check was rerun in the repository `.venv` and passed.
- The complete green suite emitted one pre-existing psycopg connection cleanup warning after completion; it is recorded in `deferred-items.md` and did not affect test results.

## User Setup Required

None - no external service configuration required.

## Verification Results

- Task 1 exact authoritative mutation command: 13 tests passed.
- Task 2 exact publication and migration command: 16 tests passed.
- Broader snapshot persistence and canonical publication suites: 128 tests passed.
- Complete repository discovery: 369 tests passed in 71.291 seconds.
- Revision 0013 lifecycle: 0012→0013, 0013→0012, re-upgrade, and base→head fresh install passed.
- Alembic model drift: `No new upgrade operations detected.`
- NCAA catalog: 942 selectable teams (308 D1, 253 D2, 381 D3), zero errors, 25 unchanged stable-ID warnings.
- Required compilation, template existence, and `git diff --check`: passed.

## Known Stubs

None - the migration, publisher, and tests contain no placeholders or unwired data paths.

## Threat Flags

None - the new database trigger and publication surfaces are covered by T-01-31-01 through T-01-31-04 in the plan threat register.

## TDD Gate Compliance

PASSED - Task 1 RED `a420956` precedes GREEN `6f125c6`; Task 2 RED `114a3ea` precedes GREEN `9e690be`.

## Self-Check: PASSED

- All four implementation/test files, revision 0013, and this summary exist.
- Task commits `a420956`, `6f125c6`, `114a3ea`, and `9e690be` resolve in repository history.
- Every task acceptance criterion, plan verification command, migration lifecycle check, and broader regression suite passed.
- `brag-output/` remains untouched and untracked.

## Next Phase Readiness

- The first round-four blocker—concurrent authoritative identity write skew—is closed at mutation and publication boundaries.
- Plan 01-32 can address the database-optional native workbook workflow without changing this lock protocol.
- Production managed-service and uninterrupted-hosting prerequisites remain unchanged.

---
*Phase: 01-canonical-snapshot-foundation*
*Completed: 2026-11-11*
