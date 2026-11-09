---
phase: 01-canonical-snapshot-foundation
plan: "05"
subsystem: database
tags: [postgresql, sqlalchemy, concurrency, canonical-publication, provenance]

requires:
  - phase: 01-canonical-snapshot-foundation
    provides: Canonical NCAA persistence, immutable snapshots, and integrity-hardened migration history through revision 0003
provides:
  - Conflict-safe PostgreSQL creation of shared authoritative players and metric definitions
  - Stored NCAA identity evidence validation before authoritative player reuse
  - Exact source-to-identity-to-fact validation before snapshot publication
  - Reversible non-null ownership links for batting and pitching facts
affects: [canonical-publication, durable-jobs, player-identity, metric-lineage, excel, power-bi]

tech-stack:
  added: []
  patterns: [postgresql-on-conflict-reselect, material-identity-validation, aggregate-publication-gate]

key-files:
  created:
    - migrations/versions/0004_publication_traceability.py
  modified:
    - roster_scout/canonical.py
    - roster_scout/jobs.py
    - roster_scout/models.py
    - roster_scout/snapshots.py
    - tests/test_canonical_publish.py
    - tests/test_snapshot_persistence.py

key-decisions:
  - "Authoritative NCAA players and versioned metric definitions resolve through PostgreSQL conflict-safe inserts followed by scoped reselects."
  - "Snapshot readiness is derived from exact section source counts and uniquely owned typed-fact links, never section flags alone."

patterns-established:
  - "Shared global rows: INSERT ON CONFLICT DO NOTHING RETURNING, then scoped reselect when another transaction won."
  - "Publication traceability: each critical row must have one same-snapshot source record, identity, and typed fact before status changes."

requirements-completed:
  - SNAP-03
  - SNAP-04
  - DATA-01
  - DATA-03
  - DATA-07
  - DATA-09
  - DATA-10

duration: 14h 8m
completed: 2026-11-09
---

# Phase 1 Plan 5: Concurrency-Safe Canonical Publication Summary

**PostgreSQL upserts now converge shared player and metric identities while atomic publication proves every declared critical row has same-snapshot source, identity, and typed-fact ownership**

## Performance

- **Duration:** 14h 8m
- **Started:** 2026-11-09T05:56:55Z
- **Completed:** 2026-11-09T20:05:39Z
- **Tasks:** 2
- **Files modified:** 7

## Accomplishments

- Replaced race-prone player and metric-definition check-then-insert logic with PostgreSQL `ON CONFLICT DO NOTHING RETURNING` and scoped reselects.
- Validated stored authoritative NCAA identities through the canonical material-name normalizer, allowing case/whitespace equivalence while failing unexplained name conflicts and retaining exact incoming evidence.
- Added barrier-coordinated two-session tests proving shared player, AVG, and ERA rows converge without `IntegrityError` or unexpected job failure.
- Made batting and pitching identity/source ownership links non-null through reversible revision 0004 after validating existing rows.
- Required exact section, source, and typed-fact counts plus unique same-snapshot identity/source joins before the ready/partial transition.
- Added negative publication regressions for flags-only state, count mismatch, missing evidence, duplicate mappings, null links, and cross-snapshot ownership while preserving verified-empty and D1/D2/D3 publication.

## Task Commits

Each TDD task was committed through separate RED and GREEN gates, followed by one compatibility-fix commit:

1. **Task 1 RED: Expose shared-record and identity races** - `2782f73` (test)
2. **Task 1 GREEN: Make shared canonical identities conflict-safe** - `0060c50` (feat)
3. **Task 2 RED: Expose publication traceability gaps** - `6b9a5ab` (test)
4. **Task 2 GREEN: Enforce canonical publication traceability** - `c47a0c4` (feat)
5. **Rule 3: Align legacy persistence fixtures with revision 0004** - `2ad0da5` (test)

## Files Created/Modified

- `migrations/versions/0004_publication_traceability.py` - Validates existing fact ownership, tightens four required links, and reversibly restores nullable columns on downgrade.
- `roster_scout/canonical.py` - Exposes the canonical material-name normalization contract for stored identity comparison.
- `roster_scout/jobs.py` - Atomically resolves shared players and metric definitions and blocks materially conflicting NCAA ID reuse.
- `roster_scout/models.py` - Mirrors revision 0004 non-null batting and pitching ownership columns.
- `roster_scout/snapshots.py` - Enforces exact source/fact counts and unique same-snapshot traceability before publication.
- `tests/test_canonical_publish.py` - Adds real two-session races, authoritative identity cases, publication negatives, D1/D2/D3 positives, and migration lifecycle coverage.
- `tests/test_snapshot_persistence.py` - Keeps prior count, tenant, and immutability tests semantically valid with traceable facts.

## Decisions Made

- Used the existing partial authoritative-player index as PostgreSQL conflict inference rather than adding a second uniqueness contract.
- Compared stored and incoming names only through normalization equivalence; aliases and suffix changes remain explicit review cases rather than silent merges.
- Kept duplicate source-to-fact detection in the atomic publication gate so malformed drafts remain writable for diagnosis but can never become customer-visible.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Updated legacy persistence fixtures for non-null fact ownership**
- **Found during:** Plan-level full repository verification
- **Issue:** Three pre-0004 immutability fixtures created orphan `BattingFact` rows or published a source row from flags alone, so the new correctness constraints rejected fixture setup before the intended assertions ran.
- **Fix:** Added traceable player, player-team-season, and source helpers; adjusted section counts; and preserved the original cross-organization, nonnegative-count, and published-immutability assertions.
- **Files modified:** `tests/test_snapshot_persistence.py`
- **Verification:** Five focused regressions passed, followed by all 132 repository tests.
- **Committed in:** `2ad0da5`

---

**Total deviations:** 1 auto-fixed (1 blocking compatibility issue).
**Impact on plan:** The fix preserves earlier integrity coverage under the stricter 0004 schema without changing production scope.

## Issues Encountered

- Initial localhost PostgreSQL executions were blocked by the managed sandbox and several automatic escalation reviews timed out. A later scoped approval succeeded against only the disposable `roster_scout_test` database.
- Context7 was unavailable; implementation syntax was checked against the official SQLAlchemy 2.1 PostgreSQL dialect documentation and PostgreSQL `INSERT` documentation.
- The first full repository run correctly exposed legacy fixtures that violated the new fact-link contract; the Rule 3 fix above restored their original test intent.

## User Setup Required

None - no external service configuration required.

## Verification Results

- Task targets: 18 PostgreSQL tests passed across concurrency, identity reuse, and publication integrity.
- Full canonical publication module: 23 tests passed.
- Revision 0004 downgrade, re-upgrade, published-fact preservation, and `alembic check`: passed with no drift.
- Full PostgreSQL-backed discovery: 132 tests passed.
- NCAA catalog validation: 942 selectable 2026 D1/D2/D3 teams, zero errors.
- Python compilation, required template existence, offline migration SQL rendering, and `git diff --check`: passed.

## Known Stubs

None.

## Threat Flags

None - atomic shared-row upserts, authoritative identity validation, non-null composite ownership, and the publication traceability gate directly implement the registered tampering, disclosure, and denial-of-service mitigations without adding a new trust boundary.

## Next Phase Readiness

- CR-05, CR-10, and WR-01 are closed with PostgreSQL-backed positive and negative regressions.
- Concurrent valid jobs can share authoritative players and metric definitions without unique-key failure.
- Drafts cannot publish unless critical section declarations exactly match snapshot-owned evidence and typed facts.
- Ready for Plan 01-06 execution-claim hardening.

## TDD Gate Compliance

- RED commits `2782f73` and `6b9a5ab` precede GREEN commits `0060c50` and `c47a0c4` respectively.
- The initial RED database run could not reach PostgreSQL because sandbox approval timed out; the unchanged regression targets passed after implementation once scoped database access became available.

## Self-Check: PASSED

- All seven created or modified implementation/test files exist.
- Task commits `2782f73`, `0060c50`, `6b9a5ab`, `c47a0c4`, and `2ad0da5` exist in git history.
- Every task acceptance criterion, plan-level verification command, migration lifecycle check, and repository handoff check passed.

---
*Phase: 01-canonical-snapshot-foundation*
*Completed: 2026-11-09*
