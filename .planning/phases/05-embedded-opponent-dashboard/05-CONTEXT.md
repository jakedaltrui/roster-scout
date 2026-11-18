---
phase: 05-embedded-opponent-dashboard
status: ready_for_planning
created: 2026-11-16
---

# Phase 5 Context

## Locked Product Decisions

- The dashboard is a customer-facing feature inside the existing signed-in
  report page, not a separate Power BI destination or replacement portal.
- The existing homepage and portal are the visual and interaction gold
  standard: DM Sans, restrained neutral surfaces, matching light/dark themes,
  familiar controls, subtle state motion, and minimal copy.
- Customers authenticate only with Roster Scout. They do not need a Power BI
  account or customer Power BI license.
- The dashboard and workbook must read the same immutable snapshot. A newer
  refresh cannot mutate the prior dashboard or workbook.
- The workbook remains exactly two sheets. Splits, fielding, game logs,
  career detail, and deeper advanced-stat exploration belong in the embedded
  dashboard.
- Dashboard pages: Overview, Roster, Hitting, Pitching, Fielding, Splits,
  Player Detail, and Data Quality.
- Last 10 and conference performance must be first-class dashboard filters and
  comparisons alongside full-season values.
- Missing data is labeled unavailable/partial and is never presented as zero.
- The report page keeps Excel and Google Sheets export independent from Power
  BI readiness. A Power BI failure never hides or invalidates the workbook.
- Production embedding is blocked until a supported Embedded, Premium, or
  Fabric capacity and a real two-organization RLS smoke test are verified.
- Do not deploy or provision billable production infrastructure without the
  user's explicit approval.

## Customer Flow

1. A signed-in coach opens an existing opponent report.
2. The page shows the opponent, snapshot freshness, completeness, and export
   actions before any embedded content.
3. If analytics for that snapshot are ready, the dashboard loads in place with
   Roster Scout tabs and a minimal Power BI canvas without Power BI chrome.
4. Filters and drill-through change only the interactive view; the snapshot
   remains immutable.
5. If semantic refresh is pending, the current workbook stays available and a
   compact progress state explains that interactive analysis is being prepared.
6. If semantic refresh fails, the page shows a sanitized retryable dashboard
   state while keeping the workbook and historical snapshots available.
7. Historical report versions continue to download exact saved workbooks; the
   initial dashboard release targets the current authorized snapshot.

## Security Contract

- Organization and snapshot authority come only from the current server-side
  membership plus the report/snapshot lineage loaded from PostgreSQL.
- The browser never supplies an organization ID, RLS role, workspace ID,
  dataset ID, report ID, or effective identity as authority.
- Embed tokens are generated server-side, short-lived, view-only, and bound to
  exactly one report, semantic model, organization, and snapshot.
- The semantic model uses one dynamic RLS role over an exact opaque security
  key derived from `organization_id` and `snapshot_id`; client filters are not
  a security boundary.
- Tokens and provider payloads never enter logs, audit metadata, HTML, query
  strings, or persistent browser storage.
- Cross-organization, missing, and mixed-identifier requests return the same
  non-enumerating response and fixed audit outcome.

## Implementation Constraints

- Use app-owns-data embedding with a Microsoft Entra service principal.
- Use the V2 Generate Token API with `EffectiveIdentity`; service-principal
  embedding of an RLS model must never omit it.
- Keep Power BI refresh state independent from report job, snapshot, and
  workbook artifact state.
- Build report source as PBIP/PBIR + TMDL in version control and validate it
  with Microsoft's authoring/schema tools before tenant publication.
- PostgreSQL analytics views are the source contract; they expose only
  published canonical facts and repeat organization/snapshot ownership.
- Local development must be useful without live Microsoft credentials through
  deterministic provider fakes and a clearly labeled synthetic dashboard
  preview. Production must fail closed rather than silently using the fake.
