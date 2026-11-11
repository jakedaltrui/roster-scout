---
phase: 01-canonical-snapshot-foundation
plan: "37"
subsystem: release-validation-and-operations
tags: [postgresql, alembic, node-test, deployment, runbook, release-gate]

requires:
  - phase: 01-canonical-snapshot-foundation
    provides: Round-four identity, cache, analytics, acknowledgement, browser, and purge corrections from Plans 01-31 through 01-36
provides:
  - Fail-fast release execution of every round-four Python and Node counterexample before full validation
  - Revision-0013 migration lifecycle plus discovery, catalog, deployment, workbook, compilation, template, and diff gates
  - Exact native/production analytics, acknowledgement-secret, cache, lock-order, and purge operations contracts
affects: [phase-verification, deployment-operations, native-workbooks, usage-analytics, phase-2-readiness]

tech-stack:
  added: []
  patterns: [focused-counterexamples-before-full-gates, executable-group-order-contract, operational-semantics-as-tests]

key-files:
  created: []
  modified:
    - scripts/validate_phase1_release.py
    - tests/test_deployment_config.py
    - DEPLOYMENT.md

key-decisions:
  - "Only exact story/database guards and minimal migrate-to-head setup may precede the round-four Python and Node counterexamples; every full validation group follows them."
  - "The native workbook remains database-optional, while production durable jobs, migrations, readiness, usage reports, and authoritative analytics remain PostgreSQL-required."
  - "Successful-download operations mean blob read, anchor initiation, and idempotent same-origin acknowledgement; they never claim proof of a filesystem save."

patterns-established:
  - "Release ordering: minimal head setup -> focused Python -> focused Node -> migration lifecycle -> discovery -> catalog -> deployment -> workbook -> compilation/templates -> diff."
  - "Runbook contracts: executable behavior, privacy boundaries, secret rotation, and concurrency rules are protected by narrow source-text tests."

requirements-completed: [SNAP-01, SNAP-02, SNAP-03, SNAP-04, DATA-01, DATA-02, DATA-03, DATA-04, DATA-05, DATA-06, DATA-07, DATA-08, DATA-09, DATA-10, OPS-01, OPS-02, OPS-04, OPS-05]

duration: 9h 15m
completed: 2026-11-11
---

# Phase 1 Plan 37: Round-Four Release Gate and Operations Summary

**One fail-fast command now executes every round-four counterexample before the complete revision-0013 lifecycle and downstream release gates, with matching operational contracts for analytics, acknowledgements, cache identity, lock order, and purge certification.**

## Performance

- **Duration:** 9h 15m
- **Started:** 2026-11-11T06:04:44Z
- **Completed:** 2026-11-11T15:19:45Z
- **Tasks:** 2
- **Files modified:** 3

## Accomplishments

- Added all Plan 01-31 through 01-36 Python counterexample classes and the executable Node browser state-machine suite to the deterministic release manifest immediately after minimal head setup.
- Made revision 0013, full migration lifecycle/drift, clean-process discovery, D1/D2/D3 catalog, Compose/deployment, two-sheet workbook, compilation/template, and diff groups mandatory in audited order.
- Documented database-optional native workbooks separately from PostgreSQL-required production paths and specified exact download proof, shared-secret, metric, cache, identity-lock, and purge-heartbeat semantics.
- Added source/runtime ordering assertions and narrow operations-runbook tests so removing, renaming, or reordering a release sentinel fails deterministically.

## Task Commits

Each task was committed atomically:

1. **Task 1: Add every round-four counterexample to the deterministic release gate** - `0347e3f` (test)
2. **Task 2: Document corrected analytics, receipt, cache, identity, and purge contracts** - `3151981` (docs)

## Files Created/Modified

- `scripts/validate_phase1_release.py` - Revision-0013, round-four Python/Node, full lifecycle, and downstream group manifest in enforced fail-fast order.
- `tests/test_deployment_config.py` - Release membership/order sentinels and exact round-four operations-runbook contracts.
- `DEPLOYMENT.md` - Native/production database boundary, acknowledgement secret and rotation, metric meaning, cache v3, identity lock order, and monotonic purge operations.

## Decisions Made

- Kept the historical Plan 14-29 manifest definitions for contract continuity while relying on clean-process full discovery to execute those regressions after the new focused and migration gates.
- Treated deployment documentation as an executable contract: the same file now supplies narrow tests for startup predicates, proof bounds, privacy exclusions, and concurrency guarantees.
- Added the required shared acknowledgement secret to the Fly secrets example so the documented deployment cannot fail the new startup preflight.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

- The complete PostgreSQL suite emitted the already-documented post-suite psycopg connection cleanup `ResourceWarning`; every release group still passed.
- The generic planning key-link scanner cannot resolve route/group labels as source files; executable manifest, source-order, endpoint, browser, and deployment tests provide the concrete verification instead.

## User Setup Required

Before Fly or any `WEB_CONCURRENCY>1` deployment, configure the same `ROSTER_SCOUT_DOWNLOAD_ACK_SECRET` on every worker. It must be URL-safe-base64 material decoding to at least 32 random bytes. Rotate it only with a coordinated restart after draining downloads or waiting the exact 15-minute proof TTL.

## Verification Results

- Exact Phase 1 MVP user-story validation: passed.
- Round-four focused Python counterexamples: 50 passed.
- Browser download state-machine suite: 8 passed with Node's built-in test runner.
- Revision 0013 upgrade/downgrade/re-upgrade/fresh-install and model-drift checks: passed.
- Clean-process full PostgreSQL-backed unittest discovery: passed.
- NCAA catalog: 942 selectable teams with zero validation errors.
- Rendered Compose configuration and complete deployment module: passed, including 54 deployment/runbook tests.
- Two-sheet workbook validation, Python compilation, required templates, template contracts, and `git diff --check`: passed.
- `gsd-sdk query verify.schema-drift 01`: no drift detected.

## Known Stubs

None - the release manifest, runbook, and contract tests contain no placeholder or unwired behavior.

## Threat Flags

None - no new endpoint, credential path, file-access boundary, dependency, or schema surface was introduced; the plan's release-decision and runbook trust boundaries are covered directly by tests.

## Self-Check: PASSED

- `scripts/validate_phase1_release.py`, `tests/test_deployment_config.py`, `DEPLOYMENT.md`, and this summary exist.
- Task commits `0347e3f` and `3151981` resolve in repository history.
- Every task acceptance criterion, the complete release validator, diff hygiene, and schema-drift checks passed.
- `brag-output/` remains untouched and untracked.

## Next Phase Readiness

- All 37 Phase 1 plans now have implementation evidence and the round-four gaps are inside the supported release command.
- Phase 1 remains active/needs-review until the independent code review and verifier replace the previous gap reports.
- Production launch remains blocked on the managed PostgreSQL, Redis, object/evidence storage, and uninterrupted Fly worker prerequisites already documented.

---
*Phase: 01-canonical-snapshot-foundation*
*Completed: 2026-11-11*
