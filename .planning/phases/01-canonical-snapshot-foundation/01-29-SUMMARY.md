---
phase: 01-canonical-snapshot-foundation
plan: "29"
subsystem: workbook-download-usage
tags: [flask, javascript, postgresql, usage-metrics, idempotency]

requires:
  - phase: 01-canonical-snapshot-foundation
    provides: Privacy-safe idempotent usage ledger and aggregate queries from Plan 01-28
provides:
  - One browser-generated UUID reused throughout each intentional workbook submission
  - Durable idempotent download events committed before successful workbook responses
  - Backward-compatible request-local event keys for legacy direct callers
  - Sanitized fail-closed behavior when usage persistence is unavailable
affects: [usage-analytics, workbook-downloads, identity-organizations, customer-reporting]

tech-stack:
  added: []
  patterns: [request-scoped browser idempotency key, commit-before-success usage accounting, anonymous aggregate-safe events]

key-files:
  created:
    - .planning/phases/01-canonical-snapshot-foundation/deferred-items.md
  modified:
    - app.py
    - templates/index.html
    - tests/test_app_routes.py

key-decisions:
  - "The browser creates one UUID only after submission validation, reuses it for the in-flight request, and clears it after terminal handling."
  - "Malformed supplied keys are rejected before collection; omitted keys receive one request-local UUID for backward compatibility."
  - "A workbook response is returned only after its usage event transaction commits; persistence failure returns a fixed sanitized 503 instead."
  - "Legacy route events explicitly keep actor_account_id and organization_id null, so anonymous downloads never inflate authenticated-user counts."

patterns-established:
  - "Download success boundary: workbook construction plus committed usage event precedes HTTP 200."
  - "Client event keys are opaque deduplication inputs only and never identity evidence."

requirements-completed: [SNAP-02, OPS-05]

duration: 10 min
completed: 2026-11-10
---

# Phase 1 Plan 29: Successful Workbook Download Tracking Summary

**Each intentional browser download now carries one stable UUID, and every successful workbook response is backed by exactly one committed anonymous usage event without inflating authenticated-user totals.**

## Performance

- **Duration:** 10 min
- **Started:** 2026-11-11T00:50:00Z
- **Completed:** 2026-11-11T00:59:35Z
- **Tasks:** 2
- **Implementation files modified:** 3

## Accomplishments

- Added a request-only browser UUID after validation while preserving the existing `scrapeInFlight` double-submit guard and terminal cleanup.
- Validated supplied event keys before collection and generated one request-local UUID for older callers that omit the new field.
- Committed an idempotent anonymous `workbook_download_served` event before returning each successful Excel response.
- Failed closed with a fixed sanitized 503 when event persistence fails, without returning workbook bytes or exposing database details and event keys.
- Proved repeated responses using the same key count once, a new key counts again, and all legacy-route events report zero authenticated users.

## Task Commits

Each task was committed atomically:

1. **Task 1 RED: Missing per-submission download event key** - `615cb1f` (test)
2. **Task 1 GREEN: Stable browser download event key** - `78ce578` (feat)
3. **Task 2 RED: Missing successful-download persistence** - `8d28576` (test)
4. **Task 2 GREEN: Commit usage event before workbook success** - `1d6b92a` (feat)

## Files Created/Modified

- `templates/index.html` - Generates and submits one nonidentifying UUID per intentional in-flight download while retaining the existing busy guard.
- `app.py` - Validates or creates the event key, records the anonymous event transactionally, and fails closed on persistence errors.
- `tests/test_app_routes.py` - Covers the browser contract, UUID validation, idempotency, anonymous counts, unchanged workbook delivery, and sanitized failures.
- `.planning/phases/01-canonical-snapshot-foundation/deferred-items.md` - Records the unrelated shared-database isolation failure observed in the repository-wide test run.

## Decisions Made

- Generated the browser key only after all client-side selector validation succeeds, so validation errors do not create phantom download attempts.
- Canonicalized supplied UUIDs server-side and treated the key solely as a deduplication token, never as actor or organization identity.
- Kept direct legacy callers compatible by assigning a fresh request-local UUID only when the field is absent or null.
- Treated usage persistence as part of the success boundary: a database failure returns no Excel response rather than creating an uncounted successful download.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

- The declared PostgreSQL and Redis test services had stopped between focused and final verification; restarting the repository's Compose services restored the required test environment.
- The complete repository discovery run reached 332 tests but failed in unrelated canonical-publication fixtures because multiple schema lifecycle operations contended on one shared PostgreSQL database. The exact Plan 01-29 suite and full app-route suite pass independently; the isolation issue is recorded in `deferred-items.md`.

## User Setup Required

None - the existing PostgreSQL readiness requirement and usage-event migration from Plan 01-28 already provide the required persistence surface.

## Verification Results

- Exact Plan 01-29 acceptance suite: 10 tests passed.
- Complete app-route suite: 36 tests passed.
- Browser contract tests prove one generated UUID, in-flight reuse, terminal clearing, and no storage, cookie, fingerprint, or hidden-field identity path.
- Existing Excel filename, MIME type, workbook ZIP, Hitters sheet, Pitchers sheet, and selector-ID behavior remain green.
- Python compilation, required template presence, 942-row NCAA catalog validation, and `git diff --check`: passed.
- Repository-wide discovery: 332 tests executed; unrelated shared-database migration/publication isolation failures deferred.

## Known Stubs

None. Legacy null actors are the intentional privacy contract for anonymous Phase 1 workbook traffic, not a placeholder identity implementation.

## Threat Flags

None - UUID spoofing, replay tampering, error disclosure, and persistence denial are the explicit T-01-29-01 through T-01-29-04 mitigations.

## TDD Gate Compliance

PASSED - both behavior-adding tasks have failing `test(01-29)` RED commits followed by passing `feat(01-29)` GREEN commits.

## Self-Check: PASSED

- `app.py`, `templates/index.html`, `tests/test_app_routes.py`, `deferred-items.md`, and this summary exist.
- Task commits `615cb1f`, `78ce578`, `8d28576`, and `1d6b92a` resolve in repository history.
- Every Plan 01-29 acceptance criterion, the exact PostgreSQL-backed suite, the full route suite, compilation, template, catalog, and diff checks passed.
- `brag-output/` remains untouched and untracked.

## Next Phase Readiness

- Plan 01-30 can verify the phase end to end with exact successful-download totals and zero anonymous unique-user inflation.
- Phase 2 can add authenticated actor and organization IDs at its authorized report boundary without changing the legacy download contract.

---
*Phase: 01-canonical-snapshot-foundation*
*Completed: 2026-11-10*
