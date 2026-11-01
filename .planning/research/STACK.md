# Stack Research: Opponent Intelligence

**Date:** 2026-11-01  
**Scope:** Add identity, organizations, persistent analytical snapshots, detailed NCAA statistics, Power BI customer embedding, and durable Excel exports to the existing Flask application.

## Preserve

- Flask remains the web application and API boundary.
- `roster_scraper.py` remains the acquisition/normalization boundary for NCAA and JUCO adapters.
- Browserless remains the proven hosted NCAA verification path.
- XlsxWriter remains the formatted workbook renderer.
- SportsDataverse lookup tables remain the current NCAA team/season ID source.

## Add

| Capability | Recommended stack boundary | Confidence | Rationale |
|---|---|---:|---|
| Transactional data | Managed PostgreSQL | High | Organizations, memberships, report versions, section statuses, provenance, and normalized facts require constraints and transactions. Power BI supports PostgreSQL Import and DirectQuery. |
| ORM and migrations | SQLAlchemy 2.x + Alembic + psycopg 3 | High | Mature Flask-compatible persistence with explicit migrations and PostgreSQL support. Pin exact versions during Phase 1 planning. |
| Authentication | Managed external identity provider supporting email OTP/magic links, Microsoft/Google federation, and enterprise OIDC/SAML | High | Do not build passwordless token issuance or SSO federation in application code. Keep application-level organizations and invitations independent from identity-provider identities. |
| Async work | Durable background-job system with retries and observable job state | High | Full report acquisition and Power BI refresh exceed a safe synchronous request lifecycle. Select the concrete queue during Phase 2 planning based on hosting and operational cost. |
| Artifact storage | S3-compatible object storage for immutable generated workbooks | Medium | Snapshot facts remain queryable in PostgreSQL; exact delivered XLSX artifacts and checksums remain reproducible without relying on an ephemeral Fly filesystem. |
| Analytics model | PostgreSQL star schema/views feeding a Power BI semantic model in Import mode | High | Snapshot data changes only when a report is generated or refreshed. Import mode isolates dashboard reads from the app database and supports deliberate API-triggered refresh. |
| Customer analytics | Power BI Embedded, app-owns-data, service principal, capacity-backed workspace | High | External customers do not need Power BI licenses. Production embedding requires Embedded/Premium/Fabric capacity. |
| Tenant enforcement | Application authorization plus Power BI effective identity/RLS | High | Flask must authorize the report before issuing an embed token; the semantic model must independently restrict rows by organization and report. |

## Power BI Delivery Contract

1. The Flask app authenticates the customer and verifies organization membership.
2. The app authorizes the requested report snapshot.
3. The server creates a short-lived Power BI embed token containing the effective organization identity.
4. The browser embeds the curated report with the Power BI JavaScript client.
5. The snapshot build writes canonical facts first, then requests an asynchronous semantic-model refresh.
6. The report page distinguishes data-ready from dashboard-refresh-pending and never exposes an embed token for an unauthorized organization.

Start with one curated report and semantic model. Use dynamic RLS for organization/report filtering only after cross-organization negative tests pass. Preserve a migration path to workspace/service-principal-profile isolation if customer count, contracts, or security requirements outgrow shared-model RLS.

## Data Delivery Contract

- PostgreSQL is the source of truth for snapshot metadata and canonical analytical facts.
- Power BI consumes typed views that include `organization_id`, `report_id`, `snapshot_id`, `team_id`, `season_team_id`, `season`, and player/source keys.
- Excel consumes the same canonical snapshot service, not independently scraped dictionaries.
- Workbook artifacts include a content hash and schema version.
- A Power BI refresh failure does not invalidate a completed snapshot or its Excel artifact.

## Do Not Add

- Do not add R or the `baseballr` runtime; Spike 001 invalidated that production path.
- Do not use a local SQLite database for production multitenancy.
- Do not store organization membership solely in identity-provider groups or email domains.
- Do not make Power BI visual export the only Excel delivery path; it is visual-scoped and subject to tenant settings and row limits.
- Do not use DirectQuery merely to avoid refresh orchestration; snapshot data benefits from an explicit publish boundary.
- Do not place Power BI service-principal secrets, embed tokens, passwordless tokens, or report access tokens in logs.

## Primary Sources

- Microsoft, Embed for your customers: https://learn.microsoft.com/en-us/power-bi/guidance/powerbi-implementation-planning-usage-scenario-embed-for-your-customers
- Microsoft, Power BI embedded security: https://learn.microsoft.com/power-bi/developer/embedded-row-level-security
- Microsoft, enhanced semantic-model refresh: https://learn.microsoft.com/en-us/power-bi/connect-data/asynchronous-refresh
- Microsoft, PostgreSQL connector: https://learn.microsoft.com/en-us/power-query/connectors/postgresql
- Microsoft, External ID overview: https://learn.microsoft.com/en-us/entra/external-id/external-identities-overview
- OWASP, Authorization Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html

