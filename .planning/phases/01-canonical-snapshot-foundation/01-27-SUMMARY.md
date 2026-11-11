---
phase: 01-canonical-snapshot-foundation
plan: "27"
subsystem: evidence-retention
tags: [postgresql, sqlalchemy, skip-locked, batching, readiness]

requires:
  - phase: 01-canonical-snapshot-foundation
    provides: Failure-evidence storage, retention enforcement, purge state, and standalone purge deployment through Plan 01-26
provides:
  - Deterministic 100-row expired-evidence purge batches with independent commits
  - Lock-skipping forward progress with fail-closed completion reporting
  - Zero-outstanding heartbeat finalization and bounded incomplete retries
  - Standalone purge healthcheck imports that work without caller-provided PYTHONPATH
affects: [evidence-retention, purge-readiness, deployment-healthchecks, operations]

tech-stack:
  added: []
  patterns: [FOR UPDATE SKIP LOCKED, independently committed batches, ordinary-MVCC completion recheck, fixed aggregate-only purge logs]

key-files:
  created: []
  modified:
    - roster_scout/evidence.py
    - scripts/run_purge.py
    - tests/test_evidence.py
    - tests/test_deployment_config.py

key-decisions:
  - "Every claimed evidence batch is limited to 100 IDs ordered by (expires_at,id) and commits before the next claim."
  - "The purge heartbeat advances only after an ordinary MVCC count confirms that zero expired rows retain bytes."
  - "Incomplete scans retry within 60 seconds, skip readiness queries, and expose only fixed statuses plus aggregate counts."

patterns-established:
  - "Contention-safe maintenance: skip locked rows, preserve unrelated progress, then report incomplete until a lock-inclusive count reaches zero."
  - "Healthcheck truthfulness: operational success state is finalized separately from individual progress commits."

requirements-completed: [DATA-07, OPS-01]

duration: 13 min
completed: 2026-11-10
---

# Phase 1 Plan 27: Contention-Safe Evidence Purge Summary

**Expired failure evidence now drains in deterministic, independently committed batches while locked rows keep readiness fail-closed and trigger bounded retries instead of rolling back unrelated progress.**

## Performance

- **Duration:** 13 min
- **Started:** 2026-11-11T00:34:45Z
- **Completed:** 2026-11-11T00:47:15Z
- **Tasks:** 2
- **Files modified:** 4

## Accomplishments

- Replaced the unbounded all-or-nothing purge with 100-row `(expires_at,id)` batches claimed through PostgreSQL `FOR UPDATE SKIP LOCKED` and committed independently.
- Added a structured purge result that distinguishes durable progress from complete success and reports aggregate purged, outstanding, and batch counts.
- Made heartbeat advancement contingent on a separate ordinary-MVCC zero-outstanding check, so skipped locked rows cannot spoof readiness.
- Added bounded 60-second retries for incomplete scans without readiness queries or success logs, then proved lock release completes deletion and advances the heartbeat.
- Made the exact deployed `python scripts/run_purge.py --check` command import the application package without relying on `PYTHONPATH`.

## Task Commits

Each task was committed atomically:

1. **Task 1 RED: Purge contention and batching gaps** - `97ab0cf` (test)
2. **Task 1 GREEN: Bounded independently committed purge batches** - `137bb4d` (fix)
3. **Task 2 RED: Incomplete purge scheduling gap** - `bf99ed1` (test)
4. **Task 2 GREEN: Bounded incomplete-scan retries** - `6df4970` (fix)
5. **Deviation RED: Standalone purge import failure** - `93986a8` (test)
6. **Deviation GREEN: Standalone project-root import bootstrap** - `704d238` (fix)

## Files Created/Modified

- `roster_scout/evidence.py` - Claims deterministic 100-row batches, commits each batch, returns structured completion state, and finalizes heartbeat only after zero outstanding bytes.
- `scripts/run_purge.py` - Retries incomplete scans within 60 seconds, keeps logs aggregate-only, gates readiness on complete results, and bootstraps standalone imports.
- `tests/test_evidence.py` - Covers partial lock progress, heartbeat truthfulness, and a 205-row three-batch backlog.
- `tests/test_deployment_config.py` - Covers incomplete retry timing, lock-release recovery, safe logging, and the actual deployed subprocess healthcheck command.

## Decisions Made

- Kept batch size fixed at 100 so a large backlog makes predictable progress without holding one transaction across the entire retention set.
- Used exact selected-ID updates after the deterministic lock-skipping claim, preventing a later predicate reevaluation from changing batch membership.
- Treated `complete=False` as a normal bounded-retry condition rather than an exception, preserving committed work while keeping persisted readiness unchanged.
- Retained integer-result compatibility for injected legacy purge callables while production uses `EvidencePurgeResult`.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed standalone purge command imports**
- **Found during:** Overall verification of `python scripts/run_purge.py --check`
- **Issue:** The exact command used by Compose, Fly, Procfile, and the container healthcheck could not import `roster_scout` unless the caller manually supplied `PYTHONPATH`.
- **Fix:** Added the repository root to the script import path before lazy runtime imports and added a PostgreSQL-backed subprocess regression with `PYTHONPATH` removed.
- **Files modified:** `scripts/run_purge.py`, `tests/test_deployment_config.py`
- **Verification:** The actual standalone subprocess exits zero with `readiness=fresh`; all focused and full suites pass.
- **Committed in:** `93986a8`, `704d238`

---

**Total deviations:** 1 auto-fixed bug.
**Impact on plan:** The fix makes the plan's required standalone `--check` verification match the production deployment commands; no architecture or public contract changed.

## Issues Encountered

- One manual verification attempt used an incorrect disposable-database credential and therefore ran zero tests; the repository's Compose test credentials were then used and all intended suites passed.
- The complete repository suite intentionally leaves the disposable schema downgraded in one lifecycle test; the schema was restored before the standalone heartbeat check.

## User Setup Required

None. Existing PostgreSQL and purge service configuration remain unchanged.

## Verification Results

- Focused contention and runner suite: 5 tests passed, including the standalone subprocess healthcheck.
- Complete evidence and deployment suites: 66 tests passed.
- Complete repository unittest discovery: passed.
- Standalone `--check`: exits zero after a complete purge heartbeat and logs `readiness=fresh` with aggregate counts only.
- NCAA catalog validation, Python compilation, template existence checks, and `git diff --check`: passed.

## Known Stubs

None. Empty test collections are local accumulators and no production value is hardcoded as an unavailable placeholder.

## Threat Flags

None - row-lock denial of service, heartbeat spoofing, batch-order tampering, and purge-log disclosure are the explicit T-01-27-01 through T-01-27-04 mitigations.

## TDD Gate Compliance

PASSED - both planned tasks have failing `test(01-27)` RED commits followed by passing `fix(01-27)` GREEN commits. The verification-discovered standalone bug also has its own RED/GREEN pair.

## Self-Check: PASSED

- All four modified implementation/test files and this summary exist.
- Task and deviation commits `97ab0cf`, `137bb4d`, `bf99ed1`, `6df4970`, `93986a8`, and `704d238` resolve in repository history.
- Every task acceptance criterion, focused suite, full plan suite, repository regression suite, standalone healthcheck, compilation, catalog validation, and diff check passed.
- `brag-output/` remains untouched and untracked.

## Next Phase Readiness

- Plan 01-29 can integrate workbook-download tracking without weakening the now fail-closed evidence-retention readiness path.
- Phase 1 operations can rely on purge heartbeat freshness only after every eligible evidence byte is absent, including rows previously skipped under contention.

---
*Phase: 01-canonical-snapshot-foundation*
*Completed: 2026-11-10*
