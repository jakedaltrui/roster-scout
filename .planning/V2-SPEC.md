# v2.0 Opponent Intelligence — Milestone Specification

**Created:** 2026-11-01  
**Ambiguity score:** 0.15 (gate: ≤ 0.20)  
**Requirements:** 77 locked in `REQUIREMENTS.md`

## Goal

Roster Scout changes from an anonymous current-season two-sheet downloader into a secure organization-based opponent intelligence product where customers generate immutable detailed reports, refresh them into versioned successors, review an opponent-branded embedded Power BI experience, and download the corresponding complete Excel snapshot.

## Background

The existing Flask application already resolves exact current-season D1/D2/D3 teams, obtains NCAA season batting and pitching grids through a verified Browserless workflow, normalizes two-way players, caches successful results, and returns a branded Hitters/Pitchers workbook. It has no authentication, persistent database, organization model, report history, background jobs, fielding/split/career/game-log acquisition, canonical metric layer, or embedded dashboard.

This milestone retains the proven NCAA boundary while introducing durable customer identity, snapshot storage, a much broader baseball dataset, and two sibling report surfaces. Power BI is the interactive review surface; Roster Scout's generated workbook remains the complete portable artifact.

## Product Contract

1. All Roster Scout use requires authentication.
2. Users belong to explicit organizations; verified email domains never authorize membership by themselves.
3. Reports and snapshots are organization-owned.
4. A published snapshot is immutable.
5. Refresh creates a new linked snapshot.
6. Every report section discloses completeness, freshness, and source provenance.
7. Excel and Power BI use the same canonical facts and metric definitions.
8. Power BI embedding never weakens Flask organization authorization.

## Phase Specification

### Phase 1: Canonical Snapshot Foundation

**Outcome:** The existing scraper can publish a typed, immutable, organization-ready snapshot through a durable job boundary without changing the public product yet.

**In scope:**
- PostgreSQL schema and migrations for reports, snapshots, sections, canonical identities/facts, provenance, jobs, and artifacts
- Canonical innings representation, missing-value semantics, player/team/season/source keys, and metric-definition versioning
- Durable queued job interface, idempotency, bounded NCAA concurrency, cache/version keys, and recoverable publication
- Compatibility adapter from current normalized rows into the canonical model

**Out of scope:**
- Login and organizations
- New fielding, split, career, or game-log collectors
- Customer-facing Power BI

**Acceptance:**
- [ ] A representative D1, D2, and D3 build publishes a snapshot with canonical player/team/source identities.
- [ ] Replaying an idempotent job does not duplicate a snapshot.
- [ ] A failed job cannot leave a snapshot marked ready.
- [ ] Existing NCAA workflow and basic workbook regression tests still pass.

### Phase 2: Identity, Organizations, and Report Library

**Outcome:** Roster Scout is login-gated and customers collaborate through explicit organization membership on a persistent report library.

**In scope:**
- Passwordless email, Microsoft/Google federation, enterprise OIDC/SAML connection capability, sessions, and logout
- Organizations, administrator/member roles, invitations, revocation, removal, and application-level authorization
- Organization-owned report creation, asynchronous status, history, explicit refresh, immutable successor lineage, retry, and partial-section visibility
- Cross-organization denial tests and security audit events

**Out of scope:**
- `.edu` auto-join
- Anonymous report sharing
- Power BI embed tokens

**Acceptance:**
- [ ] An invited member can sign in without a password and enter only the inviting organization.
- [ ] An administrator can invite and remove members.
- [ ] Refresh creates a new dated snapshot and preserves the prior snapshot.
- [ ] Identifier substitution cannot expose another organization's reports, jobs, or artifacts.

### Phase 3: Detailed NCAA Intelligence

**Outcome:** Each snapshot contains the requested season, fielding, derived, split, career, and game-level intelligence with explicit completeness and methodology.

**In scope:**
- Expanded raw batting/pitching facts and derived metrics
- NCAA fielding acquisition and normalization
- All seven requested batting and pitching split families
- Career season history, transfer-aware identity, career aggregate, and game logs
- NCAA season/division wOBA weights and FIP constants with versioned provenance or explicit unavailability
- Request budgets, caching, bounded concurrency, retry/backoff, and section-level partial publication

**Out of scope:**
- Predictive modeling
- Live in-game data
- MLB constants presented as NCAA constants

**Acceptance:**
- [ ] Representative D1/D2/D3 snapshots share one detailed metric schema.
- [ ] All requested split families are present or explicitly unavailable with source context.
- [ ] A two-way transfer player retains one player identity and distinct player-team-season facts.
- [ ] Zero, missing, and unavailable values remain distinguishable.
- [ ] Metric formulas and constant versions are auditable.

### Phase 4: Detailed Excel Report

**Outcome:** Customers can download the exact detailed snapshot as a branded, safe, analysis-ready workbook.

**In scope:**
- Report Summary, Roster, Hitters, Pitchers, Fielding, Hitting Splits, Pitching Splits, Career Stats, Game Logs, and Sources & Methodology worksheets
- Snapshot identity, lineage, completeness, freshness, schema version, and checksum
- Numeric typing, precision, formula/URL injection protection, freeze/filter/format behavior, and opponent branding
- Immutable artifact storage and exact historical download
- Canonical-to-workbook parity fixtures

**Out of scope:**
- Replacing the workbook with Power BI visual export
- User-designed workbook layouts

**Acceptance:**
- [ ] A detailed fixture workbook contains all ten worksheets and the expected typed values.
- [ ] Downloading an older snapshot returns the original checksummed artifact.
- [ ] Canonical facts and shared workbook metrics match exactly.
- [ ] A partial snapshot discloses each unavailable section in the workbook.

### Phase 5: Embedded Opponent Dashboard

**Outcome:** Customers review the same snapshot through a secure opponent-branded Power BI report and download its workbook from the report page.

**In scope:**
- Power BI app-owns-data service principal and capacity-backed deployment contract
- Semantic model and curated report pages
- Organization/report RLS or stronger workspace isolation, short-lived embed tokens, and negative tenant tests
- API-triggered asynchronous semantic-model refresh with independent readiness state
- Embedded dashboard page, filters, drill-through, branding, freshness/completeness, and full workbook download
- Capacity/refresh monitoring and production release gate

**Out of scope:**
- Requiring customers to hold Power BI licenses
- Customer-authored Power BI models or reports
- Treating visual export as the complete workbook

**Acceptance:**
- [ ] A customer views the embedded report without a Power BI account.
- [ ] Organization A cannot obtain or use a token that exposes Organization B data.
- [ ] The dashboard and Excel artifact show matching shared fixture metrics.
- [ ] A failed semantic refresh leaves the snapshot and Excel download intact and reports dashboard unavailability accurately.
- [ ] Production launch is blocked without supported Power BI capacity and two-organization RLS verification.

## Boundaries

**In scope:**
- NCAA D1, D2, and D3 opponent intelligence
- Authentication, organization membership, report versioning, report history, and explicit refresh
- Provenance, advanced metrics, fielding, splits, career history, and game logs
- Detailed Excel and embedded Power BI delivery
- Security, data-quality, and operational controls required for customer use

**Out of scope:**
- Anonymous or public reports
- Automatic organization joining by email domain
- Native mobile applications
- Live game feeds
- Predictive models
- Customer report authoring
- Cross-organization report sharing
- Full JUCO analytical parity in v2.0

## Constraints

- Preserve the current Flask/Python application and proven Browserless NCAA access path.
- Treat NCAA, Browserless, identity, database, object storage, and Power BI as independent failure domains.
- Use a capacity-backed Power BI production deployment; development tokens are not a launch substitute.
- Keep organization authorization in the application even when Power BI RLS is enabled.
- Make report building asynchronous and observable.
- Do not publish guessed weighted metrics or convert unavailable data to zero.

## Milestone Acceptance Criteria

- [ ] All 77 v2.0 requirements map to exactly one phase.
- [ ] All five phase verification gates pass.
- [ ] A complete invitation-to-dashboard-to-Excel workflow passes for two isolated test organizations.
- [ ] Refresh lineage is demonstrated across two snapshots of the same opponent.
- [ ] Representative D1/D2/D3 detailed reports pass schema and metric-parity verification.
- [ ] Existing public-workflow regression behavior is either preserved behind login or deliberately migrated with tests and release notes.

## Ambiguity Report

| Dimension | Score | Minimum | Status | Notes |
|---|---:|---:|---|---|
| Goal Clarity | 0.94 | 0.75 | ✓ | Dashboard, workbook, and customer outcome are explicit |
| Boundary Clarity | 0.86 | 0.70 | ✓ | Authentication, organizations, snapshots, analytics, and exclusions locked |
| Constraint Clarity | 0.81 | 0.65 | ✓ | Power BI, NCAA, tenancy, and immutability constraints identified |
| Acceptance Criteria | 0.76 | 0.70 | ✓ | Milestone and phase pass/fail criteria defined |
| **Ambiguity** | **0.15** | **≤0.20** | **✓** | Specification gate passed |

## Interview Log

| Round | Perspective | Decision locked |
|---:|---|---|
| 1 | Researcher | Customer-facing embedded Power BI report with Excel companion |
| 2 | Researcher / Simplifier | Roster Scout is login-gated and reports live inside customer accounts |
| 3 | Boundary Keeper | Reports are shared within explicit organizations; `.edu` is not authorization; passwordless/federated identity required |
| 4 | Failure Analyst | Snapshots are preserved and explicit refresh creates a successor for repeated-series preparation |

---
*Milestone: v2.0 Opponent Intelligence*  
*Next: plan and execute Phase 1 through Phase 5 in dependency order.*
