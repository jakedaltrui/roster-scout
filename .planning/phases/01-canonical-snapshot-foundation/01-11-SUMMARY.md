---
phase: 01-canonical-snapshot-foundation
plan: "11"
subsystem: testing
tags: [release-validation, postgresql, migrations, ncaa, immutable-snapshots, xlsx]

requires:
  - phase: 01-canonical-snapshot-foundation
    provides: Durable dispatch, exclusive workers, canonical publication, evidence retention, and secure deployment gates from Plans 01-01 through 01-10, 01-12, and 01-13
provides:
  - Goal-level D1/D2/D3 durable-job-to-immutable-snapshot release evidence
  - Failure, replay, reopen, source-first recovery, and legacy workbook regressions
  - One fail-fast Phase 1 release validator covering story, migrations, tests, catalog, deployment, templates, compilation, and diff hygiene
affects: [phase-verification, production-release, canonical-snapshots, durable-jobs]

tech-stack:
  added: []
  patterns: [public-service release composition, exact-story precondition, fail-fast release orchestration]

key-files:
  created:
    - tests/test_phase1_release.py
    - scripts/validate_phase1_release.py
  modified:
    - DEPLOYMENT.md

key-decisions:
  - "The exact ROADMAP user story is the first release precondition; no technical check runs when it is invalid."
  - "One validator runs existing assertions rather than duplicating them, with the goal-level suite composing public Phase 1 service boundaries."
  - "A green deterministic gate does not authorize launch; managed PostgreSQL, Redis, object/evidence storage, and uninterrupted Fly worker capacity remain mandatory."

patterns-established:
  - "Goal-level release proof: accept and redispatch a durable job, collect deterministic NCAA fixtures, and inspect only published canonical state."
  - "Release gate ordering: story, compile/templates, migration lifecycle/drift, full tests, catalog, rendered deployment, then diff hygiene."

requirements-completed:
  - SNAP-01
  - SNAP-02
  - SNAP-03
  - SNAP-04
  - DATA-01
  - DATA-02
  - DATA-03
  - DATA-04
  - DATA-05
  - DATA-06
  - DATA-07
  - DATA-08
  - DATA-09
  - DATA-10
  - OPS-01
  - OPS-02
  - OPS-04
  - OPS-05

duration: 11 min
completed: 2026-11-09
---

# Phase 1 Plan 11: Phase 1 Release Validation Summary

**One reproducible release gate now proves the exact operator story from durable D1/D2/D3 acceptance through auditable immutable publication while preserving failure safety and the legacy two-sheet workbook.**

## Performance

- **Duration:** 11 min
- **Started:** 2026-11-09T21:44:30Z
- **Completed:** 2026-11-09T21:55:30Z
- **Tasks:** 2
- **Files modified:** 3

## Accomplishments

- Added six PostgreSQL-backed goal-level flows covering reconciled D1/D2/D3 publication, exact provenance and fact links, idempotent replay/reopen, malformed and blocked acquisition, source-first interruption recovery, bounded evidence purge, and the unchanged workbook tuple contract.
- Added a fail-fast release command that validates the exact unchanged MVP story before migrations, all 187 tests, the 942-team NCAA catalog, rendered Compose, required templates, Python compilation, and diff hygiene.
- Proved all 16 code-review owner regressions pass together with migration upgrade/downgrade/re-upgrade, model drift, deployment security, scheduler, evidence-retention, distributed-capacity, and legacy workbook gates.
- Kept production explicitly blocked until managed PostgreSQL, Redis, object/evidence storage, and uninterrupted Fly worker billing/capacity are verified; local Compose remains development-only.

## Task Commits

Each task was committed atomically:

1. **Task 1 RED: Add failing Phase 1 release flow contract** - `df97629` (test)
2. **Task 1 GREEN: Prove the Phase 1 operator release flow** - `cbf3d61` (feat)
3. **Task 2: Add deterministic Phase 1 release gate** - `894a057` (test)

## Files Created/Modified

- `tests/test_phase1_release.py` - Public-service composition for durable D1/D2/D3 publication, immutable replay, failure evidence, recovery, and workbook compatibility.
- `scripts/validate_phase1_release.py` - Exact-story-first orchestration of the complete deterministic Phase 1 release gate.
- `DEPLOYMENT.md` - One documented invocation plus the unchanged managed-service production blockers.

## Decisions Made

- Kept the ROADMAP goal byte-for-byte unchanged and made its centralized `valid: true` result a hard precondition before any technical validation.
- Used the transactional outbox/reconciler and published-only repository entrypoints in the goal suite so the release proof exercises the real durable boundaries instead of test-only substitutes.
- Reserved the complete validator for the plan/final boundary; Task 1 used the focused release module and Task 2 used compilation, story, and documentation assertions.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Test Bug] Aligned exhausted blocked-source expectations with bounded retry semantics**
- **Found during:** Task 1 GREEN verification
- **Issue:** The blocked-source case set `max_attempts=1` but initially expected a retryable `source_blocked` exception; the job correctly converted the final retryable attempt to durable `retry_exhausted` while retaining `source_blocked` on the evidence row.
- **Fix:** Asserted the safe exhausted job category separately from the preserved evidence category and kept the purge and disclosure checks intact.
- **Files modified:** `tests/test_phase1_release.py`
- **Verification:** All six goal-level tests and the complete 187-test release gate passed.
- **Committed in:** `cbf3d61`

---

**Total deviations:** 1 auto-fixed (1 Rule 1 test bug).
**Impact on plan:** The correction preserves the existing bounded-retry contract and strengthens the goal-level assertion without changing production behavior or scope.

## Issues Encountered

- The managed sandbox denied localhost PostgreSQL and Git index access. Scoped approved execution was used only for the disposable `roster_scout_test` database and normal hooked commits.

## User Setup Required

No new local setup. Production launch remains blocked until managed PostgreSQL, Redis, object/evidence storage, and uninterrupted Fly worker billing/capacity are verified.

## Verification Results

- Exact MVP story validator: `valid: true` with the ROADMAP input unchanged.
- Goal-level release suite: 6 tests passed across D1/D2/D3 happy paths, replay/reopen, failure evidence/purge, source-first recovery, and workbook compatibility.
- Full release suite: 187 tests passed with PostgreSQL enabled, including all 16 review-finding owner regressions.
- Migration lifecycle: upgrade to head, downgrade to base, re-upgrade to head, and `alembic check` all passed with no drift.
- NCAA catalog: 942 selectable 2026 teams (308 D1, 253 D2, 381 D3), `valid: true`, zero errors.
- Python compilation, required template existence, rendered Docker Compose configuration, and `git diff --check`: passed.
- Complete command: `DATABASE_URL=postgresql+psycopg://roster_scout:roster_scout@localhost:5432/roster_scout_test .venv/bin/python scripts/validate_phase1_release.py` returned `Phase 1 release gate: PASS`.

## Known Stubs

None. Empty collections and null values in the release suite are test control state, not product or UI placeholders.

## Threat Flags

None - subprocess orchestration is confined to the registered release-environment boundary T-01-11-04, uses fixed checked-in commands, and adds no network endpoint, authentication path, schema, or customer file-access surface.

## TDD Gate Compliance

PASSED - RED commit `df97629` failed on the absent release harness, followed by GREEN commit `cbf3d61`; the focused and complete release suites pass after implementation.

## Next Phase Readiness

- The formal Phase 1 MVP verification gap is closed with executable operator-flow evidence and one reproducible release command.
- All Phase 1 plans now have summaries and every assigned Phase 1 requirement is covered by the release gate.
- Production deployment is still intentionally blocked on the documented managed services and uninterrupted hosting prerequisites.

## Self-Check: PASSED

- All three created or modified plan files exist.
- Task commits `df97629`, `cbf3d61`, and `894a057` exist in git history.
- Every task acceptance criterion and the complete plan-level release command passed.
- `brag-output/` remains untouched and untracked.

---
*Phase: 01-canonical-snapshot-foundation*
*Completed: 2026-11-09*
