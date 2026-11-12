---
phase: 02-identity-organizations-and-report-library
plan: "06"
subsystem: report-acceptance
tags: [postgresql, sqlalchemy, alembic, idempotency, outbox, audit, usage]

requires:
  - phase: 01-canonical-snapshot-foundation
    provides: Durable report jobs, generation-bound dispatch outbox, snapshots, and usage ledger
  - phase: 02-05
    provides: Active-organization report library and server-derived tenant context
provides:
  - One caller-owned transaction for report, job, outbox, authenticated-use, and audit acceptance
  - Canonical requester and snapshot-creator account binding with organization membership constraints
  - Concurrent active-job convergence across repeated and distinct browser action UUIDs
  - Bounded canonical NCAA report request DTO for Plan 02-15
affects: [02-15, 02-07, report-creation, report-refresh, adoption-analytics]

tech-stack:
  added: []
  patterns:
    - Reload active membership inside the same transaction as durable report acceptance
    - Serialize on the organization report row before active-job convergence
    - Key authenticated-use accounting by the accepted ReportJob UUID

key-files:
  created:
    - migrations/versions/0018_phase2_report_actors.py
  modified:
    - roster_scout/models.py
    - roster_scout/jobs.py
    - roster_scout/usage.py
    - roster_scout/reports.py
    - tests/test_report_library.py
    - tests/test_usage_metrics.py

key-decisions:
  - "Authorized acceptance derives actor and organization from a reloaded active membership, then commits report, job, outbox, usage, and audit evidence as one unit."
  - "An existing nonterminal job for the same organization report wins even when a repeated browser action carries a different UUID."
  - "Nullable actor columns use canonical account and organization-membership foreign keys while preserving anonymous legacy rows and the legacy snapshot created_by field."

patterns-established:
  - "Dispatch remains post-commit through the unchanged Phase 1 outbox drainer; the acceptance service never calls a broker."
  - "Exactly one authenticated_use and one acceptance audit row are keyed to the converged job, not to page views, logins, or rejected requests."

requirements-completed: [ORG-01, ORG-04, SNAP-07, SECU-02, SECU-04]

duration: 20 min
completed: 2026-11-11
---

# Phase 2 Plan 06: Atomic Authorized Report Acceptance Summary

**Authorized NCAA report requests now converge on one actor-bound durable job while report, outbox, usage, and audit evidence commit or roll back together.**

## Performance

- **Duration:** 20 min
- **Started:** 2026-11-12T00:52:43Z
- **Completed:** 2026-11-12T01:12:40Z
- **Tasks:** 2
- **Files modified:** 7

## Accomplishments

- Added a strict canonical NCAA request DTO and connection-free acceptance service that validates bounded identifiers, reloads an active membership, upserts canonical team/season/report identity, locks the scoped report, and converges on its active job.
- Added revision 0018 actor bindings for report jobs, report snapshots, and product usage with direct account and organization-membership pairing constraints while retaining anonymous legacy usage and free-text snapshot creator history.
- Preserved Phase 1 job and generation/outbox authority: the lower-level job primitive accepts an optional canonical requester, snapshot creation inherits that requester, and broker dispatch remains outside the acceptance transaction.
- Added concurrency, rollback, false-adoption, aggregate, actor-FK, and cross-organization pairing tests using distinct PostgreSQL sessions and barriers.

## Task Commits

Each task was committed atomically:

1. **Task 1: Specify atomic report acceptance, idempotency, and usage** - `bcf17ee` (test)
2. **Task 2: Implement one-transaction authorized report acceptance** - `bfa9314` (feat)
3. **Task 2 compatibility fix: Preserve historical migration fixtures** - `9c99741` (fix)
4. **Task 2 compatibility fix: Keep nullable actors drift-free across old schemas** - `3447f50` (fix)

## Files Created/Modified

- `migrations/versions/0018_phase2_report_actors.py` - Adds reversible nullable requester/creator actor columns and actor/account membership foreign keys.
- `roster_scout/models.py` - Maps canonical report-job, snapshot-creator, and usage actors with organization pairing constraints.
- `roster_scout/jobs.py` - Accepts an optional canonical requester in the caller-owned job primitive and carries it into a generated snapshot.
- `roster_scout/usage.py` - Documents the PostgreSQL-enforced canonical actor scope for authenticated adoption events.
- `roster_scout/reports.py` - Implements bounded request validation, canonical identity upserts, membership revalidation, report locking, active-job convergence, and atomic usage/audit writes.
- `tests/test_report_library.py` - Proves commit/rollback atomicity, concurrent and cross-action convergence, exact usage/audit counts, and actor constraints.
- `tests/test_usage_metrics.py` - Updates authenticated-use fixtures to use canonical accounts and memberships required by revision 0018.

## Decisions Made

- Membership is reloaded and locked inside the caller's transaction. Actor and organization IDs are never accepted as authority without that current local relationship.
- The report row is the serialization boundary for builds. A queued, running, or retrying job is reused regardless of whether the second browser action repeats or replaces its UUID.
- Usage idempotency is the accepted job UUID. That yields one adoption event and one canonical user attribution for one accepted build instead of inflating metrics for double-clicks.
- Revision 0018 uses a persisted null-producing UUID default and disables eager ORM default fetching on the two extended legacy models. This keeps old migration-boundary fixtures executable while remaining Alembic drift-free.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Updated pre-actor usage fixtures to create canonical accounts and memberships**
- **Found during:** Task 2 focused usage regression
- **Issue:** Existing Phase 1 usage tests inserted arbitrary actor UUIDs, which correctly failed once revision 0018 added the required account and membership foreign keys.
- **Fix:** Kept anonymous download cases unchanged and created canonical local actor/membership rows for actor-bearing usage cases.
- **Files modified:** `tests/test_usage_metrics.py`
- **Verification:** `tests.test_usage_metrics` and the full 501-test suite pass.
- **Committed in:** `bfa9314`

**2. [Rule 1 - Bug] Preserved ORM inserts while historical migration tests intentionally remove 0018 columns**
- **Found during:** Full Python regression after Task 2
- **Issue:** Current ORM metadata attempted to insert or eagerly return new nullable actor columns while older migration tests intentionally ran at pre-0018 schemas.
- **Fix:** Added drift-free null-producing defaults and disabled eager default fetching for report jobs and snapshots so omitted legacy actors remain compatible at historical schema boundaries.
- **Files modified:** `roster_scout/models.py`, `migrations/versions/0018_phase2_report_actors.py`
- **Verification:** Migration-boundary regressions, 0018 downgrade/upgrade, `alembic check`, and all 501 tests pass.
- **Committed in:** `9c99741`, `3447f50`

---

**Total deviations:** 2 auto-fixed bugs
**Impact on plan:** Both changes were required to enforce the new canonical actor constraints without breaking the repository's established historical migration verification. No route, UI, broker, cache, or deployment scope was added.

## Issues Encountered

- PostgreSQL optimizes a literal `DEFAULT NULL` away, which caused Alembic to report default drift. Revision 0018 therefore uses an equivalent stored `NULLIF(zero_uuid, zero_uuid)` expression; it always yields SQL NULL while remaining introspectable and drift-free.
- Local PostgreSQL checks required approved localhost access to the disposable `roster_scout_test` database. No production database, deployment, identity provider, broker, or live NCAA boundary was used.

## User Setup Required

None - no dependency, provider, broker, route, UI, or hosted configuration changes were introduced.

## Next Phase Readiness

- Plan 02-15 can construct `CanonicalReportRequest` from the exact NCAA selector, call the authorized service inside one `Session.begin()`, commit, then drain the existing outbox and redirect to the returned report ID.
- Plan 02-07 can display the converged job and eventual requester-bound snapshot through existing organization-scoped report lookups.
- No blockers remain for the next Phase 2 plan.

## Verification

- Atomic acceptance, Phase 1 job, and usage regression group - **72 tests passed**
- Clean full `unittest discover` against disposable PostgreSQL - **501 tests passed**
- `node --test tests/js/*.test.mjs` - **16 tests passed**
- Revision 0018 downgrade/upgrade plus `alembic check` - **passed; no drift**
- Application, scraper, smoke, and catalog-script compilation - **passed**
- NCAA selector catalog - **942 selectable teams; valid**
- Required templates and `git diff --check` - **passed**
- Stub and threat-surface scan across all plan files - **no blocking stubs or unplanned threat surface found**

## Security and Threat Surface

- The planned schema and acceptance trust boundaries are fully covered: strict identifier bounds, UUID-only idempotency, active-membership reload, report-row serialization, canonical actor/account organization pairing, and one transaction for every durable side effect.
- The service is connection-free and introduces no HTTP route, CSRF surface, browser field authority, external call, broker call, file access, queue, or cache. Plan 02-15 owns the protected route and post-commit dispatch composition.

## Known Stubs

None. Optional batting and pitching category IDs are intentional bounded canonical selector fields, not UI or persistence placeholders.

## Self-Check: PASSED

- Revision 0018 and every modified source/test file exist.
- Task commits `bcf17ee`, `bfa9314`, `9c99741`, and `3447f50` exist.
- Focused, full Python, JavaScript, migration round-trip/drift, compilation, catalog, template, stub, threat, and diff claims were rerun after the final compatibility fix.

---
*Phase: 02-identity-organizations-and-report-library*
*Completed: 2026-11-11*
