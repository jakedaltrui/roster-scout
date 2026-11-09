---
phase: 01-canonical-snapshot-foundation
plan: "04"
subsystem: database
tags: [postgresql, alembic, sqlalchemy, immutable-snapshots, data-validation]

requires:
  - phase: 01-canonical-snapshot-foundation
    provides: Canonical snapshot schema, publication triggers, and durable report-job migration chain
provides:
  - Frozen explicit DDL for the historical 0001 schema boundary
  - Bidirectional published-parent immutability for canonical snapshot children
  - Append-only metric definitions once referenced by published snapshots
  - Parser, ORM, and PostgreSQL rejection of negative canonical counts
affects: [canonical-publication, metric-lineage, migration-lifecycle, excel, power-bi]

tech-stack:
  added: []
  patterns: [frozen historical migrations, reversible integrity hardening, defense-in-depth validation]

key-files:
  created:
    - migrations/versions/0003_snapshot_integrity_hardening.py
  modified:
    - migrations/versions/0001_canonical_snapshot_foundation.py
    - roster_scout/models.py
    - roster_scout/canonical.py
    - tests/test_snapshot_persistence.py
    - tests/test_canonical_data.py

key-decisions:
  - "Revision 0001 is frozen from the schema that originally shipped rather than reconstructed from current ORM metadata."
  - "Canonical count validity is enforced at both the NCAA parsing boundary and the PostgreSQL storage boundary."

patterns-established:
  - "Published-parent checks inspect both OLD and NEW ownership on UPDATE so reassignment cannot bypass immutability."
  - "Metric semantics change by inserting a new definition version; referenced published definitions are never edited in place."

requirements-completed:
  - SNAP-03
  - DATA-05
  - DATA-08
  - OPS-04

duration: 11 min
completed: 2026-11-09
---

# Phase 1 Plan 4: Canonical Snapshot Integrity Hardening Summary

**Frozen Alembic foundation DDL plus PostgreSQL triggers and count checks that prevent published snapshot tampering and malformed canonical facts**

## Performance

- **Duration:** 11 min
- **Started:** 2026-11-09T02:26:33Z
- **Completed:** 2026-11-09T02:37:21Z
- **Tasks:** 2
- **Files modified:** 6

## Accomplishments

- Replaced revision 0001's mutable ORM metadata calls with explicit historical tables, indexes, named constraints, triggers, and inverse downgrade operations.
- Closed the published-parent reassignment bypass by checking both old and new snapshot ownership for updates to every protected child table.
- Made metric definitions append-only once a ready or partial snapshot references them while preserving new `(code, version)` insertion.
- Rejected negative batting and pitching counts in canonical parsing, ORM metadata, and PostgreSQL without collapsing zero into missing data.

## Task Commits

Each TDD task was committed through separate RED and GREEN gates:

1. **Task 1 RED: Expose mutable initial migration** - `0a715c2` (test)
2. **Task 1 GREEN: Freeze canonical foundation migration** - `07a4ad2` (fix)
3. **Task 2 RED: Expose snapshot integrity gaps** - `27ab0a9` (test)
4. **Task 2 GREEN: Harden canonical snapshot integrity** - `6228155` (fix)

## Files Created/Modified

- `migrations/versions/0001_canonical_snapshot_foundation.py` - Explicit reproducible 17-table historical foundation schema and reversible teardown.
- `migrations/versions/0003_snapshot_integrity_hardening.py` - Reversible published-parent, metric-definition, and nonnegative-count hardening.
- `roster_scout/models.py` - ORM metadata mirrors every batting and pitching count check.
- `roster_scout/canonical.py` - Canonical integer parsing rejects negative source counts.
- `tests/test_snapshot_persistence.py` - Migration-boundary, trigger-bypass, metric, count, and lifecycle regressions.
- `tests/test_canonical_data.py` - Negative, zero, and missing canonical count coverage.

## Decisions Made

- Used the actual 01-01 shipped model as the source for explicit revision 0001 DDL, preserving the pre-0002 boundary rather than freezing today's schema by accident.
- Kept zero and null as separate valid canonical states while treating every negative count as invalid upstream evidence.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

- The managed sandbox blocked localhost PostgreSQL connections. Scoped approved execution was used for the disposable `roster_scout_test` database; no external service or production data was accessed.

## User Setup Required

None - no external service configuration required.

## Verification Results

- Task 1 migration suite: 4 tests passed, including clean 0001 boundary inspection, downgrade-to-base, and re-upgrade.
- Task 2 targeted suites: 31 tests passed across PostgreSQL persistence and canonical parsing.
- Alembic upgrade and drift check: head reached successfully; no new upgrade operations detected.
- Full deterministic discovery with PostgreSQL enabled: 101 tests passed.
- NCAA catalog validation: 942 selectable 2026 D1/D2/D3 teams, zero errors.
- Python compilation, required template existence, and `git diff --check`: passed.

## Known Stubs

None.

## Threat Flags

None - the new SQL triggers and validation checks directly implement the plan's registered tampering and migration-history mitigations.

## Next Phase Readiness

- CR-01, WR-02, WR-04, and WR-06 are closed with failing-before/passing-after regression coverage.
- Ready for Plan 01-05 to continue gap closure on the hardened canonical schema.

## Self-Check: PASSED

- All six created or modified implementation/test files exist.
- Task commits `0a715c2`, `07a4ad2`, `27ab0a9`, and `6228155` exist in git history.
- Every task acceptance criterion, plan-level verification command, and repository handoff check passed.

---
*Phase: 01-canonical-snapshot-foundation*
*Completed: 2026-11-09*
