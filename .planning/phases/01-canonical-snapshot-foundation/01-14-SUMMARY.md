---
phase: 01-canonical-snapshot-foundation
plan: "14"
subsystem: canonical-acquisition
tags: [ncaa-parser, schema-validation, postgresql, failure-evidence, regression-testing]

requires:
  - phase: 01-canonical-snapshot-foundation
    provides: Structural NCAA parsing, durable report jobs, canonical publication, and bounded failure evidence from Plans 01-08 and 01-11
provides:
  - Versioned section-aware NCAA batting and pitching core-header signatures
  - Fail-closed verified-empty behavior for unsupported or drifted stat grids
  - PostgreSQL-backed proof that malformed core grids cannot publish snapshots or typed facts
affects: [ncaa-acquisition, durable-jobs, canonical-publication, legacy-workbook]

tech-stack:
  added: []
  patterns: [explicit parser section context, normalized exact header signatures, production-collector publication tests]

key-files:
  created: []
  modified:
    - roster_scraper.py
    - tests/test_ncaa_workflow.py
    - tests/test_report_jobs.py
    - tests/test_phase1_release.py
    - tests/test_canonical_publish.py

key-decisions:
  - "Batting accepts only an explicit Player/AB/H core signature and pitching accepts only Player/IP/ERA before rows or verified emptiness are evaluated."
  - "Header matching normalizes case and whitespace only; renamed and fuzzy lookalikes remain unsupported until backed by explicit fixtures."

patterns-established:
  - "Section-aware parsing: every internal parser caller supplies batting or pitching context explicitly."
  - "Durable fail-closed proof: upstream HTML alone is patched while the production collector, classifier, evidence store, and publisher run unchanged."

requirements-completed:
  - SNAP-03
  - SNAP-04
  - DATA-02
  - OPS-05

duration: 7 min
completed: 2026-11-10
---

# Phase 1 Plan 14: Section-Aware NCAA Core Headers Summary

**Versioned batting and pitching header signatures now reject stat-less or drifted NCAA grids before they can authorize canonical rows or verified emptiness.**

## Performance

- **Duration:** 7 min
- **Started:** 2026-11-10T01:19:21Z
- **Completed:** 2026-11-10T01:26:59Z
- **Tasks:** 2
- **Files modified:** 5

## Accomplishments

- Added explicit `ncaa-core-v1` contracts requiring Player/AB/H for batting and Player/IP/ERA for pitching, with case-insensitive whitespace-normalized comparison.
- Covered Player-only, unrelated, renamed-core, cross-section, malformed-row, valid-empty, and D1/D2/D3 fixture behavior without fuzzy inference.
- Proved malformed grids terminate durable jobs as sanitized `schema_validation`, retain exactly one bounded evidence row, and create no ready/partial snapshot or typed facts.
- Preserved exact selector, cache, two-way classification, two-sheet workbook, and full PostgreSQL publication behavior across 193 repository tests.

## Task Commits

Each task was committed atomically:

1. **Task 1 RED: Expose unsupported NCAA core headers** - `65da210` (test)
2. **Task 1 GREEN: Enforce section-aware NCAA core headers** - `c24f1ab` (feat)
3. **Task 2: Prove malformed headers block durable publication** - `c0417ff` (test)
4. **Deviation fix: Align verified-empty publication fixture** - `27237ba` (fix)

## Files Created/Modified

- `roster_scraper.py` - Versioned core signatures, normalized exact header validation, explicit section parameters, and collector wiring.
- `tests/test_ncaa_workflow.py` - Parser corruption matrix, matching empty-state cases, explicit parser sections, and legacy D1/D2/D3 behavior.
- `tests/test_report_jobs.py` - Real-collector PostgreSQL tests for sanitized failure evidence, non-publication, valid empty sections, and D1/D2/D3 publication.
- `tests/test_phase1_release.py` - Explicit batting/pitching context for the legacy two-sheet workbook release regression.
- `tests/test_canonical_publish.py` - Verified-empty batting fixture aligned with the supported Player/AB/H signature.

## Decisions Made

- Kept header acceptance deliberately exact: normalization changes only case and whitespace, while aliases require a future explicit fixture-backed contract revision.
- Required section context at both parser entrypoints instead of inferring batting or pitching from whichever columns happen to be present.
- Validated the core signature before row handling, aggregate filtering, empty markers, or `verified_empty` computation so malformed markup fails closed.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Test Regression] Updated a pre-existing verified-empty fixture to satisfy the new core contract**
- **Found during:** Plan-level full test discovery
- **Issue:** `tests/test_canonical_publish.py` modeled valid batting emptiness with a Player-only grid, which the plan intentionally makes invalid.
- **Fix:** Added the required AB and H headers while retaining an empty tbody and the original publication assertion.
- **Files modified:** `tests/test_canonical_publish.py`
- **Verification:** All 29 canonical publication tests and all 193 repository tests passed.
- **Committed in:** `27237ba`

---

**Total deviations:** 1 auto-fixed (1 Rule 1 test regression).
**Impact on plan:** The fix updates stale test evidence to the new supported schema without changing production scope.

## Issues Encountered

- The managed sandbox blocks localhost PostgreSQL sockets and Git index writes. Scoped approved execution was used for the disposable `roster_scout_test` database and normal hooked commits.

## User Setup Required

None - no external service configuration required.

## Verification Results

- Parser corruption and structured collection targets: 10 tests passed.
- Durable section-header publication target: 3 PostgreSQL-backed tests passed.
- Full `tests.test_ncaa_workflow`: 40 tests passed.
- Full `tests.test_report_jobs`: 34 PostgreSQL-backed tests passed.
- Full `tests.test_canonical_publish`: 29 PostgreSQL-backed tests passed.
- Repository discovery: 193 tests passed with PostgreSQL enabled.
- NCAA catalog validation: 942 selectable teams (308 D1, 253 D2, 381 D3), zero errors.
- Required Python compilation, template existence checks, workbook route regressions, and `git diff --check`: passed.

## Known Stubs

None - modified production paths contain no placeholder or unwired data flow.

## TDD Gate Compliance

PASSED - Task 1 has RED commit `65da210` followed by GREEN commit `c24f1ab`. Task 2 is integration proof for that implemented boundary and was committed independently as `c0417ff` after passing through the production collector and PostgreSQL publisher.

## Self-Check: PASSED

- All five modified implementation and test files exist.
- Task commits `65da210`, `c24f1ab`, `c0417ff`, and `27237ba` resolve in repository history.
- Every task acceptance criterion and plan-level verification command passed.
- `brag-output/` remains untouched and untracked.

## Next Phase Readiness

- Verification gap 5 is closed with fail-closed parser and durable publication evidence.
- Plan 01-15 can proceed; managed production PostgreSQL, Redis, object/evidence storage, and uninterrupted Fly worker capacity remain the existing launch prerequisites.

---
*Phase: 01-canonical-snapshot-foundation*
*Completed: 2026-11-10*
