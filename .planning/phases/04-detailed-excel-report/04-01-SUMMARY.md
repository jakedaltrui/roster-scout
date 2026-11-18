---
phase: 04-detailed-excel-report
plan: "01"
subsystem: workbook
tags: [xlsx, xlsxwriter, grouped-columns, ncaa, auditability]

requires:
  - phase: 01-canonical-snapshot-foundation
    provides: Shared workbook renderer and preserved NCAA player identity
provides:
  - Detailed workbook renderer with exactly Hitters and Pitchers
  - Player, Season, Derived Metrics, Last 10 Games, and Conference Games groups
  - Stable NCAA player ID and source URL retention through roster splitting
affects: [artifacts, reports, google-sheets, power-bi]

key-files:
  created: []
  modified:
    - roster_scout/workbooks.py
    - roster_scraper.py
    - tests/test_artifacts.py
    - tests/test_app_routes.py

key-decisions:
  - "Retain the established school-branded layout and add one tinted group band above the existing dark detail header."
  - "Freeze the six Player columns and all four header rows so identity remains visible while scrolling the expanded stat set."
  - "Use unique L10 and CONF detail labels so the native Excel table and filters remain valid."

requirements-completed: [XLSX-01, XLSX-02, XLSX-03]
completed: 2026-11-16
---

# Phase 4 Plan 1: Shared Detailed Workbook Renderer Summary

**The production renderer now creates the approved two-sheet workbook with five grouped column bands while preserving the original Roster Scout Excel visual system.**

## Accomplishments

- Extended Hitters and Pitchers with Player, Season, Derived Metrics, Last 10 Games, and Conference Games column groups without adding worksheets or lower-page sections.
- Preserved logo lockup, school-derived palette, Arial typography, compact alternating rows, native Excel tables, filters, 90% zoom, landscape printing, repeated headers, and footers.
- Frozen the Player identity block and header stack for practical horizontal navigation.
- Kept numeric cells typed and formatted while leaving unavailable scope values blank and labeling their status `Unavailable`.
- Retained `_player_id` and `_player_url` on both sides of two-way-player classification.
- Preserved formula/URL string safety by continuing to disable automatic formula and hyperlink conversion.

## Task Commits

1. **RED: Lock detailed two-sheet workbook contract** - `ebe362b`
2. **GREEN: Extend shared two-sheet workbook renderer** - `9f51b87`

## Deviations from Plan

- The focused contract tests were added to the clean `tests/test_artifacts.py` module instead of expanding the already user-modified route test module. The existing route visual regression was updated only where its row and table references changed.
- Last 10 and conference detail headers use short `L10` and `CONF` prefixes. This keeps every Excel table column name unique while the group band supplies the full context.

## Verification Results

- `ArtifactContractTests`: 3 passed.
- `AppRouteTests`: 23 passed.
- Python compilation for the renderer and scraper passed.
- `git diff --check` passed.

## Next Plan Readiness

- Plan 04-02 can project canonical season facts, central derived metrics, and scoped Last 10/conference facts into this renderer without changing the workbook layout.
