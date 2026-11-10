---
phase: 01-canonical-snapshot-foundation
plan: "19"
subsystem: evidence-retention
tags: [postgresql, sqlalchemy, alembic, retention, readiness]

requires:
  - phase: 01-canonical-snapshot-foundation
    provides: Failed-evidence capture, physical byte purge, and canonical provenance through Plan 01-18
provides:
  - Strict 1-28 day failure-evidence retention with a 28-day hard maximum
  - Transactional PostgreSQL purge heartbeat coupled to physical byte deletion
  - Restart-safe six-hour readiness derived from persisted success and expired-byte absence
affects: [evidence-purge, scheduler, deployment-readiness, operations]

tech-stack:
  added: []
  patterns: [transactional health heartbeat, fail-closed environment parsing, persisted readiness projection]

key-files:
  created:
    - migrations/versions/0009_evidence_purge_health.py
  modified:
    - roster_scout/models.py
    - roster_scout/evidence.py
    - tests/test_evidence.py

key-decisions:
  - "Failure evidence retention is parsed once at the insertion boundary, defaults to 28 days, and rejects every value outside the strict integer range 1 through 28."
  - "Purge success is authoritative only when byte nulling and the singleton PostgreSQL heartbeat commit in the same bounded transaction."
  - "Readiness is fresh for exactly six hours and additionally requires that no expired evidence row still contains bytes."

patterns-established:
  - "Transactional purge health: deletion precedes a singleton upsert inside one transaction, so rollback preserves both bytes and the prior heartbeat."
  - "Safe readiness result: a frozen projection exposes only status, timestamps, and integer counts; database failures fail closed without exception text."

requirements-completed:
  - DATA-07
  - OPS-04

duration: 8 min
completed: 2026-11-10
---

# Phase 1 Plan 19: Durable Evidence Purge Health Summary

**Failed-evidence bytes now expire within 672 hours, purge under bounded PostgreSQL timeouts with a transactional heartbeat, and gate restart-safe readiness on both recent success and expired-byte absence.**

## Performance

- **Duration:** 8 min
- **Started:** 2026-11-10T02:39:31Z
- **Completed:** 2026-11-10T02:47:05Z
- **Tasks:** 2
- **Files modified:** 4

## Accomplishments

- Added reversible revision 0009 and an ORM model for a constrained four-field singleton purge-health row containing no customer or evidence content.
- Replaced the fixed 696-hour path with one strict production parser that defaults to 28 days, accepts only integer days 1 through 28, and fails before insertion for malformed or excessive configuration.
- Coupled expired-byte nulling, `purged_at`, last success, and purged count in one transaction protected by PostgreSQL lock and statement timeouts.
- Added a frozen readiness projection that survives process recreation, treats exactly six hours as fresh, blocks one microsecond later, and independently detects eligible bytes left behind.
- Proved a first hourly purge at 673 hours leaves at least 47 hours before the 720-hour hard deadline.

## Task Commits

Each TDD task was committed through separate RED and GREEN gates:

1. **Task 1 RED: Define durable purge health contract** - `3f64965` (test)
2. **Task 1 GREEN: Persist transactional purge health** - `c287fb9` (feat)
3. **Task 2 RED: Define persisted purge readiness** - `3495423` (test)
4. **Task 2 GREEN: Derive persisted purge readiness** - `0b031a6` (feat)

## Files Created/Modified

- `migrations/versions/0009_evidence_purge_health.py` - Reversible singleton health table with key and nonnegative-count constraints.
- `roster_scout/models.py` - `EvidencePurgeState` ORM mapping matching revision 0009.
- `roster_scout/evidence.py` - Strict retention parser, bounded atomic purge, durable state upsert, and persisted readiness query.
- `tests/test_evidence.py` - Migration, retention boundary, timeout, rollback, restart, safety-margin, and readiness regressions.

## Decisions Made

- Treat malformed, zero, negative, fractional, blank, and greater-than-28 retention values as configuration errors without echoing the value; no fallback may silently extend retention.
- Record successful zero-row purges as health because completing the bounded eligibility scan is the operational success signal.
- Evaluate stale success before outstanding-byte status and return a separate sanitized `database_unavailable` state when readiness cannot query PostgreSQL.
- Keep the singleton state limited to last success, last purged count, and update time; URLs, HTML, checksums, source identifiers, and exceptions remain outside readiness.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

- The managed sandbox blocks localhost PostgreSQL and Git index writes. Scoped approved execution was used only for the disposable `roster_scout_test` database and normal hooked commits.
- Context7 was unavailable in the environment; installed Alembic, SQLAlchemy, and PostgreSQL behavior was verified directly through migration, timeout, rollback, and drift tests.

## User Setup Required

None for this data/service plan. Plan 01-20 owns scheduler and deployment wiring for the new 28-day retention and persisted readiness contracts.

## Verification Results

- Task 1 persistence and deadline target: 8 tests passed.
- Task 2 persisted readiness target: 6 tests passed.
- Full evidence and report-job suites: 64 tests passed.
- Full PostgreSQL-backed repository discovery: 251 tests passed.
- Revision 0009 fresh install, downgrade/re-upgrade, real lock timeout, injected rollback, and Alembic drift checks passed.
- NCAA catalog: 942 selectable teams (308 D1, 253 D2, 381 D3), zero errors; 25 expected missing-stable-ID warnings unchanged.
- Required Python compilation, template existence, and `git diff --check` passed.

## Known Stubs

None - the modified production paths contain no placeholders or unwired data flow.

## Threat Flags

None - strict retention, bounded deletion, transactional heartbeat integrity, and fail-closed readiness are the registered T-01-19-01 through T-01-19-04 surfaces and are covered by automated tests.

## TDD Gate Compliance

PASSED - Task 1 RED `3f64965` precedes GREEN `c287fb9`, and Task 2 RED `3495423` precedes GREEN `0b031a6`; every targeted and repository regression test passes afterward.

## Self-Check: PASSED

- All four created or modified implementation/test files and this summary exist.
- Task commits `3f64965`, `c287fb9`, `3495423`, and `0b031a6` resolve in repository history.
- Every task acceptance criterion and plan-level verification command passed.
- `brag-output/` remains untouched and untracked.

## Next Phase Readiness

- Gap 3's data/service contract now has durable purge authority, a 47-hour tested reserve, and restart-safe readiness.
- Plan 01-20 can replace process-local health and wire independently supervised purge/readiness without changing the transaction or result contracts.
- Existing managed-service and uninterrupted-hosting production prerequisites remain unchanged.

---
*Phase: 01-canonical-snapshot-foundation*
*Completed: 2026-11-10*
