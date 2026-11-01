# Architecture Research: Opponent Intelligence

**Date:** 2026-11-01

## Target Components

```text
Browser
  -> Flask authentication + organization authorization
  -> report library / report detail / refresh / download
  -> short-lived Power BI embed configuration

Flask application
  -> PostgreSQL metadata and canonical facts
  -> durable report-build queue
  -> immutable workbook object storage
  -> Power BI REST API

Report worker
  -> existing NCAA catalog + Browserless fetch boundary
  -> season batting/pitching/fielding tables
  -> split, career, and game-log collectors
  -> canonical normalization + derived metric engine
  -> atomic snapshot publication

Power BI
  -> curated PostgreSQL analytics views
  -> semantic model with organization/report RLS
  -> branded embedded report
```

## Canonical Domain Model

Core identity tables:
- `users` maps the external identity subject to application identity
- `organizations`
- `organization_memberships` with role and status
- `organization_invitations` with expiry and redemption audit

Report lifecycle tables:
- `reports` identifies organization + opponent + season
- `report_snapshots` is immutable after publication and links `previous_snapshot_id`
- `report_sections` records each section's status, row count, source time, and error category
- `report_artifacts` records workbook object key, SHA-256, schema version, and byte size
- `report_jobs` records queued/running/retry/terminal state without containing authentication secrets

Baseball dimensions:
- `teams`, `team_seasons`, `players`, `player_team_seasons`, `games`, `split_types`, `metric_definitions`, `sources`

Facts:
- season batting, pitching, and fielding facts
- split batting and pitching facts
- player career-season facts
- player game-log facts
- derived metric facts or deterministic views keyed to metric-definition version

Every fact includes `snapshot_id`; shared dimensions may be deduplicated, but facts never float outside snapshot lineage.

## Snapshot Publication

1. Authorize the requesting organization and create a queued job.
2. Create a draft snapshot with an explicit schema version.
3. Collect each source section with bounded concurrency and source-specific retry policy.
4. Store raw provenance metadata and normalize into staging records.
5. Validate keys, row counts, denominators, innings notation, and cross-section player linkage.
6. Compute derived metrics with versioned definitions.
7. Atomically mark the snapshot `ready` or `partial`; never publish a half-written section as complete.
8. Generate and store the exact workbook artifact from the published snapshot.
9. Trigger Power BI semantic-model refresh and track its independent status.

A failed refresh creates no successor snapshot. A partial snapshot may be published only when its missing sections are clearly identified and core season roster/batting/pitching acceptance rules pass.

## Power BI Tenancy

Initial target:
- one curated report and semantic model
- all analytics rows carry `organization_id` and `snapshot_id`
- embed token contains an effective organization identity
- dynamic RLS filters organization rows
- application separately authorizes report and snapshot ownership before token generation

Scale/security migration option:
- workspace or semantic-model isolation per organization using service principal profiles
- same Flask authorization and snapshot schema remain valid

The roadmap must include automated cross-organization denial tests before customer embedding is enabled.

## Build Order

1. Canonical snapshot schema and provenance contract
2. Authentication, organizations, invitations, and report authorization
3. Durable report jobs, report library, immutable versioning, and exact downloads
4. Expanded NCAA collectors and raw fact normalization
5. Derived metrics and parity-tested detailed workbook
6. Power BI semantic model, RLS, refresh orchestration, and embed-token API
7. Customer dashboard integration, branding, operational hardening, and release verification

The data contract precedes both workbook expansion and Power BI so neither surface becomes the accidental source of truth.

## Primary Sources

- Microsoft, embed for customers: https://learn.microsoft.com/en-us/power-bi/guidance/powerbi-implementation-planning-usage-scenario-embed-for-your-customers
- Microsoft, multitenancy: https://learn.microsoft.com/en-us/power-bi/guidance/develop-scalable-multitenancy-apps-with-powerbi-embedding
- Microsoft, embed tokens: https://learn.microsoft.com/en-us/power-bi/developer/embedded/generate-embed-token
- Microsoft, dynamic binding: https://learn.microsoft.com/en-us/power-bi/developer/embedded/embed-dynamic-binding
- Microsoft, enhanced refresh: https://learn.microsoft.com/en-us/power-bi/connect-data/asynchronous-refresh

