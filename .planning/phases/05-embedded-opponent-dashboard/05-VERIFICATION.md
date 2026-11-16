# Phase 5 Verification — Embedded Opponent Dashboard

**Verdict:** Implementation complete; production activation pending external
Microsoft capacity and tenant evidence.

## Deterministic evidence

- `scripts/validate_phase5_release.py` passed on 2026-11-16.
- Full regression: 801 tests passed.
- Focused dashboard contract: 19 tests passed.
- Workbook acceptance: exactly two styled sheets with Player, Season, Derived
  Metrics, Last 10 Games, and Conference Games groups for D1/D2/D3 fixtures.
- NCAA catalog: 942 selectable teams (308 D1, 253 D2, 381 D3).
- Migration `0026` downgraded to `0025` and reapplied cleanly; Alembic reported
  no model drift.
- PBIP gate: 8 pages, 14 RLS-bound semantic tables, 14 tenant-bound views.
- Browser review covered desktop/mobile, light/dark, keyboard navigation,
  scope switching, synthetic labeling, and dashboard state transitions.
- JavaScript syntax, Python compilation, and `git diff --check` passed.

## Security evidence

- Embed authority is entirely server-derived and bound to one authenticated
  account, organization, report, and immutable snapshot.
- Every semantic table independently requires the exact
  `<organization UUID>|<snapshot UUID>` `CUSTOMDATA()` key.
- Foreign and absent identifiers are indistinguishable and cause no Power BI
  provider call.
- Embed responses are short-lived and `no-store`; browser code persists no
  token or tenant identity.
- Semantic refresh failure cannot mutate or invalidate a snapshot or workbook.
- Fake mode is rejected in production and cannot satisfy the release gate.

## External production gate

The following cannot be proven from this repository and remains mandatory:

1. Publish the PBIP to a supported Embedded, Premium, or Fabric capacity.
2. Bind the production PostgreSQL source and least-privilege service principal.
3. Prove Organization A sees zero Organization B rows and vice versa using two
   live embed tokens.
4. Verify live token renewal and a failed semantic refresh while the exact
   saved workbook remains downloadable.
5. Only then set `POWER_BI_CAPACITY_VERIFIED=true` and request deployment.

No production publication, secret creation, capacity purchase, or deployment
was performed.
