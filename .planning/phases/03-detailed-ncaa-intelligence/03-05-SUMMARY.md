---
phase: 03-detailed-ncaa-intelligence
plan: "05"
subsystem: database
tags: [ncaa, player-history, postgres, immutable-snapshots, game-logs]

requires:
  - phase: 03-detailed-ncaa-intelligence
    provides: finalized canonical Game identity, schedule evidence, shared metric registry, and immutable snapshot publication
provides:
  - transfer-aware typed career seasons under one authoritative NCAA player identity
  - count-first career aggregates with explicit included and excluded row lineage
  - current-season batting, pitching, and fielding game logs linked only to exact canonical Games
  - bounded one-player/type request planning with same-build reuse and truthful exhaustion status
affects: [03-detailed-ncaa-intelligence, report-jobs, snapshot-publication, player-drill-path]

tech-stack:
  added: []
  patterns:
    - stable-player capture once per player and stat type rather than per historical season
    - exact contest-ID linking with unmatched and ambiguous rows kept partial
    - count-first aggregate calculation through the shared metric registry

key-files:
  created:
    - tests/test_player_history.py
    - tests/fixtures/ncaa_detail/d1/player_detail.html
    - tests/fixtures/ncaa_detail/d2/player_detail.html
    - tests/fixtures/ncaa_detail/d3/player_detail.html
  modified:
    - roster_scout/ncaa_detail.py
    - roster_scout/canonical.py
    - roster_scout/models.py
    - roster_scraper.py
    - migrations/versions/0025_detailed_ncaa_intelligence.py
    - scripts/capture_phase3_ncaa_fixtures.py
    - tests/test_snapshot_persistence.py

key-decisions:
  - "Historical rows without exact season/team/division identity remain separate evidence but are excluded from career aggregates with explicit reasons."
  - "Player game logs may reference only an exact same-snapshot NCAA contest ID; labels and dates never create or guess a Game."
  - "NCAA linked cells that expose malformed count text are stored as unavailable rather than coerced into a statistic."

patterns-established:
  - "One-player identity: transfers and two-way roles retain distinct typed facts while sharing the stable NCAA Player ID."
  - "Truthful history: missing stable IDs, historical identity gaps, malformed counts, and request-budget exhaustion produce explicit partial/unavailable status."

requirements-completed: [HIST-01, HIST-02, HIST-03, HIST-04]

duration: 41min
completed: 2026-11-16
---

# Phase 3 Plan 5: Stable-Player Career and Game-Log Intelligence Summary

**Transfer-aware NCAA career seasons and count-first aggregates now share one stable player identity with active-season typed logs linked only to exact schedule-derived Games.**

## Performance

- **Duration:** 41 min
- **Started:** 2026-11-16T09:13:54Z
- **Completed:** 2026-11-16T09:54:48Z
- **Tasks:** 2
- **Files modified:** 11

## Accomplishments

- Captured sanitized representative D1/D2/D3 player-detail evidence through the existing secret-safe Browserless boundary, retaining all available career seasons and active-season typed logs without credentials or unrelated roster data.
- Added exact stable-player parsing, transfer/two-way preservation, bounded same-build request reuse, count-first career aggregates, and fail-closed completeness disclosure.
- Added snapshot-, tenant-, player-, source-, and Game-owned PostgreSQL persistence for career facts, aggregate membership, and typed game logs with immutable publication guards.
- Verified 150 focused player-history, migration, publication, and job tests plus the repository's full authoritative Phase 2.1 corrective release gate.

## Task Commits

Each task was committed atomically:

1. **Task 1: Add failing stable-player history and game-log fixtures** - `812db76` (test)
2. **Task 2: Parse and canonicalize career seasons, aggregates, and current-season logs** - `a90276c` (feat)

## Files Created/Modified

- `tests/fixtures/ncaa_detail/{d1,d2,d3}/player_detail.html` - Sanitized stable-player career and current-season log evidence.
- `tests/test_player_history.py` - D1/D2/D3, transfer, two-way, aggregate membership, exact Game linking, budget, and completeness contracts.
- `scripts/capture_phase3_ncaa_fixtures.py` - Fixed-host, secret-safe player-detail capture and sanitization.
- `roster_scout/ncaa_detail.py` - Validated player-detail parser, typed history contracts, count-first aggregates, and exact Game linking.
- `roster_scraper.py` - One-request-per-player/type planning with same-build reuse and bounded exhaustion.
- `roster_scout/canonical.py` - One-authoritative-player canonical history with separately typed facts and explicit completeness.
- `roster_scout/models.py` - Career-season, career-aggregate, and typed game-log snapshot persistence models.
- `migrations/versions/0025_detailed_ncaa_intelligence.py` - Reversible schema, ownership constraints, exact Game foreign keys, and immutable-child triggers.
- `tests/test_snapshot_persistence.py` - Immutable snapshot-child trigger coverage for all new persistence tables.

## Decisions Made

- Historical school/team/division evidence is never inferred from names. Rows lacking exact compatible identity remain visible evidence and receive precise exclusion reasons in aggregate lineage.
- Career aggregation removes source-derived rates and recomputable derived metrics, sums compatible counts, then delegates formulas to the shared metric registry.
- Player logs link only when one exact source contest ID matches the existing same-snapshot Game universe. Missing or duplicate contest IDs remain unmatched/ambiguous and cannot create another Game.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Handled NCAA doubleheader labels and malformed linked counts without guessing**
- **Found during:** Task 2 (player-detail parser GREEN phase)
- **Issue:** NCAA emitted doubleheader dates as `MM/DD/YYYY(1)` and some linked pitching cells as truncated values such as `1/`; strict parsing initially rejected otherwise valid fixtures.
- **Fix:** Accepted the source's optional doubleheader suffix while preserving the date, and treated truncated count text as unavailable instead of coercing it.
- **Files modified:** `roster_scout/ncaa_detail.py`
- **Verification:** All `CareerSeasonTests`, `CareerAggregateTests`, and `GameLogTests` passed across D1/D2/D3.
- **Committed in:** `a90276c`

**2. [Rule 1 - Bug] Updated immutable-child regression coverage for the five new tables**
- **Found during:** Task 2 full PostgreSQL regression suite
- **Issue:** PostgreSQL correctly installed publication guards, but the test's expected trigger-table manifest predated the career and game-log tables.
- **Fix:** Added career seasons, career aggregates, and all three typed game-log tables to the shared snapshot lock contract.
- **Files modified:** `tests/test_snapshot_persistence.py`
- **Verification:** Snapshot serialization coverage and all 150 focused persistence/publication tests passed.
- **Committed in:** `a90276c`

**3. [Rule 3 - Blocking] Made the shared unreleased migration downgrade an earlier Phase 3 shape safely**
- **Found during:** Task 2 migration lifecycle verification
- **Issue:** A disposable database already stamped at the Phase 3 shared revision lacked this plan's newly assembled tables, so the first downgrade could not remove them with unconditional DDL.
- **Fix:** Kept normal fresh-install creation while making only these newly assembled table drops conditional, allowing earlier Phase 3 disposable schemas to downgrade and re-upgrade cleanly.
- **Files modified:** `migrations/versions/0025_detailed_ncaa_intelligence.py`
- **Verification:** Full base-to-head downgrade/re-upgrade and Alembic model-drift checks passed.
- **Committed in:** `a90276c`

---

**Total deviations:** 3 auto-fixed (2 bugs, 1 blocking issue)
**Impact on plan:** All fixes preserve fail-closed identity and immutable-persistence behavior; no feature scope was added.

## Issues Encountered

- D3 player pages included aggregate game-log rows with a different cell shape. The capture sanitizer excluded those non-game totals so only date-keyed current-season evidence entered fixtures.
- The configured remote player-detail capture briefly returned a transient session failure; the existing bounded capture path recovered without exposing credentials or changing production state.

## User Setup Required

None - the existing configured Browserless runtime completed the approved fixture capture without exposing or changing secrets.

## Known Stubs

None.

## Next Phase Readiness

- Career seasons, aggregate lineage, and typed exact-Game-linked logs are ready for bounded live report collection in Plan 03-07 and downstream player drill-path presentation.
- Missing historical identities, idless players, malformed counts, unmatched games, and budget exhaustion remain explicit partial/unavailable data.
- No production deployment or production customer/database mutation was performed.

## Self-Check: PASSED

- Confirmed all 11 created/modified implementation, migration, capture, fixture, and test files exist.
- Confirmed task commits `812db76` and `a90276c` exist in git history.
- Confirmed no goal-blocking stubs or unplanned trust-boundary surfaces remain.

---
*Phase: 03-detailed-ncaa-intelligence*
*Completed: 2026-11-16*
