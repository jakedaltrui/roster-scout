---
phase: 01-canonical-snapshot-foundation
plan: "10"
subsystem: infrastructure
tags: [postgresql, advisory-locks, browserless, docker-compose, fly, security]

requires:
  - phase: 01-canonical-snapshot-foundation
    provides: Durable app/worker process boundary and shared PostgreSQL persistence
provides:
  - PostgreSQL-backed account-wide Browserless capacity lease shared by app and worker
  - Crash-safe and fail-closed capacity handling with sanitized HTTP responses
  - Loopback-only local PostgreSQL and Redis host publications
  - Deployment regressions for aggregate capacity, process scope, and embedded-secret safety
affects: [ncaa-acquisition, app-runtime, durable-worker, deployment, operations]

tech-stack:
  added: []
  patterns: [session-level advisory semaphore, dedicated lock connection, rendered deployment validation]

key-files:
  created:
    - tests/test_usage_limits.py
    - tests/test_deployment_config.py
  modified:
    - usage_limits.py
    - app.py
    - scripts/run_worker.py
    - compose.yaml
    - fly.toml
    - .env.example
    - DEPLOYMENT.md

key-decisions:
  - "Hosted app and worker processes share one PostgreSQL advisory-lock namespace and account-wide Browserless limit."
  - "Each Browserless lease owns a dedicated database connection so normal exit, connection loss, and process loss release capacity."
  - "Production retains one worker machine by default even though the database lease enforces aggregate capacity across machines."

patterns-established:
  - "Capacity authority: hosted Browserless work fails closed when PostgreSQL is unavailable; it never falls back to machine-local locks."
  - "Deployment validation: parse rendered Compose and Fly source to reject network exposure, process-scope drift, and embedded credentials."

requirements-completed:
  - SNAP-02
  - OPS-01
  - OPS-04
  - OPS-05

duration: 11 min
completed: 2026-11-09
---

# Phase 1 Plan 10: Distributed Capacity and Secure Local Bindings Summary

**PostgreSQL advisory leases now cap aggregate app-and-worker Browserless sessions while loopback-only Compose bindings protect local database and broker credentials**

## Performance

- **Duration:** 11 min
- **Started:** 2026-11-09T02:40:26Z
- **Completed:** 2026-11-09T02:51:05Z
- **Tasks:** 3
- **Files modified:** 9

## Accomplishments

- Replaced hosted machine-local Browserless accounting with a stable PostgreSQL session advisory-lock semaphore shared by independent app and worker processes.
- Added crash-release, third-contender rejection, sanitized fail-closed handling, and worker/account concurrency regressions.
- Bound Compose PostgreSQL and Redis publications to `127.0.0.1` while preserving container-internal service DNS.
- Declared one common capacity backend, namespace, and account limit across Compose and Fly without embedding managed-service credentials.
- Replaced the obsolete one-machine capacity claim with the distributed-lease invariant and an explicit single-worker default boundary.

## Task Commits

Each task was committed atomically; Task 1 used separate RED and GREEN commits:

1. **Task 1 RED: Expose cross-machine Browserless capacity gap** - `d623c6b` (test)
2. **Task 1 GREEN: Share Browserless capacity across processes** - `cd99c7b` (feat)
3. **Task 2: Bind local data services to loopback** - `c4fc8c1` (fix)
4. **Task 3: Enforce shared capacity deployment contract** - `9d744f3` (chore)

## Files Created/Modified

- `usage_limits.py` - PostgreSQL advisory semaphore, local development backend, safe capacity errors, and crash-safe release.
- `app.py` - Unified sanitized HTTP handling for exhausted and unavailable capacity.
- `scripts/run_worker.py` - Separate worker concurrency/account-capacity validation with compatibility for the legacy variable.
- `compose.yaml` - Common app/worker lease configuration and loopback-only PostgreSQL/Redis ports.
- `fly.toml` - Hosted PostgreSQL capacity backend, shared namespace/account limit, and bounded worker concurrency.
- `.env.example` - Explicit local capacity mode and production TLS/authentication guidance.
- `DEPLOYMENT.md` - Distributed lease invariant, environment contract, scaling boundary, and updated release command.
- `tests/test_usage_limits.py` - Independent-process capacity, crash-release, fail-closed response, and concurrency tests.
- `tests/test_deployment_config.py` - Rendered Compose and Fly topology/security validation.

## Decisions Made

- Used PostgreSQL session-level advisory locks rather than transaction locks because the capacity lease must span the complete Browserless operation while remaining automatically releasable when the owning connection dies.
- Used a deterministic signed namespace hash plus slot index, providing a stable two-key advisory-lock space for the supported one-or-two-session account limit.
- Kept the file-lock implementation for explicit single-machine development configuration; every checked-in hosted process declares the PostgreSQL backend.
- Retained the single-worker production default until an additional-machine deployment is explicitly validated against the same database, namespace, and account limit.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

- Context7 was unavailable, so advisory-lock and connection-lifecycle behavior was verified against the official PostgreSQL and SQLAlchemy documentation before implementation.
- The sandbox blocked local PostgreSQL access; scoped approved execution was used only for the disposable `roster_scout_test` integration database.
- The first crash-release harness used a shared event whose synchronization state could hang after its waiter was terminated. The test was corrected to terminate a sleeping holder and use a fresh event for the replacement contender.

## User Setup Required

None for local development. Hosted deployments must continue to inject authenticated/TLS `DATABASE_URL` and `CELERY_BROKER_URL` secrets through the platform secret store.

## Verification Results

- Task 1 capacity/app regression: 6 tests passed, including two cross-machine holders, third-contender rejection before opener invocation, process-loss release, fail-closed backend behavior, sanitized HTTP 503, and worker/account validation.
- Task 2 local binding suite: 4 tests passed; wildcard, bare-port, and host-network variants were rejected.
- Task 3 deployment suite: 4 tests passed; shared backend/namespace/limit, bounded concurrency, app-only Fly HTTP/mount scope, and secret-free Fly configuration were confirmed.
- Full PostgreSQL-backed discovery: 114 tests passed.
- Docker Compose render/config validation passed.
- NCAA catalog validation passed with 942 selectable 2026 teams and zero errors.
- Python compilation, required template existence, and `git diff --check` passed.

## Known Stubs

None.

## Threat Flags

None - the advisory semaphore, fail-closed response, loopback bindings, and secret-injection guidance directly implement the plan's registered denial-of-service, privilege, tampering, and fallback-spoofing mitigations without adding a new trust boundary.

## Next Phase Readiness

- CR-08 and CR-09 are closed with cross-process and rendered-configuration regressions.
- App and worker acquisition can safely share the Browserless account budget across machines through PostgreSQL.
- Ready for the remaining Phase 1 gap-closure plans; production launch still depends on the managed-service and uninterrupted-hosting provisioning gate already recorded in project state.

## TDD Gate Compliance

- RED commit `d623c6b` failed because a third contender with an independent machine-local cache acquired Browserless capacity.
- GREEN commit `cd99c7b` passed the same cross-process test through the PostgreSQL advisory semaphore.

## Self-Check: PASSED

- All nine created or modified implementation, configuration, documentation, and test files exist.
- Task commits `d623c6b`, `cd99c7b`, `c4fc8c1`, and `9d744f3` exist in git history.
- Every task acceptance criterion, plan-level verification command, and repository handoff check passed.

---
*Phase: 01-canonical-snapshot-foundation*
*Completed: 2026-11-09*
