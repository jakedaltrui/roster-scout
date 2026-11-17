---
status: passed
verified_at: 2026-11-16
---

# Phase 5 Verification — Native Opponent Dashboard

**Verdict:** Passed. The native dashboard and bounded retention policy are
release-tested; production remains unchanged pending explicit approval.

## Deterministic evidence

- `scripts/validate_phase5_release.py` passed on 2026-11-16.
- Full regression: 807 tests passed.
- Final focused dashboard, retention, deployment, and artifact pass: 17 tests
  passed.
- Workbook acceptance: exactly two styled sheets with season, last-10, and
  conference groups for D1/D2/D3 fixtures.
- NCAA catalog: 942 selectable teams (308 D1, 253 D2, 381 D3).
- Migration `0027` downgraded to `0026` and reapplied cleanly; Alembic reported
  no model drift.
- Browser review covered desktop/mobile, light/dark, keyboard navigation,
  scope switching, empty states, and dashboard state transitions.
- JavaScript syntax, Python compilation, and `git diff --check` passed.

## Security and retention evidence

- Dashboard authority is entirely server-derived and bound to one
  authenticated organization, report, and immutable snapshot.
- Native projections are built request-locally from the existing PostgreSQL
  snapshot; no browser token, external BI provider, or duplicate analytics
  store is involved.
- Foreign and absent identifiers remain indistinguishable through the existing
  report authorization boundary.
- Workbook and saved-report data default to 30-day retention; raw usage events
  default to a 90-day rolling window.
- Retention deletes private workbook objects before their snapshot graph and
  advances operational readiness only after the complete purge succeeds.
- Published snapshot immutability can be bypassed only by the bounded
  transaction-local retention purge path.

## Production gate

Before production deployment:

1. [x] Deploy this release candidate to the existing staging application.
2. [ ] Run the authenticated report, refresh, dashboard, Excel, Google Sheets,
   invitation, logout, and cross-organization isolation smoke journeys.
3. [ ] Verify PostgreSQL, Redis, private object storage, WorkOS/email delivery,
   signing secrets, purge health, and uninterrupted Fly worker capacity.
4. [ ] Request explicit production approval.

### Hosted staging evidence

- PR #6 CI passed both test and dependency-audit jobs.
- Fly release migration reached `0027`; app, worker, scheduler, and purge
  processes reached healthy state.
- `/healthz`, `/readyz`, the 308-team D1 catalog, protected-route redirects,
  favicon linkage, and the hosted native JavaScript asset passed.
- A private staging object completed write, integrity-checked read, and delete.
- The hosted browser passed team selection, in-card sign-in transformation,
  non-`.edu` rejection, and light/dark presentation.
- An authenticated Abilene Christian staging build published a canonical
  partial snapshot with complete batting/pitching sections, explicit
  unavailable supplemental sections, six terminal request ledgers, and no
  stranded `planned` ledger rows.
- The private artifact reached `ready`; the authorized report page rendered
  the native full-season, last-10, conference, and 14-split dashboard model.
- The authorized Excel route returned a valid two-sheet workbook named
  `Team Stats 11-17-26.xlsx` with `Hitters` and `Pitchers` sheets.
- Staging Google OAuth credentials are configured. The authorized export route
  redirected to Google Accounts with the exact staging callback and only the
  `https://www.googleapis.com/auth/drive.file` scope.
- A reachable `.edu` inbox is still required for the real WorkOS code loop.
- Final Google consent/upload needs a real signed-in Google account; the
  server-side authorization boundary and callback contract have passed.

No production deployment was performed by this verification.
