---
phase: 01-canonical-snapshot-foundation
plan: "08"
subsystem: canonical-acquisition
tags: [ncaa-parser, same-job-cache, crash-recovery, postgresql, provenance]

requires:
  - phase: 01-canonical-snapshot-foundation
    provides: Durable report jobs, exact source persistence, whole-attempt claims, and transactional dispatch recovery
provides:
  - Structural NCAA table validation with explicit verified-empty semantics
  - Evidence-first final-attempt publication without a second collection
  - Per-section same-job recovery with immutable complete-section evidence
affects: [ncaa-acquisition, durable-jobs, canonical-publication, provenance, retry-semantics]

tech-stack:
  added: []
  patterns: [structural parse result, evidence-first retry recovery, immutable section replacement]

key-files:
  created: []
  modified:
    - roster_scraper.py
    - roster_scout/jobs.py
    - tests/test_ncaa_workflow.py
    - tests/test_report_jobs.py
    - tests/test_canonical_publish.py

key-decisions:
  - "Only a structurally valid NCAA grid with exactly one Player column can assert verified emptiness."
  - "Complete exact same-job evidence is publication authority even after the collection-attempt limit is reached."
  - "A complete section is immutable for its job/version; partial recovery fetches and replaces only missing sections."

patterns-established:
  - "Structural emptiness: empty tbody, explicit no-data markers, and aggregate-only bodies are distinct from malformed headers or row shapes."
  - "Evidence-first recovery: validate source identity and reconstruct SourceRecords before considering attempt exhaustion."
  - "Section coherence: row IDs, ordinals, raw values, checksums, versions, row counts, and collected_at belong to one immutable collection."

requirements-completed:
  - SNAP-03
  - SNAP-04
  - DATA-02
  - DATA-07
  - OPS-02
  - OPS-05

duration: 11 min
completed: 2026-11-09
---

# Phase 1 Plan 8: Truthful Parser Emptiness and Same-Job Evidence Recovery Summary

**Strict NCAA grid validation and immutable per-section evidence recovery now prevent silent empty publication while allowing complete final-attempt source rows to publish after worker loss**

## Performance

- **Duration:** 11 min
- **Started:** 2026-11-09T20:57:22Z
- **Completed:** 2026-11-09T21:08:09Z
- **Tasks:** 3
- **Files modified:** 5

## Accomplishments

- Closed CR-06 by requiring one recognized Player header, consistent candidate-row shapes, and an explicit structural-empty result before `verified_empty` can be true.
- Closed CR-04 by reconstructing complete same-job evidence before attempt exhaustion and publishing it without another collector call or attempt increment.
- Closed WR-03 by recovering cached core sections individually, fetching only missing sections, and rejecting any conflicting rewrite or ordinal extension of complete evidence.
- Preserved D1/D2/D3 structured collection, tuple workbook APIs, source checksums, row identity, parser/schema versions, and original collection freshness.

## Task Commits

Each task followed RED/GREEN TDD gates and was committed atomically:

1. **Task 1 RED: Expose malformed NCAA table acceptance** - `c8b634a` (test)
2. **Task 1 GREEN: Reject malformed NCAA stat grids** - `2a3f549` (feat)
3. **Task 2 RED: Expose final-attempt evidence loss** - `09703c7` (test)
4. **Task 2 GREEN: Recover evidence before attempt exhaustion** - `555c0f8` (feat)
5. **Task 3 RED: Expose cross-collection section mixing** - `53c6218` (test)
6. **Task 3 GREEN: Recover missing sections without mixing evidence** - `181fb98` (feat)

## Files Created/Modified

- `roster_scraper.py` - Dedicated schema errors, validated structural-empty parsing, and internal section-selective NCAA collection.
- `roster_scout/jobs.py` - Exact source-identity matching, evidence reconstruction, evidence-first exhaustion, immutable complete sections, and per-section recovery.
- `tests/test_ncaa_workflow.py` - Header/shape corruption, explicit-empty, aggregate-only, missing-grid, and D1/D2/D3 compatibility regressions.
- `tests/test_report_jobs.py` - Final-attempt crash recovery, exhaustion rejection, selective missing-section fetch, and immutable evidence regressions.
- `tests/test_canonical_publish.py` - Original SourceRecord checksum/freshness and canonical fact-link proof after final-attempt worker loss.

## Decisions Made

- Kept `_parse_ncaa_stat_table()` list-compatible for existing internal callers while `_collect_ncaa_team()` consumes the richer structural parse result.
- Treated complete cached batting and pitching evidence as sufficient to resume canonicalization directly; redelivery itself does not consume a collection attempt.
- Compared complete sections against their exact persisted representation, including raw values and payload checksums, so identical replay is idempotent and all conflicting content fails closed.
- Passed section selection only for partial recovery; full live collection retains the existing structured collector invocation contract.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

- The managed sandbox blocks localhost PostgreSQL sockets. Scoped approved execution was used only for the disposable `roster_scout_test` integration database.

## User Setup Required

None.

## Verification Results

- All three narrow task targets passed: 7 parser/compatibility tests and 11 final-attempt/per-section recovery tests.
- Complete plan suites passed: 96 tests across `test_ncaa_workflow`, `test_report_jobs`, and `test_canonical_publish`.
- Repository-wide discovery passed: 161 tests.
- NCAA catalog validation passed: 942 selectable teams (308 D1, 253 D2, 381 D3), zero errors.
- Required compilation, template existence checks, and `git diff --check` passed.

## Known Stubs

None - the modified production paths contain no placeholder or unwired data flow.

## TDD Gate Compliance

PASSED - each of the three tasks has a failing `test(01-08)` RED commit followed by a passing `feat(01-08)` GREEN commit.

## Self-Check: PASSED

- All five modified files exist.
- All six task commit hashes resolve in repository history.
- CR-04, CR-06, and WR-03 have direct passing regressions.

## Next Phase Readiness

- Parser corruption and same-job recovery gaps are closed for Plan 01-11 release validation.
- Plans 01-09, 01-11, 01-12, and 01-13 remain to complete Phase 1.

---
*Phase: 01-canonical-snapshot-foundation*
*Completed: 2026-11-09*
