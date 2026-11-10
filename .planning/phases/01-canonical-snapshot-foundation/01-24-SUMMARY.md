---
phase: 01-canonical-snapshot-foundation
plan: "24"
subsystem: database
tags: [postgresql, sqlalchemy, alembic, provenance, player-identity]

requires:
  - phase: 01-canonical-snapshot-foundation
    provides: Same-PlayerTeamSeason source/fact binding, immutable publication, and parser-2/core-v2 canonical evidence through Plan 01-23
provides:
  - Reciprocal database enforcement that authoritative source IDs equal their linked canonical Player IDs
  - Historical mismatch validation plus reversible SourceRecord, Player, and PlayerTeamSeason triggers
  - Independent publication-time authoritative Player equivalence validation
affects: [canonical-publication, provenance, player-identity, excel, power-bi]

tech-stack:
  added: []
  patterns: [reciprocal PostgreSQL trigger invariant, fail-closed historical validation, defense-in-depth publication query]

key-files:
  created:
    - migrations/versions/0010_authoritative_source_player_binding.py
  modified:
    - roster_scout/models.py
    - roster_scout/snapshots.py
    - tests/test_snapshot_persistence.py

key-decisions:
  - "Authoritative source-to-Player equivalence is enforced reciprocally from SourceRecord, Player, and PlayerTeamSeason mutation paths for both draft and published data."
  - "The authoritative trigger defers unresolved tenant ownership to the existing named composite foreign key, preserving the original ownership contract while enforcing ID equality for valid links."
  - "Publication independently joins SourceRecord through PlayerTeamSeason to Player and rejects sanitized mismatches, while null ID-less fallback evidence remains valid."

patterns-established:
  - "Reciprocal identity invariant: every mutable participant that can break authoritative equivalence owns a named database trigger."
  - "Layered publication proof: database enforcement prevents new corruption and the publisher still rejects historical or migration-injected mismatches."

requirements-completed:
  - SNAP-03
  - DATA-03
  - DATA-07
  - DATA-09
  - OPS-04

duration: 57 min
completed: 2026-11-10
---

# Phase 1 Plan 24: Authoritative Source-to-Player Equivalence Summary

**PostgreSQL and the publisher now prove that every nonnull NCAA source player ID names the exact canonical Player behind its fact, without weakening ID-less fallback or tenant ownership behavior.**

## Performance

- **Duration:** 57 min
- **Started:** 2026-11-10T16:48:30Z
- **Completed:** 2026-11-10T17:45:53Z
- **Tasks:** 2
- **Files modified:** 4

## Accomplishments

- Added reversible revision 0010 with pre-DDL historical validation and reciprocal SourceRecord, Player, and PlayerTeamSeason trigger enforcement.
- Rejected authoritative source/Player mismatches introduced by inserts, source ID changes, Player source changes, and season-identity reassignment in both draft and published snapshots.
- Added an independent publisher query that fails closed on historical mismatches with sanitized errors while preserving correct authoritative and ID-less rows.
- Proved downgrade/re-upgrade, base-to-head fresh install, model drift, direct SQL counterexamples, and the complete Phase 1 release gate.

## Task Commits

Each TDD task was committed through separate RED and GREEN gates:

1. **Task 1 RED: Expose authoritative player identity gaps** - `6c4fd95` (test)
2. **Task 1 GREEN: Enforce authoritative player identity** - `c701af9` (fix)
3. **Task 2 RED: Expose publication identity mismatch** - `417e48d` (test)
4. **Task 2 GREEN: Recheck player identity at publication** - `b191355` (fix)
5. **Release regression fix: Preserve source ownership enforcement** - `fbab032` (fix)

## Files Created/Modified

- `migrations/versions/0010_authoritative_source_player_binding.py` - Historical mismatch rejection and reversible reciprocal trigger functions.
- `roster_scout/models.py` - Named database-trigger metadata for each invariant participant.
- `roster_scout/snapshots.py` - Transactional publication-time SourceRecord-to-Player equality proof.
- `tests/test_snapshot_persistence.py` - Draft/published direct-SQL negatives, positive/null controls, publication defense, and migration lifecycle coverage.

## Decisions Made

- Applied the equivalence rule to every nonnull source player ID regardless of snapshot status so draft corruption cannot become publishable history.
- Kept null source IDs legal for source-first and unresolved/fallback flows; equivalence begins only when a source ID and tenant-owned identity are both present.
- Let the existing composite foreign key report cross-snapshot or cross-organization ownership violations instead of masking them with the new trigger.
- Kept publication verification independent of trigger availability so migration controls or historical rows cannot bypass the final snapshot transition.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Rolled back expected direct-SQL failures to their savepoint**
- **Found during:** Task 1 GREEN
- **Issue:** PostgreSQL correctly rejected the mismatch, but the test helper left the transaction aborted before trigger restoration.
- **Fix:** Made the helper explicitly roll back its nested savepoint after each expected database error and restored the existing conflicting-identity fixture value.
- **Files modified:** `tests/test_snapshot_persistence.py`
- **Verification:** All SourceRecord, Player, and PlayerTeamSeason draft/published mutation tests pass.
- **Committed in:** `c701af9`

**2. [Rule 1 - Bug] Preserved named composite ownership errors alongside the new invariant**
- **Found during:** Full Phase 1 release gate
- **Issue:** The new BEFORE trigger treated a cross-tenant identity lookup miss as an authoritative mismatch, masking the existing named ownership foreign key; older fixtures also generated ordinal IDs instead of their canonical Player IDs.
- **Fix:** The trigger now defers unresolved tenant-scoped identities to the composite foreign key, and source-identity fixtures read the linked canonical Player ID.
- **Files modified:** `migrations/versions/0010_authoritative_source_player_binding.py`, `tests/test_snapshot_persistence.py`
- **Verification:** Source ownership, authoritative identity, migration, and full release suites pass.
- **Committed in:** `fbab032`

---

**Total deviations:** 2 auto-fixed (2 Rule 1 bugs).
**Impact on plan:** Both fixes preserve existing transaction and tenant-ownership contracts while retaining the planned authoritative equivalence guarantee.

## Issues Encountered

- The managed sandbox required scoped approval for localhost PostgreSQL and Git index writes.
- An initial release-gate retry overlapped a still-running captured process and produced disposable-database DDL collisions. After the original process finished, one non-overlapping release run passed completely.

## User Setup Required

None - no external service configuration required.

## Verification Results

- Task 1 authoritative constraint plus complete migration class: 14 tests passed.
- Task 2 publication defense: 3 tests passed.
- Ownership plus authoritative constraint/publication regression target: 10 tests passed.
- Snapshot persistence and canonical publication suites passed.
- Complete Phase 1 release gate: 299 tests passed.
- Migration lifecycle: revision 0010 downgrade/re-upgrade, base-to-head fresh install, and historical mismatch rejection passed.
- Alembic model drift: `No new upgrade operations detected.`
- NCAA catalog: 942 selectable teams (308 D1, 253 D2, 381 D3), zero errors; 25 expected missing-stable-ID warnings unchanged.
- Required compilation, template existence, Docker Compose rendering, and `git diff --check`: passed.

## Known Stubs

None - modified production paths contain no placeholders or unwired data flow.

## TDD Gate Compliance

PASSED - Task 1 RED `6c4fd95` precedes GREEN `c701af9`; Task 2 RED `417e48d` precedes GREEN `b191355`.

## Self-Check: PASSED

- All four created or modified implementation/test files and this summary exist.
- Task commits `6c4fd95`, `c701af9`, `417e48d`, `b191355`, and `fbab032` resolve in repository history.
- Every task acceptance criterion and the complete release gate passed.
- `brag-output/` remains untouched and untracked.

## Next Phase Readiness

- The authoritative source-ID equivalence verification gap is closed at both storage and publication boundaries.
- Downstream workbook and Power BI consumers can rely on published source evidence naming the same canonical Player as each fact.
- Remaining Phase 1 gap plans can proceed; production managed-service and uninterrupted-hosting prerequisites remain unchanged.

---
*Phase: 01-canonical-snapshot-foundation*
*Completed: 2026-11-10*
