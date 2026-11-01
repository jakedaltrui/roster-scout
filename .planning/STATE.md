# Project State

## Project Reference

See: `.planning/PROJECT.md` (updated 2026-11-01)

**Core value:** A baseball staff can reliably turn fragmented opponent data into a trustworthy, reusable scouting report with interactive analysis and a portable Excel download.
**Current focus:** Phase 1 — Canonical Snapshot Foundation

## Current Position

Phase: 1 of 5 (Canonical Snapshot Foundation)
Plan: 0 of 3 in current phase
Status: Ready to discuss and plan
Last activity: 2026-11-01 — v2.0 specification, requirements, research, and roadmap created

Progress: [░░░░░░░░░░] 0%

## Performance Metrics

**Velocity:**
- Total plans completed: 0
- Average duration: —
- Total execution time: 0 hours

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|---|---:|---:|---:|
| 1. Canonical Snapshot Foundation | 0 | 0 min | — |

## Accumulated Context

### Decisions

Full decision log: `.planning/PROJECT.md`.

- v2.0 uses immutable snapshots and explicit refresh-to-successor behavior.
- Organizations share reports through explicit membership; email domain is not authorization.
- Excel and Power BI are sibling projections of one canonical snapshot.
- The existing Python/Browserless NCAA acquisition boundary remains in place.

### Pending Todos

None recorded under `.planning/todos/pending/`.

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

Last session: 2026-11-01
Stopped at: Roadmap ready; Phase 1 has not been discussed or planned
Resume file: None
