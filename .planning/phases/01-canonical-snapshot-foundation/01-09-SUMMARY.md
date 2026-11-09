---
phase: 01-canonical-snapshot-foundation
plan: "09"
subsystem: failure-evidence
tags: [postgresql, gzip, sha256, url-sanitization, ncaa-collector]

requires:
  - phase: 01-canonical-snapshot-foundation
    provides: Durable report jobs, failed-only evidence schema, and strict NCAA collection failures
provides:
  - Runtime failed/blocked NCAA HTML capture with a 64 KiB private boundary
  - Atomic terminal-job and failure-evidence persistence
  - Deterministic gzip/SHA-256 evidence with fixed 696-hour byte expiry
  - Credential-free HTTP/HTTPS evidence URL metadata
affects: [evidence-purge, operator-diagnostics, durable-jobs, ncaa-acquisition]

tech-stack:
  added: []
  patterns: [private bounded failure carrier, caller-owned evidence transaction, reconstructed safe URL authority]

key-files:
  created:
    - tests/test_evidence.py
  modified:
    - roster_scraper.py
    - roster_scout/jobs.py
    - roster_scout/evidence.py
    - tests/test_report_jobs.py

key-decisions:
  - "Failure HTML crosses the acquisition-to-job boundary only as a private 64 KiB byte payload with safe category, section, URL, and collection time."
  - "Evidence byte expiry is fixed at collected_at plus 696 hours; Plan 01-13 owns the independent purge cadence and 720-hour deletion guarantee."
  - "Stored URL authority is rebuilt from validated hostname and optional port; userinfo, query, fragment, and malformed authorities are never persisted."

patterns-established:
  - "Coordinated failure evidence: terminal state and the failed-only evidence row flush in one caller-owned PostgreSQL transaction."
  - "Safe diagnostic boundary: raw HTML remains compressed restricted evidence and never enters public exceptions, job messages, or logs."

requirements-completed: [SNAP-02, SNAP-04, DATA-07]

duration: 8 min
completed: 2026-11-09
---

# Phase 1 Plan 9: Failed Collection Evidence and Safe Metadata Summary

**Real NCAA collection failures now retain bounded deterministic gzip evidence for 696 hours while successful jobs retain none and persisted URLs exclude credentials, queries, fragments, and malformed authorities**

## Performance

- **Duration:** 8 min
- **Started:** 2026-11-09T21:21:14Z
- **Completed:** 2026-11-09T21:29:40Z
- **Tasks:** 2
- **Files modified:** 5

## Accomplishments

- Closed CR-07 capture ownership by carrying malformed or blocked NCAA HTML through a private capped payload and inserting evidence atomically with the terminal job transition.
- Made string and bytes-like HTML converge on the same UTF-8 bytes, deterministic gzip payload, SHA-256 checksum, and immutable `collected_at + 696 hours` expiry.
- Proved successful jobs store zero failure evidence and public exception/job text contains no HTML, credentials, query data, fragments, or raw exception content.
- Closed WR-05 by rebuilding safe authorities from validated DNS, IPv4, or bracketed IPv6 hosts and valid optional ports.

## Task Commits

Each task followed RED/GREEN TDD gates and was committed atomically:

1. **Task 1 RED: Expose missing failed collection evidence** - `79a79e9` (test)
2. **Task 1 GREEN: Persist bounded failed collection evidence** - `de6b184` (feat)
3. **Task 2 RED: Expose unsafe evidence URL metadata** - `07bbb2f` (test)
4. **Task 2 GREEN: Strip credentials from evidence URLs** - `2484a7f` (fix)

**Plan summary:** committed separately before project tracking updates.

## Files Created/Modified

- `tests/test_evidence.py` - End-to-end failed collector, byte encoding/expiry, URL sanitizer, and safe metadata regressions.
- `tests/test_report_jobs.py` - Successful durable-job assertion proving zero retained HTML evidence.
- `roster_scraper.py` - Private capped failure-evidence carrier at HTTP, browser, Browserless, and parser boundaries.
- `roster_scout/jobs.py` - Safe failure classification and coordinated terminal-state/evidence persistence.
- `roster_scout/evidence.py` - Deterministic bounded storage, fixed 696-hour expiry, and strict source URL authority reconstruction.

## Decisions Made

- Capped raw diagnostic HTML at 64 KiB before it leaves acquisition and capped again at storage as defense in depth.
- Kept evidence storage compatible with standalone callers while allowing the job service to supply its existing transaction, satisfying the failed/blocked-only database trigger atomically.
- Classified malformed NCAA grids as deterministic `schema_validation` and blocked pages as retryable `source_blocked`; only a terminal failed/blocked transition stores evidence.
- Returned `None` for malformed source authorities instead of retaining partial unsafe strings or propagating `urllib.parse` exceptions.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

- The managed sandbox blocks localhost PostgreSQL sockets. Scoped approved execution was used only for the disposable `roster_scout_test` integration database.

## User Setup Required

None.

## Verification Results

- Both exact plan task targets passed: failed-collector/success non-retention and URL/metadata sanitization.
- Complete evidence, durable report-job, and NCAA workflow suites passed: 72 tests.
- Repository-wide discovery passed: 174 tests.
- NCAA catalog validation passed: 942 selectable 2026 teams (308 D1, 253 D2, 381 D3), zero errors.
- Required compilation, template existence checks, and `git diff --check` passed.

## Known Stubs

None - every modified production path is wired to a real collector or persistence boundary. Plan 01-13 intentionally owns purge scheduling, physical deletion by 720 hours, readiness, and deployment documentation.

## Threat Flags

None - the new restricted HTML and source-URL surfaces are the explicit T-01-09-01 through T-01-09-03 mitigations in this plan.

## TDD Gate Compliance

PASSED - both tasks have a failing `test(01-09)` RED commit followed by a passing GREEN implementation commit.

## Self-Check: PASSED

- All five created/modified files exist.
- Task commits `79a79e9`, `de6b184`, `07bbb2f`, and `2484a7f` resolve in repository history.
- All task acceptance criteria and plan-level verification commands passed.

## Next Phase Readiness

- CR-07 capture ownership and WR-05 are closed for Phase 1 release validation.
- Plan 01-13 can now schedule purging, enforce physical byte deletion by 720 hours, add stale-purge readiness, and document the complete retention lifecycle.

---
*Phase: 01-canonical-snapshot-foundation*
*Completed: 2026-11-09*
