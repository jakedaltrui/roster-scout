---
phase: 01-canonical-snapshot-foundation
plan: "34"
subsystem: download-acknowledgement
tags: [flask, itsdangerous, hmac, postgresql, usage-metrics, same-origin]

requires:
  - phase: 01-canonical-snapshot-foundation
    provides: Database-optional native workbook analytics and idempotent usage events from Plans 01-28, 01-30, and 01-33
provides:
  - Prepared-workbook UUID and 15-minute signed proof headers without premature usage persistence
  - Stateless same-origin acknowledgement endpoint with bounded input and database-backed idempotency
  - Fail-fast shared-secret preflight for Fly and multi-worker runtimes
affects: [browser-download-state-machine, deployment-secrets, usage-analytics, native-workbooks]

tech-stack:
  added: []
  patterns: [stateless timed receipt proof, exact same-origin acknowledgement, shared-secret runtime preflight]

key-files:
  created: []
  modified:
    - app.py
    - tests/test_app_routes.py

key-decisions:
  - "A workbook response is preparation only; successful-download usage is recorded after a recent same-origin browser acknowledgement."
  - "Acknowledgement proofs contain only a fixed purpose and opaque UUID, expire after exactly 15 minutes, and rely on the existing unique usage-event key for replay idempotency."
  - "Fly and WEB_CONCURRENCY greater than one require one validated shared URL-safe base64 secret; only native single-process local mode may generate a process-random fallback."

patterns-established:
  - "Prepared response: emit canonical UUID plus signed proof only after a successful XLSX build, with Cache-Control no-store and no usage write."
  - "Acknowledgement boundary: exact JSON shape, 1 KiB body, 512-character proof, exact Origin, cross-site rejection, timed signature, then best-effort persistence."

requirements-completed: [OPS-05]

duration: 8 min
completed: 2026-11-11
---

# Phase 1 Plan 34: Protected Download Acknowledgement Summary

**Workbook preparation now returns a short-lived UUID-bound receipt, while only a protected same-origin browser acknowledgement records one idempotent successful-download event.**

## Performance

- **Duration:** 8 min
- **Started:** 2026-11-11T05:42:49Z
- **Completed:** 2026-11-11T05:50:24Z
- **Tasks:** 2
- **Files modified:** 2

## Accomplishments

- Removed successful-download persistence from `/scrape` and added canonical event/proof headers only to completed XLSX responses.
- Added import-time runtime preflight that validates positive worker concurrency and a decoded 32-byte-or-longer URL-safe base64 secret for Fly or multi-worker service.
- Added a stateless `/api/downloads/ack` verifier with exact same-origin, JSON-shape, body-size, proof-size, UUID, signature, and 15-minute age checks.
- Preserved idempotent retry semantics through the existing `(event_type, event_key)` uniqueness contract, including simulated lost-response retry.
- Preserved native no-database delivery: an accepted acknowledgement returns 202 with `persisted=false` and emits one fixed privacy-safe warning.

## Task Commits

Each task was committed atomically:

1. **Task 1 RED: Prepared responses persisted download success and had no receipt contract** - `19407a7` (test)
2. **Task 1 GREEN: Issue validated prepared-workbook receipts without usage writes** - `e827e95` (feat)
3. **Task 2 RED: Acknowledgement endpoint and abuse boundaries were absent** - `b4097ce` (test)
4. **Task 2 GREEN: Verify and persist protected browser acknowledgements** - `d26e26b` (feat)

## Files Created/Modified

- `app.py` - Validates acknowledgement secrets, signs prepared responses, verifies bounded same-origin acknowledgements, and records accepted UUIDs best-effort.
- `tests/test_app_routes.py` - Covers startup preflight, cross-process proof interoperability, prepared-response semantics, replay, origin/payload forgery, age boundaries, bounds, and native drops.

## Decisions Made

- The server keeps no receipt registry, cache, table, list, or set; proof freshness is cryptographic and replay safety remains database idempotency on the opaque UUID.
- A configured secret is always validated, including local mode, so a local override cannot silently weaken proof strength.
- Invalid acknowledgement inputs return fixed errors and never echo or log secrets, proofs, payloads, identities, teams, or database details.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

- The full suite continues to emit the pre-existing psycopg cleanup `ResourceWarning` already recorded in `deferred-items.md`; all 386 tests pass.
- The generic key-link checker reports `Source file not found` for the plan's route-to-route labels even though the concrete header, endpoint, producer call, and preflight patterns are implemented and exercised by focused tests.

## User Setup Required

Before any Fly or `WEB_CONCURRENCY>1` deployment, set the same `ROSTER_SCOUT_DOWNLOAD_ACK_SECRET` on every app process. It must be URL-safe base64 that decodes to at least 32 random bytes, such as the output of `secrets.token_urlsafe(32)`. Never commit or log this value.

Native single-process local mode may omit the variable and will generate one process-random secret at startup.

## Verification Results

- Prepared receipt and secret-configuration suites: 7 passed.
- Complete focused Plan 01-34 receipt/secret/acknowledgement suites: 12 passed.
- Complete app-route module: 45 passed.
- Complete PostgreSQL-backed repository discovery: 386 passed.
- Proof age boundary: 899 and 900 seconds accepted; 901 seconds rejected.
- Clean-process same-secret proof interoperability and different-secret rejection: passed.
- Clean-process no-`DATABASE_URL` acknowledgement: 202, `acknowledged=true`, `persisted=false`, fixed sanitized warning.
- Python compilation, required templates, 942-team NCAA catalog validation, and `git diff --check`: passed.

## Known Stubs

None. The native `persisted=false` response is an explicit observed analytics disposition, not fallback storage.

## Threat Flags

None - the new acknowledgement endpoint and signing boundary are the planned surfaces covered by T-01-34-01 through T-01-34-06.

## TDD Gate Compliance

PASSED - both behavior groups have failing RED commits followed by passing GREEN commits.

## Self-Check: PASSED

- `app.py`, `tests/test_app_routes.py`, and this summary exist.
- Task commits `19407a7`, `e827e95`, `b4097ce`, and `d26e26b` resolve in repository history.
- Every acceptance criterion, focused suite, complete 386-test discovery, compilation, templates, catalog, and diff check passed.
- `brag-output/` remains untouched and untracked.

## Next Phase Readiness

- Plan 01-35 can consume the prepared UUID/proof headers and acknowledge only after blob receipt and download initiation.
- Deployment must provision one shared acknowledgement secret before Fly or multi-worker startup.

---
*Phase: 01-canonical-snapshot-foundation*
*Completed: 2026-11-11*
