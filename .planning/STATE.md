---
gsd_state_version: 1.0
milestone: v2.0
milestone_name: milestone
status: executing
stopped_at: Completed 01-10-PLAN.md
last_updated: "2026-11-09T02:52:49.296Z"
last_activity: 2026-11-09 -- Completed 01-10 distributed capacity and secure local bindings
progress:
  total_phases: 5
  completed_phases: 0
  total_plans: 13
  completed_plans: 5
  percent: 38
---

# Project State

## Project Reference

See: `.planning/PROJECT.md` (updated 2026-11-01)

**Core value:** A baseball staff can reliably turn fragmented opponent data into a trustworthy, reusable scouting report with interactive analysis and a portable Excel download.
**Current focus:** Phase 01 — canonical-snapshot-foundation

## Current Position

Phase: 01 (canonical-snapshot-foundation) — EXECUTING
Plan: 5 of 13
Status: Ready to execute
Last activity: 2026-11-09 -- Completed 01-10 distributed capacity and secure local bindings

Progress: [████░░░░░░] 38%

## Performance Metrics

**Velocity:**

- Total plans completed: 5
- Average duration: 8 min
- Total execution time: 39 min

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|---|---:|---:|---:|
| 1. Canonical Snapshot Foundation | 5 | 39 min | 8 min |

**Plan History:**

| Phase | Plan | Duration | Tasks | Files |
|---|---|---:|---:|---:|
| 01 | 01 | 6 min | 3 | 16 |
| 01 | 02 | 5 min | 3 | 17 |
| 01 | 03 | 6 min | 3 | 9 |
| 01 | 04 | 11 min | 2 | 6 |
| 01 | 10 | 11 min | 3 | 9 |

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

Last session: 2026-11-09T02:52:49.282Z
Stopped at: Completed 01-10-PLAN.md
Resume file: None
