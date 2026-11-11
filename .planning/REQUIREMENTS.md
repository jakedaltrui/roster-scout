# Requirements: Roster Scout v2.0 Opponent Intelligence

**Defined:** 2026-11-01  
**Core Value:** A baseball staff can reliably turn fragmented opponent data into a trustworthy, reusable scouting report with both interactive analysis and a portable Excel download.

## v2.0 Requirements

### Authentication

- [x] **AUTH-01**: A visitor must authenticate before accessing team search, report creation, report history, dashboards, exports, or refresh actions.
- [x] **AUTH-02**: A user can sign in without a password through an emailed single-use code or magic link with bounded expiry and request throttling.
- [x] **AUTH-03**: A user can sign in through Microsoft or Google federation while retaining the same Roster Scout account and organization memberships.
- [x] **AUTH-04**: An organization can use an approved OIDC or SAML SSO connection without changing report ownership or authorization rules.
- [x] **AUTH-05**: A user session persists across ordinary browser refreshes and can be explicitly terminated through logout.

### Organizations

- [x] **ORG-01**: Every report, snapshot, artifact, job, and invitation belongs to exactly one organization.
- [x] **ORG-02**: An organization administrator can invite a user at any valid email address; an `.edu` domain is not required.
- [x] **ORG-03**: Accepting an invitation grants membership only in the inviting organization.
- [x] **ORG-04**: Organization roles distinguish administrators who manage membership from members who create and review reports.
- [x] **ORG-05**: An organization administrator can view pending invitations, revoke invitations, and remove members.
- [ ] **ORG-06**: All active organization members can review the organization's report library and the snapshots they are authorized to access.
- [x] **ORG-07**: Email-domain matching may suggest an organization but can never grant membership automatically.

### Snapshot Foundation

- [x] **SNAP-01**: A report identifies one organization, one opponent team, one NCAA division, and one season.
- [x] **SNAP-02**: A report build runs outside the request process and exposes queued, running, partial, ready, and failed states.
- [x] **SNAP-03**: A published snapshot is immutable and records schema version, creator, source collection time, publication time, and predecessor snapshot when present.
- [x] **SNAP-04**: Each report section records its own status, row count, source time, and sanitized failure category.
- [ ] **SNAP-05**: A user can explicitly refresh a report, producing a new linked snapshot without modifying prior snapshots.
- [ ] **SNAP-06**: A user can review all dated snapshots for a report and identify the newest complete or partial version.
- [ ] **SNAP-07**: Repeating an accepted report-build request with the same idempotency key does not create duplicate jobs or snapshots.
- [ ] **SNAP-08**: A failed report build leaves no snapshot falsely marked ready and can be retried safely.
- [ ] **SNAP-09**: A Power BI refresh failure does not invalidate a completed snapshot or its Excel artifact.

### Provenance and Canonical Data

- [x] **DATA-01**: NCAA player records retain stable player ID when available, source-scoped fallback identity when unavailable, and unresolved-identity status when neither is safe.
- [x] **DATA-02**: Every snapshot retains team ID, season-team ID, season ID, division, stat-category IDs, source URLs, and collection timestamps.
- [x] **DATA-03**: Batting, pitching, and fielding facts relate to one canonical player identity so two-way players are not duplicated as people.
- [x] **DATA-04**: Transfers retain one player identity with distinct player-team-season records.
- [x] **DATA-05**: Canonical numerical values distinguish zero from missing or unavailable source data.
- [x] **DATA-06**: Innings pitched are stored canonically as outs or thirds and are never calculated as ordinary base-10 decimals.
- [x] **DATA-07**: Raw source values needed for audit and metric recomputation remain associated with their snapshot facts.
- [x] **DATA-08**: Derived values identify the metric-definition version and any season/division weight set used.
- [x] **DATA-09**: Database constraints prevent canonical facts from referencing another organization's snapshot.
- [x] **DATA-10**: Power BI-facing views and Excel generation read from the same published snapshot facts.

### Detailed Statistics

- [ ] **STAT-01**: Hitter season facts include the available NCAA counting statistics needed to report PA, AVG, OBP, SLG, OPS, ISO, BB%, K%, BABIP, singles, and total bases.
- [ ] **STAT-02**: Pitcher season facts include the available NCAA counting statistics needed to report ERA, WHIP, K/9, BB/9, H/9, HR/9, K/BB, K%, BB%, BABIP allowed, and FIP.
- [ ] **STAT-03**: Fielding facts include games/starts when available, PO, A, E, total chances, double plays, fielding percentage, and available catcher PB/SBA/CSB values.
- [ ] **STAT-04**: Every derived metric handles a zero or unavailable denominator explicitly without emitting infinity or a misleading zero.
- [ ] **STAT-05**: wOBA uses a versioned NCAA season/division weight set and is unavailable with an explanatory reason when no verified weight set exists.
- [ ] **STAT-06**: FIP uses a versioned NCAA season/division constant and is unavailable with an explanatory reason when no verified constant exists.
- [ ] **STAT-07**: Metric definitions and formulas are available in report methodology metadata.
- [ ] **STAT-08**: Representative D1, D2, and D3 fixtures produce the same metric schema and classification behavior.

### Split Statistics

- [ ] **SPLT-01**: Batting split facts support vs LHP, vs RHP, runners on, bases empty, bases loaded, runners in scoring position, and two-outs contexts.
- [ ] **SPLT-02**: Pitching split facts support vs LHB, vs RHB, runners on, bases empty, bases loaded, runners in scoring position, and two-outs contexts when the NCAA source provides them.
- [ ] **SPLT-03**: Each split record retains canonical split code, display label, season/stat type, source identifier, source URL, and availability status.
- [ ] **SPLT-04**: Split source identifiers are discovered or validated per season and stat type rather than assumed permanent.
- [ ] **SPLT-05**: A missing split is displayed as unavailable and never as a zero-stat split.

### Career and Game Logs

- [ ] **HIST-01**: A player's career view lists each available season with school, team ID, division, and season statistics under the stable NCAA player identity.
- [ ] **HIST-02**: A career aggregate is labeled separately from individual season rows and is recomputed only from available compatible facts.
- [ ] **HIST-03**: Player game logs identify date, opponent, opponent ID when available, and available batting, pitching, and fielding lines.
- [ ] **HIST-04**: Missing historical seasons or games are disclosed in section completeness instead of being silently omitted.
- [ ] **HIST-05**: Career and game-log collection uses bounded concurrency, source caching, request budgets, and retry/backoff controls.
- [ ] **HIST-06**: A customer can drill from a roster player into season, split, career, and game-log views for the same snapshot.

### Excel Report

- [ ] **XLSX-01**: A ready or partial snapshot can produce a branded workbook containing Report Summary, Roster, Hitters, Pitchers, Fielding, Hitting Splits, Pitching Splits, Career Stats, Game Logs, and Sources & Methodology worksheets.
- [ ] **XLSX-02**: The workbook identifies the opponent, season, snapshot generation time, refresh lineage, section completeness, and data freshness.
- [ ] **XLSX-03**: The workbook writes numeric cells as numbers, preserves safe plain-text handling, and applies the correct display precision for each metric.
- [ ] **XLSX-04**: Downloading an older snapshot returns its exact stored workbook artifact rather than rebuilding it from current upstream data.
- [ ] **XLSX-05**: The workbook records a schema version and SHA-256 artifact checksum in snapshot metadata.
- [ ] **XLSX-06**: Shared fixture values match across canonical facts, Excel cells, and Power BI-facing views.

### Power BI Embedded

- [ ] **PBI-01**: An authenticated Roster Scout customer can view the embedded report without signing in to Power BI or holding a Power BI license.
- [ ] **PBI-02**: The embedded report includes Opponent Overview, Roster, Hitting, Pitching, Fielding, Situational Splits, Player Detail, and Sources & Data Quality pages.
- [ ] **PBI-03**: The report is branded for the selected opponent and identifies the active snapshot and data freshness.
- [ ] **PBI-04**: The server generates only short-lived embed tokens after authorizing the user's organization and requested snapshot.
- [ ] **PBI-05**: The semantic model enforces organization isolation using effective identity and row-level security or a stronger workspace-isolation model.
- [ ] **PBI-06**: A snapshot publication can trigger an asynchronous semantic-model refresh and expose pending, completed, and failed refresh states.
- [ ] **PBI-07**: The dashboard remains unavailable for a new snapshot until its semantic refresh completes, while the completed Excel artifact remains downloadable.
- [ ] **PBI-08**: The report page provides a full snapshot Excel download independent of Power BI visual export.
- [ ] **PBI-09**: A customer can filter and drill through report visuals without changing the immutable snapshot data.
- [ ] **PBI-10**: Production embedding is blocked unless the report resides in an Embedded, Premium, or Fabric capacity-backed workspace.

### Security and Operations

- [ ] **SECU-01**: Cross-organization access is denied for report HTML, JSON APIs, refresh actions, workbook downloads, job status, and embed-token endpoints.
- [ ] **SECU-02**: Authorization checks derive organization context from the authenticated membership, never from an untrusted request field alone.
- [x] **SECU-03**: Passwordless, invitation, session, Power BI, and service-principal secrets or tokens never appear in application logs.
- [x] **SECU-04**: Security-relevant membership, report, refresh, export, and embed-token events are auditable by actor, organization, target, and time without recording secret material.
- [x] **SECU-05**: External failures from NCAA, Browserless, identity, object storage, database, and Power BI produce distinct sanitized operational states.
- [ ] **SECU-06**: Production readiness includes automated cross-organization denial tests and a manual two-organization Power BI RLS verification.
- [x] **OPS-01**: Report jobs apply bounded retries and never run more NCAA browser sessions than the configured capacity permits.
- [x] **OPS-02**: Successful source responses and normalized sections are cached with schema/version keys to control upstream requests and rebuild cost.
- [ ] **OPS-03**: Operators can identify stuck jobs, failed Power BI refreshes, unavailable source sections, and capacity exhaustion without reading customer data.
- [x] **OPS-04**: Database migrations and snapshot schema upgrades preserve existing published snapshots or explicitly migrate them with verification.
- [x] **OPS-05**: Existing current-season D1/D2/D3 selector and basic workbook behavior remain covered by regression tests throughout the migration.

## Future Requirements

### Comparison and Collaboration

- **COMP-01**: A user can compare metric changes between two snapshots of the same opponent.
- **COMP-02**: A user can add organization-private notes to a report or player.
- **COMP-03**: A user can share a report with another organization through an explicit, revocable grant.

### Broader Coverage

- **COVR-01**: JUCO reports use the canonical snapshot model with parity appropriate to the available source data.
- **COVR-02**: Users can generate arbitrary historical team-season reports independent of a current opponent workflow.

## Out of Scope

| Feature | Reason |
|---|---|
| Anonymous report access | Conflicts with organization-owned customer data and embed-token security |
| Automatic `.edu` domain membership | Email control is not authorization to a school's reports |
| Live in-game tracking | Requires a different real-time data product and reliability model |
| Customer-authored Power BI reports | The milestone delivers a curated scouting product, not a BI authoring platform |
| Native mobile applications | Responsive web and downloadable Excel remain the delivery surfaces |
| Predictive player or game models | Requires separate model validation, evaluation, and disclosure work |
| Silent substitution of stale data | Users must see source freshness and section completeness |

## Milestone Acceptance Criteria

- [ ] An invited member can authenticate passwordlessly, enter the correct organization, create a report, and later reopen its saved snapshot.
- [ ] Refreshing a report creates a second snapshot while the first dashboard and workbook remain reproducible.
- [ ] A representative D1, D2, and D3 report contains season batting, pitching, fielding, derived metrics, split statistics, career history, game logs, and provenance or an explicit unavailable state for each section.
- [ ] The same fixture player and snapshot produce matching shared metric values in canonical storage, Excel, and Power BI-facing views.
- [ ] A member of Organization A cannot access any Organization B report, job, workbook, or Power BI data through identifier substitution.
- [ ] A customer can review the opponent-branded embedded report and download the complete snapshot workbook from the same report page.
- [ ] NCAA, Browserless, Power BI, or object-storage failure cannot corrupt an already published snapshot.

## Traceability

| Requirement group | Phase | Status |
|---|---:|---|
| SNAP-01–04, DATA-01–10, OPS-01–02, OPS-04–05 | Phase 1 | Complete |
| AUTH-01–05, ORG-01–07, SNAP-05–08, SECU-01–05 | Phase 2 | Pending |
| STAT-01–08, SPLT-01–05, HIST-01–06, OPS-03 | Phase 3 | Pending |
| XLSX-01–06 | Phase 4 | Pending |
| SNAP-09, PBI-01–10, SECU-06 | Phase 5 | Pending |

**Coverage:**
- v2.0 requirements: 77 total
- Mapped to phases: 77
- Unmapped: 0 ✓

---
*Requirements defined: 2026-11-01*  
*Last updated: 2026-11-01 after v2.0 Opponent Intelligence research*
