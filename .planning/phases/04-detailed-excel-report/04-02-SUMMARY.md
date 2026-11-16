---
phase: 04-detailed-excel-report
plan: "02"
subsystem: artifacts
tags: [canonical-snapshot, metrics, scopes, immutable-artifacts, xlsx]

requires:
  - phase: 04-detailed-excel-report
    provides: Detailed two-sheet grouped renderer
provides:
  - Exact canonical snapshot projection for season, derived, Last 10, and conference columns
  - Versioned detailed workbook artifact keys with historical key compatibility
  - Published/source timestamps and snapshot state in saved-workbook branding
affects: [report-downloads, google-sheets, power-bi, historical-snapshots]

key-files:
  created: []
  modified:
    - roster_scout/artifacts.py
    - roster_scout/workbooks.py
    - tests/test_artifacts.py
    - tests/test_report_jobs.py

key-decisions:
  - "Full-season and scoped rates use the shared Decimal metric registry; persisted available MetricValue rows take precedence."
  - "Partial, unavailable, and failed scopes expose state without zero-filling player statistics."
  - "New workbooks use detailed-two-sheet-xlsx-v2 while basic-xlsx-v1 keys remain valid for exact historical reads."

requirements-completed: [XLSX-02, XLSX-03, XLSX-04, XLSX-05, XLSX-06]
completed: 2026-11-16
---

# Phase 4 Plan 2: Canonical Snapshot Workbook Projection Summary

**New saved workbooks are deterministic projections of one immutable snapshot, including auditable player identity, central derived metrics, scoped samples, and explicit scope availability.**

## Accomplishments

- Loaded player identities, source evidence, batting/pitching facts, metric values, Last 10/conference scopes, and scoped facts using the same snapshot and organization boundary.
- Projected full-season counts and centrally calculated AVG, OBP, SLG, OPS, ISO, BB%, K%, BABIP, wOBA, ERA, WHIP, K/9, BB/9, H/9, HR/9, K/BB, BABIP allowed, and FIP.
- Reused persisted verified metric values where present and left constant-dependent metrics blank when verified NCAA constants are unavailable.
- Projected complete scoped counts/rates through the same metric registry; partial, unavailable, and failed scopes retain their state without fabricated zeroes.
- Added stable NCAA player IDs, exact player/source URLs, season year, publish time, source collection time, and snapshot status to the saved workbook contract.
- Bumped new artifact keys to `detailed-two-sheet-xlsx-v2` while preserving validation and exact reads for `basic-xlsx-v1` history.

## Task Commits

1. **RED: Lock canonical workbook projection** - `97165b8`
2. **GREEN: Project canonical snapshots into detailed workbooks** - `71c4178`

## Verification Results

- Artifact and snapshot-persistence suites: 91 passed against disposable PostgreSQL.
- Artifact unit/contract suite: 12 passed.
- Report artifact schema regression: passed.
- Alembic drift check: no new upgrade operations detected.
- Python compilation and `git diff --check`: passed.

## Next Plan Readiness

- Plan 04-03 can now generate branded D1/D2/D3 release samples, inspect both sheets visually, and run the end-to-end regression gate.
