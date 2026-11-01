# Pitfall Research: Opponent Intelligence

**Date:** 2026-11-01

| Pitfall | Warning signs | Prevention | Roadmap phase |
|---|---|---|---|
| Treating authentication as authorization | Any signed-in user can change a report ID and view another school | Organization-scoped queries, default-deny service methods, negative integration tests on pages/APIs/exports/embed tokens | Identity and tenancy |
| Auto-joining by `.edu` domain | Staff from unrelated campuses or shared systems gain access | Explicit invitation and admin approval; domains only suggest an organization | Identity and tenancy |
| Leaking Power BI data through RLS mistakes | Embed token works but a modified filter reveals another organization | Effective identity on every token, RLS test harness, organization/report keys in every fact, application authorization before token creation | Power BI foundation |
| Building one synchronous scrape request | Timeouts, duplicate browser sessions, abandoned jobs, user retries | Durable queued jobs, idempotency keys, bounded concurrency, status polling | Report lifecycle |
| Mutating a snapshot on refresh | Yesterday's workbook changes after a new scrape | Immutable facts/artifacts, successor linkage, checksum, generated/source timestamps | Report lifecycle |
| Conflating zero with missing | Blocked fielding or split page appears as a player having zero events | Section availability status and nullable values; publish completeness in dashboard/workbook | Data foundation |
| Hard-coding split IDs forever | A new NCAA season returns the wrong table or no rows | Discover/validate IDs per season and stat type; cache with schema validation; record source IDs | Detailed NCAA data |
| Excessive NCAA request fan-out | Browserless cost and NCAA blocking spike for career/game logs | Request budget, bounded concurrency, caching by source URL/season/player, progressive sections, backoff | Detailed NCAA data |
| Mishandling baseball innings notation | `7.2` is treated as 7.2 innings instead of 7 2/3 | Store outs or thirds as canonical value; format innings only at presentation | Data foundation |
| Duplicate/merged player identity errors | Same-name players merge or transfers split incorrectly | NCAA player ID first, source-scoped fallback key, explicit unresolved identity state | Detailed NCAA data |
| Unsupported advanced metrics | wOBA/FIP silently use MLB constants | Version NCAA season/division weights; return unavailable when constants fail validation | Metric engine |
| Excel and Power BI drift | Same player shows different OPS or FIP in each surface | Shared canonical metric service/views and parity fixtures | Metric engine |
| Power BI refresh controls report readiness | A complete workbook is hidden because semantic refresh is delayed | Independent snapshot, artifact, and semantic-refresh statuses | Power BI foundation |
| Depending on Power BI visual export | Export omits hidden fields, is disabled, or hits row/visual limits | Preserve Roster Scout's complete server-generated workbook | Detailed report |
| Capacity/licensing surprise | Development works with tokens but production embed fails | Capacity decision and cost gate before production embedding; capacity health monitoring | Power BI foundation |
| Storing secrets/tokens in logs | Embed, invitation, or passwordless URLs appear in error logs | Structured redaction, short-lived tokens, secret manager, security tests | Identity and operations |
| Unbounded snapshot storage | Repeated series refreshes grow DB/artifacts unexpectedly | Retention/accounting metrics without deleting user-visible snapshots silently | Operations |

## Release Blockers

Do not launch customer embedding until:
- cross-organization access tests cover HTML, JSON, XLSX, refresh, and embed-token paths;
- Power BI RLS denial is verified with at least two organizations;
- a capacity-backed production workspace is available;
- snapshot creation remains recoverable when NCAA, Browserless, object storage, or Power BI independently fails;
- metric parity tests compare Excel cells, canonical facts, and Power BI-facing views.

## Primary Sources

- OWASP authorization: https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html
- OWASP email identity guidance: https://cheatsheetseries.owasp.org/cheatsheets/Email_Validation_and_Verification_Cheat_Sheet.html
- Microsoft Power BI embedded security: https://learn.microsoft.com/power-bi/developer/embedded-row-level-security
- Microsoft Power BI export limitations: https://learn.microsoft.com/en-us/power-bi/visuals/power-bi-visualization-export-data
- Microsoft Power BI Embedded FAQ and production capacity: https://learn.microsoft.com/power-bi/developer/embedded/embedded-faq

