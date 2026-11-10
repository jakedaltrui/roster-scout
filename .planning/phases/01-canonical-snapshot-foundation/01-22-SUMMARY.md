---
phase: 01-canonical-snapshot-foundation
plan: "22"
subsystem: ncaa-parser-and-job-recovery
tags: [ncaa, parser-contract, postgresql, section-cache, generation-cas]

requires:
  - phase: 01-canonical-snapshot-foundation
    provides: Section-aware NCAA schema validation, same-job evidence recovery, and PostgreSQL-owned retry generations from Plans 01-14 and 01-16
provides:
  - Exact-once validation for every required normalized NCAA batting and pitching header
  - Parser-2 plus ncaa-core-v2 persisted identity across collection, sections, source rows, and cache recovery
  - Safe replacement of complete stale-version same-job sections while preserving immutable current-version evidence
  - Accurate PostgreSQL generation-CAS retry-authority documentation
affects: [ncaa-acquisition, report-jobs, source-provenance, section-cache, deployment-operations]

tech-stack:
  added: []
  patterns: [contract-bearing parser identity, exact normalized-header cardinality, version-gated evidence replacement]

key-files:
  created: []
  modified:
    - roster_scraper.py
    - roster_scout/jobs.py
    - tests/test_ncaa_workflow.py
    - tests/test_report_jobs.py
    - .env.example
    - DEPLOYMENT.md

key-decisions:
  - "The persisted NCAA parser identity is parser-2+ncaa-core-v2, binding implementation and accepted core-header contract in one cache key."
  - "A complete section may be replaced only when its schema/parser identity is stale; conflicting content under the current identity remains immutable and fails closed."
  - "PostgreSQL generation compare-and-swap and outbox due times are the sole retry authority; Celery carries only (job_id, generation) and never calls self.retry."

requirements-completed:
  - SNAP-04
  - OPS-02
  - OPS-05

duration: 6 min
completed: 2026-11-10
---

# Phase 1 Plan 22: NCAA Parser Contract and Cache Safety Summary

**Duplicate required NCAA columns now fail before row or empty-state classification, and parser-1 evidence cannot satisfy the contract-bearing parser-2 build.**

## Performance

- **Duration:** 6 min
- **Started:** 2026-11-10T16:29:03Z
- **Completed:** 2026-11-10T16:35:15Z
- **Tasks:** 3
- **Files modified:** 6

## Accomplishments

- Required batting headers (`Player`, `AB`, `H`) and pitching headers (`Player`, `IP`, `ERA`) must each occur exactly once after case/whitespace normalization, before `_unique_headers`, row parsing, or verified-empty classification.
- Bound the `ncaa-core-v2` header contract to one persisted `parser-2+ncaa-core-v2` identity shared by scraper metadata, job section matching, source records, environment defaults, and deployment guidance.
- Added PostgreSQL recovery coverage proving complete parser-1 sections trigger live recollection, stale rows are replaced by current evidence, and a later same-version recovery preserves collection timestamps without another collector call.
- Corrected maintainer and operator language so PostgreSQL generation CAS/outbox timing—not Celery retries—is the sole retry authority.

## Task Commits

Each task was committed atomically:

1. **Task 1 RED: Duplicate core-header counterexamples** - `9b7e86d` (test)
2. **Task 1 GREEN: Exact required-header cardinality** - `c81d67a` (fix)
3. **Task 2 RED: Stale parser-contract recovery counterexample** - `f54f08d` (test)
4. **Task 2 GREEN: Contract-bearing parser identity and recovery** - `0fb8795` (fix)
5. **Task 3: PostgreSQL retry-authority documentation** - `a9bdd3c` (docs)

## Files Created/Modified

- `roster_scraper.py` - Defines the parser-2/core-v2 identity and rejects duplicate normalized core headers before parsing.
- `roster_scout/jobs.py` - Imports the shared parser identity, replaces stale-version evidence safely, rejects non-current collected sections, and documents generation-bearing outbox delivery.
- `tests/test_ncaa_workflow.py` - Covers every duplicated required batting/pitching header in empty and row-bearing tables.
- `tests/test_report_jobs.py` - Proves parser-1 recollection, current evidence persistence, timestamp preservation, and same-version reuse.
- `.env.example` - Supplies `NCAA_PARSER_VERSION=parser-2+ncaa-core-v2`.
- `DEPLOYMENT.md` - Documents the current parser identity and PostgreSQL-owned retry protocol.

## Decisions Made

- Kept case and whitespace normalization as the only core-header aliases; duplicates never become suffixed columns that can authorize publication.
- Allowed a stale complete section to be replaced only by evidence carrying the exact current schema and parser identity. Same-version conflicting content remains an error.
- Imported the scraper's resolved parser identity into the durable job layer so environment overrides cannot diverge between collection and persistence within one process.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking Issue] Corrected a nonexistent verification class name**
- **Found during:** Task 3 verification
- **Issue:** The plan named `tests.test_report_jobs.ReportAttemptGenerationTests`, but the implemented generation contract is split across `AttemptGenerationSchemaTests`, `AttemptGenerationDeliveryTests`, and `CrashAfterGenerationCasRecoveryTests`.
- **Fix:** Ran all three real generation-CAS regression classes plus `Phase1ReleaseValidatorContractTests`.
- **Files modified:** None
- **Verification:** 15 corrected generation/deployment tests passed.
- **Commit:** Not applicable (verification-command correction only)

---

**Total deviations:** 1 auto-fixed blocking verification-command typo.
**Impact on plan:** No scope change; the corrected command exercised a broader set of the intended retry-generation behavior.

## Issues Encountered

- The managed sandbox blocks localhost PostgreSQL sockets and Git index writes. Scoped approved execution was used for the disposable `roster_scout_test` database and normal hooked commits.

## User Setup Required

None - no new external service configuration is required. Deployments should use the checked-in parser identity unless deliberately rolling to a newer contract-bearing version.

## Verification Results

- Duplicate-header parser and structured-collection gate: 11 tests passed.
- Parser-contract recovery plus adjacent cache/publication regressions: 10 tests passed.
- Corrected generation-CAS and release-validator gate: 15 tests passed.
- Combined NCAA workflow, PostgreSQL report-job, and workbook-route suites: 115 tests passed.
- NCAA catalog validation: 942 selectable teams (308 D1, 253 D2, 381 D3), zero errors.
- Required Python compilation, template existence, and `git diff --check`: passed.
- Browserless hotfix commit `8677513` remained intact; its lifecycle tests stayed outside this plan's staged changes.

## Known Stubs

None - the parser identity, rejection path, cache replacement, and documentation are fully wired.

## Threat Flags

None - no new endpoint or trust boundary was introduced; the planned untrusted-header and stale-evidence mitigations were applied.

## TDD Gate Compliance

PASSED - Task 1 has RED `9b7e86d` followed by GREEN `c81d67a`; Task 2 has RED `f54f08d` followed by GREEN `0fb8795`.

## Self-Check: PASSED

- All six modified implementation, test, environment, and documentation files exist.
- Task commits `9b7e86d`, `c81d67a`, `f54f08d`, `0fb8795`, and `a9bdd3c` resolve in repository history.
- Every task acceptance criterion and plan-level verification command passed after correcting the nonexistent class name.
- `brag-output/` remains untouched and untracked.

## Next Phase Readiness

- Verification gaps CR-01, CR-07, and WR-01 are closed.
- Plan 01-23 can build on the parser-safe canonical input while tightening ID-less cross-section matching.

---
*Phase: 01-canonical-snapshot-foundation*
*Completed: 2026-11-10*
