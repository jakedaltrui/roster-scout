# Roadmap: Roster Scout v2.0 Opponent Intelligence

## Overview

Roster Scout v2.0 is delivered in five dependency-ordered slices. The roadmap first establishes an immutable canonical snapshot and durable job boundary, then gates the product behind organization identity and exposes versioned report history. It next expands NCAA collection and metrics, renders the complete detailed workbook, and finally publishes the same snapshot through a secure customer-facing Power BI experience. Each phase preserves the existing D1/D2/D3 workflow while creating a verifiable capability used by the next phase.

## Phases

- [x] **Phase 1: Canonical Snapshot Foundation** - Persist provenance-rich immutable snapshots through durable report jobs. (completed 2026-11-01)
- [ ] **Phase 2: Identity, Organizations, and Report Library** - Gate Roster Scout behind passwordless organization access and versioned report workflows.
- [ ] **Phase 3: Detailed NCAA Intelligence** - Add fielding, advanced metrics, situational splits, career history, and game logs.
- [ ] **Phase 4: Detailed Excel Report** - Deliver the exact detailed snapshot as a branded ten-sheet workbook.
- [ ] **Phase 5: Embedded Opponent Dashboard** - Securely embed the opponent-branded Power BI report and harden production delivery.

## Phase Details

### Phase 1: Canonical Snapshot Foundation

**Goal:** As a Roster Scout operator, I want to publish existing NCAA D1/D2/D3 results through a durable job into an immutable, provenance-rich canonical snapshot, so that every downstream report can rely on one auditable source of truth without breaking today's workbook workflow.
**Mode:** mvp
**UI hint:** no
**Depends on:** Nothing
**Requirements:** SNAP-01, SNAP-02, SNAP-03, SNAP-04, DATA-01, DATA-02, DATA-03, DATA-04, DATA-05, DATA-06, DATA-07, DATA-08, DATA-09, DATA-10, OPS-01, OPS-02, OPS-04, OPS-05
**Success Criteria** (what must be TRUE):

1. A representative D1, D2, and D3 build publishes canonical player, team, season, source, and section facts in an immutable snapshot.
2. Report jobs expose durable state, respect browser capacity, and cannot publish a failed or half-written snapshot as ready.
3. Idempotent retries do not duplicate jobs or snapshots, and cached source sections remain schema/version aware.
4. Innings, missing values, two-way players, transfers, raw values, and metric-definition lineage are represented without ambiguity.
5. Existing selector, scraper, cache, and basic workbook regression tests continue to pass.

**Plans:** 9/13 plans executed

Plans:
**Wave 1**

- [x] 01-01: Add managed persistence, migrations, and the canonical snapshot/provenance schema.

**Wave 2** *(blocked on Wave 1 completion)*

- [x] 01-02: Add durable report jobs, idempotency, atomic publication, section state, and artifact contracts.

**Wave 3** *(blocked on Wave 2 completion)*

- [x] 01-03: Adapt the existing NCAA workflow to canonical facts and verify D1/D2/D3 regression behavior.

**Wave 4** *(blocked on Wave 3 completion)*

- [x] 01-04: Harden published-snapshot integrity, metric lineage, canonical counts, and migration reproducibility.
- [x] 01-10: Enforce account-wide Browserless capacity and loopback-only local database/broker exposure.

**Wave 5** *(blocked on Wave 4 completion)*

- [x] 01-05: Enforce publication traceability and concurrency-safe shared player and metric identities.

**Wave 6** *(blocked on Wave 5 completion)*

- [x] 01-06: Add an exclusive whole-attempt execution claim for duplicate Celery deliveries.

**Wave 7** *(blocked on Wave 6 completion)*

- [x] 01-07: Replace the permanent sent bit with a transactional dispatch outbox and safe reconciliation.

**Wave 8** *(blocked on Wave 7 completion)*

- [x] 01-08: Distinguish malformed NCAA tables from verified-empty sections and recover same-job evidence safely.
- [ ] 01-12: Deploy one focused scheduler for dispatch reconciliation.

**Wave 9** *(blocked on Wave 8 completion)*

- [ ] 01-09: Capture bounded failed/blocked HTML evidence with credential-safe metadata.

**Wave 10** *(blocked on Wave 9 completion)*

- [ ] 01-13: Enforce failed-evidence expiry, purge health, and the 30-day physical-deletion deadline.

**Wave 11** *(blocked on Wave 10 completion)*

- [ ] 01-11: Run the goal-level D1/D2/D3, migration, deployment, and workbook release gate.

### Phase 2: Identity, Organizations, and Report Library

**Goal:** Authenticated customers collaborate inside explicit organizations and manage persistent, refreshable opponent reports without cross-organization access.
**Mode:** mvp
**UI hint:** yes
**Depends on:** Phase 1
**Requirements:** AUTH-01, AUTH-02, AUTH-03, AUTH-04, AUTH-05, ORG-01, ORG-02, ORG-03, ORG-04, ORG-05, ORG-06, ORG-07, SNAP-05, SNAP-06, SNAP-07, SNAP-08, SECU-01, SECU-02, SECU-03, SECU-04, SECU-05
**Success Criteria** (what must be TRUE):

1. A visitor must authenticate through passwordless email or an approved federated identity before using Roster Scout.
2. An organization administrator can invite, revoke, and remove users, and accepting an invitation grants access only to that organization.
3. Members can create reports, observe job/section state, browse dated snapshots, and explicitly refresh a report into a new successor snapshot.
4. Prior snapshots and artifacts remain unchanged and accessible after refresh.
5. Cross-organization identifier substitution is denied across pages, APIs, jobs, refresh actions, and downloads, with auditable security events.

**Plans:** 3 plans

Plans:

- [ ] 02-01: Integrate managed passwordless/federated identity, sessions, logout, and enterprise SSO connection capability.
- [ ] 02-02: Add organizations, roles, invitations, membership administration, authorization services, and denial tests.
- [ ] 02-03: Build the organization report library, asynchronous status UI, snapshot history, explicit refresh, retry, and partial-state experience.

### Phase 3: Detailed NCAA Intelligence

**Goal:** A snapshot contains auditable season, fielding, derived, split, career, and game-level NCAA intelligence or an explicit unavailable state for each section.
**Mode:** mvp
**UI hint:** no
**Depends on:** Phase 2
**Requirements:** STAT-01, STAT-02, STAT-03, STAT-04, STAT-05, STAT-06, STAT-07, STAT-08, SPLT-01, SPLT-02, SPLT-03, SPLT-04, SPLT-05, HIST-01, HIST-02, HIST-03, HIST-04, HIST-05, HIST-06, OPS-03
**Success Criteria** (what must be TRUE):

1. Representative D1, D2, and D3 snapshots expose one consistent batting, pitching, fielding, and derived-metric schema.
2. All seven batting and pitching split families are captured with season-specific source identity or shown as explicitly unavailable.
3. Player detail links season facts, transfer-aware career seasons, separately labeled career aggregates, and dated game logs under one stable identity.
4. wOBA and FIP use verified versioned NCAA season/division inputs or return an explanatory unavailable state; no MLB constants are silently substituted.
5. Collection respects request budgets, bounded concurrency, caching, and retry/backoff while operators can identify unavailable or failed sections.

**Plans:** 4 plans

Plans:

- [ ] 03-01: Expand season batting/pitching facts and add NCAA fielding acquisition and normalization.
- [ ] 03-02: Discover, validate, collect, and normalize the seven batting and pitching split families.
- [ ] 03-03: Add stable-player career seasons, transfers, career aggregates, and player game logs with request controls.
- [ ] 03-04: Implement the versioned metric engine, NCAA weight/constant provenance, methodology, and D1/D2/D3 validation fixtures.

### Phase 4: Detailed Excel Report

**Goal:** Customers can download the exact detailed snapshot as a safe, branded, analysis-ready workbook with complete methodology and section status.
**Mode:** mvp
**UI hint:** no
**Depends on:** Phase 3
**Requirements:** XLSX-01, XLSX-02, XLSX-03, XLSX-04, XLSX-05, XLSX-06
**Success Criteria** (what must be TRUE):

1. A complete snapshot renders Report Summary, Roster, Hitters, Pitchers, Fielding, Hitting Splits, Pitching Splits, Career Stats, Game Logs, and Sources & Methodology worksheets.
2. A partial snapshot identifies each unavailable section and preserves opponent, season, source freshness, and refresh lineage.
3. Workbook numeric types, precision, formula/URL safety, branding, and navigation remain correct for representative D1/D2/D3 fixtures.
4. Downloading an older snapshot returns its original checksummed artifact rather than rebuilding from newer data.
5. Shared fixture metrics match canonical storage and Power BI-facing views.

**Plans:** 3 plans

Plans:

- [ ] 04-01: Build the ten-sheet workbook contract, formatting, methodology, completeness, and branding.
- [ ] 04-02: Add immutable object storage, checksums, schema versions, and exact historical artifact downloads.
- [ ] 04-03: Add canonical-to-Excel parity, safety, partial-report, and D1/D2/D3 workbook verification.

### Phase 5: Embedded Opponent Dashboard

**Goal:** Customers securely review the same snapshot in an opponent-branded Power BI report and download its full workbook from the report page.
**Mode:** mvp
**UI hint:** yes
**Depends on:** Phase 4
**Requirements:** SNAP-09, PBI-01, PBI-02, PBI-03, PBI-04, PBI-05, PBI-06, PBI-07, PBI-08, PBI-09, PBI-10, SECU-06
**Success Criteria** (what must be TRUE):

1. An authenticated customer views the curated embedded report without a Power BI account or customer Power BI license.
2. The report provides Overview, Roster, Hitting, Pitching, Fielding, Splits, Player Detail, and Sources/Data Quality pages for the authorized snapshot.
3. Organization A cannot obtain or use an embed token that reveals Organization B data, verified through automated denial tests and a manual two-organization RLS test.
4. Semantic-model refresh has independent pending/completed/failed state, and its failure never invalidates a completed snapshot or Excel artifact.
5. The opponent-branded report page exposes freshness, completeness, interactive filters/drill-through, and the exact full-workbook download, and production launch is blocked without supported capacity.

**Plans:** 4 plans

Plans:

- [ ] 05-01: Provision the capacity-backed Power BI foundation, analytics views, semantic model, curated report, and organization isolation.
- [ ] 05-02: Add service-principal authentication, short-lived embed-token APIs, semantic refresh orchestration, and readiness state.
- [ ] 05-03: Build the branded customer report page with embedded navigation, filters, drill-through, completeness, and workbook download.
- [ ] 05-04: Verify tenant isolation, metric parity, capacity/refresh operations, external failure recovery, and the production release gate.

## Progress

**Execution Order:** Phase 1 → Phase 2 → Phase 3 → Phase 4 → Phase 5

| Phase | Plans Complete | Status | Completed |
|---|---:|---|---|
| 1. Canonical Snapshot Foundation | 9/13 | In Progress|  |
| 2. Identity, Organizations, and Report Library | 0/3 | Not started | - |
| 3. Detailed NCAA Intelligence | 0/4 | Not started | - |
| 4. Detailed Excel Report | 0/3 | Not started | - |
| 5. Embedded Opponent Dashboard | 0/4 | Not started | - |
