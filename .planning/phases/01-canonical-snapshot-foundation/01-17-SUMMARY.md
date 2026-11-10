---
phase: 01-canonical-snapshot-foundation
plan: "17"
subsystem: database
tags: [postgresql, sqlalchemy, alembic, provenance, player-identity]

requires:
  - phase: 01-canonical-snapshot-foundation
    provides: Immutable canonical snapshots, exact source evidence, typed fact traceability, and generation-safe source-first recovery through Plan 01-16
provides:
  - Durable nullable SourceRecord-to-PlayerTeamSeason identity binding for draft recovery
  - Composite fact/source identity foreign keys that reject same-snapshot player swaps
  - Migration-safe historical backfill that aborts ambiguous source mappings
  - Publication validation requiring nonnull matching source and fact identities
affects: [canonical-publication, provenance, player-identity, excel, power-bi]

tech-stack:
  added: []
  patterns: [same-player composite foreign key, fail-closed migration backfill, source-before-fact identity assignment]

key-files:
  created:
    - migrations/versions/0008_source_identity_binding.py
  modified:
    - roster_scout/models.py
    - roster_scout/jobs.py
    - roster_scout/snapshots.py
    - tests/test_snapshot_persistence.py
    - tests/test_canonical_publish.py

key-decisions:
  - "Source records keep a nullable snapshot identity link so exact evidence may commit before canonicalization, while every typed fact requires the same source/identity composite key."
  - "Revision 0008 backfills only uniquely determined existing mappings and aborts when one source record points to multiple player identities."
  - "Canonicalization assigns and flushes every source identity before facts, and publication independently rechecks nonnull equality before changing snapshot status."

patterns-established:
  - "Draft-to-canonical binding: source rows may be null while incomplete, then become identity-bound before any typed fact is inserted."
  - "Historical hardening: deterministic backfills may suspend only the affected immutability trigger inside transactional DDL, then restore it before constraints are installed."

requirements-completed:
  - SNAP-03
  - DATA-03
  - DATA-07
  - DATA-09
  - OPS-04

duration: 13 min
completed: 2026-11-10
---

# Phase 1 Plan 17: Same-Player Source Identity Binding Summary

**PostgreSQL now binds every published NCAA source row to the exact PlayerTeamSeason used by its typed fact, rejecting swapped identities at both database and publication boundaries.**

## Performance

- **Duration:** 13 min
- **Started:** 2026-11-10T02:10:26Z
- **Completed:** 2026-11-10T02:23:27Z
- **Tasks:** 2
- **Files modified:** 6

## Accomplishments

- Added reversible revision 0008 with nullable source identity links, tenant-scoped player ownership, unique source identity keys, and batting/pitching same-player composite foreign keys.
- Backfilled valid historical facts without data loss and failed closed when one pre-upgrade source row mapped to multiple player identities.
- Assigned source identities before typed fact insertion, rejected conflicting second assignments, and required matching nonnull links in the atomic publication query.
- Proved ORM and direct-SQL player swaps fail, D1/D2/D3 and two-way players retain one correct identity, and source-first final-attempt recovery preserves exact evidence.

## Task Commits

Each TDD task was committed through separate RED and GREEN gates:

1. **Task 1 RED: Expose source identity binding gaps** - `182d8d9` (test)
2. **Task 1 GREEN: Enforce source identity binding** - `83dc9e4` (feat)
3. **Task 2 RED: Expose swapped publication identity gaps** - `c532631` (test)
4. **Task 2 GREEN: Bind canonical evidence to player identity** - `900ca10` (feat)

## Files Created/Modified

- `migrations/versions/0008_source_identity_binding.py` - Unique historical backfill, source identity ownership, same-player fact constraints, and reversible downgrade.
- `roster_scout/models.py` - ORM metadata for nullable source identities and composite fact/source foreign keys.
- `roster_scout/jobs.py` - Source identity assignment and conflicting-assignment rejection before fact persistence.
- `roster_scout/snapshots.py` - Explicit nonnull source/fact identity equality in publication traceability.
- `tests/test_snapshot_persistence.py` - Direct SQL constraint, tenant ownership, ambiguous backfill, fresh-install, and downgrade/re-upgrade coverage.
- `tests/test_canonical_publish.py` - Swapped-ID negatives plus D1/D2/D3, two-way-player, draft, and final-attempt positives.

## Decisions Made

- Kept `SourceRecord.player_team_season_id` nullable because source-first crash recovery intentionally persists exact evidence before canonical identities exist.
- Used `(source_record_id, player_team_season_id, snapshot_id, organization_id)` as the fact-to-source foreign key, retaining the direct fact-to-PlayerTeamSeason constraint as an independent ownership guarantee.
- Treated multiple historical identities for one source row as invalid migration input; revision 0008 never guesses or creates an identity during backfill.
- Flushed source identity assignments before adding typed facts so database enforcement is valid even without ORM relationships or deferred constraints.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Allowed valid published rows to receive the deterministic migration backfill**
- **Found during:** Task 2 GREEN migration regression
- **Issue:** The existing published-child immutability trigger correctly rejected revision 0008's source-row backfill, preventing valid published history from upgrading.
- **Fix:** Revision 0008 disables only `trg_source_records_immutable` for the deterministic transactional backfill and re-enables it immediately before installing the new constraints. Runtime immutability remains unchanged.
- **Files modified:** `migrations/versions/0008_source_identity_binding.py`
- **Verification:** Published revision-0003 data upgrades through 0008, downgrade/re-upgrade and fresh install pass, and published source mutations remain rejected after migration.
- **Committed in:** `900ca10`

---

**Total deviations:** 1 auto-fixed (1 Rule 1 migration bug).
**Impact on plan:** The fix is required to satisfy the planned preservation of valid published history and does not weaken runtime immutability or expand scope.

## Issues Encountered

- The managed sandbox blocks localhost PostgreSQL and Git index writes. Scoped approved execution was used only for the disposable `roster_scout_test` database and normal hooked commits.
- Context7 was unavailable in the environment; installed Alembic, SQLAlchemy, and PostgreSQL behavior was verified directly through migration and constraint tests.

## User Setup Required

None - no external service configuration required.

## Verification Results

- Task 1 constraint and migration target: 11 tests passed.
- Task 2 source-binding and publication target: 23 tests passed.
- Final-attempt source-first recovery target: 6 tests passed.
- Full plan gate across snapshot persistence, canonical publication, and Phase 1 release: 80 tests passed.
- Full PostgreSQL-backed repository discovery: 227 tests passed.
- Migration lifecycle: valid-history backfill, ambiguous-history rejection, 0008-to-0007 downgrade, re-upgrade, and base-to-head fresh install passed.
- Alembic model drift: `No new upgrade operations detected.`
- NCAA catalog: 942 selectable teams (308 D1, 253 D2, 381 D3), zero errors; 25 expected missing-stable-ID warnings unchanged.
- Required Python compilation, template existence, and `git diff --check`: passed.

## Known Stubs

None - modified production paths contain no placeholders or unwired data flow.

## TDD Gate Compliance

PASSED - Task 1 RED `182d8d9` precedes GREEN `83dc9e4`; Task 2 RED `c532631` precedes GREEN `900ca10`.

## Self-Check: PASSED

- All six created or modified implementation/test files and this summary exist.
- Task commits `182d8d9`, `83dc9e4`, `c532631`, and `900ca10` resolve in repository history.
- Every task acceptance criterion and plan-level verification command passed.
- `brag-output/` remains untouched and untracked.

## Next Phase Readiness

- Verification gap 4 / CR-04 is closed at schema, persistence, and publication layers.
- Published evidence can no longer be reassigned to another canonical player while preserving counts and tenant ownership.
- Plan 01-18 can proceed; existing managed-service and uninterrupted-hosting production prerequisites remain unchanged.

---
*Phase: 01-canonical-snapshot-foundation*
*Completed: 2026-11-10*
