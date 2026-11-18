---
phase: 05-embedded-opponent-dashboard
status: complete
created: 2026-11-16
---

# Phase 5 Technical Research

## Recommended Architecture

Use Power BI **embed for your customers** (app owns data) with a Microsoft
Entra service principal. Roster Scout remains the identity provider and creates
a short-lived embed token only after it revalidates the signed-in account,
active organization membership, report, and snapshot. This is the Microsoft
scenario intended for external application users who should not sign in to
Power BI.

The semantic model uses dynamic RLS, but its identity is snapshot-specific:

```text
security_key = <organization UUID>|<snapshot UUID>
```

Every Power BI-facing fact/view repeats that key. The embed token supplies an
opaque account identifier as `username`, the one model role, the semantic-model
ID, and the exact `security_key` as `customData`. The RLS rule compares the
Snapshot dimension's key with `CUSTOMDATA()`. Client filters improve usability
but do not grant access.

This follows Microsoft's requirement that a service principal provide an
effective identity for an RLS semantic model and its guidance to validate RLS
with actual embed tokens. Workspace-per-customer isolation is stronger at very
large multitenant scale, but it would add lifecycle and cost complexity that is
not justified for the current simple school-organization model. Exact
snapshot-key RLS is the chosen MVP boundary, with automated denial tests and a
mandatory manual two-organization token test before production.

## Provider Flow

1. Acquire an Entra application token through client credentials. Cache only
   the application token in process, bounded by its provider expiry.
2. Fetch/validate configured report metadata from the fixed workspace/report
   allowlist; never accept these IDs from the browser.
3. Call Generate Token V2 with view-only report access, semantic-model read
   access, and the exact effective identity/RLS role/custom data.
4. Return only `reportId`, allowlisted `embedUrl`, embed token, and expiration
   from a no-store JSON endpoint.
5. Embed with `models.TokenType.Embed`, view mode, hidden Power BI page chrome,
   and Roster Scout-owned navigation.
6. Renew app-owns-data tokens before expiry by calling the same authorized
   endpoint and `report.setAccessToken`; automatic token-provider refresh is
   not available for this embedding scenario.

## Semantic Refresh Flow

Snapshot publication may enqueue a separate Power BI refresh attempt. The
application stores provider-neutral `pending`, `completed`, or `failed`
diagnostics against the exact organization/report/snapshot. Enhanced refresh
is asynchronous: a successful POST returns `202` and a provider request ID,
which the worker polls with bounded backoff. Provider failure only changes the
external refresh diagnostic. It cannot alter the published snapshot or ready
workbook artifact.

Production enhanced refresh requires a semantic model on Embedded, Premium,
Premium Per User, or Fabric capacity plus `Dataset.ReadWrite.All`. The current
production contract is stricter: customer embedding remains disabled unless
the report and semantic-model workspaces are supported capacity-backed.

## Source-Controlled Power BI Project

Use a PBIP project containing:

- a TMDL semantic model;
- a PBIR report with eight curated pages;
- one Roster Scout theme with light neutral canvas, DM Sans-compatible Segoe
  fallback, restrained school/accent colors, accessible semantic status colors,
  and no custom visuals;
- explicit measures sourced from canonical counts and the same metric formulas
  used by the application;
- the `RosterScoutSnapshotReader` RLS role;
- a deployment manifest that names required environment bindings without
  containing connection strings, tenant IDs, client secrets, tokens, or
  production resource IDs.

Microsoft documents PBIP/PBIR and TMDL as source-control-friendly formats.
Microsoft's `powerbi-report-author` CLI can scaffold and schema-validate PBIR
without hand-guessing schema versions. Power BI Desktop or the Fabric service
is still required for the final live data-source binding and rendered report
verification.

## Report Content Contract

| Page | Primary question | Required content |
|---|---|---|
| Overview | What kind of opponent is this right now? | freshness, completeness, key hitting/pitching/fielding cards, full-season vs Last 10 vs conference comparison |
| Roster | Who is available and how are they used? | identity, class, position, two-way flag, player drill-through |
| Hitting | Where is the lineup dangerous or vulnerable? | counts, AVG/OBP/SLG/OPS/ISO, BB%, K%, BABIP, wOBA availability, scope comparison |
| Pitching | How should we prepare for the staff? | ERA/WHIP/K9/BB9/H9/HR9/KBB/K%/BB%/BABIP/FIP availability, role and scope comparison |
| Fielding | Where does run prevention change? | PO/A/E/TC/DP/FLD%, catcher values, availability |
| Splits | How does performance change by situation? | all seven canonical batting and pitching split families with unavailable states |
| Player Detail | What is the complete scouting context for one player? | season, scopes, splits, career seasons, aggregate disclosure, game log |
| Data Quality | What can staff trust and what is missing? | section status, source time, metric methodology/version, source lineage, missing-data explanations |

## Operational Boundaries

- Production secrets: tenant ID, client ID, client credential/certificate,
  workspace ID, report ID, semantic-model ID, role name, allowed embed origin.
- Validate every UUID and HTTPS endpoint at startup; redact values in errors.
- Use fixed Microsoft public-cloud hosts unless a separately configured cloud
  profile is implemented.
- Never include provider response bodies in logs.
- Audit token issuance by actor, organization, report, snapshot, fixed outcome,
  and time only.
- Block production if capacity verification, workspace membership, semantic
  model/RLS role, or the two-organization live test is missing.

## Primary Sources

- Microsoft Learn, [Generate an embed token](https://learn.microsoft.com/en-us/power-bi/developer/embedded/generate-embed-token)
- Microsoft Learn, [Embed for your customers](https://learn.microsoft.com/en-us/power-bi/developer/embedded/embed-customer-app)
- Microsoft Learn, [Row-level security](https://learn.microsoft.com/en-us/fabric/security/service-admin-row-level-security)
- Microsoft Learn, [Move an embedded app to production](https://learn.microsoft.com/en-us/power-bi/developer/embedded/move-to-production)
- Microsoft Learn, [Enhanced refresh with the REST API](https://learn.microsoft.com/en-us/power-bi/connect-data/asynchronous-refresh)
- Microsoft Learn, [Refresh an embedded token](https://learn.microsoft.com/en-us/javascript/api/overview/powerbi/refresh-token)
- Microsoft Learn, [PBIR enhanced report format](https://learn.microsoft.com/en-us/power-bi/developer/embedded/projects-enhanced-report-format)
