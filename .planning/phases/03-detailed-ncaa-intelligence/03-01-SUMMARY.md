---
phase: 03-detailed-ncaa-intelligence
plan: "01"
subsystem: canonical-data
tags: [ncaa, decimal, postgresql, sqlalchemy, metric-lineage]
requires:
  - phase: 01-canonical-snapshot-foundation
    provides: Immutable tenant-scoped canonical snapshots and source provenance
  - phase: 02-authenticated-team-workspace
    provides: Durable report jobs and published report lifecycle
provides:
  - Expanded NCAA batting and pitching season counting facts
  - One versioned Decimal metric registry with explicit availability states
  - Verified season/division constant-set storage for wOBA and FIP
  - Published metric inputs, samples, formula versions, statuses, and constant lineage
affects: [03-02, 03-03, 03-04, phase-4, phase-5]
tech-stack:
  added: []
  patterns:
    - Tagged metric results with exact Decimal arithmetic
    - Count-first aggregation with zero-versus-missing preservation
    - Verified global constants scoped by metric, season, and division
    - Migration-owned immutable-row guard suspension for bounded backfills
key-files:
  created:
    - roster_scout/metrics.py
    - migrations/versions/0025_detailed_ncaa_intelligence.py
    - scripts/prepare_phase3_test_database.py
    - tests/test_detailed_stats.py
  modified:
    - roster_scout/enums.py
    - roster_scout/models.py
    - roster_scout/canonical.py
    - roster_scout/jobs.py
    - roster_scout/snapshots.py
    - tests/test_report_jobs.py
key-decisions:
  - "Keep legacy AVG/ERA publication beside the new detailed metric stream so existing consumers do not regress."
  - "Treat wOBA and FIP as unavailable until one verified NCAA constant set exactly matches definition, season, and division."
  - "Store constants globally without organization ownership, while retaining tenant isolation on every snapshot metric value."
  - "Serialize constant references before accepting a metric value so a referenced verified methodology is immutable."
patterns-established:
  - "Metric contract: value, status, reason, inputs, sample, definition version, and optional constant version travel together."
  - "Publication contract: durable NCAA builds must contain the complete detailed metric matrix before a snapshot can publish."
requirements-completed: [STAT-01, STAT-02, STAT-04, STAT-05, STAT-06, STAT-07]
duration: 45 min
completed: 2026-11-16
---

# Phase 3 Plan 1: Auditable Detailed Season Metrics Summary

**Exact Decimal season metrics now publish with recomputable inputs, sample context, explicit availability, versioned formulas, and verified NCAA constant provenance.**

## Performance

- **Duration:** 45 min
- **Started:** 2026-11-16T05:06:15Z
- **Completed:** 2026-11-16T05:50:51Z
- **Tasks:** 3
- **Files modified:** 10

## Accomplishments

- Added a single 23-metric registry covering detailed batting and pitching rates, derived counts, and constant-dependent wOBA/FIP without substituting MLB constants.
- Expanded canonical batting and pitching facts while preserving exact integer counts, innings as outs, and distinct zero, missing, undefined, incompatible, and unavailable-constant states.
- Added additive PostgreSQL schema for metric lineage and verified constants, including append-only publication and constant-reference serialization guarantees.
- Wired durable report jobs to persist complete detailed season metrics and made snapshot publication reject missing formulas, inputs, samples, statuses, or incorrectly scoped constants.
- Preserved legacy `AVG` and `ERA` metric definitions and values alongside the detailed contract.

## Task Commits

Each plan task was committed atomically:

1. **Task 1: Lock golden metric and database-preflight contracts** - `ff96767` (test)
2. **Task 2: Add expanded facts and the versioned Decimal metric engine** - `1822dd2` (feat)
3. **Task 3: Persist and validate detailed metric lineage** - `d2d35f7` (feat)

Follow-up release-gate fixes:

- `9e0bcd6` - Preserve published metric downgrade compatibility
- `e8ef2c7` - Retain legacy metric publication
- `84784cc` - Migrate published metric lineage safely

## Files Created/Modified

- `roster_scout/metrics.py` - Formula registry, exact calculation, count aggregation, and tagged metric results.
- `roster_scout/canonical.py` - Expanded NCAA count normalization and detailed metric generation.
- `roster_scout/models.py` - Detailed fact columns, metric lineage, and constant-set models.
- `roster_scout/enums.py` - Explicit metric availability statuses.
- `roster_scout/jobs.py` - Detailed and legacy metric persistence with verified constant selection.
- `roster_scout/snapshots.py` - Complete detailed-lineage validation before publication.
- `migrations/versions/0025_detailed_ncaa_intelligence.py` - Additive, reversible Phase 3 schema and database guards.
- `scripts/prepare_phase3_test_database.py` - Fail-closed disposable PostgreSQL preflight.
- `tests/test_detailed_stats.py` - Golden formulas, status semantics, lineage, constant, and migration contracts.
- `tests/test_report_jobs.py` - End-to-end persistence, publication, isolation, and immutability coverage.

## Decisions Made

- Preserved the legacy metric stream as a compatibility layer while making `ncaa-detailed-1.0` the auditable detailed contract.
- Kept verified constant sets global because the methodology is shared, but required exact definition, season, division, verification, and collection provenance before use.
- Seeded no wOBA or FIP constants; both metrics remain explanatory unavailable values until verified NCAA data is supplied.
- Limited the publication completeness gate to durable schema-versioned builds so historical direct fixtures remain readable without weakening new report builds.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing Critical Functionality] Serialized referenced constant sets**
- **Found during:** Task 3
- **Issue:** Append-only checks alone did not close the race between a constant mutation and the first metric reference.
- **Fix:** Added a PostgreSQL row-lock trigger on metric constant references and verified referenced constants reject mutation.
- **Files modified:** `migrations/versions/0025_detailed_ncaa_intelligence.py`, `tests/test_report_jobs.py`
- **Commit:** `d2d35f7`

**2. [Rule 1 - Bug] Made downgrade backfills compatible with published rows**
- **Found during:** Repository release verification
- **Issue:** Restoring the legacy unavailable reason during downgrade was blocked by the published-row immutability trigger.
- **Fix:** Suspended only the metric-value trigger for the bounded migration backfill and restored it immediately afterward.
- **Files modified:** `migrations/versions/0025_detailed_ncaa_intelligence.py`, `tests/test_detailed_stats.py`
- **Commit:** `9e0bcd6`

**3. [Rule 1 - Bug] Restored legacy metric publication**
- **Found during:** Repository release verification
- **Issue:** The detailed persistence path stopped emitting the existing uppercase `AVG` and `ERA` definitions and values.
- **Fix:** Persisted legacy rows alongside detailed rows using the new status and lineage constraints.
- **Files modified:** `roster_scout/jobs.py`, `tests/test_report_jobs.py`
- **Commit:** `e8ef2c7`

**4. [Rule 1 - Bug] Made upgrade backfills compatible with published rows**
- **Found during:** Repository release verification
- **Issue:** Upgrading an older database with published metric rows could not populate new lineage columns through the immutable-row trigger.
- **Fix:** Applied the same bounded trigger suspension to the additive upgrade backfill and locked both directions with regression coverage.
- **Files modified:** `migrations/versions/0025_detailed_ncaa_intelligence.py`, `tests/test_detailed_stats.py`
- **Commit:** `84784cc`

## Issues Encountered

- The authoritative release gate exposed migration paths and legacy concurrency contracts that the focused Phase 3 suite did not exercise. All discovered compatibility issues were fixed and the complete gate was rerun successfully.

## User Setup Required

None. The existing loopback `roster_scout_test` PostgreSQL service passed the fail-closed disposable-database preflight. Production wOBA/FIP constants intentionally remain empty until verified NCAA methodology is available.

## Verification

- `uv run python -m unittest tests.test_detailed_stats tests.test_canonical_data -v` - passed.
- `uv run python -m unittest tests.test_report_jobs.FullSeasonMetricPublicationTests tests.test_detailed_stats tests.test_canonical_data -v` - passed.
- `uv run python -m unittest tests.test_report_jobs -v` - passed (53 tests).
- `uv run python -m unittest tests.test_snapshot_persistence -v` - passed (76 tests).
- `uv run python scripts/validate_phase21_release.py` with the guarded disposable PostgreSQL environment - passed, including 636 clean-process tests and 87 deployment/security tests.
- Python compilation, NCAA catalog validation, Alembic head/drift checks, and `git diff --check` - passed.

## Next Phase Readiness

- Split and recent-form scopes can reuse the same count-first registry and tagged metric result contract without redefining formulas.
- Excel and Power BI consumers can distinguish available zeroes from missing, undefined, incompatible, and unavailable-constant values.
- No blockers remain for Plan 03-02.

## Self-Check: PASSED

- All created artifacts exist.
- All six implementation and release-gate fix commits are present.
- The final authoritative release gate completed successfully.
