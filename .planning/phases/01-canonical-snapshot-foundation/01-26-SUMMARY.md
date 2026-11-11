---
phase: 01-canonical-snapshot-foundation
plan: "26"
subsystem: readiness-and-terminal-jobs
tags: [postgresql, flask, celery, retention, failure-evidence]

requires:
  - phase: 01-canonical-snapshot-foundation
    provides: Persisted purge readiness, bounded evidence retention, and durable terminal job transitions from Plans 01-19, 01-21, and 01-25
provides:
  - Retention-aware `/readyz` that fails closed before database construction
  - Worker preflight that rejects invalid evidence retention before cache or Celery startup
  - Terminal job persistence that safely omits optional evidence under invalid retention configuration
affects: [evidence-purge, worker-startup, report-jobs, operational-readiness]

tech-stack:
  added: []
  patterns: [single strict retention parser, fail-closed readiness, optional-diagnostic isolation]

key-files:
  created: []
  modified:
    - app.py
    - scripts/run_worker.py
    - roster_scout/jobs.py
    - roster_scout/evidence.py
    - tests/test_app_routes.py
    - tests/test_deployment_config.py
    - tests/test_report_jobs.py

key-decisions:
  - "Readiness and worker startup use the same strict 1-28 day retention parser before constructing external dependencies."
  - "Invalid optional-evidence configuration is persisted as the fixed safe unexpected failure while HTML evidence is omitted, so the authoritative terminal job state cannot roll back."
  - "A prevalidated retention duration is passed into coordinated evidence insertion, preventing configuration drift between validation and the terminal transaction."

patterns-established:
  - "Configuration preflight: validate sensitive lifecycle settings before database, broker, cache, or Celery construction."
  - "Optional diagnostics cannot own authoritative state: terminal job state commits even when evidence retention is unusable."

requirements-completed: [SNAP-02, DATA-07, OPS-01]

duration: 4 min
completed: 2026-11-10
---

# Phase 1 Plan 26: Retention Readiness and Terminal-State Safety Summary

**Invalid evidence-retention settings now fail readiness and worker startup without leaking values, while in-flight terminal jobs durably fail without optional HTML evidence instead of rolling back to running.**

## Performance

- **Duration:** 4 min
- **Started:** 2026-11-11T00:15:17Z
- **Completed:** 2026-11-11T00:19:53Z
- **Tasks:** 2
- **Files modified:** 7

## Accomplishments

- Made `/readyz` validate `FAILURE_EVIDENCE_RETENTION_DAYS` before constructing the purge database session and return the existing fixed unavailable projection for every invalid value.
- Made the worker validate the same setting before cache creation or Celery execution, while preserving valid boundary values 1 and 28 and leaving `/healthz` connection-free.
- Prevalidated retention before `_record_failure()` opens its terminal transaction; invalid configuration now commits a sanitized failed job, closes active dispatches, and stores zero evidence bytes.
- Preserved atomic terminal-state plus evidence insertion for valid retention by passing the resolved duration into the existing evidence service boundary.

## Task Commits

Each task followed RED/GREEN TDD gates and was committed atomically:

1. **Task 1 RED: Invalid readiness and worker-preflight regressions** - `c2bde8f` (test)
2. **Task 1 GREEN: Retention-aware readiness and worker startup** - `d7dcf8e` (fix)
3. **Task 2 RED: Terminal rollback under invalid retention** - `4669b33` (test)
4. **Task 2 GREEN: Terminal-state-safe optional evidence** - `dfeb098` (fix)

## Files Created/Modified

- `app.py` - Validates evidence retention before the persisted purge readiness query and sanitizes configuration failure.
- `scripts/run_worker.py` - Rejects invalid retention before cache directory creation or Celery exec with a fixed safe token.
- `roster_scout/jobs.py` - Resolves evidence retention before the terminal transaction and commits a safe terminal failure when evidence is unavailable.
- `roster_scout/evidence.py` - Accepts an already validated retention duration for caller-owned coordinated insertion.
- `tests/test_app_routes.py` - Covers invalid/valid retention readiness and connection-free liveness.
- `tests/test_deployment_config.py` - Covers worker preflight ordering and valid boundaries.
- `tests/test_report_jobs.py` - Proves durable terminal state, closed dispatches, zero invalid evidence, and the valid atomic control.

## Decisions Made

- Used the existing `unexpected` safe category/message for invalid operational retention configuration rather than introducing a new public error surface.
- Kept `/healthz` shallow and connection-free; retention compliance remains a traffic-readiness concern in `/readyz` and worker preflight.
- Kept valid terminal state and evidence in one transaction by passing the prevalidated duration into `store_failure_evidence()`.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Extended the evidence service boundary for prevalidated retention**
- **Found during:** Task 2 (Preserve terminal job state when optional evidence configuration fails)
- **Issue:** `store_failure_evidence()` always reparsed process environment inside the terminal transaction, so the plan's required prevalidation could not be passed into coordinated insertion without using a private helper.
- **Fix:** Added an optional `retention` argument that defaults to the original strict parser for all existing callers; `_record_failure()` passes the resolved duration explicitly.
- **Files modified:** `roster_scout/evidence.py`, `roster_scout/jobs.py`
- **Verification:** Invalid and valid PostgreSQL controls passed; the complete route/job/deployment suite passed 119 tests.
- **Committed in:** `dfeb098`

---

**Total deviations:** 1 auto-fixed (1 blocking integration issue).
**Impact on plan:** The service-boundary extension is backward compatible and required to make prevalidation race-free without bypassing evidence encapsulation.

## Issues Encountered

None.

## User Setup Required

None.

## Verification Results

- Focused readiness and worker-preflight suite: 12 tests passed.
- Focused PostgreSQL terminal-state suite: 2 tests passed for five invalid values plus the valid control.
- Complete app-route, report-job, and deployment suite: 119 tests passed.
- NCAA catalog validation: 942 selectable 2026 teams (308 D1, 253 D2, 381 D3), zero errors.
- Required Python compilation, template existence, and `git diff --check` passed.

## Known Stubs

None.

## Threat Flags

None - readiness, worker configuration, terminal-state durability, and information-disclosure risks are the explicit T-01-26-01 through T-01-26-04 mitigations.

## TDD Gate Compliance

PASSED - both tasks have failing `test(01-26)` RED commits followed by passing `fix(01-26)` GREEN commits.

## Self-Check: PASSED

- All seven modified files exist.
- Task commits `c2bde8f`, `d7dcf8e`, `4669b33`, and `dfeb098` resolve in repository history.
- Every task acceptance criterion and the plan-level verification command passed.

## Next Phase Readiness

- CR-04 and the readiness/terminal-state portion of the retention gap are closed.
- Plan 01-27 can now make purge progress under contention; Plan 01-28 can add usage-event storage independently.

---
*Phase: 01-canonical-snapshot-foundation*
*Completed: 2026-11-10*
