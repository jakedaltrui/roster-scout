---
phase: 01-canonical-snapshot-foundation
plan: "01"
subsystem: database
tags: [postgresql, sqlalchemy, alembic, psycopg, celery, redis, immutable-snapshots]

requires: []
provides:
  - Organization-scoped canonical PostgreSQL schema with 17 foundation tables
  - Atomic draft-to-published snapshot service and published-only reads
  - Exact JSONB source evidence, typed nullable facts, and metric-definition lineage
  - Database-enforced tenant isolation, publication immutability, and report lineage
affects: [durable-report-jobs, canonical-data-adapter, organization-authorization, excel, power-bi]

tech-stack:
  added: [SQLAlchemy 2.1.6, Alembic 1.20.1, Psycopg 3.3.6, Celery 5.6.3, PostgreSQL 16, Redis 7]
  patterns: [caller-owned transactions, composite tenant foreign keys, draft-then-publish, database immutability triggers]

key-files:
  created:
    - roster_scout/models.py
    - roster_scout/snapshots.py
    - migrations/versions/0001_canonical_snapshot_foundation.py
    - tests/test_snapshot_persistence.py
  modified:
    - requirements.txt
    - compose.yaml
    - .env.example

key-decisions:
  - "Every tenant-owned snapshot child carries organization_id and uses a composite ownership foreign key."
  - "Published canonical data is protected by PostgreSQL triggers while artifact lifecycle state remains independently mutable."
  - "Database imports remain connection-free; callers create one session and own the publication transaction."

patterns-established:
  - "Draft-then-publish: validate both critical sections and identity state before one atomic ready/partial transition."
  - "Evidence beside facts: exact JSONB source rows and provenance coexist with nullable typed canonical values."
  - "Published-only repository: customer-facing reads can never return draft or failed snapshots."

requirements-completed:
  - SNAP-01
  - SNAP-03
  - DATA-02
  - DATA-04
  - DATA-05
  - DATA-06
  - DATA-08
  - DATA-09
  - DATA-10
  - OPS-04

duration: 6 min
completed: 2026-11-01
---

# Phase 1 Plan 1: Canonical Snapshot Foundation Summary

**PostgreSQL-backed immutable report snapshots with exact source evidence, organization-scoped constraints, atomic publication, and reversible Alembic migrations**

## Performance

- **Duration:** 6 min
- **Started:** 2026-11-01T22:59:40Z
- **Completed:** 2026-11-01T23:05:53Z
- **Tasks:** 3
- **Files modified:** 16

## Accomplishments

- Added the complete 17-table Phase 1 persistence schema with UUID identity, provenance, nullable facts, baseball innings as integer outs, and versioned metric definitions.
- Enforced organization ownership, legal predecessor lineage, identity uniqueness, and published-row immutability in PostgreSQL rather than application convention.
- Added transactional publication that locks and validates drafts, requires successful or verified-empty batting and pitching sections, and exposes only ready/partial snapshots.
- Added real PostgreSQL integration coverage for migration lifecycle, cross-tenant denial, rollback, evidence retention, artifact independence, and immutable publication.

## Task Commits

Each task was committed atomically:

1. **Task 1: Establish the PostgreSQL contract and failing invariant tests** - `d572e7a` (test)
2. **Task 2: Publish the first canonical snapshot end to end** - `e7f9ad1` (feat)
3. **Task 3: Enforce immutability, lineage, and migration reversibility** - `18e3fa8` (fix)

## Files Created/Modified

- `requirements.txt` - Exact persistence and worker dependency pins.
- `compose.yaml` - Health-checked local PostgreSQL and Redis services.
- `.env.example` - Empty database and broker configuration names.
- `scripts/create-test-database.sql` - Creates the disposable local integration-test database.
- `alembic.ini`, `migrations/` - Reviewed migration environment, initial schema, triggers, and downgrade path.
- `roster_scout/database.py` - Lazy engine, session factory, and caller-owned transaction helper.
- `roster_scout/enums.py` - Shared string-backed lifecycle states.
- `roster_scout/models.py` - Canonical SQLAlchemy schema and named constraints.
- `roster_scout/repositories.py` - Organization-scoped published-only snapshot reads.
- `roster_scout/snapshots.py` - Atomic publication validation and state transition.
- `tests/db_support.py` - PostgreSQL-only migration and cleanup helpers with production-database safeguards.
- `tests/test_snapshot_persistence.py` - Migration, tenant, publication, provenance, identity, and immutability coverage.

## Decisions Made

- Used composite `(id, organization_id)` ownership keys at every tenant boundary so PostgreSQL rejects identifier substitution across organizations.
- Kept canonical snapshot readiness separate from `report_artifacts.status`, allowing artifact retries after publication without altering facts.
- Used string states with named checks for migration-friendly evolution instead of PostgreSQL native enums.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Added deterministic test-database initialization**
- **Found during:** Task 1 (Establish the PostgreSQL contract and failing invariant tests)
- **Issue:** Compose declared both application and test databases, but the PostgreSQL image creates only one database from `POSTGRES_DB`.
- **Fix:** Added `scripts/create-test-database.sql` as a read-only initialization script so `roster_scout_test` always exists on a fresh local volume.
- **Files modified:** `compose.yaml`, `scripts/create-test-database.sql`
- **Verification:** Migration upgrade and all 16 PostgreSQL integration tests passed against `roster_scout_test`.
- **Committed in:** `d572e7a`

---

**Total deviations:** 1 auto-fixed (1 blocking)
**Impact on plan:** The extra file is required for repeatable local PostgreSQL verification and adds no product scope.

## Issues Encountered

- Sandbox networking initially blocked PyPI and localhost PostgreSQL access; approved scoped execution was used for the exact pinned install and disposable local database checks.

## User Setup Required

None - no external service configuration required.

## Verification Results

- PostgreSQL integration suite: 16 tests passed.
- Alembic upgrade, downgrade/re-upgrade, and drift check: passed; no new upgrade operations detected.
- Full deterministic discovery: 49 tests passed when database tests were not enabled (48 legacy tests plus the URL safety test; database classes skipped as designed).
- NCAA catalog validation: 942 selectable 2026 teams, no errors.
- Python compilation, template existence checks, `pip check`, and `git diff --check`: passed.

## Known Stubs

None. Empty environment values and nullable canonical columns are deliberate configuration and missing-data contracts, not UI or runtime stubs.

## Next Phase Readiness

- Ready for Plan 01-02 to add durable job delivery, idempotency, cache/version state, and retention services on this schema.
- Local PostgreSQL and Redis services are healthy; production managed-service provisioning remains a later operational gate.

## Self-Check: PASSED

- All four key created files exist.
- Task commits `d572e7a`, `e7f9ad1`, and `18e3fa8` exist in git history.
- All task acceptance criteria and plan-level verification commands passed.

---
*Phase: 01-canonical-snapshot-foundation*
*Completed: 2026-11-01*
