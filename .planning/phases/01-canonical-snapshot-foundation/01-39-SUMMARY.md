---
phase: 01-canonical-snapshot-foundation
plan: "39"
subsystem: deployment-security-and-download-analytics
tags: [flask, fly-io, origin-validation, same-origin, tdd]

requires:
  - phase: 01-canonical-snapshot-foundation
    provides: Signed privacy-bounded workbook download acknowledgements and Fly internal-HTTP topology
provides:
  - Startup-validated canonical browser origin for Fly and every multi-worker runtime
  - Public-HTTPS-to-internal-HTTP acknowledgement compatibility without forwarded-header trust
  - Exact Fly origin configuration and coordinated custom-domain cutover guidance
  - Clean-process and route regressions for malformed configuration and header spoofing
affects: [fly-deployment, download-analytics, production-operations, phase-verification]

tech-stack:
  added: []
  patterns: [startup-validated-external-origin, configured-trust-anchor, local-only-request-origin-fallback]

key-files:
  created: []
  modified:
    - app.py
    - fly.toml
    - DEPLOYMENT.md
    - tests/test_app_routes.py
    - tests/test_deployment_config.py

key-decisions:
  - "Fly or WEB_CONCURRENCY>1 requires one configured canonical public origin; only non-Fly single-process development may derive the origin from the direct request."
  - "Fly public origins are HTTPS-only, while non-Fly multi-worker deployments may use a validated HTTP or HTTPS origin."
  - "Forwarded and Host headers never redefine the shared-runtime acknowledgement trust anchor."

patterns-established:
  - "External edge identity is validated once at startup and compared directly at the security boundary."
  - "Custom-domain changes use one coordinated origin value and full app-worker restart before traffic cutover."

requirements-completed: [OPS-05]

duration: 8m
completed: 2026-11-11
---

# Phase 1 Plan 39: Trusted Public Download Origin Summary

**Workbook acknowledgement now accepts the canonical public HTTPS Fly origin across the internal HTTP hop while failing shared runtimes closed and leaving attacker-controlled forwarding headers inert.**

## Performance

- **Duration:** 8 min
- **Started:** 2026-11-11T17:12:08Z
- **Completed:** 2026-11-11T17:20:11Z
- **Tasks:** 2
- **Files modified:** 5

## Accomplishments

- Added strict standard-library parsing and canonicalization for `ROSTER_SCOUT_PUBLIC_ORIGIN`, including sanitized startup failure for missing or malformed shared-runtime configuration.
- Corrected Fly's public-HTTPS/internal-HTTP acknowledgement mismatch without installing proxy middleware or trusting `Forwarded`, `X-Forwarded-*`, or `Host` headers.
- Preserved direct request-origin behavior for native non-Fly single-process use while requiring configured trust for every multi-worker process.
- Declared `https://roster-scout.fly.dev` in Fly's nonsecret environment and documented exact validation and custom-domain cutover operations.

## Task Commits

Each task was committed atomically through RED/GREEN cycles:

1. **Task 1 RED: Define trusted public origin contract** - `70d72fa` (test)
2. **Task 1 GREEN: Trust configured acknowledgement origin** - `43e1655` (fix)
3. **Task 2 RED: Require deployed public origin contract** - `1d9acba` (test)
4. **Task 2 GREEN: Configure canonical Fly public origin** - `701e4d2` (docs)

## Files Created/Modified

- `app.py` - Validates and canonicalizes the configured origin at startup, then uses it for acknowledgement-origin comparison.
- `fly.toml` - Declares the exact canonical public HTTPS origin while retaining port 5000 and Fly edge HTTPS enforcement.
- `DEPLOYMENT.md` - Documents the complete shared-runtime predicate, header non-trust, and coordinated custom-domain cutover.
- `tests/test_app_routes.py` - Covers clean-process failure modes, local compatibility, Fly edge topology, and forwarded/Host spoofing.
- `tests/test_deployment_config.py` - Parses the manifest and locks the Fly/runbook public-origin contract.

## Decisions Made

- Canonicalization lowercases scheme and host, removes a sole trailing `/`, and retains an explicit valid port; credentials, other paths, queries, fragments, invalid ports, and non-HTTP(S) schemes fail startup.
- A nonempty `FLY_APP_NAME` is the HTTPS requirement discriminator, while the existing shared-runtime predicate remains nonempty Fly name OR positive `WEB_CONCURRENCY` greater than one.
- Local fallback deliberately uses only Flask's direct `request.scheme://request.host`; it is unavailable in shared runtimes and does not inspect forwarding headers.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

- The sandbox initially denied the local PostgreSQL socket connection; rerunning the same supported test command with approved local-database access completed successfully.
- The full suite emitted the existing psycopg connection cleanup `ResourceWarning`; all 418 tests passed and no database test failed.

## User Setup Required

None for the checked-in Fly hostname. A future custom-domain launch must follow the coordinated `ROSTER_SCOUT_PUBLIC_ORIGIN` cutover documented in `DEPLOYMENT.md`.

## Verification Results

- Focused acknowledgement origin, secret, body/proof bound, replay, and database-optional tests: 12 passed.
- Focused deployment manifest and operator runbook tests: 8 passed.
- Full supported PostgreSQL-backed unittest discovery: 418 passed.
- Python compilation: passed for app, scraper, live smoke, and catalog validator.
- Template contract: `templates/index.html` and `templates/demo.html` exist.
- NCAA catalog validation: 942 selectable teams (D1 308, D2 253, D3 381), zero errors.
- `git diff --check`: passed.

## Known Stubs

None - no placeholder or unwired behavior was introduced.

## Threat Flags

None - the configured external-origin trust anchor and acknowledgement route are the planned trust surfaces and are covered by startup, topology, and spoofing regressions.

## Self-Check: PASSED

- All five modified implementation, deployment, documentation, and regression files exist.
- Task commits `70d72fa`, `43e1655`, `1d9acba`, and `701e4d2` resolve in repository history.
- Every acceptance criterion, both focused suites, the full 418-test suite, catalog check, compilation check, template check, and diff hygiene check passed.
- `brag-output/` remains untouched and untracked.

## Next Phase Readiness

- The verifier's Fly TLS acknowledgement gap is closed without broadening proxy trust.
- Plans 01-40 and 01-41 can proceed; no blocker remains from Plan 01-39.

---
*Phase: 01-canonical-snapshot-foundation*
*Completed: 2026-11-11*
