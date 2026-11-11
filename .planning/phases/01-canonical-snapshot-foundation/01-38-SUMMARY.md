---
phase: 01-canonical-snapshot-foundation
plan: "38"
subsystem: database-concurrency-and-publication
tags: [postgresql, alembic, advisory-locks, canonical-identity, tdd]

requires:
  - phase: 01-canonical-snapshot-foundation
    provides: Revision-0013 snapshot-first tenant Player and PlayerTeamSeason serialization
provides:
  - Visibility-independent advisory locking keyed only by canonical Player UUID
  - Universal snapshot, global Player, tenant Player, and PlayerTeamSeason lock order
  - Publication-side global Player locking before authoritative validation
  - Two-connection invisible-organization race and complete revision-0014 lifecycle coverage
affects: [canonical-publication, identity-integrity, phase-verification, release-gate]

tech-stack:
  added: []
  patterns: [global-before-tenant-advisory-locking, sorted-uuid-batches, exact-prior-revision-restoration]

key-files:
  created:
    - migrations/versions/0014_global_authoritative_player_serialization.py
  modified:
    - roster_scout/snapshots.py
    - roster_scout/jobs.py
    - tests/test_snapshot_persistence.py
    - tests/test_canonical_publish.py

key-decisions:
  - "The common authoritative identity lock is derived only from Player UUID, while organization ownership remains a separate tenant-scoped lock domain."
  - "Publication retains snapshot-first ordering and acquires sorted global Player locks before tenant Player and PlayerTeamSeason locks."
  - "Shared metric definitions are resolved after identity creation but before source linkage acquires snapshot/global locks, preserving both Player convergence and metric-first serialization."

patterns-established:
  - "Authoritative order: snapshot -> global Player -> tenant Player -> PlayerTeamSeason -> validation."
  - "Migration downgrade exactness: revision 0014 removes its objects and executes immutable revision 0013's upgrade contract."

requirements-completed: [SNAP-02, SNAP-03, DATA-01, DATA-03, DATA-07, DATA-09, OPS-04]

duration: 19m
completed: 2026-11-11
---

# Phase 1 Plan 38: Global Authoritative Player Serialization Summary

**Revision 0014 now serializes every authoritative identity mutation and publication through a visibility-independent Player UUID lock, closing the uncommitted-new-organization write skew without weakening snapshot or metric-definition ordering.**

## Performance

- **Duration:** 19 min
- **Started:** 2026-11-11T16:50:00Z
- **Completed:** 2026-11-11T17:09:11Z
- **Tasks:** 2
- **Files modified:** 5

## Accomplishments

- Added scalar and deterministic batched global Player advisory-lock helpers plus replacement SourceRecord, Player, and PlayerTeamSeason triggers in revision 0014.
- Reproduced the verifier's real uncommitted organization/snapshot/identity/source race with two independent PostgreSQL connections and proved both start orders leave exactly one valid outcome and zero stored mismatches.
- Wired publication into the same snapshot -> global Player -> tenant Player -> PlayerTeamSeason protocol before authoritative validation.
- Verified exact downgrade to revision 0013, re-upgrade, fresh base-to-head installation, one head at 0014, and drift-free models.

## Task Commits

Each task was committed atomically through RED/GREEN cycles:

1. **Task 1 RED: Reproduce invisible organization identity race** - `7ff3279` (test)
2. **Task 1 GREEN: Serialize identity writes by global Player** - `83b3899` (feat)
3. **Task 2 RED: Require global Player lock in publication** - `8615ee9` (test)
4. **Task 2 GREEN: Lock global Players during publication** - `72ce8d7` (feat)
5. **Full-suite correction: Preserve metric-first concurrency ordering** - `0412495` (fix)

## Files Created/Modified

- `migrations/versions/0014_global_authoritative_player_serialization.py` - Global Player UUID advisory domain, replacement authoritative triggers, and exact 0013 downgrade restoration.
- `roster_scout/snapshots.py` - Sorted publication-side global Player locking before tenant and identity locks.
- `roster_scout/jobs.py` - Shared metric definitions resolve before source linkage reaches snapshot/global identity locks.
- `tests/test_snapshot_persistence.py` - Both invisible-organization race schedules, key/order assertions, and revision-0014 lifecycle/drift coverage.
- `tests/test_canonical_publish.py` - Exact publication lock-order assertion and existing opposing transaction regressions.

## Decisions Made

- Kept the global key namespace immutable and organization-independent: `roster-scout:authoritative-global-player:` plus canonical Player UUID and fixed seed `31032026`.
- Continued to lock PlayerTeamSeason insertions through their subsequent authoritative SourceRecord linkage rather than adding a broad insert trigger; updates lock OLD and NEW Player UUIDs, while source insertion is the first authoritative equivalence boundary.
- Restored revision 0013 by loading its immutable migration implementation during 0014 downgrade, preventing a duplicated approximation from drifting away from the exact prior contract.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Preserved metric-definition-first concurrency after global locking**
- **Found during:** Full-suite verification after Task 2
- **Issue:** A broad PlayerTeamSeason insert lock allowed one shared-Player job to hold the global lock before both workers reached metric-definition convergence, breaking the established metric-first concurrent workflow.
- **Fix:** Limited PlayerTeamSeason enforcement to authoritative reassignment paths and resolved shared metric definitions before source linkage acquires snapshot/global locks. This keeps authoritative SourceRecord coverage complete while retaining deterministic metric and Player convergence.
- **Files modified:** `migrations/versions/0014_global_authoritative_player_serialization.py`, `roster_scout/jobs.py`
- **Verification:** Both `ConcurrentSharedRecordTests` pass, all 33 focused plan/order tests pass, and all 411 repository tests pass.
- **Committed in:** `0412495`

---

**Total deviations:** 1 auto-fixed bug.
**Impact on plan:** The correction is required by the plan's metric-definition-before-snapshot invariant and adds no unrelated feature scope.

## Issues Encountered

- The first full suite identified the metric-definition concurrency regression described above; the corrected ordering passed the second complete run.
- The complete suite emitted the existing psycopg connection cleanup `ResourceWarning` twice; all 411 tests passed and no connection failure occurred.

## User Setup Required

None - no external service or dependency configuration was added.

## Verification Results

- Focused global Player serialization, migration lifecycle, publication serialization, and shared-record concurrency: 33 passed.
- Full supported uv/PostgreSQL unittest discovery: 411 passed.
- Alembic `upgrade head`, `current`, and `check`: head `0014`; no new upgrade operations detected.
- Python compilation: passed for app, scraper, publication, and revision 0014.
- NCAA catalog validation: 942 selectable teams, zero errors.
- `git diff --check`: passed.

## Known Stubs

None - no placeholder or unwired behavior was introduced.

## Threat Flags

None - revision 0014's database trigger surface and application publication boundary are the planned trust surfaces and are covered by the plan's race, lifecycle, and ordering tests.

## Self-Check: PASSED

- Revision 0014, publication/job changes, both regression files, and this summary exist.
- Task commits `7ff3279`, `83b3899`, `8615ee9`, `72ce8d7`, and `0412495` resolve in repository history.
- Every acceptance criterion, focused suite, migration drift check, full 411-test suite, catalog check, compilation check, and diff hygiene check passed.
- `brag-output/` remains untouched and untracked.

## Next Phase Readiness

- The verifier's final known authoritative-identity write skew is closed and revision 0014 is ready for the round-five release validator.
- Plans 01-39 through 01-41 can proceed; no blocker remains from Plan 01-38.

---
*Phase: 01-canonical-snapshot-foundation*
*Completed: 2026-11-11*
