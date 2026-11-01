# Research Summary: v2.0 Opponent Intelligence

**Date:** 2026-11-01

## Recommendation

Build v2.0 around an immutable canonical report snapshot, not around an expanded workbook or a Power BI file. NCAA collectors publish versioned facts and provenance into PostgreSQL; those same facts render the exact Excel artifact and feed a curated Power BI semantic model. Flask owns customer authentication, organization authorization, report history, refresh actions, downloads, and short-lived embed configuration.

## Stack Additions

- Managed PostgreSQL with SQLAlchemy/Alembic
- Managed passwordless/federated identity provider; application-owned organizations and invitations
- Durable background jobs and status polling
- S3-compatible immutable workbook storage
- Power BI Embedded app-owns-data with a service principal, production capacity, API-triggered Import-model refresh, and organization RLS

## Table Stakes

- Login-gated organization workspaces
- Explicit membership invitations and roles
- Asynchronous report jobs with section-level completeness
- Immutable snapshots and explicit refresh-to-successor behavior
- NCAA provenance, fielding, seven split families, career seasons, and game logs
- Versioned derived metrics, including guarded NCAA-specific wOBA/FIP constants
- Opponent-branded Power BI pages and full snapshot Excel download
- Cross-surface metric parity and cross-organization denial tests

## Critical Architectural Decisions

1. PostgreSQL canonical facts are the source of truth.
2. Excel and Power BI are sibling projections of the same snapshot.
3. Report readiness and Power BI refresh readiness are separate states.
4. Authentication never implies organization authorization.
5. Missing source data is explicit; it is never converted to zero.
6. NCAA split identifiers and weighted-metric constants are versioned source data.
7. Long-running report work moves out of the request process.

## Watch Outs

- Power BI production embedding is a paid capacity dependency.
- Shared semantic-model RLS requires strict effective-identity and negative testing; workspace isolation remains the scale-up path.
- Career and game-log acquisition can multiply NCAA requests dramatically and needs caching, request budgets, and partial-section behavior.
- The current NCAA ecosystem now publishes additional situational and player datasets, but freshness and coverage must be validated before treating them as authoritative fallbacks.
- `collegebaseball` is useful for feature vocabulary but its live request and static metadata paths are too stale to adopt directly.

## Suggested Roadmap Shape

1. Canonical data and snapshot foundation
2. Identity, organizations, and authorization
3. Report jobs, library, immutable refresh, and artifact delivery
4. Detailed NCAA collectors and metric engine
5. Detailed Excel report and parity verification
6. Power BI semantic model, tenant security, and refresh orchestration
7. Embedded customer dashboard and production hardening

## Research Files

- `STACK.md` — recommended component boundaries
- `FEATURES.md` — detailed customer and analytics capabilities
- `ARCHITECTURE.md` — data model, flow, and build order
- `PITFALLS.md` — release risks and prevention

