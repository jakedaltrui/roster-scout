---
phase: 01-canonical-snapshot-foundation
plan: "20"
subsystem: evidence-purge-operations
tags: [postgresql, scheduler, readiness, docker-compose, fly]

requires:
  - phase: 01-canonical-snapshot-foundation
    provides: Transactional evidence deletion and persisted purge readiness from Plan 01-19
provides:
  - Independently supervised PostgreSQL-only evidence purge runner
  - Immediate startup purge with hourly cadence and bounded failure retry
  - Persisted readiness check mode for fresh, stale, outstanding, and unavailable states
  - Dispatch-only reconciliation scheduler with no purge dependency or wakeup path
  - Two-engine proof that blocked dispatch cannot delay physical evidence deletion
affects: [evidence-retention, dispatch-scheduler, deployment-readiness, operations]

tech-stack:
  added: []
  patterns: [independent process ownership, monotonic bounded loop, persisted readiness health check, environment allowlisting]

key-files:
  created:
    - scripts/run_purge.py
  modified:
    - scripts/run_scheduler.py
    - tests/test_deployment_config.py
    - compose.yaml
    - fly.toml
    - Dockerfile
    - Procfile
    - .env.example
    - DEPLOYMENT.md

key-decisions:
  - "Evidence purge runs in its own PostgreSQL-only process group; dispatch reconciliation has no purge import, timing field, health state, or call site."
  - "Purge validates a 60-3600 second cadence before dependency construction, runs immediately, and retries failures after at most 60 seconds."
  - "Deployment readiness calls the persisted PostgreSQL readiness projection through run_purge.py --check rather than process-local memory."

patterns-established:
  - "Independent maintenance loop: one stop event, engine, session factory, monotonic cadence, and supervisor boundary per maintenance responsibility."
  - "Safe process health: expose only fixed status tokens and integer counts; discard raw database, broker, evidence, and exception content."

requirements-completed:
  - SNAP-02
  - DATA-07

duration: 10 min
completed: 2026-11-10
---

# Phase 1 Plan 20: Independent Evidence Purge Runner Summary

**A separately supervised PostgreSQL-only purge process now deletes expired evidence on an immediate hourly loop, exposes persisted readiness, and continues committing while dispatch reconciliation is blocked.**

## Performance

- **Duration:** 10 min
- **Started:** 2026-11-10T02:51:56Z
- **Completed:** 2026-11-10T03:02:15Z
- **Tasks:** 2
- **Files modified:** 9

## Accomplishments

- Added a standalone purge runner that validates timing before opening PostgreSQL, runs once immediately, waits on a monotonic hourly deadline, retries failures after a bounded 60 seconds, and finishes active transactions before shutdown.
- Added `--check` readiness mode backed exclusively by persisted purge state and expired-byte queries, with nonzero exits for never-successful, stale, outstanding-byte, and database-unavailable states.
- Removed every purge field, import, dependency, health object, wakeup, and invocation from the dispatch scheduler.
- Added separate Compose, Fly, and Procfile purge supervision with a PostgreSQL-only environment; Compose health checks call the persisted readiness mode.
- Proved with separate SQLAlchemy engines, session factories, PostgreSQL backend connections, and stop events that a real purge commits deletion and heartbeat state while scheduler reconciliation remains blocked.

## Task Commits

Each TDD task was committed through separate RED and GREEN gates:

1. **Task 1 RED: Define standalone purge runner contract** - `9be1c83` (test)
2. **Task 1 GREEN: Add independent evidence purge runner** - `8461480` (feat)
3. **Task 2 RED: Expose coupled purge deployment topology** - `c1f108a` (test)
4. **Task 2 GREEN: Isolate purge from dispatch scheduling** - `3778d18` (feat)

## Files Created/Modified

- `scripts/run_purge.py` - Validated PostgreSQL-only purge loop, graceful signal handling, safe logging, and persisted `--check` mode.
- `scripts/run_scheduler.py` - Dispatch reconciliation only, with all evidence-purge ownership removed.
- `tests/test_deployment_config.py` - Runner timing/shutdown/readiness tests, deployment contracts, and blocked-reconciliation PostgreSQL isolation proof.
- `compose.yaml` - Separately restarted purge service with PostgreSQL-only environment and persisted readiness health check.
- `fly.toml` - Dedicated allowlisted purge process group and dispatch-only scheduler command.
- `Dockerfile` - Includes the standalone purge entrypoint in the shared immutable image.
- `Procfile` - Declares scheduler and purge as separate supervised process types.
- `.env.example` - Documents the hourly purge cadence and valid 28-day retention maximum.
- `DEPLOYMENT.md` - Documents independent ownership, six-hour persisted readiness, bounded retry, least privilege, and the 47-hour deadline reserve.

## Decisions Made

- Keep purge and dispatch as separate operating-system process groups rather than separate threads in one scheduler; a stalled broker call therefore cannot share the purge execution path.
- Use one hour as both the default and maximum successful cadence, while failed purge attempts retry after 60 seconds without busy-looping or recording false success.
- Treat the Plan 01-19 database projection as the sole readiness authority; restart-local timestamps and counts were deleted with the scheduler purge code.
- Preserve the existing `scheduler` Compose profile as the operator entry point while starting both independently supervised scheduler and purge services under that profile.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing Critical] Wired the purge runner into supervised deployment boundaries**
- **Found during:** Task 2 (Remove purge from dispatch and prove blocked reconciliation cannot delay it)
- **Issue:** The plan's listed files created the runner but did not include the Compose, Fly, image, Procfile, environment, or runbook changes required for an independently supervised production boundary.
- **Fix:** Added a separate purge service/process with a PostgreSQL-only allowlist, image inclusion, health check, operator scaling contract, and deployment documentation.
- **Files modified:** `compose.yaml`, `fly.toml`, `Dockerfile`, `Procfile`, `.env.example`, `DEPLOYMENT.md`
- **Verification:** Rendered Compose, deployment-contract tests, Python compilation, and the full repository suite passed.
- **Committed in:** `3778d18`

**2. [Rule 1 - Bug] Corrected deployment retention configuration to the enforced maximum**
- **Found during:** Task 2 deployment wiring
- **Issue:** Compose, the example environment, and the runbook still supplied or documented 30 days after Plan 01-19 made values above 28 fail closed; deployed failure-evidence insertion would reject that configuration.
- **Fix:** Changed the deployment value and documentation to the strict 28-day maximum and added the separate 3600-second purge cadence.
- **Files modified:** `compose.yaml`, `.env.example`, `DEPLOYMENT.md`
- **Verification:** Full evidence, deployment, and repository regression suites passed with the corrected contract.
- **Committed in:** `3778d18`

---

**Total deviations:** 2 auto-fixed (1 missing critical deployment boundary, 1 configuration bug).
**Impact on plan:** Both changes were required for the planned supervised, least-privilege deletion guarantee; no customer-facing scope was added.

## Issues Encountered

- The managed sandbox blocks localhost PostgreSQL and Git index writes. Scoped approved execution was used only for the disposable `roster_scout_test` database and normal hooked commits.
- Context7 was unavailable; the current official Fly process-group guidance was checked before preserving separate scheduler and purge process groups in one deployment image.

## User Setup Required

Production launch remains blocked on the existing managed PostgreSQL, Redis, object/evidence storage, and uninterrupted Fly worker billing/capacity prerequisites. When those are available, operators must scale exactly one `purge` process and verify `python scripts/run_purge.py --check` succeeds against persisted production state.

## Verification Results

- Standalone runner contract: 6 tests passed for configuration, immediate execution, monotonic waits, bounded retry, safe logging, graceful shutdown, and persisted check exits.
- Blocked-reconciliation target: 5 tests passed, including a real PostgreSQL purge commit through an independent engine while scheduler reconciliation remained blocked.
- Full evidence and deployment suites: 44 tests passed.
- Full PostgreSQL-backed repository discovery: 256 tests passed.
- Compose rendering, both Python entrypoint compilations, all `CONTEXT.md` compilation/template checks, and `git diff --check` passed.
- NCAA catalog: 942 selectable teams (308 D1, 253 D2, 381 D3), zero errors; 25 expected missing-stable-ID warnings unchanged.
- `rg -n "purge|EvidencePurgeHealth|FAILURE_EVIDENCE" scripts/run_scheduler.py` returned no matches.

## Known Stubs

None - the modified production paths contain no placeholders, mock data, or unwired empty values.

## Threat Flags

None - the new PostgreSQL-only purge process, safe error logging, restart behavior, and dispatch separation are the registered T-01-20-01 through T-01-20-04 surfaces and are covered by automated tests.

## TDD Gate Compliance

PASSED - Task 1 RED `9be1c83` precedes GREEN `8461480`, and Task 2 RED `c1f108a` precedes GREEN `3778d18`; every focused and repository regression test passes afterward.

## Self-Check: PASSED

- All nine created or modified implementation, deployment, documentation, and test files plus this summary exist.
- Task commits `9be1c83`, `8461480`, `c1f108a`, and `3778d18` resolve in repository history.
- Every task acceptance criterion and plan-level verification command passed.
- `brag-output/` remains untouched and untracked.

## Next Phase Readiness

- Gap 3's execution-topology requirement is closed: dispatch blockage cannot postpone physical deletion, and restart-safe readiness is externally callable.
- Plan 01-21 can consume the completed purge guarantee without coupling deployment readiness back to Redis or Browserless.
- Existing managed-service and uninterrupted-hosting production prerequisites remain unchanged.

---
*Phase: 01-canonical-snapshot-foundation*
*Completed: 2026-11-10*
