---
phase: 01-canonical-snapshot-foundation
plan: "12"
subsystem: infrastructure
tags: [scheduler, transactional-outbox, celery, docker-compose, fly]

requires:
  - phase: 01-canonical-snapshot-foundation
    provides: Transactional dispatch outbox, stale-job reconciliation, and secure app/worker deployment boundaries
provides:
  - Bounded singleton scheduler for dispatch draining and stale-job reconciliation
  - Least-privilege Compose and Fly scheduler process definitions
  - Production provisioning gate for managed dependencies and uninterrupted hosting
affects: [durable-jobs, dispatch-recovery, deployment, operations, evidence-purge]

tech-stack:
  added: []
  patterns: [validated long-running launcher, allowlisted process environment, singleton reconciliation process]

key-files:
  created:
    - scripts/run_scheduler.py
  modified:
    - compose.yaml
    - fly.toml
    - .env.example
    - DEPLOYMENT.md
    - Dockerfile
    - tests/test_deployment_config.py

key-decisions:
  - "Dispatch reconciliation runs as one dedicated scheduler process that calls existing job services instead of duplicating outbox SQL or state rules."
  - "The Fly scheduler replaces its inherited environment with an allowlist containing only PostgreSQL, Redis, and nonsecret timing configuration."
  - "Production remains blocked until managed PostgreSQL, Redis, object/evidence storage, and uninterrupted Fly worker billing/capacity are verified."

patterns-established:
  - "Scheduler configuration: validate bounded timing relationships before constructing database or broker clients."
  - "Scheduler observability: emit health and aggregate counts only; never log raw exceptions, URLs, credentials, or task payloads."

requirements-completed:
  - SNAP-02
  - OPS-01
  - OPS-04

duration: 5 min
completed: 2026-11-09
---

# Phase 1 Plan 12: Dispatch Reconciliation Scheduler Summary

**A bounded singleton scheduler now drains durable dispatch intents and reconciles stale jobs through least-privilege Compose and Fly process boundaries**

## Performance

- **Duration:** 5 min
- **Started:** 2026-11-09T21:12:49Z
- **Completed:** 2026-11-09T21:17:49Z
- **Tasks:** 2
- **Files modified:** 7

## Accomplishments

- Added a validated long-running scheduler that lazily creates clients, calls the Plan 01-07 service boundaries, finishes in-flight cycles on shutdown, and exposes only safe health/count logs.
- Added exactly one scheduler definition to Compose and Fly without HTTP service, app volume, Browserless credentials, or duplicated domain SQL.
- Added deployment regressions for scheduler uniqueness, command separation, release migration, environment scope, timing defaults, shared-image contents, and production readiness gates.
- Kept production launch explicitly blocked on managed PostgreSQL, Redis, object/evidence storage, and uninterrupted Fly worker billing/capacity; Compose remains development-only.

## Task Commits

Each task was committed atomically; Task 1 used separate RED and GREEN commits:

1. **Task 1 RED: Expose missing dispatch scheduler boundary** - `69a9213` (test)
2. **Task 1 GREEN: Add bounded dispatch scheduler** - `120cf7d` (feat)
3. **Task 2: Wire singleton dispatch scheduler** - `1fca8cf` (chore)

## Files Created/Modified

- `scripts/run_scheduler.py` - Bounded configuration, safe polling loop, graceful signal handling, and lazy runtime dependencies.
- `compose.yaml` - Development-only singleton scheduler profile with database/broker/timing configuration and no ports or volume.
- `fly.toml` - Singleton scheduler process with an allowlisted runtime environment and app-only HTTP/mount scopes.
- `.env.example` - Nonsecret poll, lease, queued redispatch, and running-stale defaults.
- `DEPLOYMENT.md` - Scheduler operation, least-privilege topology, and exact production provisioning gate.
- `Dockerfile` - Copies the scheduler entrypoint into the shared immutable image.
- `tests/test_deployment_config.py` - Scheduler entrypoint, shutdown, disclosure, topology, migration, privilege, and provisioning regressions.

## Decisions Made

- Reconciliation runs stale-job recovery before each bounded outbox drain so newly reopened intents can be published in the same cycle.
- SIGTERM and SIGINT set a stop event rather than interrupting service calls, allowing the current transaction-owned cycle to finish before exit.
- Fly retains one shared image/process-group deployment, but the scheduler command executes Python under `env -i` with only the database URL, broker URL, and timing values forwarded.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing Critical] Included the scheduler in the immutable image**
- **Found during:** Task 2 (Compose and Fly wiring)
- **Issue:** The plan required the new scheduler process to use the shared image, but `Dockerfile` was not listed in `files_modified` and copied only the worker launcher. Fly would have started a command whose script was absent.
- **Fix:** Added an explicit `COPY scripts/run_scheduler.py` instruction and a deployment regression that rejects its removal.
- **Files modified:** `Dockerfile`, `tests/test_deployment_config.py`
- **Verification:** `DispatchSchedulerConfigTests` and `docker compose config --quiet` passed.
- **Committed in:** `1fca8cf`

**2. [Rule 1 - Bug] Made scheduler-count validation tolerate default commands**
- **Found during:** Task 2 verification
- **Issue:** Rendered Compose services that inherit the image default expose `command: null`; the new test attempted to join `None` and failed before validating scheduler uniqueness.
- **Fix:** Normalized absent rendered commands to an empty list in the count assertion.
- **Files modified:** `tests/test_deployment_config.py`
- **Verification:** All 16 deployment configuration tests passed.
- **Committed in:** `1fca8cf`

---

**Total deviations:** 2 auto-fixed (1 Rule 2 missing critical functionality, 1 Rule 1 bug).
**Impact on plan:** Both fixes were required to make the planned shared-image topology executable and its regression test reliable; no feature scope was added.

## Issues Encountered

- Context7 and its CLI fallback were unavailable. Current Fly process-group and secret behavior was checked against official Fly documentation, then enforced locally through parsed TOML and rendered Compose tests.
- Full unittest discovery passed 83 tests with 18 PostgreSQL classes skipped because `DATABASE_URL` was not set; this plan's deployment/config targets do not require a database connection.

## User Setup Required

Production launch remains blocked. Operators must provision and verify managed PostgreSQL, managed Redis, object/evidence storage, and uninterrupted Fly worker billing/capacity before deploying app, worker, and exactly one scheduler machine.

## Verification Results

- `DispatchSchedulerEntrypointTests`: 4 passed, covering bounded configuration, service invocation, graceful in-flight shutdown, and safe degraded logging.
- `DispatchSchedulerConfigTests`: 4 passed, covering one scheduler per topology, shared-image inclusion, release migration, allowlisted runtime configuration, no HTTP/mount ownership, and the production gate.
- Full `tests.test_deployment_config`: 16 passed.
- Full unittest discovery: 83 passed, 18 PostgreSQL-dependent classes skipped without `DATABASE_URL`.
- `python3 -m py_compile scripts/run_scheduler.py`: passed.
- `docker compose config --quiet`: passed.
- NCAA catalog validation: 942 selectable 2026 D1/D2/D3 teams, zero errors.
- Required repository compilation, template existence, and `git diff --check`: passed.

## Known Stubs

None.

## Threat Flags

None - the scheduler process, environment filtering, deployment topology, and local-versus-production boundary are the exact trust surfaces registered in the plan threat model.

## Next Phase Readiness

- Plan 01-07 dispatch recovery now runs automatically through a bounded process and is ready for operational deployment after the managed-service gate clears.
- Plan 01-13 can extend `scripts/run_scheduler.py` with its independent evidence-purge cadence without changing the app/worker privilege boundary.
- Production readiness remains blocked on the four documented managed dependency and uninterrupted-hosting prerequisites.

## TDD Gate Compliance

- RED commit `69a9213` failed because `scripts.run_scheduler.py` did not exist.
- GREEN commit `120cf7d` passed all four entrypoint behaviors and Python compilation.

## Self-Check: PASSED

- All seven created or modified implementation, configuration, documentation, and test files exist.
- Task commits `69a9213`, `120cf7d`, and `1fca8cf` exist in git history.
- Every task acceptance criterion, plan-level verification command, and required repository handoff check passed.

---
*Phase: 01-canonical-snapshot-foundation*
*Completed: 2026-11-09*
