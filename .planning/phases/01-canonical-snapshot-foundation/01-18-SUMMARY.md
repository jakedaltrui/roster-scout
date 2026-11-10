---
phase: 01-canonical-snapshot-foundation
plan: "18"
subsystem: canonical-data
tags: [ncaa, identity-resolution, postgresql, provenance, publication]

requires:
  - phase: 01-canonical-snapshot-foundation
    provides: Same-player source identity binding and immutable canonical publication through Plan 01-17
provides:
  - Explicit authoritative, safe-fallback, and unresolved NCAA identity classification
  - Reciprocal-unique cross-section matching with deterministic team-season-scoped keys
  - PostgreSQL publication and source-first recovery proof for isolated unresolved identities
affects: [canonical-publication, provenance, player-identity, excel, power-bi]

tech-stack:
  added: []
  patterns: [reciprocal-unique bipartite matching, explicit unresolved identity, deterministic source-scoped identity keys]

key-files:
  created: []
  modified:
    - roster_scout/canonical.py
    - tests/test_canonical_data.py
    - tests/test_canonical_publish.py

key-decisions:
  - "Only reciprocal-unique compatible batting/pitching evidence merges; ambiguous graph edges remain isolated unresolved rows."
  - "A repeated ID-less name is safe fallback only when every remaining row has a unique nonempty support signature; duplicate or missing support is unresolved."
  - "The existing canonical persistence path remains status-transparent, so unresolved publication required regression coverage rather than a jobs.py behavior change."

patterns-established:
  - "Identity group result: grouping carries rows, identity status, and its deterministic key as one internal value."
  - "Ambiguity isolation: every unresolved row receives its own season-team-scoped digest and exact source/fact binding."

requirements-completed:
  - DATA-01
  - DATA-03
  - DATA-07
  - OPS-05

duration: 7 min
completed: 2026-11-10
---

# Phase 1 Plan 18: Explicit Unresolved NCAA Identity Summary

**Unsafe ID-less NCAA evidence now publishes as isolated unresolved identities, while reciprocal unique matches and distinguishable teammates retain deterministic fallback behavior.**

## Performance

- **Duration:** 7 min
- **Started:** 2026-11-10T02:30:01Z
- **Completed:** 2026-11-10T02:36:17Z
- **Tasks:** 2
- **Files modified:** 3

## Accomplishments

- Replaced tuple-only identity grouping with an internal result that carries rows, status, and deterministic identity key together.
- Implemented a precise decision table for authoritative IDs, reciprocal-unique cross-section pairs, distinguishable same-name teammates, missing/duplicate support, and one-to-many or many-to-one ambiguity.
- Scoped every fallback and unresolved key to the NCAA season-team ID and made every ambiguous row independently addressable.
- Proved unresolved rows publish with exact one-source/one-fact identity binding, survive source-first recovery without duplication, and remain visible to downstream readers through `PlayerTeamSeason.identity_status`.
- Preserved terminal authoritative-name conflicts, database rejection of swapped identities, D1/D2/D3 canonical publication, and the two-sheet workbook contract.

## Task Commits

1. **Task 1 RED: Expose unsafe ID-less ambiguity** - `81a3773` (test)
2. **Task 1 GREEN: Classify unsafe ID-less identities as unresolved** - `423e2cf` (feat)
3. **Task 2: Prove unresolved persistence and publication** - `f2014e5` (test)

Task 2's new PostgreSQL tests passed against the existing generic persistence implementation once Task 1 emitted unresolved groups. No `jobs.py` behavior change was necessary or appropriate.

## Files Created/Modified

- `roster_scout/canonical.py` - Status-carrying identity groups, support signatures, reciprocal-unique matching, and deterministic fallback/unresolved keys.
- `tests/test_canonical_data.py` - Table-driven ambiguity matrix plus stable and cross-team key assertions.
- `tests/test_canonical_publish.py` - Unresolved publication, source binding, recovery, teammate fallback, and authoritative-conflict regressions.

## Decisions Made

- Treat compatible one-to-many or many-to-one cross-section evidence as ambiguous for every involved row; no best-effort merge is allowed.
- Preserve distinct fallback identities when same-name rows have unique nonempty support signatures and no ambiguous cross-section edge.
- Keep unresolved status publishable and explicit; only authoritative material-name conflict remains terminal.
- Reuse Plan 01-17's exact source-to-identity constraint instead of adding a parallel linkage mechanism.

## Deviations from Plan

None - plan executed exactly as written. The plan explicitly required `_persist_canonical()` to remain status-transparent, and its existing generic implementation already persisted the Task 1 status and key without modification.

## Issues Encountered

- The managed sandbox blocked local PostgreSQL connections and Git index writes; scoped approved execution was used for the disposable `roster_scout_test` database and normal hooked commits.
- The system `python3` lacks project dependencies, so catalog validation used the repository virtual environment after compilation completed successfully.

## User Setup Required

None - no external service configuration required.

## Verification Results

- Identity decision-table target: 11 tests passed.
- Unresolved persistence plus source-binding target: 18 tests passed.
- Full PostgreSQL-backed repository discovery: 239 tests passed.
- D1/D2/D3 canonical publication, report-job recovery, selector, and workbook regressions passed within the full suite.
- NCAA catalog: 942 selectable teams (308 D1, 253 D2, 381 D3), zero errors; 25 expected missing-stable-ID warnings unchanged.
- Required Python compilation, template existence, and `git diff --check`: passed.

## Known Stubs

None - modified production code contains no placeholder or unwired identity path.

## Threat Flags

None - the plan changes no network, authentication, file-access, or schema trust boundary beyond the documented NCAA identity resolver and published identity-status surface.

## TDD Gate Compliance

- Task 1 RED `81a3773` failed on ambiguous cases before GREEN `423e2cf` made the decision table pass.
- Task 2 added regression coverage after Task 1 because the pre-existing generic persistence path already satisfied status transparency; no redundant production change was introduced.

## Self-Check: PASSED

- All three modified implementation/test files and this summary exist.
- Task commits `81a3773`, `423e2cf`, and `f2014e5` resolve in repository history.
- Every task acceptance criterion and plan-level verification command passed.
- `brag-output/` remains untouched and untracked.

## Next Phase Readiness

- Verification gap 6 is closed: unsafe ID-less evidence reaches `unresolved` and stays source-isolated through publication.
- Safe reciprocal pairs and distinguishable same-name teammates remain deterministic fallback identities.
- Plan 01-19 can proceed; existing managed-service and uninterrupted-hosting production prerequisites remain unchanged.

---
*Phase: 01-canonical-snapshot-foundation*
*Completed: 2026-11-10*
