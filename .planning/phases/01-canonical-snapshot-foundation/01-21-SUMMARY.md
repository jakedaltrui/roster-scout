---
phase: 01-canonical-snapshot-foundation
plan: "21"
subsystem: deployment-readiness
tags: [flask, postgresql, purge-readiness, docker-compose, fly, release-gate]

requires:
  - phase: 01-canonical-snapshot-foundation
    provides: Section-safe publication, unresolved identity handling, persisted purge health, and independent purge supervision through Plans 01-14, 01-18, and 01-20
provides:
  - Persisted purge-backed `/readyz` with sanitized fail-closed responses
  - Process-specific Compose health checks and app-only Fly HTTP readiness
  - Operational 1-28 day retention configuration with a 672-hour cap and 720-hour deletion runbook
  - One deterministic release command covering every Phase 1 gap, migration, deployment, catalog, and workbook regression
affects: [production-readiness, evidence-retention, deployment, phase-2]

tech-stack:
  added: []
  patterns: [shallow liveness versus persisted readiness, per-process health ownership, executable release manifest]

key-files:
  created: []
  modified:
    - app.py
    - Dockerfile
    - compose.yaml
    - fly.toml
    - .env.example
    - DEPLOYMENT.md
    - scripts/validate_phase1_release.py
    - tests/test_app_routes.py
    - tests/test_deployment_config.py

key-decisions:
  - "Keep `/healthz` connection-free for liveness and gate production traffic on sanitized persisted purge state through `/readyz`."
  - "Remove image-global health semantics; Compose owns role-specific checks while Fly HTTP and the persistent mount remain app-only."
  - "Pass the bounded retention setting only to app and worker evidence creators; scheduler and purge retain least-privilege environments."
  - "Run focused gap/workbook classes first, then full clean-process discovery, so the release gate is both explicit and isolation-safe."

patterns-established:
  - "Readiness projection: public health responses contain only fixed status tokens and never database, evidence, source, or exception content."
  - "Release manifest: every authoritative gap class is named explicitly before the complete repository suite runs."

requirements-completed:
  - SNAP-02
  - SNAP-03
  - DATA-01
  - DATA-03
  - DATA-07
  - OPS-01
  - OPS-04
  - OPS-05

duration: 10 min
completed: 2026-11-10
---

# Phase 1 Plan 21: Persisted Readiness and Release Gate Summary

**Production traffic now fails closed on persisted purge compliance, every runtime has truthful health ownership, and one command proves all six repaired Phase 1 gaps without workbook regression.**

## Performance

- **Duration:** 10 min
- **Started:** 2026-11-10T03:07:24Z
- **Completed:** 2026-11-10T03:16:57Z
- **Tasks:** 3
- **Files modified:** 9

## Accomplishments

- Added `/readyz` with lazy PostgreSQL construction, sanitized fixed-token results, and HTTP 503 for never-run, stale, outstanding-byte, configuration, or database-unavailable states while preserving shallow `/healthz` liveness.
- Removed the image-global web health check and assigned explicit app, worker, scheduler, and persisted purge checks in Compose; Fly gates only the app process on `/readyz` and keeps its HTTP service and mount app-only.
- Exposed `FAILURE_EVIDENCE_RETENTION_DAYS=28` only to app/worker evidence creators and documented its strict 1-28 integer parser, earlier permitted expiry, 672-hour cap, hourly purge, six-hour readiness, alert response, and physical absence before 720 hours.
- Expanded the release validator to run every Plan 14-21 gap class, migration cycle, full PostgreSQL-backed discovery, D1/D2/D3 catalog/canonical flow, deployment rendering, workbook route regression, and diff hygiene.

## Task Commits

1. **Task 1 RED: Define persisted readiness route contract** - `43862be` (test)
2. **Task 1 GREEN: Expose persisted purge readiness** - `7452b6a` (feat)
3. **Task 2: Assign process-specific deployment health checks** - `20e86fd` (fix)
4. **Task 3: Finalize retention and Phase 1 release gate** - `c539065` (chore)
5. **Deviation fix: Isolate focused workbook release regression** - `d66758b` (fix)

## Files Created/Modified

- `app.py` - Lazy purge-readiness session factory and sanitized `/readyz` route; unchanged shallow `/healthz`.
- `Dockerfile` - Shared immutable image without a web-only global health check.
- `compose.yaml` - Role-specific health checks, least-privilege purge, and creator-only retention configuration.
- `fly.toml` - App-only `/readyz` traffic gate, app-only mount, and isolated scheduler/purge commands.
- `.env.example` - Safe 28-day operational retention default and strict range note.
- `DEPLOYMENT.md` - Retention, readiness, alerting, supervision, and final release instructions.
- `scripts/validate_phase1_release.py` - Explicit gap-regression and complete deterministic release command manifest.
- `tests/test_app_routes.py` - Fresh/non-fresh readiness, sanitized failure, and shallow liveness regressions.
- `tests/test_deployment_config.py` - Process health, purge privilege, retention, and release-manifest contracts.

## Decisions Made

- Keep liveness and compliance readiness separate: process survival never implies evidence-retention compliance.
- Normalize internal `database_unavailable` and construction failures to the single public token `unavailable`; unknown internal statuses also fail closed.
- Use Compose process checks for background roles and Fly app-only HTTP readiness rather than pretending every process serves HTTP.
- Retain the 28-day environment control because Plan 01-19 made it operational and strictly bounded; never pass it to scheduler or purge processes that do not create evidence.
- Run the exact workbook download route in the explicit gap suite and the complete route class in clean-process full discovery to prevent shared limiter state from invalidating an unrelated assertion.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Isolated the explicit workbook release target from route-suite state**
- **Found during:** Plan-level release validation after Task 3
- **Issue:** The first validator manifest ran the entire route class after the goal-level suite. Shared request-limit state caused a later exception-sanitization test to exit through rate limiting before its intended assertion, even though clean-process discovery passed the route behavior.
- **Fix:** Replaced the broad explicit route class with the exact D2 workbook-download regression. The goal-level workbook suite remains explicit, and full discovery still runs every route test in a clean subprocess.
- **Files modified:** `scripts/validate_phase1_release.py`, `tests/test_deployment_config.py`
- **Verification:** The corrected validator passed 87 focused regressions followed by all 273 repository tests.
- **Committed in:** `d66758b`

---

**Total deviations:** 1 auto-fixed (1 Rule 1 release-manifest isolation bug).
**Impact on plan:** The release gate remains complete while avoiding cross-suite state pollution; no production scope changed.

## Issues Encountered

- The managed sandbox blocks local PostgreSQL sockets and Git index writes. Scoped approved execution was used for the disposable `roster_scout_test` database and normal hooked commits.
- The initial complete validator exposed the manifest isolation issue above at 107 tests; it was corrected and the entire validator was rerun from the beginning.

## User Setup Required

Production launch remains blocked on the existing managed PostgreSQL, managed Redis, managed object/evidence storage, and uninterrupted Fly worker billing/capacity prerequisites. Operators must scale exactly one scheduler and purge process and obtain a fresh persisted purge heartbeat before `/readyz` can admit traffic.

## Verification Results

- Task 1 readiness/liveness target: 7 tests passed.
- Task 2 complete deployment suite: 32 tests passed, including real PostgreSQL purge progress while dispatch reconciliation remained blocked.
- Task 3 retention/validator contract target: 5 tests passed.
- Final explicit Plans 14-21 gap and focused workbook target: 87 tests passed.
- Final full PostgreSQL-backed repository discovery: 273 tests passed.
- Alembic head upgrade, full downgrade, re-upgrade, and model drift check passed.
- NCAA catalog validation: 942 selectable teams (308 D1, 253 D2, 381 D3), zero errors, and 25 expected missing-stable-ID warnings.
- Python compilation, required templates, rendered Compose configuration, and `git diff --check` passed.

## Known Stubs

None - modified production, deployment, and validator paths contain no placeholders or unwired empty values.

## Threat Flags

None - `/readyz`, process environment isolation, health ownership, and retention configuration are the registered T-01-21-01 through T-01-21-05 surfaces and are covered by automated tests.

## TDD Gate Compliance

PASSED - Task 1 RED commit `43862be` failed on the absent `/readyz` contract, then GREEN commit `7452b6a` made all seven readiness/liveness tests pass. Tasks 2 and 3 were configuration/integration work and were committed after their focused acceptance gates.

## Self-Check: PASSED

- All nine modified implementation, deployment, documentation, validator, and test files plus this summary exist.
- Task commits `43862be`, `7452b6a`, `20e86fd`, `c539065`, and `d66758b` resolve in repository history.
- Every task acceptance criterion and the complete plan-level release validator passed.
- `brag-output/` remains untouched and untracked.

## Next Phase Readiness

- All 21 Phase 1 plans are implemented, and the deterministic release gate proves all six authoritative verification gaps together.
- Existing selector, acquisition, cache, D1/D2/D3 canonical publication, and two-sheet workbook behavior remain green.
- Phase 2 planning can proceed; the managed-service and uninterrupted-hosting production prerequisites remain explicit launch blockers rather than hidden code gaps.

---
*Phase: 01-canonical-snapshot-foundation*
*Completed: 2026-11-10*
