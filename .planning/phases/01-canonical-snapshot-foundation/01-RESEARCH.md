# Phase 1: Canonical Snapshot Foundation - Research

**Researched:** 2026-11-01
**Domain:** PostgreSQL-backed immutable analytical snapshots and durable Python report jobs
**Confidence:** HIGH

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- Batting and pitching are publication-critical. If either is unavailable, malformed, or fails validation, the job publishes no snapshot.
- A zero-row core section is valid only when the NCAA page loaded successfully and explicitly represents an empty result.
- Future supplemental sections may fail into an explicitly partial snapshot; core and supplemental sections are distinct.
- Snapshot publication is atomic, and no interrupted build may appear ready.
- Canonical snapshot readiness is independent from artifact readiness; artifacts are independently retryable.
- Saved snapshots reopen without NCAA access. Every explicit refresh performs a live collection and creates a successor.
- Cache reuse is scoped to one accepted build. Duplicate refresh requests attach to the active job.
- NCAA player IDs are authoritative and permanently preserved.
- ID-less rows merge only within one team-season when supporting evidence makes the match unique. They never auto-link across teams.
- Conflicting names under one NCAA player ID block publication. Distinguishable same-name teammates remain separate fallback identities.
- Published snapshots permanently retain exact extracted source rows, provenance, parser/schema versions, and checksums.
- HTML is retained only for failed or blocked builds, is restricted, and expires after 30 days.
- Failed builds retain sanitized operational metadata after HTML expires.

### the agent's Discretion
- Select the concrete PostgreSQL, migration, queue, and worker libraries.
- Define exact table/module boundaries while preserving the locked data semantics.
- Keep the current public `/scrape` request and workbook behavior unchanged in this phase.

### Deferred Ideas (OUT OF SCOPE)
- Authentication, memberships, invitations, and customer report-library UI.
- Fielding, split, career, and game-log collectors.
- Detailed ten-sheet Excel and immutable object storage.
- Power BI semantic model and embedded dashboard.
</user_constraints>

<architectural_responsibility_map>
## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Canonical report and snapshot state | Database/Storage | API/Backend | PostgreSQL constraints and transactions own durability; services define legal transitions. |
| NCAA collection and normalization | API/Backend worker | External NCAA/Browserless | Existing Python collectors remain the acquisition boundary and run outside requests. |
| Durable job delivery | Worker/Broker | Database/Storage | Celery/Redis delivers at least once; PostgreSQL remains authoritative for job and idempotency state. |
| Atomic publication | Database/Storage | Worker | A transaction publishes validated facts and section state together. |
| Legacy workbook compatibility | API/Backend | Existing browser flow | `/scrape` remains synchronous until the report-library phase. |
| Migration/release safety | Deployment | Database/Storage | Alembic migrations run once before new web/worker processes receive traffic. |
</architectural_responsibility_map>

<research_summary>
## Summary

The standard brownfield shape is SQLAlchemy 2.1 with Psycopg 3 against managed PostgreSQL, Alembic for reviewed migrations, and a separate Celery worker using Redis only as a message broker. Celery is intentionally not the source of truth: task messages contain a `job_id`, while PostgreSQL owns idempotency, attempts, state transitions, section status, and the final snapshot. This makes at-least-once delivery safe and prevents broker/result-backend state from disagreeing with customer-visible state.

Snapshot publication should use an append-only draft-to-published lifecycle. Collection happens outside a long database transaction. Exact raw rows and normalized facts are written under an unpublished snapshot, validated, and then exposed through a single legal status transition. All analytics/export reads filter to published snapshot states. PostgreSQL uniqueness, composite organization-scoped foreign keys, named checks, and immutability triggers enforce invariants that application code alone cannot guarantee.

The current Flask route should not be converted to asynchronous behavior in Phase 1. Add an internal report-build service and Celery task used by integration tests and future Phase 2 routes, while retaining the existing `scrape_ncaa_by_id() -> split_roster() -> _create_workbook()` path. The compatibility adapter must expose richer collection evidence without changing the existing tuple-returning public functions until callers migrate deliberately.

**Primary recommendation:** Add a small `roster_scout/` package containing SQLAlchemy models, repositories, canonical adapters, publication services, and Celery tasks; run web and worker as separate Fly process groups; use PostgreSQL as the only authoritative state store.
</research_summary>

<standard_stack>
## Standard Stack

### Core
| Library/service | Version | Purpose | Why Standard |
|-----------------|---------|---------|--------------|
| PostgreSQL | 16+ managed | Canonical facts, provenance, jobs, constraints, transactions | Strong relational integrity, JSONB for exact raw rows, transactional publication, Power BI compatibility. |
| SQLAlchemy | 2.1.6 | Models, typed queries, explicit session/transaction scopes | Mature Python data layer shared by Flask and workers; session-per-operation model fits this boundary. |
| Alembic | 1.20.1 | Reviewed forward migrations and schema drift checks | Official SQLAlchemy migration tool; supports autogeneration as a starting point while requiring manual review. |
| Psycopg | 3.3.6 (`binary`) | PostgreSQL driver | Current Psycopg implementation for new Python applications. |
| Celery | 5.6.3 with Redis extra | At-least-once background task delivery and bounded retry/backoff | Mature Python worker system with late acknowledgements and explicit retry controls. |
| Redis | Managed, protocol-compatible | Celery broker only | Fast small-message transport; PostgreSQL, not Redis, keeps durable application state. |

### Supporting
| Tool | Version/policy | Purpose | When to Use |
|------|----------------|---------|-------------|
| Docker Compose | Existing project format | Local PostgreSQL and Redis dependencies | Integration tests and local development. |
| `unittest` | Python 3.12 stdlib | Preserve current test conventions | Unit and integration tests without a framework migration. |
| Fly process groups | Current Fly Apps platform | Separate web and worker machines from one image | Production deployment after billing and managed services are provisioned. |
| SHA-256 | stdlib `hashlib` | Payload, row-set, and future artifact checksums | Provenance and idempotent audit evidence. |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Celery + Redis | Custom PostgreSQL polling queue | One fewer service, but leases, heartbeats, wakeups, retries, scheduling, and crash recovery become application-owned queue infrastructure. |
| Celery + Redis | Celery PostgreSQL PGMQ broker | Attractive single-store design, but Fly Managed Postgres does not promise the nonstandard PGMQ extension required by that transport. |
| Direct SQLAlchemy | Flask-SQLAlchemy | Flask extension is convenient for request-scoped CRUD, but this phase needs the same explicit transaction layer in Flask, workers, CLI migrations, and tests. |
| PostgreSQL integration tests | SQLite substitutes | SQLite is faster but cannot validate PostgreSQL JSONB, partial indexes, constraint behavior, locking, or trigger-based immutability. |
| String states + named checks | Native PostgreSQL enums | Native enums are stricter but complicate rolling migrations; named check constraints preserve explicit allowed values with simpler evolution. |

**Dependencies to add:**
```text
SQLAlchemy==2.1.6
alembic==1.20.1
psycopg[binary]==3.3.6
celery[redis]==5.6.3
```
</standard_stack>

<architecture_patterns>
## Architecture Patterns

### System Architecture

```text
Future Phase 2 request / integration test
          |
          v
Report build service -- unique idempotency key --> PostgreSQL report_jobs
          |                                           |
          +-------------- job_id ---------------------+
                              |
                              v
                         Redis broker
                              |
                              v
                       Celery worker task
                              |
              +---------------+----------------+
              |                                |
              v                                v
      Existing NCAA collector          PostgreSQL draft snapshot
      + Browserless capacity            raw rows + typed facts
              |                                |
              +---------- validation ----------+
                                               |
                                  core sections complete?
                                     /               \
                                   no                 yes
                                   |                   |
                         failed job record       atomic publish
                         no published snapshot   ready/partial status
```

### Recommended Project Structure

```text
roster_scout/
├── __init__.py
├── database.py          # engine/session factory and transaction boundaries
├── enums.py             # string state constants shared by models/services
├── models.py            # SQLAlchemy metadata and canonical tables
├── repositories.py      # tenant-scoped reads/writes and idempotent job creation
├── canonical.py         # NCAA collection -> raw rows + typed fact records
├── snapshots.py         # validation and legal draft/publication transitions
├── celery_app.py        # broker configuration; no authoritative result backend
└── tasks.py             # job_id-only worker entry point and retry classification
migrations/
├── env.py
└── versions/
tests/
├── test_canonical_data.py
├── test_snapshot_persistence.py
└── test_report_jobs.py
```

### Pattern 1: Transaction Scope Outside Domain Functions
Use a fresh SQLAlchemy `Session` per request or worker operation and explicit `Session.begin()` blocks. Repository/domain functions accept the session; they do not create or commit hidden transactions. Publication validation and the final state transition share one transaction.

### Pattern 2: Broker Delivery Is a Hint, Database State Is Truth
Create or retrieve a unique `report_jobs` row before enqueueing. The Celery message contains only the job UUID. On delivery, the worker locks the row, returns immediately for terminal jobs, resumes retryable work, and never creates another snapshot for the same accepted job. Use late acknowledgement only because the task is idempotent.

Recommended Celery policy:
- `task_acks_late = True`
- `task_reject_on_worker_lost = True`
- `worker_prefetch_multiplier = 1`
- JSON task serialization
- no Celery result backend for customer-visible state
- bounded retry with exponential backoff and jitter for transient NCAA/Browserless/network failures
- no retry for schema, validation, identity-conflict, or other deterministic failures

### Pattern 3: Draft Facts, Published Views
Facts always reference `organization_id` and `snapshot_id`. Collection and normalization may populate an unpublished draft. Customer/analytics/export queries use repository methods or database views that require `snapshot.status IN ('ready', 'partial')`. The final publication transaction verifies both core section rows/statuses and then stamps `published_at`.

### Pattern 4: Layered Identity
Represent source identity explicitly:
- `source_player_id` present: globally authoritative within NCAA.
- missing ID with unique team-season match: deterministic fallback scoped to the snapshot/team-season.
- ambiguous: unresolved status; never inferred across schools.
- same authoritative ID with conflicting names: deterministic validation failure.

### Pattern 5: Raw and Canonical Side by Side
Store exact extracted rows in JSONB with row ordinal, section, URL, source identifiers, collection time, parser version, schema version, and SHA-256. Store numeric canonical facts in nullable typed columns. Preserve raw innings text and store canonical innings as nonnegative integer outs.

### Data Model Boundaries

Minimum Phase 1 tables:
- `organizations` — minimal tenant anchor only; membership/auth fields wait for Phase 2.
- `teams`, `seasons`, `team_seasons` — NCAA identity and division/season context.
- `players`, `player_team_seasons` — authoritative and fallback identity state.
- `reports`, `report_snapshots`, `report_sections` — immutable report lineage and section completeness.
- `source_records` — exact extracted rows and provenance.
- `batting_facts`, `pitching_facts` — nullable typed current-season facts; pitching stores `innings_outs`.
- `metric_definitions`, `metric_values` — source-provided versus computed values and definition versions.
- `report_jobs` — durable/idempotent execution state and sanitized failure category.
- `report_artifacts` — independent artifact status contract; actual object storage waits for Phase 4.
- `failure_evidence` — gzip-compressed failed/blocked HTML with checksum, expiry, purge timestamp, and no customer-facing read path.

Every tenant-owned child table should carry `organization_id` and use a composite foreign key to the parent `(id, organization_id)`. This makes cross-organization relationships invalid at the database layer rather than merely unlikely in application code.

### Anti-Patterns to Avoid
- Treating Celery task state or Redis as the report-job database.
- Holding a database transaction open while Browserless and NCAA network calls run.
- Publishing by inserting directly into tables queried without a snapshot-status filter.
- Using only application checks for organization isolation or published-row immutability.
- Converting missing strings to `0`, including absent innings or denominators.
- Treating NCAA innings notation as decimal arithmetic.
- Changing `/scrape` to return job JSON during this no-UI foundation phase.
- Adding full Phase 3 collectors while designing schemas for their future facts.
</architecture_patterns>

<dont_hand_roll>
## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Database migrations | Startup-time `CREATE TABLE` logic | Alembic revisions + release command | Repeatability, ordering, review, downgrade/forward verification. |
| Distributed task transport | Ad hoc HTTP worker calls or filesystem queue | Celery + managed Redis | Redelivery, acknowledgements, retry scheduling, process separation. |
| Transaction cleanup | Scattered commit/rollback calls | SQLAlchemy session context managers | Predictable atomicity and cleanup. |
| Tenant isolation | Remembering `WHERE organization_id` in arbitrary queries | Tenant-scoped repositories + composite foreign keys | Default-deny access and database-enforced ownership. |
| Idempotency | Checking for duplicates and then inserting | Unique constraints + transactional insert/conflict handling | Race-safe under duplicate delivery. |
| Baseball innings | Floating-point parsing of `7.2` | Integer outs conversion | Correct baseball semantics and exact aggregation. |
| Raw evidence format | One column per changing source header | JSONB raw row plus typed canonical facts | Preserves exact evidence without binding schema to NCAA header drift. |

**Key insight:** durable jobs are not made exactly-once by the queue. They become safe through at-least-once delivery plus database-enforced idempotency and atomic publication.
</dont_hand_roll>

<common_pitfalls>
## Common Pitfalls

### Pitfall 1: Duplicate Snapshot After Worker Redelivery
**What goes wrong:** A worker commits data, crashes before acknowledgement, and the broker runs the task again.
**Avoidance:** Unique idempotency key, terminal-state short circuit, one job-to-snapshot relationship, late acknowledgement only with idempotent task logic.
**Warning signs:** Multiple snapshot IDs for one `report_jobs.id` or idempotency key.

### Pitfall 2: Ready Snapshot With Missing Facts
**What goes wrong:** Section rows and snapshot status commit separately or analytics queries can see drafts.
**Avoidance:** Validate core sections and publish within one transaction; filter all consumers to published states; add rollback and crash-boundary tests.
**Warning signs:** `ready` snapshot with missing batting/pitching section or zero source records without verified-empty evidence.

### Pitfall 3: Migration Drift
**What goes wrong:** SQLAlchemy models change without a migration, or autogenerated constraints are incomplete.
**Avoidance:** Name every constraint, manually review migrations, run `alembic upgrade head`, `alembic downgrade base`, re-upgrade, and `alembic check` against PostgreSQL.
**Warning signs:** Autogenerate proposes changes immediately after applying head.

### Pitfall 4: Redis Becomes the Truth
**What goes wrong:** Celery reports success while the database job is running/failed, or broker retention loses historical status.
**Avoidance:** Ignore Celery results for product state; write every transition to `report_jobs` and expose only that state.
**Warning signs:** API or tests inspect `AsyncResult.state` for customer-visible status.

### Pitfall 5: Cross-Tenant Foreign Keys
**What goes wrong:** A fact uses organization A but points at organization B's snapshot.
**Avoidance:** Composite `(id, organization_id)` parent keys and child foreign keys, plus negative database tests.
**Warning signs:** Tenant-owned child tables have only a single-column `snapshot_id` foreign key.

### Pitfall 6: Browser Capacity Escapes the Old File Lock
**What goes wrong:** Multiple worker machines each honor a local lock but exceed the Browserless account limit globally.
**Avoidance:** Use one PostgreSQL-backed distributed lease shared by synchronous app and worker acquisition paths; aggregate active leases must never exceed `BROWSERLESS_ACCOUNT_MAX_SESSIONS`.
**Warning signs:** Any hosted acquisition path uses a process-local/file lock, a per-process quota, or a namespace/account limit different from the other paths.

### Pitfall 7: Successful HTML Retained Forever
**What goes wrong:** Raw pages become an unbounded duplicate archive and broaden data-retention exposure.
**Avoidance:** Permanently retain exact extracted rows and hashes; retain HTML only for failed/blocked jobs with 30-day expiry metadata and purge tests.
**Warning signs:** Published snapshot rows contain an HTML blob or successful-page object key.

### Pitfall 8: One Fly Volume Applied to Both Process Groups
**What goes wrong:** Adding a worker process causes deployment failure or attempts to attach one local volume to separate web and worker machines.
**Avoidance:** Scope the existing `/data` mount to the `app` process only. The worker uses an ephemeral cache directory because durable job/section state lives in PostgreSQL.
**Warning signs:** `[[mounts]]` has no `processes = ["app"]` selector after the worker group is added.
</common_pitfalls>

<code_examples>
## Code Examples

### SQLAlchemy Transaction Boundary
```python
# Source: https://docs.sqlalchemy.org/en/20/orm/session_basics.html
with Session.begin() as session:
    session.add(snapshot)
    publish_snapshot(session, snapshot.id)
```

### Celery Idempotent Late-Acknowledged Task
```python
# Source: https://docs.celeryq.dev/en/stable/userguide/tasks.html
@celery.task(bind=True, acks_late=True, ignore_result=True)
def build_report(self, job_id: str):
    run_idempotent_report_job(job_id)
```

### Database-Enforced Idempotency
```sql
-- One accepted key always resolves to one durable job, including terminal replay.
ALTER TABLE report_jobs
ADD CONSTRAINT uq_report_jobs_idempotency
UNIQUE (organization_id, idempotency_key);
```

These examples illustrate the boundary only. Migration and service code must use the repository's concrete names, named constraints, and transition validation.
</code_examples>

<validation_architecture>
## Validation Architecture

### Test Layers
1. **Pure unit tests:** innings conversion, missing-versus-zero normalization, deterministic fallback identities, conflict detection, section validation, retry classification.
2. **PostgreSQL integration tests:** migrations, composite tenant foreign keys, uniqueness/idempotency races, immutable publication, rollback behavior, nullable typed facts, JSONB source records.
3. **Worker integration tests:** Celery eager-mode task entry with PostgreSQL state, duplicate delivery, retry transition, terminal-state short circuit, sanitized failures. Broker transport itself is covered by a Compose smoke test rather than mocked as business state.
4. **Compatibility tests:** all existing route, NCAA workflow, cache, branding, and workbook tests remain green; representative D1/D2/D3 fixtures publish equivalent canonical facts.
5. **Deployment/config checks:** Docker image contains the new package and migrations; Compose defines PostgreSQL/Redis; Fly defines one release migration command and separate web/worker process groups.

### Commands
- Quick pure suite: `.venv/bin/python -m unittest tests.test_canonical_data -v`
- Existing regression suite: `.venv/bin/python -m unittest discover -s tests -v`
- PostgreSQL integration: `DATABASE_URL=postgresql+psycopg://roster_scout:roster_scout@localhost:5432/roster_scout_test .venv/bin/python -m unittest tests.test_snapshot_persistence tests.test_report_jobs -v`
- Migration verification: `DATABASE_URL=... .venv/bin/alembic upgrade head` followed by `.venv/bin/alembic check`
- Full phase check: compilation, full unittest discovery, catalog validation, migration check, and `git diff --check`

### Required Fixtures
- D1, D2, and D3 batting/pitching HTML already represented in `tests/test_ncaa_workflow.py`.
- Verified-empty core section fixture.
- Missing-ID unique fallback, same-name teammate, cross-team same-name, and conflicting authoritative-ID fixtures.
- Failure boundaries before fact write, after fact write/before publish, and after publish/before task acknowledgement.
- Two organizations attempting cross-linked snapshot/fact insertion.

### Nyquist Rules
- Every task that changes canonical behavior adds an automated assertion in the same task.
- No three consecutive implementation tasks may rely only on manual verification.
- PostgreSQL-specific invariants must run against PostgreSQL, not SQLite.
- Deployment provisioning remains manual, but config syntax and process commands are automated source assertions.
</validation_architecture>

<threat_research>
## Threat Model Inputs for Planning

| Threat | Severity | Required mitigation |
|--------|----------|---------------------|
| Cross-organization fact relationship | HIGH | Composite organization-scoped foreign keys and negative integration tests. |
| Duplicate delivery publishes duplicate snapshot | HIGH | Unique idempotency constraint, locked job transition, one job-to-snapshot relationship. |
| Draft/failed data appears ready | HIGH | Atomic legal transition and published-only query boundary. |
| Secret or raw exception text stored in job failure | HIGH | Failure-category allowlist and sanitized operator details. |
| Successful HTML retained beyond policy | MEDIUM | No successful HTML column/object key; 30-day failed-page expiry and purge behavior. |
| Queue message leaks customer/source data | MEDIUM | Put only opaque job UUID in Celery messages. |
| Migration creates unscoped data window | HIGH | Non-null tenant keys, reviewed migration, release command failure blocks rollout. |
</threat_research>

<resolved_questions>
## Open Questions — RESOLVED

1. **Production managed-service provisioning and cost — RESOLVED**
   - Production launch remains blocked until managed PostgreSQL, Redis, object/evidence storage, and uninterrupted Fly worker billing/capacity are verified.
   - Local Compose is development-only and does not satisfy the production provisioning gate.

2. **Global Browserless capacity after horizontal worker scaling — RESOLVED**
   - Use a PostgreSQL-backed distributed lease shared by both synchronous app and worker paths.
   - Aggregate active leases must never exceed the account-wide configured limit; no process group receives an independent quota.

3. **Thirty-day failed HTML storage location — RESOLVED**
   - Keep gzip failed/blocked HTML in PostgreSQL `failure_evidence` with a hard 30-day maximum and scheduled purge.
   - Successful HTML is never retained; purge clears expired bytes while preserving sanitized audit metadata.
</resolved_questions>

<sources>
## Sources

### Primary (HIGH confidence)
- [SQLAlchemy session basics](https://docs.sqlalchemy.org/en/20/orm/session_basics.html) — explicit transaction/session scope and concurrency model.
- [Alembic autogeneration](https://alembic.sqlalchemy.org/en/latest/autogenerate.html) — reviewed migrations, named constraint limitations, and schema comparison.
- [Celery task documentation](https://docs.celeryq.dev/en/stable/userguide/tasks.html) — idempotency, late acknowledgements, retries, backoff, and redelivery.
- [Celery Redis transport](https://docs.celeryq.dev/en/stable/getting-started/backends-and-brokers/redis.html) — broker configuration and visibility timeout.
- [PostgreSQL constraints](https://www.postgresql.org/docs/current/ddl-constraints.html) and [CREATE INDEX](https://www.postgresql.org/docs/current/sql-createindex.html) — uniqueness, composite constraints, and partial unique indexes.
- [Fly background workers](https://fly.io/learn/background-workers/) — process groups, worker isolation, shutdown behavior, and scaling.
- [Fly Volumes overview](https://fly.io/docs/volumes/overview/) — one-to-one Machine/volume attachment and ephemeral root filesystem boundaries.
- [Fly Managed Postgres](https://fly.io/docs/mpg/) — managed service, backups, HA, encryption, pooling, and current service boundaries.
- [PyPI SQLAlchemy](https://pypi.org/project/SQLAlchemy/), [Alembic](https://pypi.org/project/alembic/), [Psycopg](https://pypi.org/project/psycopg/), and [Celery](https://pypi.org/project/celery/) — current stable versions as of research date.

### Project Evidence (HIGH confidence)
- `.planning/phases/01-canonical-snapshot-foundation/01-CONTEXT.md` — locked user decisions.
- `.planning/research/STACK.md` and `.planning/research/ARCHITECTURE.md` — milestone-wide canonical architecture.
- `CONTEXT.md`, `app.py`, `roster_scraper.py`, `usage_limits.py`, `fly.toml`, `Dockerfile`, `compose.yaml`, and `DEPLOYMENT.md` — current runtime and deployment seams.
</sources>

<metadata>
## Metadata

**Research scope:** PostgreSQL schema/migrations, immutable snapshot publication, canonical baseball facts, Celery/Redis delivery, Fly worker deployment, and regression validation.

**Confidence breakdown:**
- Standard stack: HIGH — current official documentation and release metadata.
- Architecture: HIGH — established transaction/idempotency patterns mapped to the current code.
- Deployment: MEDIUM-HIGH — process-group pattern is official; live billing/provisioning remains unresolved.
- Validation: HIGH — existing deterministic fixtures plus PostgreSQL-specific integration coverage.

**Research date:** 2026-11-01
**Valid until:** 2026-12-01
</metadata>

---

*Phase: 01-canonical-snapshot-foundation*
*Research completed: 2026-11-01*
*Ready for planning: yes*
