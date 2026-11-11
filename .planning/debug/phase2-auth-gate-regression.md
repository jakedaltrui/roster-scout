---
status: resolved
trigger: "Phase 2 Plan 02-12 full-suite integration regressions: legacy download acknowledgement routes return 401 and logout warning assertion is order-dependent."
created: 2026-11-11T23:25:12Z
updated: 2026-11-11T23:42:00Z
---

## Current Focus

hypothesis: confirmed and fully self-verified
test: complete
expecting: complete
next_action: resolved by deterministic full-suite verification; continue Phase 2

## Symptoms

expected: The signed same-origin Phase 1 download acknowledgement boundary retains its established 200/202/403 outcomes; logout always revokes locally and emits a sanitized warning when provider logout fails.
actual: Full discovery returns 401 for legacy download acknowledgement cases and one whole-suite logout test observes no warning.
errors: 15 unittest failures: 14 acknowledgement responses are 401 authentication_required; AuthGateRouteMatrixTests.test_logout_requires_csrf_and_revokes_locally_when_provider_fails captures no WARNING from app.
reproduction: DATABASE_URL=postgresql+psycopg://roster_scout:roster_scout@127.0.0.1:5432/roster_scout_test uv run python -m unittest discover -s tests -q
started: After Phase 2 Plan 02-12 introduced the default-deny authenticated route gate.

## Eliminated


## Evidence

- timestamp: 2026-11-11T23:28:00Z
  checked: `app.py` route gate and acknowledgement route
  found: `_PUBLIC_ENDPOINTS` omits endpoint `acknowledge_workbook_download`, so `require_authenticated_organization` returns authentication_required before the signed proof and same-origin checks execute.
  implication: the 14 acknowledgement failures are one policy-classification regression, not failures in proof or origin validation.
- timestamp: 2026-11-11T23:29:00Z
  checked: logout route, `complete_provider_logout`, and unittest `assertLogs`
  found: provider logout failures call `current_app.logger.warning` through `complete_provider_logout`, but Python's `assertLogs` changes handlers/level/propagation and does not clear `logger.disabled`; another callback test already guards this exact shared logger state explicitly.
  implication: the missing warning can be an order-dependent test isolation failure while local revocation remains correct.
- timestamp: 2026-11-11T23:32:00Z
  checked: focused acknowledgement and logout tests
  found: acknowledgement fails alone with 401; logout passes alone.
  implication: acknowledgement is a deterministic policy regression while logout is order-dependent.
- timestamp: 2026-11-11T23:33:00Z
  checked: logout test with `logging.getLogger('app').disabled=True`
  found: the isolated logout test then fails with the same no-WARNING assertion as full discovery.
  implication: shared logger disabled state is sufficient to reproduce the second failure without changing provider or revocation behavior.
- timestamp: 2026-11-11T23:37:00Z
  checked: 16 focused acknowledgement, proof, auth-gate, CSRF, and logout tests after the fix
  found: all 16 pass; the legacy endpoint again returns its established signed-proof/origin outcomes and protected routes remain 401-gated.
  implication: the route classification fix is narrow and preserves the default-deny policy for all other product and legacy routes.
- timestamp: 2026-11-11T23:38:00Z
  checked: logout test with app logger forced disabled before setup
  found: the test now passes and restores the prior disabled flag.
  implication: the order-dependent logging assertion is isolated without changing production logout behavior.
- timestamp: 2026-11-11T23:42:00Z
  checked: complete repository validation surface
  found: 463 PostgreSQL-backed Python tests, 16 Node browser tests, Python compilation, 942-team catalog validation, and diff hygiene all pass.
  implication: the original 15 failures are resolved without regressions across Phase 1 or the completed Phase 2 slices.

## Resolution

root_cause: The Plan 02-12 default-deny gate omitted the legacy signed acknowledgement endpoint from its explicit public-at-auth classification. Separately, in-process Alembic logging setup disables existing loggers, and the logout test did not re-enable/restore the app logger around `assertLogs`.
fix: Added `acknowledge_workbook_download` to the explicit public-at-auth endpoint set while retaining same-origin signed-proof validation; updated the route matrix regression and made the logout log assertion re-enable and restore the app logger.
verification: 16 focused route/security tests pass; forced-disabled-logger reproduction passes; full 463-test Python suite and 16 Node tests pass; compilation, catalog validation, and git diff hygiene pass.
files_changed: [app.py, tests/test_identity.py]
