---
phase: 01-canonical-snapshot-foundation
plan: "25"
subsystem: database
tags: [postgresql, sqlalchemy, alembic, retention, failure-evidence]

requires:
  - phase: 01-canonical-snapshot-foundation
    provides: Durable failure-evidence storage, purge health, and authoritative player identity through Plan 01-24
provides:
  - Named PostgreSQL enforcement of positive failure-evidence retention through exactly 28 days
  - Fail-closed revision 0011 validation for invalid historical retention windows
  - Exact direct-SQL and service-level retention boundary coverage
affects: [failure-evidence, evidence-purge, compliance, canonical-operations]

tech-stack:
  added: []
  patterns: [named PostgreSQL interval constraint, fail-closed migration validation, direct-SQL boundary proof]

key-files:
  created:
    - migrations/versions/0011_failure_evidence_retention_constraint.py
  modified:
    - roster_scout/models.py
    - tests/test_snapshot_persistence.py
    - tests/test_evidence.py

key-decisions:
  - "PostgreSQL owns the positive-through-28-day failure-evidence retention invariant so application and direct-SQL callers cannot bypass policy."
  - "Revision 0011 aborts on invalid historical rows instead of rewriting, extending, truncating, or deleting evidence timestamps."

patterns-established:
  - "Exact interval contract: accept expires_at strictly after collected_at through collected_at plus 28 days, inclusive at the upper bound."
  - "Retention migration safety: validate existing rows before DDL and identify failures with the same named constraint used by the table."

requirements-completed:
  - DATA-07
  - OPS-04

duration: 6h 20m
completed: 2026-11-10
---

# Phase 1 Plan 25: Failure-Evidence Retention Invariant Summary

**PostgreSQL now rejects every failure-evidence retention window outside the exact positive-through-28-day policy, including direct SQL one microsecond beyond the cap.**

## Performance

- **Duration:** 6h 20m
- **Started:** 2026-11-10T17:50:11Z
- **Completed:** 2026-11-11T00:09:52Z
- **Tasks:** 2
- **Files modified:** 4

## Accomplishments

- Added reversible revision 0011 with pre-DDL validation that aborts on zero, negative, or over-28-day historical windows without mutating evidence.
- Mirrored the named `ck_failure_evidence_retention_window` constraint in SQLAlchemy metadata with the exact PostgreSQL interval expression.
- Proved exact direct-SQL acceptance at one microsecond and 28 days, rejection at zero, negative, and 28 days plus one microsecond, and transaction isolation after rejected inserts.
- Preserved normal service writes for configured 1-day retention and the default 28-day retention.
- Verified downgrade/re-upgrade, base-to-head fresh installation, invalid-history failure, and clean Alembic model drift.

## Task Commits

1. **Task 1 RED: Expose missing database retention invariant** - `8bc39b3` (test)
2. **Task 1 GREEN: Enforce the retention window in PostgreSQL** - `854fa02` (fix)
3. **Task 2: Prove exact database retention boundaries** - `8dbf392` (test)

## Files Created/Modified

- `migrations/versions/0011_failure_evidence_retention_constraint.py` - Fail-closed historical validation plus reversible named check constraint.
- `roster_scout/models.py` - Matching SQLAlchemy retention-window metadata.
- `tests/test_snapshot_persistence.py` - Invalid/valid history, downgrade/re-upgrade, fresh-install, and ORM drift coverage.
- `tests/test_evidence.py` - Exact direct-SQL boundaries, transaction rollback protection, and service controls.

## Decisions Made

- Used a named database check rather than application-only validation because migration SQL and future direct callers share the evidence table trust boundary.
- Made the maximum inclusive at exactly 28 days while requiring a strictly positive duration at the lower bound.
- Raised sanitized SQLSTATE `23514` migration failures that name the retention constraint without exposing stored troubleshooting content.
- Kept downgrade precise: it removes only the new constraint and leaves historical revisions and evidence rows untouched.

## Deviations from Plan

None - the implementation and acceptance scope followed the plan exactly.

## Issues Encountered

- Task 2 is test-only durable proof for behavior introduced by Task 1, so its new tests passed on their first run after Task 1 GREEN. The constraint implementation was not duplicated and no artificial failing test or production commit was created.
- The managed sandbox required scoped approval for localhost PostgreSQL and Git index writes.

## User Setup Required

None - no external service configuration required.

## Verification Results

- Task 1 retention migration plus complete migration class: 13 tests passed.
- Task 2 exact boundary class: 3 tests passed.
- Evidence and snapshot persistence suites: 69 tests passed.
- Complete Phase 1 release gate: 305 tests passed.
- Migration lifecycle: revision 0011 invalid-history rejection, downgrade/re-upgrade, and base-to-head fresh install passed.
- Alembic model drift: `No new upgrade operations detected.`
- NCAA catalog: 942 selectable teams (308 D1, 253 D2, 381 D3), zero errors; 25 expected missing-stable-ID warnings unchanged.
- Required compilation, template existence, and `git diff --check`: passed.

## Known Stubs

None - modified production paths contain no placeholders or unwired data flow.

## TDD Gate Compliance

PASSED for the behavior-adding task: Task 1 RED `8bc39b3` failed on the absent constraint before GREEN `854fa02`. Task 2 is a test-only verification task whose planned behavior was already implemented by Task 1; its boundary tests passed immediately and were committed separately as `8dbf392`.

## Self-Check: PASSED

- All four created or modified implementation/test files and this summary exist.
- Task commits `8bc39b3`, `854fa02`, and `8dbf392` resolve in repository history.
- Every task acceptance criterion and the complete release gate passed.
- `brag-output/` remains untouched and untracked.

## Next Phase Readiness

- Review CR-03 and the database-enforcement half of the retention verification gap are closed.
- Evidence purge and operations code can rely on PostgreSQL preventing nonpositive or over-policy retention windows.
- Remaining Phase 1 gap plans can proceed without changing the evidence service contract.

---
*Phase: 01-canonical-snapshot-foundation*
*Completed: 2026-11-10*
