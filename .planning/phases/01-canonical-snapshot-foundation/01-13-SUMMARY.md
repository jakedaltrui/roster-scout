---
phase: 01-canonical-snapshot-foundation
plan: "13"
subsystem: evidence-lifecycle
tags: [postgresql, scheduler, retention, readiness, docker-compose, fly]

requires:
  - phase: 01-canonical-snapshot-foundation
    provides: Failed-only bounded HTML evidence with fixed 696-hour expiry and singleton dispatch scheduler
provides:
  - Idempotent physical deletion of expired evidence bytes with permanent sanitized metadata
  - Fail-closed purge health at the exact 24-hour readiness boundary
  - Independent at-least-daily purge cadence in the singleton Compose and Fly scheduler
  - Operator runbook for up-to-30-day retention and stale-purge response
affects: [failure-evidence, scheduler, production-readiness, deployment, operations]

tech-stack:
  added: []
  patterns: [post-commit health recording, deadline-aware singleton scheduling, safe aggregate-only alerts]

key-files:
  created: []
  modified:
    - roster_scout/evidence.py
    - scripts/run_scheduler.py
    - tests/test_evidence.py
    - tests/test_deployment_config.py
    - compose.yaml
    - fly.toml
    - DEPLOYMENT.md

key-decisions:
  - "Purge health is process-local to the singleton scheduler and starts blocked after restart until one purge transaction succeeds."
  - "The scheduler wakes against the earlier dispatch or purge deadline, advances the purge deadline only after success, and retries failures at the normal poll cadence."
  - "Exactly 24 hours since the last successful purge is fresh; the smallest later increment is stale and blocks readiness."

patterns-established:
  - "Purge commit boundary: record last-success timestamp and aggregate count only after purge_expired_evidence returns from its committed transaction."
  - "Retention scheduling: validate a 1-86400 second cadence before external clients are created and keep purge ownership in the one existing scheduler process."

requirements-completed: [DATA-07, OPS-04]

duration: 6 min
completed: 2026-11-09
---

# Phase 1 Plan 13: Failed Evidence Purge Lifecycle Summary

**Expired failure HTML is now physically removed by the 720-hour maximum through an independently scheduled daily purge, with sanitized metadata retained and stale execution blocking readiness**

## Performance

- **Duration:** 6 min
- **Started:** 2026-11-09T21:33:18Z
- **Completed:** 2026-11-09T21:39:08Z
- **Tasks:** 2
- **Files modified:** 7

## Accomplishments

- Proved bytes remain immediately before 696 hours, are physically nulled at expiry, remain absent under repeat purge, and retain checksum plus every stored D-18-safe audit field.
- Proved the worst case—evidence collected immediately after a prior purge—is physically deleted at the next allowed run before its age exceeds 720 hours.
- Added fail-closed purge readiness and safe alerts for never-successful or older-than-24-hour execution while keeping exactly 24 hours fresh.
- Extended the existing singleton scheduler with an independent validated 1-86400 second purge cadence in Compose and Fly without adding HTTP, volume, Browserless, or second-process privileges.
- Documented failed HTML as retained for up to 30 days, the 696-hour expiry plus 24-hour reserve, and operator response to stale or never-successful purge health.

## Task Commits

Each task was committed atomically; Task 1 followed RED/GREEN TDD gates:

1. **Task 1 RED: Prove purge deadlines and readiness boundaries** - `26dd003` (test)
2. **Task 1 GREEN: Enforce purge health contract** - `1f2d2fd` (feat)
3. **Task 2: Wire daily purge operations** - `f287cc6` (chore)

**Plan summary:** committed separately before project tracking updates.

## Files Created/Modified

- `roster_scout/evidence.py` - Returns a stable safe purge count after the expired-byte transaction commits.
- `scripts/run_scheduler.py` - Tracks safe purge health, exposes readiness, validates cadence, and schedules purge independently of dispatch polling.
- `tests/test_evidence.py` - PostgreSQL-controlled 696-hour eligibility, metadata preservation, idempotency, and 720-hour deletion regressions.
- `tests/test_deployment_config.py` - Exact readiness boundary, post-commit health, safe alert, cadence validation, independence, and topology regressions.
- `compose.yaml` - Supplies the maximum 24-hour nonsecret purge cadence to the singleton scheduler.
- `fly.toml` - Allowlist-forwards the same cadence to the one non-HTTP, volume-free scheduler process.
- `DEPLOYMENT.md` - Defines the bounded retention promise, deadline mechanics, readiness blockers, and alert response.

## Decisions Made

- Kept health state in the singleton scheduler instead of adding persistence schema. A process restart therefore fails closed until the immediate startup purge succeeds.
- Advanced the next purge deadline only after success. A failed transaction keeps purge due and retries at the bounded dispatch poll interval instead of postponing deletion another day.
- Derived scheduler sleeps from both clocks so dispatch polling frequency cannot postpone a due purge.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

- The managed sandbox blocks localhost PostgreSQL sockets and Git index writes. Approved scoped execution was used for the disposable `roster_scout_test` integration database and required hooked commits.

## User Setup Required

Production launch remains blocked on the existing managed-service and uninterrupted-hosting prerequisites. Operators must run exactly one scheduler with `FAILURE_EVIDENCE_PURGE_SECONDS` set between 1 and 86400 seconds; the checked-in Compose and Fly default is 86400.

## Verification Results

- Exact plan Task 1 target passed: worst-case physical deletion by 720 hours and exact 24-hour readiness boundary.
- Exact plan Task 2 target passed: maximum-cadence wiring plus required retention-policy wording.
- `EvidencePurgeDeadlineTests`, `EvidencePurgeHealthTests`, and `EvidencePurgeConfigTests`: 6 passed.
- Full PostgreSQL-backed repository discovery: 180 tests passed.
- NCAA catalog validation: 942 selectable 2026 teams (308 D1, 253 D2, 381 D3), zero errors.
- Required compilation, template existence, Compose rendering, and `git diff --check` passed.

## Known Stubs

None.

## Threat Flags

None - expired-byte deletion, audit preservation, purge health, and deployment cadence are the registered T-01-13-01 through T-01-13-04 trust surfaces and are covered by automated tests.

## TDD Gate Compliance

PASSED - RED commit `26dd003` failed on the missing purge-health boundary, followed by GREEN commit `1f2d2fd`; all deadline and readiness tests pass after implementation.

## Self-Check: PASSED

- All seven modified implementation, test, configuration, and documentation files exist.
- Task commits `26dd003`, `1f2d2fd`, and `f287cc6` resolve in repository history.
- Every task acceptance criterion, plan-level verification command, and repository handoff check passed.

## Next Phase Readiness

- CR-07 is complete across Plan 01-09 capture ownership and Plan 01-13 purge/deployment ownership.
- Failed evidence bytes are mechanically bounded to 720 hours while safe audit metadata remains available.
- Phase 1 release validation can now treat never-successful or stale purge health as a production-readiness blocker.

---
*Phase: 01-canonical-snapshot-foundation*
*Completed: 2026-11-09*
