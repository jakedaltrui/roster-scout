---
phase: 03-detailed-ncaa-intelligence
plan: "08"
subsystem: database
tags: [postgresql, alembic, tenant-isolation, immutability, release-gate]

requires:
  - phase: 03-detailed-ncaa-intelligence
    provides: detailed NCAA metrics, scopes, history, game logs, bounded collection, and sanitized diagnostics
provides:
  - reversible revision 0025 with the complete Phase 3 schema and publication boundary
  - exact 0024-to-0025 preservation proof for published Phase 1/2 snapshots
  - PostgreSQL ownership, immutability, constant-set, and external-refresh lifecycle counterexamples
  - one fail-fast disposable-PostgreSQL Phase 3 release validator
affects: [phase-4-workbooks, phase-5-power-bi, database-migrations, release-operations]

tech-stack:
  added: []
  patterns:
    - validate destructive database targets as loopback PostgreSQL test databases before opening a connection
    - prove migration preservation with exact row, payload, checksum, and artifact projections
    - compose focused security gates before inherited and repository-wide regression gates

key-files:
  created:
    - scripts/validate_phase3_release.py
  modified:
    - migrations/versions/0025_detailed_ncaa_intelligence.py
    - roster_scout/models.py
    - roster_scout/snapshots.py
    - tests/test_phase3_release.py
    - CONTEXT.md

key-decisions:
  - "Revision 0025 remains the single additive and reversible Phase 3 boundary; verified reference constants and published snapshot children are protected in PostgreSQL."
  - "Requested supplemental families must materialize an explicit section status before publication, preserving partial and unavailable states instead of silently omitting work."
  - "The authoritative release gate accepts only reachable loopback postgresql+psycopg databases ending in _test with the explicit local-disposable marker, and it never deploys."

patterns-established:
  - "Exact migration preservation: compare published snapshot metadata, canonical rows, source payload checksums, and artifact digests across the upgrade."
  - "Release-gate ordering: migration head and lifecycle, schema drift, focused Phase 3 contracts, inherited security gate, full discovery, catalog, compilation, and diff hygiene."

requirements-completed: [STAT-08, OPS-03]

duration: 20min
completed: 2026-11-16
---

# Phase 3 Plan 8: Blocking Migration and Phase 3 Release Gate Summary

**Revision 0025 now preserves prior published evidence while enforcing Phase 3 tenancy and immutability, backed by one safe disposable-PostgreSQL gate covering all 742 repository tests.**

## Performance

- **Duration:** 20 min
- **Started:** 2026-11-16T10:41:27Z
- **Completed:** 2026-11-16T11:01:36Z
- **Tasks:** 3
- **Files modified:** 6

## Accomplishments

- Added exact 0024-to-0025 preservation and lifecycle tests covering upgrade, downgrade/re-upgrade, fresh head, D1/D2/D3 parity, every Phase 3 table family, published-child immutability, constant-set semantics, and external-refresh diagnostics.
- Finalized revision 0025 and matching ORM metadata with the complete metric constant type contract, verified-reference protection, reverse-order downgrade behavior, and explicit requested-supplemental publication status validation.
- Added a fail-fast release command that refuses unsafe database targets before destructive work, then proves migrations, drift, focused Phase 3 behavior, inherited Phase 2.1 security, full regression discovery, catalog validity, compilation, and diff hygiene.
- Passed the authoritative gate on the executor-owned loopback `roster_scout_test` database: 102 focused Phase 3 tests, all inherited gates, 742 full-discovery tests, the 942-team D1/D2/D3 catalog, compilation, and diff hygiene.

## Task Commits

Each task was committed atomically:

1. **Task 1: Add blocking PostgreSQL migration, ownership, and immutability counterexamples** - `151b950` (test)
2. **Task 2: Finalize and apply revision 0025 as a blocking schema gate** - `2d1bf79` (feat)
3. **Task 3: Run one authoritative Phase 3 release validator** - `4ba3e97` (test)

## Files Created/Modified

- `tests/test_phase3_release.py` - Exact migration-preservation fixtures, lifecycle gates, D1/D2/D3 parity, schema ownership/immutability inventory, constant-set constraints, and external-refresh lifecycle coverage.
- `migrations/versions/0025_detailed_ncaa_intelligence.py` - Final additive Phase 3 schema boundary, constant code/type coherence, trigger coverage, and reversible cleanup.
- `roster_scout/models.py` - ORM parity for the metric constant code/type check.
- `roster_scout/snapshots.py` - Publication rejection when a requested supplemental family lacks a materialized status.
- `scripts/validate_phase3_release.py` - Safe database preflight and ordered authoritative Phase 3 release manifest.
- `CONTEXT.md` - Canonical detail, player drill, diagnostics, and release-check change-impact map.

## Decisions Made

- Kept metric code/type coherence as a named database check while retaining the established composite metric-definition foreign key as the exact definition-substitution boundary.
- Required section-status materialization only for supplemental families that the durable request ledger records as requested; complete families retain their existing exact parity, trace, and membership checks.
- Included the authoritative Phase 2.1 validator inside the Phase 3 gate before a clean second full discovery so the new boundary cannot regress prior customer, tenant, deployment-contract, or security behavior.
- Treated the live NCAA smoke as an independent optional source-boundary probe; deterministic D1/D2/D3 fixtures remain the release authority when Akamai or browser access is unavailable.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Made downgrade tolerate an earlier disposable 0025 trigger shape**
- **Found during:** Task 2 disposable PostgreSQL lifecycle verification
- **Issue:** The executor-owned test database had exercised an earlier uncommitted shape of revision 0025 containing a constant-definition trigger/function that the finalized migration no longer installs.
- **Fix:** Added idempotent reverse-order cleanup for that trigger/function so the evolving unreleased revision can downgrade safely without affecting the finalized upgrade schema.
- **Files modified:** `migrations/versions/0025_detailed_ncaa_intelligence.py`
- **Verification:** `0025 -> 0024 -> head`, base-to-head, exact 0024 preservation, and Alembic drift checks all passed.
- **Committed in:** `2d1bf79`

---

**Total deviations:** 1 auto-fixed blocking migration-compatibility issue.
**Impact on plan:** The adjustment was limited to safe cleanup of an unreleased disposable-database shape; no production, deployment, dependency, or product scope was added.

## Issues Encountered

- An exploratory constant-definition trigger made an existing cross-definition substitution test fail at an earlier constraint than its established composite foreign-key boundary. The trigger was removed before commit; the final named code/type check plus composite foreign key preserve the intended semantics and all focused tests pass.
- One exploratory focused-test selector used the stale class name `SplitPersistenceTests`; it was corrected to the current `SplitProvenanceTests` and `SplitPublicationTests` selectors before the authoritative gate.
- Full discovery emitted pre-existing psycopg `ResourceWarning` notices for connections collected while open, but all 742 tests passed and no release assertion failed.

## User Setup Required

None - the repository-owned loopback disposable PostgreSQL bootstrap was sufficient. No production database, Fly service, cloud database, dependency installation, or deployment was touched.

## Known Stubs

None.

## Threat Flags

No unplanned threat surface. Revision 0025, publication validation, and the release subprocess/database boundary are the trust surfaces registered in T-03-08-01 through T-03-08-05 and are covered by exact preservation, composite ownership, published immutability, sanitized diagnostics, argument-array subprocesses, and fail-closed database target checks.

## Next Phase Readiness

- Phase 4 can build detailed workbook views from one immutable canonical snapshot with explicit partial/unavailable sections, exact formulas/constants, and protected player drill paths.
- Phase 5 can consume provider-neutral external-refresh diagnostics without mutating canonical snapshot content or weakening tenant ownership.
- Production remains unchanged. Launch and deployment remain separate, explicitly unauthorized work.

## Self-Check: PASSED

- Confirmed all six created or modified implementation, test, validator, and context files exist.
- Confirmed task commits `151b950`, `2d1bf79`, and `4ba3e97` exist in git history.
- Confirmed revision 0025 upgrade/current, exact 0024 preservation, downgrade/re-upgrade, fresh-head, and model-drift checks pass on the disposable loopback PostgreSQL database.
- Confirmed the authoritative Phase 3 gate passes 102 focused tests, inherited Phase 2.1 validation, 742 full-discovery tests, catalog validation, compilation, and diff hygiene.
- Confirmed no goal-blocking stubs, unexpected file deletions, untracked generated outputs, or unplanned trust-boundary surfaces were introduced.

---
*Phase: 03-detailed-ncaa-intelligence*
*Completed: 2026-11-16*
