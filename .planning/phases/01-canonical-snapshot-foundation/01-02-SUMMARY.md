---
phase: 01-canonical-snapshot-foundation
plan: "02"
subsystem: jobs
tags: [celery, redis, postgresql, idempotency, evidence-retention, fly]

requires:
  - phase: 01-canonical-snapshot-foundation
    provides: Immutable organization-owned snapshot schema and atomic publication
provides:
  - PostgreSQL-authoritative idempotent report jobs with bounded retries
  - Opaque job-ID Celery delivery and terminal replay protection
  - Same-build versioned section evidence and 30-day failed-HTML retention
  - Separate Fly app/worker processes with release-time migrations
affects: [canonical-publish, report-library, ncaa-collector, deployment]

tech-stack:
  added: []
  patterns: [database-authoritative jobs, late-acked opaque task delivery, failure-only evidence retention]

key-files:
  created:
    - roster_scout/jobs.py
    - roster_scout/celery_app.py
    - roster_scout/tasks.py
    - roster_scout/evidence.py
    - scripts/run_worker.py
    - migrations/versions/0002_durable_report_jobs.py
    - tests/test_report_jobs.py
  modified:
    - roster_scout/models.py
    - roster_scout/repositories.py
    - Dockerfile
    - compose.yaml
    - fly.toml
    - DEPLOYMENT.md

key-decisions:
  - "PostgreSQL remains authoritative for job acceptance, attempts, section state, and terminal results; Redis carries only opaque job UUIDs."
  - "A broker publish failure leaves the accepted queued row undispatched so the same idempotency key can recover the handoff."
  - "One worker machine with concurrency 1-2 is mandatory until Browserless capacity has a distributed lease."

patterns-established:
  - "Commit-before-dispatch: accept the unique job in PostgreSQL before best-effort broker publication."
  - "Terminal replay: repeated delivery returns the existing snapshot without collection or publication."
  - "Failure evidence: gzip bytes expire, while sanitized checksum metadata remains auditable."

requirements-completed: [SNAP-02, SNAP-03, SNAP-04, DATA-07, OPS-01, OPS-02, OPS-04]

duration: 5 min
completed: 2026-11-01
---

# Phase 1 Plan 2: Durable Report Jobs and Worker Boundaries Summary

**PostgreSQL-authoritative report jobs with opaque Celery delivery, duplicate-safe publication, versioned same-build cache identity, and bounded failure evidence**

## Performance

- **Duration:** 5 min
- **Started:** 2026-11-01T23:08:18Z
- **Completed:** 2026-11-01T23:14:06Z
- **Tasks:** 3
- **Files modified:** 17

## Accomplishments

- Added conflict-safe job acceptance, recoverable database-to-broker handoff, bounded retry classification, safe terminal replay, and one job-to-snapshot publication.
- Added job/section state, source identity, parser/schema version, original collection time, artifact independence, and sanitized failure coverage against PostgreSQL.
- Added gzip failure evidence with a 30-day maximum lifetime and metadata-preserving purge while prohibiting successful HTML retention.
- Split app and worker deployment, constrained worker Browserless concurrency to 1-2, scoped HTTP and `/data` to the app process, and made Alembic the Fly release command.

## Task Commits

Each task was committed atomically:

1. **Task 1: Specify durable job and duplicate-delivery behavior** - `e25d57a` (test)
2. **Task 2: Run idempotent report jobs through Celery** - `d0dee54` (feat)
3. **Task 3: Add section evidence retention and production worker boundaries** - `80dc6f9` (feat)

Additional plan verification:

- `cde51ac` (test) - exercises the real Celery task entrypoint in eager mode with only an opaque job UUID.

## Files Created/Modified

- `roster_scout/jobs.py` - Durable acceptance, legal transitions, retry classification, section persistence, and terminal publication.
- `roster_scout/celery_app.py` - JSON-only, late-acked, resultless Celery configuration with one-message prefetch.
- `roster_scout/tasks.py` - Job-ID-only Celery task with bounded exponential backoff and jitter.
- `roster_scout/evidence.py` - Restricted gzip storage and expiry purge for failed/blocked HTML.
- `roster_scout/models.py` - Durable delivery timestamps, safe failures, bounded attempts, and section cache identity.
- `migrations/versions/0002_durable_report_jobs.py` - Reversible schema upgrade for job delivery, cache provenance, and evidence metadata.
- `scripts/run_worker.py` - Strict 1-2 concurrency launcher using `/tmp/roster-scout-worker`.
- `tests/test_report_jobs.py` - PostgreSQL and Celery eager coverage for duplicate clicks, redelivery, retry, publication, cache, and retention.
- `Dockerfile`, `Procfile`, `compose.yaml`, `fly.toml` - Shared immutable image with separate app, worker, and release boundaries.
- `.env.example`, `DEPLOYMENT.md` - Safe environment contract, local startup, rollback verification, managed-service gate, and scaling prohibition.

## Decisions Made

- Kept all customer-visible job state in PostgreSQL and disabled Celery results; the broker message contains only the job UUID.
- Committed the durable job before broker publication and recorded `enqueued_at` only after publication succeeds, making a repeated accepted request the handoff recovery path.
- Used an all-state organization/idempotency uniqueness constraint rather than active-only uniqueness so accepted identities remain durable across terminal history.
- Limited cache reuse to a key containing accepted job UUID, source identity, schema version, and parser version, while preserving the original collection timestamp.
- Restricted production to exactly one worker machine because Browserless locking is machine-local.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing Critical] Added a reversible durable-job migration**
- **Found during:** Task 1 (Specify durable job and duplicate-delivery behavior)
- **Issue:** The planned model additions would not alter an existing Plan 01-01 database, leaving production without delivery timestamps, bounded attempts, cache identity, or safe failure metadata.
- **Fix:** Added migration `0002` with upgrade/downgrade coverage and compatibility for the existing metadata-based initial migration.
- **Files modified:** `migrations/versions/0002_durable_report_jobs.py`, `tests/test_snapshot_persistence.py`
- **Verification:** Sixteen snapshot persistence tests passed, including downgrade-to-base and re-upgrade.
- **Committed in:** `e25d57a`

**2. [Rule 2 - Missing Critical] Added explicit Celery eager-entry verification**
- **Found during:** Plan-wide verification
- **Issue:** Service tests proved the underlying durable runner but did not execute the actual Celery task entrypoint required by the plan-level verification.
- **Fix:** Added an eager-mode test that supplies only the opaque job UUID and publishes through the collector seam.
- **Files modified:** `tests/test_report_jobs.py`
- **Verification:** Fourteen report-job tests and the 78-test full suite passed.
- **Committed in:** `cde51ac`

---

**Total deviations:** 2 auto-fixed (2 missing critical)
**Impact on plan:** Both additions close required durability and verification gaps without expanding product scope.

## Issues Encountered

- The sandbox initially denied local PostgreSQL socket access; scoped approved execution was used for the disposable `roster_scout_test` integration checks.
- Context7 was unavailable in the environment, so Celery 5.6 behavior was verified against the official Celery documentation before implementation.

## User Setup Required

None for local development. Production remains behind the documented manual billing/provisioning gate for managed PostgreSQL, managed Redis, one app machine, and exactly one uninterrupted worker machine.

## Verification Results

- `tests.test_report_jobs`: 14 passed, including Celery eager-mode execution.
- Full deterministic discovery with PostgreSQL enabled: 78 passed.
- Alembic drift check: no new upgrade operations detected; downgrade/re-upgrade passed.
- Docker Compose configuration: valid.
- NCAA catalog validation: 942 selectable 2026 teams, no errors.
- Python compilation, required template existence, Fly/Docker source assertions, and `git diff --check`: passed.

## Known Stubs

- `roster_scout/tasks.py:collect_report_sections` is the deliberate Plan 01-03 collector seam. It fails safely until the compatibility adapter wires the existing NCAA batting/pitching collector; injected collectors already exercise the complete durable job and publication path in this plan.

## Next Phase Readiness

- Ready for Plan 01-03 to adapt the existing D1/D2/D3 NCAA workflow into `CollectedSection` values without changing the public synchronous workbook route.
- Durable job delivery, retry, evidence, and worker deployment boundaries are in place.
- Production deployment still requires the documented managed-service billing and uninterrupted-hosting approval gate.

## Self-Check: PASSED

- All seven key created files exist.
- Task and verification commits `e25d57a`, `d0dee54`, `80dc6f9`, and `cde51ac` exist in git history.
- All task acceptance criteria and plan-level verification commands passed.

---
*Phase: 01-canonical-snapshot-foundation*
*Completed: 2026-11-01*
