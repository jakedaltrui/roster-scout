---
phase: 01-canonical-snapshot-foundation
plan: "03"
subsystem: canonical-data
tags: [ncaa, provenance, postgresql, identity-resolution, immutable-snapshots, xlsx]

requires:
  - phase: 01-canonical-snapshot-foundation
    provides: Immutable snapshot schema, atomic publication, durable jobs, and same-build cache identity
provides:
  - Provenance-rich structured NCAA D1/D2/D3 collection with compatibility wrappers
  - Deterministic identity resolution, nullable typed facts, innings outs, and metric lineage
  - Live durable collection with same-job evidence recovery and canonical PostgreSQL publication
  - Full selector, scraper, cache, capacity, branding, and two-sheet workbook regression evidence
affects: [report-library, canonical-analytics, excel, power-bi, ncaa-collector]

tech-stack:
  added: []
  patterns: [structured compatibility adapter, conservative team-season identity, source-before-facts publication]

key-files:
  created:
    - roster_scout/canonical.py
    - tests/test_canonical_data.py
    - tests/test_canonical_publish.py
  modified:
    - roster_scraper.py
    - roster_scout/jobs.py
    - roster_scout/tasks.py
    - tests/test_ncaa_workflow.py
    - tests/test_app_routes.py
    - DEPLOYMENT.md

key-decisions:
  - "The legacy tuple-returning scraper remains the workbook boundary while durable jobs use a live-only structured collection API."
  - "ID-less fallback identities are deterministic and scoped to one team-season; only unique reciprocal batting/pitching matches merge."
  - "Exact source rows commit before typed facts and publication so worker redelivery can reuse only same-job, version-matched evidence."

patterns-established:
  - "Structured compatibility adapter: richer NCAA metadata is added beside, not instead of, the established tuple API."
  - "Evidence-first publication: source rows survive a worker loss before canonical facts become customer-visible."
  - "Metric lineage: source-provided, calculated, and unavailable values carry versioned definitions."

requirements-completed:
  - SNAP-01
  - SNAP-02
  - SNAP-03
  - SNAP-04
  - DATA-01
  - DATA-02
  - DATA-03
  - DATA-05
  - DATA-06
  - DATA-07
  - DATA-08
  - DATA-10
  - OPS-01
  - OPS-02
  - OPS-05

duration: 6 min
completed: 2026-11-01
---

# Phase 1 Plan 3: Canonical NCAA Collection and Publication Summary

**Live D1/D2/D3 NCAA collection now publishes immutable typed facts with exact row provenance, conservative player identity, and an unchanged branded workbook path**

## Performance

- **Duration:** 6 min
- **Started:** 2026-11-01T23:16:15Z
- **Completed:** 2026-11-01T23:22:01Z
- **Tasks:** 3
- **Files modified:** 9

## Accomplishments

- Added structured team, season, section, source-row, and collection metadata while preserving `scrape_ncaa_by_id()` and `scrape_ncaa_by_name()` tuple behavior.
- Preserved NCAA player IDs and full player/source URLs through parsing, legacy normalization, exact source storage, canonical identities, typed facts, and published reads.
- Implemented nullable numeric conversion, baseball innings-to-outs conversion, versioned source/calculated metric lineage, and deterministic D-12 through D-15 identity handling.
- Published representative D1, D2, and D3 fixtures through PostgreSQL with one canonical schema, exact source traceability, two-way linkage, verified-empty handling, and duplicate-safe replay.
- Proved the existing selector, successful-result cache, error behavior, Browserless capacity response, branding, count headers, and two-sheet workbook remain unchanged.

## Task Commits

Each task was committed atomically:

1. **Task 1: Preserve NCAA identity and source evidence through normalization** - `247a0ee` (feat)
2. **Task 2: Publish representative D1, D2, and D3 NCAA snapshots** - `54aff7d` (feat)
3. **Task 3: Prove legacy behavior and operational checks remain intact** - `8df1115` (test)

## Files Created/Modified

- `roster_scraper.py` - Structured live NCAA collection and backward-compatible identity/source metadata.
- `roster_scout/canonical.py` - Deterministic source-to-identity/fact/metric transformation.
- `roster_scout/jobs.py` - Same-job evidence recovery, canonical persistence, and source-before-publication boundary.
- `roster_scout/tasks.py` - Real opaque-job NCAA collector wiring for Celery workers.
- `tests/test_canonical_data.py` - Missing values, innings, identity, provenance, and metric-lineage unit coverage.
- `tests/test_canonical_publish.py` - PostgreSQL-backed D1/D2/D3 publication, failure boundary, replay, and refresh coverage.
- `tests/test_ncaa_workflow.py` - Structured collector and compatibility identity/source assertions.
- `tests/test_app_routes.py` - Count-header, uniform division failure, and no-job-route regressions.
- `DEPLOYMENT.md` - Complete deterministic, migration, catalog, worker-capacity, and optional live-smoke release gate.

## Decisions Made

- Kept the public synchronous scraper and workbook API intact; durable report builds call a separate `collect_ncaa_by_id(..., force_refresh=True)` path.
- Used authoritative NCAA IDs whenever present and deterministic team-season-scoped fallback keys only for unique consistent ID-less evidence.
- Committed exact source rows in a transaction before canonical facts and final publication, enabling safe same-job recovery without exposing a half-ready snapshot.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing Critical] Replaced the worker collector stub**
- **Found during:** Task 2 (Publish representative D1, D2, and D3 NCAA snapshots)
- **Issue:** Plan 01-02 intentionally left `roster_scout/tasks.py::collect_report_sections` as a failing stub, but `tasks.py` was omitted from Plan 01-03's declared file list even though durable live collection could not operate without wiring it.
- **Fix:** Routed the opaque-job Celery entrypoint through the canonical NCAA collector with `force_refresh=True` and same-job cache validation.
- **Files modified:** `roster_scout/tasks.py`, `roster_scout/jobs.py`
- **Verification:** PostgreSQL publication tests and the real Celery eager-entry test passed in the 92-test suite.
- **Committed in:** `54aff7d`

---

**Total deviations:** 1 auto-fixed (1 missing critical)
**Impact on plan:** The additional file change closes the explicitly documented Plan 01-02 seam and is required for the planned worker behavior; no public route or product scope was added.

## Issues Encountered

- The filesystem sandbox blocked `.git` writes and local PostgreSQL sockets; scoped approved execution was used for atomic commits and the disposable `roster_scout_test` integration database.

## User Setup Required

None for local development. Production still requires the documented managed PostgreSQL, managed Redis, Browserless, billing, and one-worker-machine provisioning gate.

## Verification Results

- Canonical data and NCAA workflow target: 36 tests passed.
- Canonical publication and durable job target: 19 tests passed against PostgreSQL.
- Full PostgreSQL-enabled deterministic discovery: 92 tests passed.
- Alembic drift check: no new upgrade operations detected.
- NCAA catalog: valid with all 942 current-season teams selectable (308 D1, 253 D2, 381 D3).
- Python compilation, required template existence, worker concurrency assertion, Docker Compose validation, and `git diff --check`: passed.

## Known Stubs

None. The Plan 01-02 collector stub is now wired to the live structured NCAA path.

## Next Phase Readiness

- Phase 1 is complete: immutable snapshot persistence, durable job delivery, and real D1/D2/D3 canonical publication are integrated.
- Phase 2 can add authentication, organization membership, report-library routes, and refresh controls without changing the acquisition or publication contracts.
- Production deployment remains gated on uninterrupted managed services and one successful target-network NCAA smoke.

## Self-Check: PASSED

- All nine key implementation/test/documentation files exist.
- Task commits `247a0ee`, `54aff7d`, and `8df1115` exist in git history.
- All task acceptance criteria and plan-level verification commands passed.

---
*Phase: 01-canonical-snapshot-foundation*
*Completed: 2026-11-01*
