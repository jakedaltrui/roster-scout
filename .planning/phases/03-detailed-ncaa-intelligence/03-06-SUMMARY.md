---
phase: 03-detailed-ncaa-intelligence
plan: "06"
subsystem: database
tags: [ncaa, schedules, postgres, immutable-snapshots, scoped-metrics]

requires:
  - phase: 03-detailed-ncaa-intelligence
    provides: detailed NCAA facts, shared metric definitions, source splits, and immutable publication
provides:
  - exact last-10-completed-game membership for batting, pitching, and fielding
  - season-correct conference-only membership backed by NCAA conference evidence
  - canonical snapshot-owned games, scope memberships, scoped facts, and metric lineage
  - schedule-derived publication parity and immutable refresh validation
affects: [03-detailed-ncaa-intelligence, report-jobs, snapshot-publication, workbook-intelligence]

tech-stack:
  added: []
  patterns:
    - source-first schedule evidence before canonical game and scope persistence
    - count-first scoped aggregation through the shared metric registry
    - explicit complete-stat-family declarations for game-log evidence

key-files:
  created:
    - roster_scout/scopes.py
    - tests/test_scoped_stats.py
    - tests/fixtures/ncaa_detail/d1/schedule.html
    - tests/fixtures/ncaa_detail/d2/schedule.html
    - tests/fixtures/ncaa_detail/d3/schedule.html
  modified:
    - roster_scout/models.py
    - roster_scout/ncaa_detail.py
    - roster_scout/jobs.py
    - roster_scout/snapshots.py
    - migrations/versions/0025_detailed_ncaa_intelligence.py
    - scripts/capture_phase3_ncaa_fixtures.py
    - tests/test_report_jobs.py
    - tests/test_snapshot_persistence.py

key-decisions:
  - "Production Game identity accepts only exact source contest IDs; deterministic fallback identity remains fixture-test-only."
  - "Game-log families become complete only through an explicit complete_stat_types declaration; the presence of some rows never implies exhaustive evidence."
  - "Computed scope publication validates exact same-snapshot membership and every contributing game-log fact instead of comparing aggregate row counts alone."

patterns-established:
  - "Schedule-first persistence: commit schedule and game-log SourceRecords before creating canonical Games, memberships, facts, or metrics."
  - "Truthful availability: incomplete schedule, conference, identity, or game-log evidence produces partial/unavailable scopes with no fabricated facts."

requirements-completed: [STAT-01, STAT-02, STAT-03, STAT-04, STAT-07, HIST-03, HIST-04]

duration: 47min
completed: 2026-11-16
---

# Phase 3 Plan 6: Exact Schedule-Derived Scopes Summary

**Snapshot-owned NCAA games now drive exact last-10 and season-correct conference batting, pitching, and fielding scopes with count-first metrics and publication-verified lineage.**

## Performance

- **Duration:** 47 min
- **Started:** 2026-11-16T08:22:20Z
- **Completed:** 2026-11-16T09:09:33Z
- **Tasks:** 3
- **Files modified:** 13

## Accomplishments

- Captured sanitized D1/D2/D3 schedule fixtures through the configured Browserless boundary, retaining exact contest, doubleheader, result, opponent, and season-conference evidence without secrets or unrelated customer data.
- Added canonical snapshot-owned Game, season-conference evidence, normalized scope membership, and typed batting/pitching/fielding aggregate contracts with production-only source identity rules.
- Integrated source-first schedule/game-log collection into the immutable report build, including successor refresh behavior, truthful partial status, exact contributor lineage, and publication rejection for disconnected facts.
- Verified the full focused suite (150 tests) and the repository's authoritative Phase 2.1 corrective release gate, including migrations, full discovery, Node regressions, catalog checks, compilation, and diff hygiene.

## Task Commits

Each task was committed atomically:

1. **Task 1: Capture schedule/conference evidence and lock exact scope membership tests** - `b17d41c` (test)
2. **Task 2: Implement audited game identity and deterministic scoped aggregation** - `b8e0339` (feat)
3. **Task 3: Collect and publish schedule-derived scopes inside the immutable build** - `89ca0dc` (feat)

## Files Created/Modified

- `roster_scout/scopes.py` - Pure completed-game selection, season-conference selection, and count-first scoped aggregation.
- `roster_scout/models.py` - Canonical games, conference evidence, scope membership, and scoped fielding persistence.
- `roster_scout/ncaa_detail.py` - Validated sanitized schedule evidence parser and audited-game adapter.
- `roster_scout/jobs.py` - Source-first schedule/game-log collection, canonical persistence, membership, aggregate facts, metrics, and cache recovery.
- `roster_scout/snapshots.py` - Computed-scope membership, fact, metric, and contributing-source publication validation.
- `migrations/versions/0025_detailed_ncaa_intelligence.py` - Reversible PostgreSQL schema, constraints, indexes, and immutable-child triggers.
- `scripts/capture_phase3_ncaa_fixtures.py` - Secret-safe bounded schedule capture through the configured Browserless runtime.
- `tests/test_scoped_stats.py` - D1/D2/D3 membership, doubleheader, cancellation, realignment, evidence, and aggregation contracts.
- `tests/test_report_jobs.py` - Complete build, immutable successor refresh, and disconnected-lineage publication coverage.
- `tests/test_snapshot_persistence.py` - Immutable snapshot-child trigger manifest for the new canonical tables.
- `tests/fixtures/ncaa_detail/{d1,d2,d3}/schedule.html` - Sanitized representative schedule/conference evidence.

## Decisions Made

- Production canonicalization requires an exact NCAA contest ID. Deterministic fallback keys are accepted only with explicit fixture test mode, preventing schedule position or inferred identity from entering published data.
- Game-log completeness is explicit per stat type. A nonempty subset of rows is not treated as exhaustive evidence, so missing inputs remain partial instead of becoming misleading zeros.
- Last-10 and conference scopes share one persisted Game universe and normalized memberships. Player logs and scope aggregators can only reference those same-snapshot rows and never create contests independently.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Added a bounded schedule-page fetch path**
- **Found during:** Task 1 (schedule evidence capture)
- **Issue:** The existing Browserless page helper correctly requires `#stat_grid` for statistics pages, but NCAA schedule pages do not contain that grid.
- **Fix:** Reused the configured sticky Browserless session and capacity boundary with a schedule-specific bounded fetch, while preserving fixed sanitized failures and secret handling.
- **Files modified:** `scripts/capture_phase3_ncaa_fixtures.py`
- **Verification:** D1/D2/D3 schedule captures sanitized successfully and `--preflight` returned `phase3_browserless_ready`.
- **Committed in:** `b17d41c`

**2. [Rule 1 - Bug] Updated immutable-child regression coverage for new tables**
- **Found during:** Task 3 full snapshot regression suite
- **Issue:** PostgreSQL correctly installed immutable snapshot triggers on the four new child tables, but the test's expected-table manifest predated them.
- **Fix:** Added games, season-conference evidence, scope-game memberships, and scoped fielding facts to the trigger contract.
- **Files modified:** `tests/test_snapshot_persistence.py`
- **Verification:** The failed serialization test passed, followed by all 150 focused tests and the full release gate.
- **Committed in:** `89ca0dc`

---

**Total deviations:** 2 auto-fixed (1 blocking, 1 bug)
**Impact on plan:** Both fixes were necessary to capture the planned evidence safely and keep immutable-persistence verification aligned; no feature scope was added.

## Issues Encountered

- The system `python3` environment lacked project dependencies for the catalog validator. The same repository check passed under the project's required `uv run python` environment with all 942 D1/D2/D3 teams valid.

## User Setup Required

None - the existing configured Browserless runtime completed capture without exposing or changing secrets.

## Known Stubs

None.

## Next Phase Readiness

- Exact schedule-derived scopes are ready for downstream report/workbook presentation.
- Missing or ambiguous game-log and conference evidence remains explicit partial/unavailable data; no production deployment was performed.

## Self-Check: PASSED

- Confirmed all 13 created/modified implementation and test files exist.
- Confirmed task commits `b17d41c`, `b8e0339`, and `89ca0dc` exist in git history.
- Confirmed no unknown stubs or unplanned trust-boundary surfaces remain.

---
*Phase: 03-detailed-ncaa-intelligence*
*Completed: 2026-11-16*
