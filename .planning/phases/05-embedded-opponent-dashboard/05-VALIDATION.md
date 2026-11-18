---
phase: 05-embedded-opponent-dashboard
status: ready
created: 2026-11-16
---

# Phase 5 Validation Strategy

## Automated gates

1. Migration round-trip and drift checks prove every Power BI view exposes the
   same `organization_id`, `snapshot_id`, and opaque `security_key` and reads
   only published snapshots.
2. Fixture parity tests compare shared season, derived, Last 10, conference,
   split, fielding, career, and game-log values across canonical facts,
   workbook projection, and Power BI views.
3. Provider contract tests prove fixed Microsoft hosts, fixed configured
   resource IDs, short token expiry, exact effective identity, dynamic RLS
   role, redacted failures, and no production fake-provider fallback.
4. Route tests substitute foreign, missing, and mixed report/snapshot IDs and
   require the same non-enumerating response with no provider call.
5. Refresh lifecycle tests prove pending/completed/failed Power BI state never
   mutates snapshot or workbook readiness.
6. PBIP validation checks the TMDL model, RLS expression, report page manifest,
   theme, and absence of secrets or production resource IDs.
7. Browser tests cover light/dark desktop/mobile states, keyboard navigation,
   pending/error/ready transitions, tab selection, and independent workbook
   exports.

## Manual production gate

Production remains blocked until an operator records all of the following:

- the workspace and semantic model are on supported Embedded, Premium, or
  Fabric capacity;
- the service principal has only the required workspace/API permissions;
- an Organization A embed token cannot reveal Organization B rows;
- an Organization B embed token cannot reveal Organization A rows;
- token renewal succeeds without persisting tokens in browser storage;
- a failed semantic refresh leaves the exact saved workbook downloadable.

No local fake, unit test, or development tenant substitutes for this live
two-organization RLS verification.
