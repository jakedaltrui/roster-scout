---
phase: 03-detailed-ncaa-intelligence
plan: "04"
subsystem: ncaa-data
tags: [ncaa, split-stats, postgresql, sqlalchemy, provenance]
requires:
  - phase: 03-detailed-ncaa-intelligence
    plan: "03"
    provides: Certified D1/D2/D3 split controls and sanitized batting/pitching result fixtures
provides:
  - Seven canonical batting and seven canonical pitching scope families per snapshot
  - Typed scoped facts with exact player, source, tenant, snapshot, season, and sample lineage
  - Truthful complete, partial, unavailable, and failed split publication without synthetic zeros
  - Live Browserless split discovery and exact same-build cache identity
affects: [03-05, 03-08, phase-04-workbook, player-detail, ncaa-splits]
tech-stack:
  added: []
  patterns:
    - Requested supplemental scopes are materialized even when their source is unavailable
    - Typed split facts share the metric registry while retaining source-specific sample context
    - Publication validates scope, source, fact, and metric parity before freezing a snapshot
key-files:
  created: []
  modified:
    - migrations/versions/0025_detailed_ncaa_intelligence.py
    - roster_scout/models.py
    - roster_scout/canonical.py
    - roster_scout/ncaa_detail.py
    - roster_scout/jobs.py
    - roster_scout/snapshots.py
    - roster_scraper.py
    - tests/test_ncaa_detail.py
    - tests/test_snapshot_persistence.py
key-decisions:
  - "Normalize source labels to fourteen stable typed codes while discovering every numeric NCAA identifier from the requested season/type controls."
  - "Persist unavailable and failed requested scopes with safe reasons and no facts or metric zeros."
  - "Run available split values through the shared metric registry and bind the resulting metrics to the same scope, player, source, tenant, and snapshot."
patterns-established:
  - "Split identity: job + season + stat type + canonical code + discovered source identifier + parser/schema version defines reusable same-build evidence."
  - "Split publication: all seven scopes per present family must agree with source, typed-fact, and metric row counts before publication."
requirements-completed: [SPLT-01, SPLT-02, SPLT-03, SPLT-04, SPLT-05, HIST-06]
duration: 21m
completed: 2026-11-16
---

# Phase 3 Plan 4: Auditable Batting and Pitching Splits Summary

**Immutable NCAA snapshots now carry all seven batting and seven pitching split families with discovered source identity, typed facts, shared metrics, and explicit unavailable or failed states instead of invented zeros.**

## Performance

- **Duration:** 21m
- **Started:** 2026-11-16T07:52:43Z
- **Completed:** 2026-11-16T08:13:37Z
- **Tasks:** 3
- **Files modified:** 9

## Accomplishments

- Added snapshot-owned `StatisticScope`, `ScopedBattingFact`, and `ScopedPitchingFact` persistence with composite ownership, nonnegative value checks, source/player lineage, and published-row immutability.
- Derived and validated all numeric source identifiers from certified season/type controls, then normalized live NCAA presentation rows without promoting fixture identifiers into production constants.
- Materialized all seven batting and seven pitching requests with exact status and reason, publishing mixed availability as partial while keeping unavailable scopes free of facts and synthetic metric zeros.
- Routed available split averages through the shared metric registry with exact scope and sample context, preserving one authoritative player for batting and pitching evidence.
- Extended collector caching and publication checks so exact same-build evidence may be reused but stale source/parser/schema identities cannot substitute.

## Task Commits

Each task was committed atomically:

1. **Task 1: Complete failing split behavior and persistence contracts** - `67ad442` (test)
2. **Task 2: Implement discovered split parsing and canonical scoped facts** - `bcc9889` (feat)
3. **Task 3: Persist split families before atomic partial/ready publication** - `3a83790` (feat)

## Files Created/Modified

- `migrations/versions/0025_detailed_ncaa_intelligence.py` - Scope/fact tables, metric scope ownership, constraints, indexes, and immutable publication triggers.
- `roster_scout/models.py` - SQLAlchemy models for requested scopes and separately typed batting/pitching facts.
- `roster_scout/canonical.py` - Canonical scoped fact normalization with exact null and sample preservation.
- `roster_scout/ncaa_detail.py` - Certified and live split discovery, response validation, result parsing, and stable code mapping.
- `roster_scout/jobs.py` - Supplemental family collection, exact cache signatures, scope/source persistence, player linkage, and metric creation.
- `roster_scout/snapshots.py` - Pre-publication family completeness and scope/source/fact/metric parity validation.
- `roster_scraper.py` - Managed Browserless interaction for current split controls and results.
- `tests/test_ncaa_detail.py` - Parser, provenance, availability, publication, ownership, and immutability contracts.
- `tests/test_snapshot_persistence.py` - Trigger coverage for the new snapshot-owned tables.

## Decisions Made

- Canonicalized NCAA's runners-in-scoring-position labels to `risp` while retaining the exact source label and numeric source identifier as evidence.
- Limited live interactive collection to the existing managed Browserless provider so no unmanaged browser path bypasses capacity, challenge, or terms controls.
- Treated complete split evidence as cacheable only within the same job and exact season/type/code/source/parser/schema identity; explicit refresh creates a new build and performs live discovery.
- Required all seven scopes once a split family is present, but kept the families supplemental so absent or failed source data produces a truthful partial snapshot instead of blocking the core report.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Flushed authoritative source-player linkage before inserting scoped facts**
- **Found during:** Task 3 (persist split families before publication)
- **Issue:** SQLAlchemy could order a scoped fact insert before the in-memory source row's authoritative player assignment, causing the composite source/player foreign key to reject valid evidence.
- **Fix:** Flushed the source linkage before inserting its typed split fact.
- **Files modified:** `roster_scout/jobs.py`
- **Verification:** Fully available and mixed-availability publication tests pass, including ownership and cross-snapshot rejection.
- **Committed in:** `3a83790`

**Total deviations:** 1 auto-fixed bug.
**Impact on plan:** The fix preserves the planned ownership contract and adds no scope.

## Issues Encountered

- The new migration was replayed from revision `0024`, re-upgraded to `0025`, and checked against the SQLAlchemy metadata after the scoped schema was added. No schema drift remains.

## Verification

- `uv run python -m unittest tests.test_ncaa_detail.SplitDiscoveryTests tests.test_ncaa_detail.BattingSplitTests tests.test_ncaa_detail.PitchingSplitTests tests.test_ncaa_detail.SplitProvenanceTests tests.test_ncaa_detail.SplitAvailabilityTests tests.test_ncaa_detail.SplitPublicationTests -v` — 21 tests passed.
- `uv run python -m unittest tests.test_ncaa_detail tests.test_report_jobs tests.test_snapshot_persistence -v` — 173 tests passed.
- `uv run alembic downgrade 0024 && uv run alembic upgrade head && uv run alembic check` — migration lifecycle passed; no new upgrade operations detected.
- `uv run python -m py_compile roster_scout/ncaa_detail.py roster_scout/canonical.py roster_scout/jobs.py roster_scout/snapshots.py roster_scraper.py` — passed.
- `git diff --check` — passed.

## Known Stubs

None.

## User Setup Required

None - the implementation reuses the existing managed Browserless and PostgreSQL configuration.

## Next Phase Readiness

- Later workbook and player-detail plans can query stable split codes and distinguish sourced zero from unavailable evidence.
- Snapshot publication now guarantees frozen split lineage and count parity for any present family.
- No blockers remain for the next Detailed NCAA Intelligence plan.

## Self-Check: PASSED

- All nine source, migration, and test files modified by this plan exist.
- Task commits `67ad442`, `bcc9889`, and `3a83790` exist in git history.
- All task acceptance criteria, migration checks, focused tests, and the 173-test regression suite passed.

---
*Phase: 03-detailed-ncaa-intelligence*
*Completed: 2026-11-16*
