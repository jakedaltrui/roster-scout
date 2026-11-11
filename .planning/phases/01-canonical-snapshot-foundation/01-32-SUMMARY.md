---
phase: 01-canonical-snapshot-foundation
plan: "32"
subsystem: ncaa-acquisition-cache
tags: [ncaa, cache, parser-contract, browserless, concurrency]

requires:
  - phase: 01-canonical-snapshot-foundation
    provides: Contract-bearing NCAA parser identity and same-machine Browserless request coalescing
provides:
  - Version-3 legacy NCAA result payloads bound to parser, core-header, and report-schema identities
  - Contract-qualified cache and team-lock namespaces for safe collector rollouts
  - Regression coverage for stale payload rejection, cold-call coalescing, and force refresh
affects: [ncaa-acquisition, workbook-generation, browserless-capacity, cache-operations]

tech-stack:
  added: []
  patterns: [exact semantic cache contract, shared cache-lock namespace identity, stale-input fail-closed]

key-files:
  created: []
  modified:
    - roster_scraper.py
    - tests/test_ncaa_workflow.py
    - tests/test_browserless_lifecycle.py

key-decisions:
  - "Legacy NCAA result payloads must exactly match cache format, parser, core-header, and report-schema identities before normalized players are trusted."
  - "The successful-result cache and per-team file lock derive from one source-and-contract key so incompatible collectors neither share artifacts nor coalesce together."

patterns-established:
  - "Cache trust: derive the expected contract from runtime constants and compare every persisted identity exactly before reading players."
  - "Collector coalescing: acquire a lock in the same contract namespace as the artifact, then recheck the cache inside that lock."

requirements-completed:
  - SNAP-01
  - DATA-02
  - DATA-03
  - OPS-01
  - OPS-02
  - OPS-05

duration: 6 min
completed: 2026-11-11
---

# Phase 1 Plan 32: Legacy NCAA Result Cache Contract Summary

**Version-3 NCAA result caches now reject stale parser/header/schema payloads while contract-qualified file locks preserve one-collection coalescing and explicit refresh.**

## Performance

- **Duration:** 6 min
- **Started:** 2026-11-11T05:10:08Z
- **Completed:** 2026-11-11T05:16:21Z
- **Tasks:** 2
- **Files modified:** 3

## Accomplishments

- Bumped the legacy successful-result cache to format 3 and persisted exact parser, core-header, and report-schema identities with every payload.
- Rejected version-2, missing-field, malformed, and individually mismatched contract payloads without returning their player rows.
- Derived result filenames and per-team lock filenames from the same team-season and semantic contract identity.
- Preserved the lock-protected post-acquisition cache recheck, 90-second bounded wait, one-collection cold-call behavior, and explicit force refresh.

## Task Commits

Each TDD task was committed as a RED/GREEN pair:

1. **Task 1 RED: Expose stale NCAA result cache contracts** - `785a3e3` (test)
2. **Task 1 GREEN: Bind NCAA result cache to parser contract** - `6e7c840` (fix)
3. **Task 2 RED: Expose cross-contract team lock collision** - `6c1060f` (test)
4. **Task 2 GREEN: Coalesce NCAA requests by cache contract** - `9ef3678` (fix)

## Files Created/Modified

- `roster_scraper.py` - Version-3 contract generation, exact payload validation, contract-qualified result key, and matching team-lock key.
- `tests/test_ncaa_workflow.py` - Version-2 and per-field stale contract counterexamples plus current-payload reuse proof.
- `tests/test_browserless_lifecycle.py` - Same-team cold-call, force-refresh, and cross-contract namespace regressions.

## Decisions Made

- Included the cache format in the key prefix tuple as well as the payload contract so a future format bump cannot collide with an older filename even when semantic identities remain unchanged.
- Kept stale files in place as harmless misses; the current-contract recollection writes a separate artifact and does not trust or mutate stale player rows.
- Kept cache validation independent of payload claims by deriving expected values only from runtime collector constants.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking Issue] Used the repository virtual environment for handoff checks**
- **Found during:** Plan-level verification
- **Issue:** The literal `python3` command in `CONTEXT.md` used the dependency-free system interpreter and failed to import `requests`.
- **Fix:** Re-ran the same compile and catalog checks through the checked-in `.venv` interpreter used by the application tests.
- **Files modified:** None
- **Verification:** Compilation, template checks, and the 942-team catalog validation passed.
- **Commit:** Not applicable (verification-command correction only)

---

**Total deviations:** 1 auto-fixed blocking verification-command issue.
**Impact on plan:** No implementation or scope change; the supported project interpreter executed the intended checks.

## Issues Encountered

- The full PostgreSQL-backed suite emitted the pre-existing post-suite psycopg open-connection `ResourceWarning`; all 373 tests passed and this warning was already recorded as deferred by Plan 01-31.

## User Setup Required

None - no external service configuration required.

## Verification Results

- Task 1 cache-contract suite: 2 passed.
- Task 2 lock/cache suite: 5 passed.
- Complete NCAA workflow and Browserless lifecycle modules: 48 passed.
- Full PostgreSQL-backed discovery: 373 passed.
- NCAA catalog: 942 selectable teams (308 D1, 253 D2, 381 D3), zero errors; 25 expected missing-stable-ID warnings unchanged.
- Python compilation, required template existence, and `git diff --check`: passed.

## Known Stubs

None - cache identity, validation, recollection, and lock coalescing are fully wired.

## Threat Flags

None - the change tightens the planned disk-cache and concurrent-caller trust boundaries without adding a new endpoint, credential path, or external access surface.

## TDD Gate Compliance

- Task 1 RED `785a3e3` returned a version-2 cached player and lacked a semantic cache key; GREEN `6e7c840` rejected every stale contract and reused the recollected payload.
- Task 2 RED `6c1060f` proved four collector contracts collapsed into one lock file; GREEN `9ef3678` produced distinct cache and lock namespaces while preserving coalescing.

## Self-Check: PASSED

- All three modified implementation/test files and this summary exist.
- Task commits `785a3e3`, `6e7c840`, `6c1060f`, and `9ef3678` resolve in git history.
- Every task acceptance criterion and plan-level verification check passed.
- `brag-output/` remains untouched and untracked.

## Next Phase Readiness

- The verifier's stale legacy cache counterexample is closed and OPS-02 can rely on semantic cache invalidation across collector deployments.
- Ready for Plan 01-33's native-workbook and unique-user aggregate corrections.

---
*Phase: 01-canonical-snapshot-foundation*
*Completed: 2026-11-11*
