---
phase: 03-detailed-ncaa-intelligence
plan: "02"
subsystem: canonical-data
tags: [ncaa, fielding, postgresql, sqlalchemy, provenance, partial-publication]
requires:
  - phase: 03-detailed-ncaa-intelligence
    plan: "01"
    provides: Expanded NCAA season facts, versioned metrics, and Phase 3 migration foundation
  - phase: 01-canonical-snapshot-foundation
    provides: Tenant-owned immutable snapshots, source evidence, and publication validation
provides:
  - Fail-closed D1/D2/D3 NCAA fielding parser with fixed-host numeric-ID acquisition
  - Typed snapshot-owned fielding and catcher facts linked to exact source rows
  - Evidence-first fielding persistence with complete, unavailable, failed, and partial publication semantics
  - Exact fielding source/fact/row-count parity validation at immutable publication
affects: [03-03, 03-04, 03-05, 03-08, phase-4, phase-5]
tech-stack:
  added: []
  patterns:
    - Numeric category discovery gates fixed-host supplemental acquisition
    - Optional catcher values preserve sourced zero separately from missing
    - Supplemental fielding failure publishes partial without weakening batting/pitching core
key-files:
  created:
    - roster_scout/ncaa_detail.py
    - tests/test_ncaa_detail.py
    - tests/fixtures/ncaa_detail/d1/fielding.html
    - tests/fixtures/ncaa_detail/d2/fielding.html
    - tests/fixtures/ncaa_detail/d3/fielding.html
  modified:
    - roster_scraper.py
    - roster_scout/canonical.py
    - roster_scout/models.py
    - roster_scout/jobs.py
    - roster_scout/snapshots.py
    - migrations/versions/0025_detailed_ncaa_intelligence.py
    - tests/test_report_jobs.py
    - tests/test_snapshot_persistence.py
key-decisions:
  - "Request fielding only after the season lookup supplies a numeric category ID; legacy core-only metadata remains compatible."
  - "Keep PB, SBA, and CSB nullable and preserve zero only when the NCAA source explicitly supplies zero."
  - "Treat fielding as supplemental: unavailable or failed evidence publishes an explicit partial snapshot while batting and pitching remain critical."
patterns-established:
  - "Fielding parser contract: exact Player/G/GS/PO/A/E/TC/DP/FPCT headers, optional catcher headers, exact row shape, numeric validation, and stable player links."
  - "Fielding publication contract: exact source rows commit before facts, and complete sections require one same-snapshot fact per source row."
requirements-completed: [STAT-03, STAT-08]
duration: 30 min
completed: 2026-11-16
---

# Phase 3 Plan 2: NCAA Fielding Vertical Slice Summary

**D1/D2/D3 fielding and catcher evidence now reaches immutable tenant-owned snapshots through a fail-closed parser, exact source lineage, and truthful supplemental completeness.**

## Performance

- **Duration:** 30 min
- **Started:** 2026-11-16T05:55:13Z
- **Completed:** 2026-11-16T06:25:09Z
- **Tasks:** 3
- **Files modified:** 13

## Accomplishments

- Added sanitized representative D1, D2, and D3 fielding grids with one canonical G/GS/PO/A/E/TC/DP/FPCT contract and nullable PB/SBA/CSB values.
- Added fixed-host, numeric-ID NCAA fielding acquisition that rejects blocked pages, changed headers, malformed links, row-shape drift, negative counts, and invalid percentages before verified-empty classification.
- Added frozen canonical fielding facts and a composite-owned `FieldingFact` table with snapshot, player-team-season, and exact source identity constraints plus published-child immutability.
- Wired fielding through the durable report job after source evidence persistence and made publication validate exact source/fact/row parity.
- Preserved valid core publication: complete fielding produces `ready`; unavailable or failed fielding produces a sanitized `partial` snapshot without synthesizing zero rows.

## Task Commits

Each plan task was committed atomically:

1. **Task 1: Add failing D1/D2/D3 fielding source contracts** - `23fd3a3` (test)
2. **Task 2: Parse and canonicalize typed fielding evidence** - `1ab3670` (feat)
3. **Task 3: Publish fielding with truthful supplemental completeness** - `8238404` (feat)

Follow-up release-gate fix:

- `ceca772` - Preserve legacy core-only builds when season metadata has no discovered fielding category.

## Files Created/Modified

- `roster_scout/ncaa_detail.py` - Fixed-host fielding URL builder, fail-closed grid parser, typed canonical values, and shared collector adapter.
- `roster_scraper.py` - Discovered fielding-category acquisition through the existing NCAA/Browserless section seam.
- `roster_scout/canonical.py` - Stable-player fielding identity and typed nullable fielding/catcher values.
- `roster_scout/models.py` - Composite-owned `FieldingFact` model and named numeric constraints.
- `roster_scout/jobs.py` - Evidence-first fielding persistence, same-build cache validation, and explicit supplemental failure rows.
- `roster_scout/snapshots.py` - Complete fielding parity and same-snapshot provenance checks before publication.
- `migrations/versions/0025_detailed_ncaa_intelligence.py` - Additive fielding table, ownership constraints, numeric checks, and immutable-child trigger.
- `tests/test_ncaa_detail.py` - Parser, fixed URL, D1/D2/D3 parity, database ownership, ready publication, partial publication, and mismatch rejection contracts.
- `tests/test_report_jobs.py` - Fielding-capable recovery and real-collector fixtures.
- `tests/test_snapshot_persistence.py` - Fielding included in the shared immutable snapshot-child protocol.
- `tests/fixtures/ncaa_detail/{d1,d2,d3}/fielding.html` - Sanitized division fixtures.

## Decisions Made

- Used the existing season lookup's `fielding_id` as the discovery authority. Only numeric discovered IDs can construct an outbound `stats.ncaa.org` URL.
- Required exact relative `/players/<numeric-id>` links for fielding rows so typed facts reuse the authoritative NCAA player identity used by batting and pitching.
- Kept catcher fields nullable across divisions and accepted explicit zero without converting missing markers to zero.
- Reused the existing `ReportSection` vocabulary and made incomplete fielding evidence contradictory if it carries rows, verified-empty state, or typed facts.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Preserved legacy core-only build compatibility**
- **Found during:** Repository release verification after Task 3
- **Issue:** Builds using pre-fielding season metadata were treated as requested fielding failures, changing established ready snapshots to partial and changing the historical full-core collector call shape.
- **Fix:** Gated fielding acquisition on an explicitly discovered numeric `fielding_id` and retained the original batting/pitching call contract for legacy metadata.
- **Files modified:** `roster_scout/jobs.py`
- **Verification:** Focused refresh and Phase 1 happy-path tests passed; the 652-test discovery suite and Phase 2.1 corrective release gate passed.
- **Commit:** `ceca772`

**Total deviations:** 1 auto-fixed bug.
**Impact on plan:** The fix preserves backward compatibility without weakening new fielding collection; current NCAA season metadata includes fielding IDs and exercises the new path.

## Issues Encountered

- The disposable PostgreSQL database already carried revision 0025 from Plan 03-01, so the exact `roster_scout_test` database was recreated before validating the updated additive migration. No production or user data was touched.
- The root catalog command uses the project environment (`uv run`) because system Python does not contain the pinned runtime dependencies.

## Verification

- `uv run python -m unittest tests.test_ncaa_detail tests.test_canonical_data tests.test_report_jobs tests.test_snapshot_persistence -v` — 161 tests passed.
- `uv run python scripts/validate_phase21_release.py` with the documented disposable PostgreSQL environment — 652-test discovery, 83-test customer journey, and 87-test deployment groups passed; corrective release gate passed.
- Plan and repository Python compilation passed.
- NCAA catalog validation passed for 942 selectable 2026 teams across D1/D2/D3.
- `git diff --check` passed.

## Known Stubs

None.

## Next Phase Readiness

- Fielding is available as a typed supplemental season section for later splits, scoped statistics, history, workbook, and dashboard plans.
- Current season metadata already exposes numeric fielding category IDs; live upstream availability remains subject to the existing NCAA/Browserless boundary.

## Self-Check: PASSED

- All five created files exist.
- Task commits `23fd3a3`, `1ab3670`, `8238404`, and compatibility commit `ceca772` exist in git history.
- All task acceptance criteria and plan-level verification commands passed.

---
*Phase: 03-detailed-ncaa-intelligence*
*Completed: 2026-11-16*
