---
gsd_state_version: 1.0
milestone: v2.0
milestone_name: milestone
status: executing
stopped_at: Completed 01-09-PLAN.md
last_updated: "2026-11-09T21:31:22.955Z"
last_activity: 2026-11-09
progress:
  total_phases: 5
  completed_phases: 0
  total_plans: 13
  completed_plans: 11
  percent: 85
---

# Project State

## Project Reference

See: `.planning/PROJECT.md` (updated 2026-11-01)

**Core value:** A baseball staff can reliably turn fragmented opponent data into a trustworthy, reusable scouting report with interactive analysis and a portable Excel download.
**Current focus:** Phase 01 — canonical-snapshot-foundation

## Current Position

Phase: 01 (canonical-snapshot-foundation) — EXECUTING
Plan: 11 of 13
Status: Ready to execute
Last activity: 2026-11-09

Progress: [█████████░] 85%

## Performance Metrics

**Velocity:**

- Total plans completed: 11
- Average duration: 1h 27m
- Total execution time: 15h 52m

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|---|---:|---:|---:|
| 1. Canonical Snapshot Foundation | 11 | 15h 52m | 1h 27m |

**Plan History:**

| Phase | Plan | Duration | Tasks | Files |
|---|---|---:|---:|---:|
| 01 | 01 | 6 min | 3 | 16 |
| 01 | 02 | 5 min | 3 | 17 |
| 01 | 03 | 6 min | 3 | 9 |
| 01 | 04 | 11 min | 2 | 6 |
| 01 | 10 | 11 min | 3 | 9 |
| 01 | 05 | 14h 8m | 2 | 7 |
| 01 | 06 | 6 min | 2 | 3 |
| 01 | 07 | 35 min | 2 | 7 |
| 01 | 08 | 11 min | 3 | 5 |
| 01 | 12 | 5 min | 2 | 7 |
| 01 | 09 | 8 min | 2 | 5 |

## Accumulated Context

### Decisions

Full decision log: `.planning/PROJECT.md`.

- v2.0 uses immutable snapshots and explicit refresh-to-successor behavior.
- Organizations share reports through explicit membership; email domain is not authorization.
- Excel and Power BI are sibling projections of one canonical snapshot.
- The existing Python/Browserless NCAA acquisition boundary remains in place.
- [Phase 01]: Every tenant-owned snapshot child carries organization_id and uses a composite ownership foreign key.
- [Phase 01]: Published canonical data is protected by PostgreSQL triggers while artifact lifecycle state remains independently mutable.
- [Phase 01]: Database imports remain connection-free; callers create one session and own the publication transaction.
- [Phase 01]: PostgreSQL remains authoritative for durable job state; Redis carries only opaque job UUIDs. — At-least-once delivery and broker failure cannot diverge customer-visible state.
- [Phase 01]: Accepted jobs commit before broker publication and null enqueued_at is the recoverable handoff marker. — A repeated idempotent request can dispatch the same durable job after broker failure.
- [Phase 01]: Production is limited to one worker machine with Browserless concurrency 1-2. — Machine-local locks do not provide a distributed capacity lease.
- [Phase 01]: Keep the tuple scraper for workbooks and use live structured collection for durable jobs — Preserves the public API while making refresh provenance explicit.
- [Phase 01]: Scope ID-less fallback identities to one team-season and merge only reciprocal unique matches — Prevents silent teammate and transfer identity collisions.
- [Phase 01]: Commit exact source rows before canonical facts and publication — Allows same-job redelivery recovery without exposing half-ready snapshots.
- [Phase 01]: Revision 0001 is frozen from the schema that originally shipped rather than reconstructed from current ORM metadata.
- [Phase 01]: Canonical count validity is enforced at both the NCAA parsing boundary and the PostgreSQL storage boundary.
- [Phase 01]: Hosted app and worker processes share one PostgreSQL advisory-lock namespace and account-wide Browserless limit.
- [Phase 01]: Each Browserless lease owns a dedicated database connection so normal exit, connection loss, and process loss release capacity.
- [Phase 01]: Production retains one worker machine by default even though the database lease enforces aggregate capacity across machines.
- [Phase 01]: Authoritative NCAA players and metric definitions resolve through PostgreSQL conflict-safe inserts followed by scoped reselects. — Concurrent valid jobs converge without unique-key failure.
- [Phase 01]: Snapshot readiness requires exact snapshot-owned source, identity, and typed-fact traceability. — Section flags alone cannot prove canonical completeness.
- [Phase 01]: A detached PostgreSQL session advisory claim owns one report job across collection and publication.
- [Phase 01]: Claim contention returns a sanitized retry without incrementing attempts or mutating failure state.
- [Phase 01]: Job acceptance and one active dispatch intent commit in the same PostgreSQL transaction; enqueued_at remains audit metadata only.
- [Phase 01]: Publisher ownership uses token-matched PostgreSQL leases claimed with FOR UPDATE SKIP LOCKED; Redis carries only the opaque job UUID.
- [Phase 01]: Stale running jobs are redispatched only after timeout when the whole-attempt advisory claim is absent.
- [Phase 01]: Only a structurally valid NCAA grid with exactly one Player column can assert verified emptiness. — Malformed upstream markup must fail closed instead of publishing silent data loss.
- [Phase 01]: Complete exact same-job evidence is publication authority even after collection attempts are exhausted. — Redelivery can finalize committed source rows without another live fetch or attempt increment.
- [Phase 01]: Complete report sections are immutable for their job and version. — Partial recovery fetches only missing sections and cannot mix ordinals, checksums, versions, or collection times.
- [Phase 01]: Dispatch reconciliation runs as one dedicated scheduler process that calls existing job services instead of duplicating outbox SQL or state rules. — Preserves PostgreSQL outbox authority and keeps deployment glue separate from domain transitions.
- [Phase 01]: The Fly scheduler replaces its inherited environment with an allowlist containing only PostgreSQL, Redis, and nonsecret timing configuration. — Keeps Browserless credentials and app-specific configuration outside the scheduler runtime.
- [Phase 01]: Production remains blocked until managed PostgreSQL, Redis, object/evidence storage, and uninterrupted Fly worker billing/capacity are verified. — Local Compose is development-only and cannot satisfy durable production availability requirements.
- [Phase 01]: Failure HTML crosses the acquisition-to-job boundary only as a private 64 KiB byte payload with safe category, section, URL, and collection time.
- [Phase 01]: Evidence byte expiry is fixed at collected_at plus 696 hours; Plan 01-13 owns purge cadence and the 720-hour deletion guarantee.
- [Phase 01]: Evidence URLs rebuild authority from validated hostname and optional port, excluding userinfo, query, fragment, and malformed authorities.

### Pending Todos

- Add last-10 and conference-only stats (Phase 3) — define auditable recent-form and conference-only batting, pitching, and fielding scopes for canonical storage, Excel, and Power BI.

### Blockers/Concerns

- Fly trial billing was previously unresolved and can stop machines after five minutes; durable report workers require verified uninterrupted hosting.
- Production Power BI Embedded/Fabric capacity and the Microsoft Entra/service-principal environment are not yet provisioned.
- The managed identity provider and durable queue/object-storage vendors are intentionally deferred to phase planning.
- NCAA split IDs and NCAA-specific wOBA/FIP inputs require current-season validation before metrics can be published.

## Deferred Items

| Category | Item | Status | Deferred At |
|---|---|---|---|
| Comparison | Compare two snapshots of the same opponent | Future requirement | v2.0 initialization |
| Collaboration | Notes and cross-organization sharing | Future requirement | v2.0 initialization |
| Coverage | Full JUCO parity and arbitrary historical team-season reports | Future requirement | v2.0 initialization |

## Session Continuity

Last session: 2026-11-09T21:31:22.801Z
Stopped at: Completed 01-09-PLAN.md
Resume file: None
