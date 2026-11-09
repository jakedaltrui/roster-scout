---
phase: 01-canonical-snapshot-foundation
plan: "06"
subsystem: jobs
tags: [postgresql, advisory-locks, celery, concurrency, redelivery]

requires:
  - phase: 01-canonical-snapshot-foundation
    provides: Durable report jobs, atomic canonical publication, and conflict-safe shared canonical identities
provides:
  - Whole-attempt PostgreSQL execution claims for one logical report job
  - Retryable duplicate-delivery deferral without attempt or failure-state mutation
  - Barrier-controlled service and Celery entrypoint concurrency regressions
affects: [durable-jobs, browserless-capacity, celery-workers, canonical-publication, retry-accounting]

tech-stack:
  added: []
  patterns: [session-level advisory claim, dedicated detached lock connection, overlapping-delivery barrier test]

key-files:
  created: []
  modified:
    - roster_scout/database.py
    - roster_scout/jobs.py
    - tests/test_report_jobs.py

key-decisions:
  - "One detached PostgreSQL connection owns the report-job advisory claim across attempt transition, live collection, source persistence, canonical persistence, and publication."
  - "A duplicate delivery that misses the claim returns the allowlisted job_busy retry signal without changing durable attempt or failure state."

patterns-established:
  - "Whole-attempt claim: derive one stable signed advisory-lock key from the opaque job UUID and bind every attempt transaction to the owning connection."
  - "Concurrency proof: hold the winning collector behind an event while an independent delivery must finish as a non-mutating loser."

requirements-completed:
  - SNAP-02
  - SNAP-03
  - OPS-01

duration: 6 min
completed: 2026-11-09
---

# Phase 1 Plan 6: Whole-Attempt Report Job Claim Summary

**A detached PostgreSQL advisory-lock session now serializes each report job across live collection and atomic publication while overlapping Celery deliveries defer without consuming attempts or Browserless work**

## Performance

- **Duration:** 6 min
- **Started:** 2026-11-09T20:08:59Z
- **Completed:** 2026-11-09T20:15:13Z
- **Tasks:** 2
- **Files modified:** 3

## Accomplishments

- Added a deterministic session-level PostgreSQL claim derived from the opaque report-job UUID and held it on a dedicated pool-detached connection.
- Bound attempt transition, source persistence, canonical persistence, publication, and failure recording to sessions on the same claiming connection.
- Made a losing duplicate delivery return the sanitized `job_busy` retry outcome before collection, attempt increment, source writes, facts, snapshots, or terminal failure mutation.
- Added independent-session overlap tests at both `run_report_job()` and the real eager Celery task-entry boundary, including terminal replay and crash-release coverage.
- Proved timing stability with three consecutive concurrency runs, the full report-job module, and the 136-test PostgreSQL-backed repository suite.

## Task Commits

1. **Task 1 RED: Expose overlapping report job execution** - `d0e8943` (test)
2. **Task 1 GREEN: Hold whole-attempt PostgreSQL job claim** - `1f33db3` (feat)
3. **Task 2: Prove overlapping Celery delivery safety** - `bac6de1` (test)

## Files Created/Modified

- `roster_scout/database.py` - Deterministic job lock key plus a pool-detached session advisory claim whose connection loss releases PostgreSQL ownership.
- `roster_scout/jobs.py` - Whole-attempt claim integration and allowlisted non-mutating `job_busy` delivery outcome.
- `tests/test_report_jobs.py` - Independent-session service overlap, claim connection-loss recovery, eager Celery overlap, terminal replay, and safe failure-message assertions.

## Decisions Made

- Detached the advisory connection from SQLAlchemy's pool so closing the owner represents an actual PostgreSQL session loss instead of returning a still-locked backend connection to the pool.
- Committed the lock-acquisition statement's implicit transaction before creating bound ORM sessions; the advisory lock remains session-scoped while each attempt transaction commits independently.
- Kept contention outside `_record_failure()` so a losing duplicate cannot move the running job to `retrying`, overwrite failure fields, or consume a durable attempt.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

- The managed sandbox blocked localhost PostgreSQL access; scoped approved execution was used only for the disposable `roster_scout_test` database.
- Context7 was unavailable, so no version-specific documentation payload could be fetched through the prescribed documentation helper.
- The first bound-session implementation inherited the advisory-lock query's implicit transaction, causing ORM commits to remain inside the outer connection transaction. Committing that acquisition transaction preserved the session lock and restored independent durable attempt commits.

## User Setup Required

None - no external service configuration required.

## Verification Results

- `ConcurrentExecutionClaimTests`: 2 passed, including an actual overlapping delivery and connection-loss claim release.
- `CeleryConcurrentDeliveryTests`: 2 passed, including eager task-entry overlap, terminal replay, and allowlisted persisted failure text.
- Combined concurrency classes passed three consecutive runs without timing failure.
- Full `tests.test_report_jobs`: 18 passed.
- Full PostgreSQL-backed discovery: 136 passed.
- NCAA catalog validation: 942 selectable 2026 D1/D2/D3 teams, zero errors.
- Required Python compilation, template existence, and `git diff --check`: passed.

## Known Stubs

None.

## Threat Flags

None - the advisory claim and sanitized contention path directly implement the registered duplicate-delivery denial-of-service, attempt-state tampering, repudiation, and disclosure mitigations without adding a new trust boundary.

## Next Phase Readiness

- CR-02 is closed with real overlapping-delivery regressions at the service and Celery entry boundaries.
- Duplicate broker delivery cannot multiply collection, attempt accounting, source/fact persistence, or snapshot publication for one logical job.
- Ready for Plan 01-07 transactional dispatch/outbox recovery; the managed-service provisioning blockers already recorded in project state remain unchanged.

## TDD Gate Compliance

- Task 1 RED commit `d0e8943` failed because the second delivery remained live during collection and the advisory-claim helper did not exist; GREEN commit `1f33db3` passed both regressions.
- Task 2 added entry-boundary regression coverage in `bac6de1`; the underlying whole-attempt behavior was already supplied by Task 1, so no redundant production commit was required.

## Self-Check: PASSED

- All three modified implementation/test files exist.
- Task commits `d0e8943`, `1f33db3`, and `bac6de1` exist in git history.
- Every task acceptance criterion, plan-level verification command, and repository handoff check passed.

---
*Phase: 01-canonical-snapshot-foundation*
*Completed: 2026-11-09*
