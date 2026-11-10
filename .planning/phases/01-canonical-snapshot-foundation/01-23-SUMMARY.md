---
phase: 01-canonical-snapshot-foundation
plan: "23"
subsystem: canonical-data
tags: [ncaa, identity-resolution, postgresql, provenance, two-way-players]

requires:
  - phase: 01-canonical-snapshot-foundation
    provides: Explicit unresolved identity status and source-to-fact binding from Plans 01-17 and 01-18
provides:
  - Shared nonempty jersey or normalized-position corroboration for reciprocal ID-less merges
  - Isolated unresolved identities for name-only and one-sided-support batting/pitching pairs
  - PostgreSQL proof of exact source and typed-fact ownership for unsafe and valid two-way cases
affects: [canonical-publication, player-identity, provenance, excel, power-bi]

tech-stack:
  added: []
  patterns: [positive identity corroboration, normalized support comparison, unresolved provenance isolation]

key-files:
  created: []
  modified:
    - roster_scout/canonical.py
    - tests/test_canonical_data.py
    - tests/test_canonical_publish.py

key-decisions:
  - "Name equality and reciprocal uniqueness are insufficient: an ID-less cross-section merge requires a shared nonempty jersey or normalized position."
  - "Missing support remains non-conflicting for graph construction but never counts as positive corroboration."
  - "Conflicting nonempty support keeps distinguishable teammates separate with the existing deterministic fallback semantics."

patterns-established:
  - "Support normalization: jersey uses stripped case-folding and position uses whitespace-collapsed case-folding before compatibility or corroboration checks."
  - "Publication proof: every unresolved source row and typed fact retains the same unique PlayerTeamSeason identity."

requirements-completed:
  - SNAP-03
  - DATA-01
  - DATA-03

duration: 4 min
completed: 2026-11-10
---

# Phase 1 Plan 23: Corroborated ID-less Identity Matching Summary

**ID-less batting and pitching rows now merge only with shared nonempty support, while unsupported namesakes publish as separate unresolved players with exact provenance.**

## Performance

- **Duration:** 4 min
- **Started:** 2026-11-10T16:41:19Z
- **Completed:** 2026-11-10T16:45:03Z
- **Tasks:** 2
- **Files modified:** 3

## Accomplishments

- Added a positive corroboration gate requiring equal nonempty jersey or normalized position before a reciprocal-unique cross-section pair can merge.
- Proved name-only and one-sided-support rows produce two stable unresolved identities, while matching jersey or position still produces one two-way fallback identity.
- Added PostgreSQL publication coverage for distinct Players, PlayerTeamSeason rows, identity keys, SourceRecord links, and batting/pitching fact ownership.
- Preserved authoritative identities, repeated-name handling, distinguishable teammate fallback behavior, D1/D2/D3 publication, and source-first recovery.

## Task Commits

1. **Task 1 RED: Expose unsupported ID-less merges** - `511940a` (test)
2. **Task 1 GREEN: Require corroborated ID-less matches** - `c887281` (fix)
3. **Task 2: Prove corroborated publication identity** - `6fb4585` (test)
4. **Integration fix: Align publication fixtures with the parser contract** - `2b45b42` (fix)

## Files Created/Modified

- `roster_scout/canonical.py` - Normalizes support fields and requires shared nonempty evidence before reciprocal merging.
- `tests/test_canonical_data.py` - Covers absent, one-sided, matching, and conflicting support with deterministic identity assertions.
- `tests/test_canonical_publish.py` - Proves unsafe pairs remain source-isolated and a corroborated two-way player retains one identity.

## Decisions Made

- Kept missing support compatible for ambiguity graph construction but prohibited it from establishing identity.
- Normalized position whitespace and case consistently for compatibility, corroboration, and deterministic signatures.
- Preserved Plan 01-18's fallback status for distinguishable conflicting teammates; the safety invariant is separation, while unresolved is reserved for unsupported or ambiguous evidence.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Updated stale parser-version fixtures after Plan 01-22**
- **Found during:** Plan-level full canonical publication verification
- **Issue:** Four pre-existing test helpers still emitted `parser-1`, so Plan 01-22's new parser-2/core-v2 fail-closed contract rejected them before their intended assertions ran.
- **Fix:** Replaced hardcoded parser versions with `rs.NCAA_PARSER_VERSION`, keeping the fixtures bound to the production parser contract.
- **Files modified:** `tests/test_canonical_publish.py`
- **Verification:** The full 70-test canonical data/publication gate passed.
- **Committed in:** `2b45b42`

**2. [Plan clarification] Preserved distinguishable-teammate fallback status**
- **Found during:** Task 1 acceptance review
- **Issue:** One acceptance bullet called conflicting support `unresolved`, while the task action, D-15, and Plan 01-18 require uniquely distinguishable teammates to remain separate deterministic fallback identities.
- **Fix:** Asserted that conflicting support never merges and preserved the established fallback classification; name-only and one-sided-support pairs are unresolved as required by the plan's must-haves.
- **Files modified:** `tests/test_canonical_data.py`
- **Verification:** Existing identity decision-table and distinguishable-teammate publication tests remain green.
- **Committed in:** `511940a`

---

**Total deviations:** 1 auto-fixed blocking integration issue and 1 locked-decision clarification.
**Impact on plan:** Both preserve prior contracts while closing the unsafe name-only merge; no product scope was added.

## Issues Encountered

- Local PostgreSQL access requires scoped elevated execution in this environment; the disposable `roster_scout_test` database completed all required checks.
- Task 2's integration tests passed immediately because Task 1 had already implemented the production behavior; no redundant production change was introduced.

## User Setup Required

None - no external service configuration required.

## Verification Results

- Focused identity unit suite: 4 tests passed.
- Existing identity-resolution suite: 3 tests passed.
- PostgreSQL corroboration publication class: 8 tests passed.
- Full canonical data/publication gate: 70 tests passed.
- Python compilation and `git diff --check`: passed.

## Known Stubs

None - the modified identity and publication paths contain no placeholder behavior.

## Threat Flags

None - the documented NCAA identity and canonical publication trust boundaries are fully covered by the plan threat model.

## TDD Gate Compliance

- Task 1 RED `511940a` failed on name-only and one-sided-support cases before GREEN `c887281` made them pass.
- Task 2 added PostgreSQL regression coverage after Task 1; it required no separate GREEN implementation because the production path was already complete.

## Self-Check: PASSED

- All three modified implementation/test files and this summary exist.
- Task commits `511940a`, `c887281`, `6fb4585`, and `2b45b42` resolve in repository history.
- Every task acceptance criterion and plan-level verification command passed, with the locked D-15 classification clarification documented above.
- `brag-output/` remains untouched and untracked.

## Next Phase Readiness

- Verification CR-02 is closed: name-only and one-sided-support cross-section pairs cannot silently merge.
- Plan 01-24 can build authoritative source/player equivalence on identities that now have safe fallback grouping.

---
*Phase: 01-canonical-snapshot-foundation*
*Completed: 2026-11-10*
