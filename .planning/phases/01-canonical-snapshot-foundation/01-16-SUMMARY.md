---
phase: 01-canonical-snapshot-foundation
plan: "16"
subsystem: jobs
tags: [postgresql, celery, transactional-outbox, compare-and-set, crash-recovery]

requires:
  - phase: 01-canonical-snapshot-foundation
    provides: Durable report-job attempts, transactional dispatch outbox, whole-attempt advisory claims, and committed-evidence recovery from Plans 01-06 through 01-08
provides:
  - PostgreSQL-owned retry scheduling with persisted generation and bounded due time
  - Generation CAS that admits one worker attempt and rejects duplicate, stale, or overlapping deliveries before collection
  - Exact N-to-N+1 recovery after worker process loss, with max-attempt enforcement and duplicate-safe reconciliation
affects: [report-jobs, celery-delivery, transactional-outbox, crash-recovery, migration-lifecycle]

tech-stack:
  added: []
  patterns: [generation compare-and-set, database-owned exponential backoff, advisory-claim crash reconciliation, stale-delivery no-op]

key-files:
  created:
    - migrations/versions/0007_report_attempt_generations.py
  modified:
    - roster_scout/models.py
    - roster_scout/repositories.py
    - roster_scout/jobs.py
    - roster_scout/tasks.py
    - tests/test_report_jobs.py
    - tests/test_canonical_publish.py
    - tests/test_phase1_release.py

key-decisions:
  - "PostgreSQL outbox state is the sole retry authority; Celery workers return after durable scheduling and never invoke self.retry."
  - "Every delivery carries only the canonical job UUID and positive generation integer; only the matching generation CAS may increment attempts or collect."
  - "Reconciliation advances an abandoned started generation N to one N+1 intent, while complete committed evidence may use N+1 only for evidence finalization without another collector attempt."

patterns-established:
  - "Delivery protocol: validate UUID/generation, acquire the whole-attempt advisory claim, CAS current generation ownership, then collect."
  - "Recovery protocol: hold the same advisory claim through row lock and commit, then either advance once to N+1 or terminally exhaust without a new intent."

requirements-completed:
  - SNAP-02
  - SNAP-03
  - OPS-01
  - OPS-04

duration: 17 min
completed: 2026-11-10
---

# Phase 1 Plan 16: Retry Generation Safety Summary

**PostgreSQL now owns retry generations and due times, while a generation CAS makes duplicate, stale, and crash-recovery deliveries safe under at-least-once execution.**

## Performance

- **Duration:** 17 min
- **Started:** 2026-11-10T01:47:11Z
- **Completed:** 2026-11-10T02:03:59Z
- **Tasks:** 2
- **Files modified:** 8

## Accomplishments

- Added reversible revision 0007 with job and outbox generation fields, named integrity checks, existing-row backfill, and complete downgrade behavior.
- Made generation-specific outbox leasing, acknowledgement, release, closure, and retry timing authoritative in PostgreSQL with capped exponential backoff.
- Changed the Celery boundary to exactly `(job_id, generation)`, removed Celery retry authority, and rejected invalid, stale, duplicate, busy, exhausted, and terminal deliveries before collection.
- Proved abrupt process loss after generation CAS releases the PostgreSQL advisory claim and reconciliation creates exactly one N+1 intent through the real outbox/task path.
- Preserved committed-evidence finalization, migration lifecycle, the full 213-test PostgreSQL suite, and existing workbook/catalog contracts.

## Task Commits

Each task was committed atomically through its TDD gates:

1. **Task 1 RED: Expose retry-generation persistence gaps** - `1f60ced` (test)
2. **Task 1 GREEN: Persist retry attempt generations** - `a2a6f77` (feat)
3. **Task 2 RED: Expose stale-delivery and crash-recovery gaps** - `8d5bc82` (test)
4. **Task 2 GREEN: Claim worker attempts by generation CAS** - `b097517` (feat)
5. **Regression alignment: Exercise generation recovery in older crash suites** - `a4f2b8b` (test)

## Files Created/Modified

- `migrations/versions/0007_report_attempt_generations.py` - Generation columns, backfill, constraints, indexes, and reversible migration lifecycle.
- `roster_scout/models.py` - Durable job and dispatch generation state with model-level checks.
- `roster_scout/repositories.py` - Generation-bearing leases and token-plus-generation dispatch mutations.
- `roster_scout/jobs.py` - Bounded backoff, generation CAS execution, stale-delivery no-ops, and exact crash reconciliation.
- `roster_scout/tasks.py` - UUID-plus-generation Celery entry point without `self.retry` or Celery retry options.
- `tests/test_report_jobs.py` - Schema, retry, duplicate, stale, exhaustion, process-loss, and real outbox/task regressions.
- `tests/test_canonical_publish.py` - Existing crash-evidence test aligned with N-to-N+1 reconciliation.
- `tests/test_phase1_release.py` - Release-path dispatch payload and process-loss test aligned with generation recovery.

## Decisions Made

- Kept retry scheduling entirely in the transactional outbox so broker delivery cannot create a second retry clock.
- Required both lease token and generation on mutating dispatch operations, preventing an old delivery from acknowledging, releasing, or closing a newer intent.
- Counted attempts only when the current generation CAS succeeds; all stale and duplicate outcomes return before the collector or failure metadata can run.
- Allowed complete exact-job evidence to advance to an evidence-only finalization generation even when the attempt budget is spent; that path neither collects nor increments `attempt_count`.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing Critical Functionality] Preserved committed-evidence authority at an exhausted attempt budget**
- **Found during:** Task 2 GREEN full report-job verification
- **Issue:** Strictly terminalizing every abandoned final generation would discard already committed, complete, exact-job canonical evidence after a worker died before final job-state publication, regressing Plan 01-08.
- **Fix:** Reconciliation may create one N+1 intent solely to finalize complete committed evidence. `run_report_job` verifies that evidence without collection or another attempt increment; incomplete evidence still exhausts terminally with no N+1 intent.
- **Files modified:** `roster_scout/jobs.py`, `tests/test_report_jobs.py`
- **Verification:** Complete-evidence crash recovery, incomplete-evidence rejection, retry exhaustion, and full report-job suites pass.
- **Committed in:** `b097517`

**2. [Rule 1 - Bug] Updated older crash regressions for generation-safe recovery**
- **Found during:** Full PostgreSQL unittest discovery after Task 2
- **Issue:** Existing canonical-publication and phase-release tests replayed started generation N directly and expected UUID-only task payloads, contradicting the new rule that an abandoned started generation must advance to N+1.
- **Fix:** Routed those tests through `reconcile_stale_report_jobs()` before finalization and asserted `(job UUID, generation)` dispatch payloads.
- **Files modified:** `tests/test_canonical_publish.py`, `tests/test_phase1_release.py`
- **Verification:** Targeted canonical/release suites and full 213-test PostgreSQL discovery pass.
- **Committed in:** `a4f2b8b`

---

**Total deviations:** 2 auto-fixed (1 Rule 2 missing critical functionality, 1 Rule 1 regression bug).
**Impact on plan:** Both changes preserve previously guaranteed crash semantics while enforcing the planned generation protocol; neither adds a new external surface or retry authority.

## Issues Encountered

- The managed sandbox blocks localhost PostgreSQL sockets and Git index writes. Scoped approved execution was used for the disposable `roster_scout_test` database and normal hooked commits.
- Context7 tooling was unavailable in this environment. No version-ambiguous library behavior was introduced; installed Alembic, SQLAlchemy, Celery, and PostgreSQL behavior was exercised directly by the regression suites.

## User Setup Required

None - no external service configuration required.

## Verification Results

- Mandatory generation suites: 17 tests passed in each of three consecutive runs.
- Task 2 delivery/process-loss suite: 12 tests passed, including a separate worker process terminated immediately after the generation CAS.
- Full report-job suite: passed.
- Full repository discovery with PostgreSQL enabled: 213 tests passed.
- Migration lifecycle: 0006-to-0007 upgrade, 0007-to-0006 downgrade, re-upgrade, base-to-head fresh install, and historical migration regressions passed.
- Alembic model drift: `No new upgrade operations detected.`
- Retry authority scan: `roster_scout/tasks.py` contains no `self.retry` call or Celery retry configuration.
- NCAA catalog: 942 selectable teams (308 D1, 253 D2, 381 D3), zero errors; 25 expected missing-stable-ID warnings remain unchanged.
- Required Python compilation, template existence checks, and `git diff --check`: passed.

## Known Stubs

None - modified production paths contain no placeholder or unwired data flow.

## TDD Gate Compliance

PASSED - Task 1 has RED commit `1f60ced` followed by GREEN commit `a2a6f77`; Task 2 has RED commit `8d5bc82` followed by GREEN commit `b097517`. Regression alignment is isolated in follow-up test commit `a4f2b8b`.

## Self-Check: PASSED

- All eight created or modified implementation/test files exist.
- Task commits `1f60ced`, `a2a6f77`, `8d5bc82`, `b097517`, and `a4f2b8b` resolve in repository history.
- Every task acceptance criterion and plan-level verification command passed.
- `brag-output/` remains untouched and untracked.

## Next Phase Readiness

- Verification gap 2 is closed: PostgreSQL owns the retry clock, every delivery is generation-scoped, and process loss after CAS has one bounded recovery path.
- Plan 01-17 can proceed; existing managed-service and uninterrupted-hosting production prerequisites remain unchanged.

---
*Phase: 01-canonical-snapshot-foundation*
*Completed: 2026-11-10*
