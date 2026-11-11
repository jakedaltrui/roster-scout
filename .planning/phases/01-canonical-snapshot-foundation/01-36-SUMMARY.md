---
phase: 01-canonical-snapshot-foundation
plan: "36"
subsystem: evidence-retention
tags: [postgresql, sqlalchemy, advisory-locks, skip-locked, readiness]

requires:
  - phase: 01-canonical-snapshot-foundation
    provides: Bounded SKIP LOCKED evidence purge, persisted readiness heartbeat, and database-enforced retention through Plan 01-27
provides:
  - Shared transaction advisory lock for eligible evidence insertion and complete-scan certification
  - Monotonic PostgreSQL purge heartbeat timestamps with timestamp-associated counts
  - Real two-connection count/upsert and out-of-order completion regressions
affects: [evidence-retention, purge-readiness, operations, release-gate]

tech-stack:
  added: []
  patterns: [transaction advisory lock, monotonic PostgreSQL upsert, conditional conflict update, deterministic concurrency barriers]

key-files:
  created: []
  modified:
    - roster_scout/evidence.py
    - tests/test_evidence.py

key-decisions:
  - "Eligible evidence writers and final complete-scan certification share one stable signed-bigint transaction advisory lock."
  - "The singleton lock covers only the final count and heartbeat upsert; bounded SKIP LOCKED deletion batches remain independently concurrent."
  - "PostgreSQL GREATEST preserves heartbeat time while last_purged_count changes only when the incoming certification timestamp wins."

patterns-established:
  - "Certification protocol: serialize every eligibility-creating write with the final complete-scan count and success heartbeat, not with ordinary batch progress."
  - "Monotonic metadata: update a timestamp-associated value only when its timestamp is at least as new as the stored timestamp."

requirements-completed: [SNAP-04, DATA-07, OPS-02]

duration: 6 min
completed: 2026-11-11
---

# Phase 1 Plan 36: Serialized Monotonic Purge Certification Summary

**Evidence purge success now certifies one serialized complete scan, while PostgreSQL preserves the newest heartbeat and its matching purge count across out-of-order completions.**

## Performance

- **Duration:** 6 min
- **Started:** 2026-11-11T05:32:18Z
- **Completed:** 2026-11-11T05:38:22Z
- **Tasks:** 2
- **Files modified:** 2

## Accomplishments

- Added one stable transaction advisory-lock domain shared by failure-evidence insertion and purge finalization, preventing eligible bytes from committing inside the final zero-count-to-heartbeat window.
- Kept the singleton lock out of the bounded `FOR UPDATE SKIP LOCKED` deletion loop, preserving independent batch commits and contention-safe progress.
- Made `last_success_at` and `updated_at` monotonic with PostgreSQL `GREATEST`, and tied `last_purged_count` to the timestamp that wins the conflict update.
- Added deterministic real-connection regressions that observe an insertion waiting on the advisory lock and force an older logical purge to commit after a newer one.

## Task Commits

Each task was committed atomically through the shared TDD RED gate and task-specific GREEN commits:

1. **Tasks 1-2 RED: Expose count/upsert and out-of-order races** - `44bfc44` (test)
2. **Task 1 GREEN: Serialize eligible inserts with purge certification** - `ffd2480` (fix)
3. **Task 2 GREEN: Preserve the newest heartbeat and associated count** - `4757ddc` (fix)

## Files Created/Modified

- `roster_scout/evidence.py` - Shares a stable advisory lock across evidence writers and final certification, then performs a monotonic conditional heartbeat upsert.
- `tests/test_evidence.py` - Uses real PostgreSQL connections, bounded thread joins, backend wait-state observation, and deterministic barriers to prove both race fixes.

## Decisions Made

- Used the stable signed-bigint value represented by ASCII `EVIDENCE` as the process-independent PostgreSQL advisory-lock key.
- Acquired the eligibility lock before a writer inserts compressed bytes and immediately before the purge's final ordinary-MVCC count; deletion batches intentionally remain outside that lock.
- Preserved the newer purge count on an older conflict by comparing `excluded.last_success_at` to the stored timestamp inside SQL rather than relying on Python call order.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

- The local PostgreSQL connection and existing `uv` cache required the managed execution permission expected for this workspace; once allowed, every required command completed normally.
- Full discovery emitted the already-documented post-suite psycopg `ResourceWarning`; it did not fail tests and is unchanged by this plan.

## User Setup Required

None - the implementation uses the existing PostgreSQL database and evidence-purge process.

## Verification Results

- Task 1 serialization command: 3 tests passed, including both existing bounded SKIP LOCKED batch regressions.
- Task 2 monotonicity/readiness command: 7 tests passed.
- Full evidence module plus app readiness: 34 tests passed.
- Complete PostgreSQL-backed discovery: 379 tests passed.
- Alembic schema drift: no new upgrade operations detected.
- Python compilation, required templates, 942-team NCAA catalog validation, and `git diff --check`: passed.

## Known Stubs

None. The modified runtime and test files contain no placeholder, TODO, FIXME, or unavailable-data implementation.

## Threat Flags

None - the evidence-writer boundary, purge-batching denial-of-service concern, heartbeat repudiation risk, and aggregate-only readiness surface are the explicit T-01-36-01 through T-01-36-04 mitigations.

## TDD Gate Compliance

PASSED - the failing concurrency regressions were committed in `44bfc44` before the serialization and monotonic-upsert implementations in `ffd2480` and `4757ddc`.

## Self-Check: PASSED

- Both modified files and this summary exist.
- Commits `44bfc44`, `ffd2480`, and `4757ddc` resolve in repository history.
- Every task acceptance criterion, focused suite, evidence/readiness suite, full discovery run, schema-drift check, compilation check, template check, catalog validation, and diff check passed.
- `brag-output/` remains untouched and untracked.

## Next Phase Readiness

- Purge readiness can now be used by the final release gate without heartbeat regression or count-to-upsert insertion ambiguity.
- Plans 01-34 and 01-35 can complete secure client acknowledgement without changing the evidence-retention protocol.

---
*Phase: 01-canonical-snapshot-foundation*
*Completed: 2026-11-11*
