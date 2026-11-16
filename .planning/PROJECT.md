# Roster Scout

## What This Is

Roster Scout is a Flask application for college-baseball scouting. Its public polished homepage lets a coach choose an opponent before a passwordless school-email handoff; verified staff then generate, review, refresh, and download shared persistent reports in a minimal portal. The remaining milestone work expands the NCAA intelligence, builds the complete workbook, and adds embedded Power BI analytics.

Each report is an immutable, dated snapshot of the source data available when it was generated. Organizations can retain consecutive snapshots, explicitly refresh a report into a new version, review an opponent-branded dashboard, and download the complete underlying report as an Excel workbook.

## Core Value

A baseball staff can reliably turn fragmented opponent data into a trustworthy, reusable scouting report with both interactive analysis and a portable Excel download.

## Current Milestone: v2.0 Opponent Intelligence

**Goal:** Expand Roster Scout from a public two-sheet workbook generator into a secure organization-based opponent intelligence product with detailed statistics, versioned reports, embedded Power BI analysis, and full Excel delivery.

**Target features:**
- Passwordless authentication, optional federated sign-in, organizations, invitations, roles, and organization-scoped report access
- Immutable report snapshots with explicit refresh-to-new-version behavior and report history
- NCAA player provenance, derived metrics, fielding, split statistics, career statistics, and game logs
- A stable analytics data model that feeds both Excel and Power BI without duplicating metric definitions
- Opponent-branded embedded Power BI reports with a full formatted Excel download

## Requirements

### Validated

- ✓ Users can select exact current-season NCAA Division I, II, or III teams from a validated catalog — existing
- ✓ The hosted app can retrieve NCAA batting and pitching grids through a verified Browserless session when direct HTTP is blocked — existing
- ✓ Successful NCAA results are normalized, cached, and split into hitter and pitcher records, including two-way-player handling — existing
- ✓ Users can download an in-memory, school-branded Excel workbook with Hitters and Pitchers worksheets — existing
- ✓ The request path applies bounded input validation, per-client rate limits, browser-capacity protection, and consistent error responses — existing
- ✓ Durable jobs publish immutable organization-owned snapshots with section status, provenance, source identifiers, collection times, and canonical player identity — Phase 1
- ✓ Published snapshot facts provide one versioned canonical boundary for downstream Excel and Power BI consumers without duplicating source truth — Phase 1
- ✓ Visitors can select an opponent publicly; report generation and delivery require a secure authenticated school session — Phase 02.1
- ✓ Provider-verified `.edu` roots and ordinary school subdomains join one shared school organization, with exact aliases and invitations for exceptions — Phase 02.1
- ✓ Saved reports expose dated immutable snapshots, and explicit refresh creates a linked successor without changing prior evidence — Phase 2
- ✓ NCAA snapshots expose auditable season, fielding, derived, situational split, last-10, conference-only, career, and game-level intelligence—or an explicit unavailable state—through one tenant-scoped canonical model — Phase 3

### Active

- [ ] One canonical analytics model supplies both the formatted Excel workbook and the embedded Power BI report
- [ ] Customers can review an opponent-branded embedded dashboard and download the corresponding complete Excel snapshot
- [ ] Decide future organization pricing, plans, entitlements, and subscription ownership after the core product flow is validated

### Out of Scope

- Anonymous report generation, workbook delivery, saved history, refresh, or dashboards — only the team selector is public
- Automatic membership from unverified, malformed, lookalike, or non-`.edu` domains — only a provider-verified institutional address may auto-join
- Editing historical snapshots in place — refresh always creates a new version so prior game-preparation evidence remains reproducible
- Live in-game scoring, pitch tracking, or play-by-play streaming — this milestone analyzes published NCAA roster and statistics data
- A native mobile application — the responsive web experience remains the delivery surface
- A customer-authored Power BI report builder — customers consume the curated Roster Scout report and its filters
- Replacing the existing JUCO adapter with Power BI-specific logic — the canonical model may support JUCO later, but NCAA analytical parity is the v2.0 target

## Context

- Phase 02.1 restores the original homepage as the primary interface, adds passwordless authentication only at generation, maps verified school domains to shared organizations, and keeps a minimal saved-report/history/refresh/download portal. Production identity provisioning stays an explicit launch gate.
- NCAA places the live statistics boundary behind terms acceptance and Akamai verification. The existing Browserless sticky residential workflow is proven for representative D1, D2, and D3 teams and must remain the acquisition path until a safer official alternative exists.
- The current normalizer extracts NCAA player IDs to merge batting and pitching rows, then discards those IDs from the public record. It also parses several raw values needed for advanced metrics without retaining all of them in workbook output.
- Power BI customer embedding uses the app-owns-data model. End users do not need Power BI licenses, but production embedding requires paid Embedded, Premium, or Fabric capacity plus an embedding identity and Power BI workspace.
- Passwordless email is the universal sign-in fallback. Microsoft/Google federation can reduce friction for school users, while organization-specific enterprise SSO can be added when customer demand justifies configuration complexity.
- A school may play the same opponent on consecutive days, so the report library must preserve each dated snapshot while making refresh explicit and easy.
- Spike 001 invalidated adding `baseballr` as a live runtime dependency. Roster Scout may reuse maintained lookup data and proven concepts without adding R to production.

## Constraints

- **Existing application:** Preserve Flask, the current NCAA selector contract, workbook generation, and proven Browserless flow while introducing new boundaries incrementally.
- **Data provenance:** Every displayed or exported value must be traceable to a report snapshot, source URL, source identifier, and collection timestamp.
- **Metric consistency:** Excel and Power BI must consume the same canonical facts and metric definitions; duplicated calculations that can drift are not acceptable.
- **Security:** Authentication and organization authorization are separate checks. Every report, snapshot, export, embed token, and refresh action must be organization-scoped and deny access by default.
- **History:** Refresh creates a new immutable snapshot linked to the prior snapshot; it never overwrites the earlier report.
- **NCAA coverage:** D1, D2, and D3 must remain behaviorally consistent, including error handling and output schema.
- **Power BI:** Production customer embedding requires capacity-backed licensing; development-only tokens cannot be treated as a launch architecture.
- **External reliability:** NCAA, Browserless, identity providers, and Power BI are independent failure domains and must fail without corrupting a saved report.
- **Compatibility:** The full Excel workbook remains a first-class deliverable even when Power BI visual-level export is available.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Make the branded web dashboard the primary review surface and keep Excel as a complete companion download | Embedded analytics and portable scouting files solve different customer jobs | — Pending |
| Authenticate only at report generation | Coaches should evaluate and select an opponent before friction, while customer reports and downloads still require an accountable boundary | ✓ Phase 02.1 keeps the homepage/catalog public and protects generation, delivery, history, refresh, and downloads |
| Model customers as organizations with shared reports | Coaching and scouting staffs collaborate on the same opponents | ✓ Phase 2 implemented canonical memberships and a shared report library |
| Use verified institutional domains for the default organization membership | The simple school workflow should connect staff who control addresses at the same `.edu` institution while rejecting lookalikes and non-school domains | ✓ Phase 02.1 adds strict root/alias mapping, provenance, transfer handling, and secondary exact invitations |
| Support passwordless email and federated school/work identities | Customers may use `.edu`, athletics, vendor, or personal addresses across Microsoft and Google ecosystems | ✓ Phase 2 implemented the provider-neutral WorkOS boundary; live provider setup remains a deployment gate |
| Keep pricing and billing out of the corrective flow | The authentication and shared-report experience must be validated before choosing plan structure or subscription ownership | — Open product decision after Phase 02.1 |
| Preserve report snapshots and make refresh create a linked new version | Teams may prepare for the same opponent on consecutive days and need the original evidence | ✓ Phase 1 established immutable predecessor-aware snapshots; Phase 2 adds the customer refresh workflow |
| Use one canonical analytics model for Excel and Power BI | Shared definitions prevent conflicting metrics and make verification practical | ✓ Phase 1 established the published canonical fact boundary; Phases 3–5 add detailed consumers |
| Keep the proven Python/Browserless acquisition layer | The prior live `baseballr` runtime experiment was invalidated by NCAA verification | ✓ Good |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition:**
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone:**
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-11-16 after Phase 3 completion*
