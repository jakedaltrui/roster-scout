---
phase: 01-canonical-snapshot-foundation
plan: "40"
subsystem: browser-download-reliability
tags: [javascript, session-storage, acknowledgements, idempotency, tdd]

requires:
  - phase: 01-canonical-snapshot-foundation
    provides: Signed privacy-bounded workbook acknowledgement receipts and trusted public-origin validation
provides:
  - Per-page safe storage shadow that survives restricted or mid-session browser storage failures
  - Explicit acknowledged, definitive, and retryable acknowledgement outcomes
  - Fresh UUID allocation only after a later explicit submit following definitive rejection
  - Same-UUID acknowledgement-only retry for network, 5xx, and retryable HTTP ambiguity
affects: [browser-downloads, download-analytics, idempotency, phase-verification]

tech-stack:
  added: []
  patterns: [shadowed-web-storage, permanent-memory-fallback, three-outcome-acknowledgement-state-machine]

key-files:
  created: []
  modified:
    - static/download_tracking.js
    - templates/index.html
    - tests/js/download_tracking.test.mjs
    - tests/test_app_routes.py

key-decisions:
  - "Every successful browser storage read or mutation updates a private per-page shadow before return; the first storage exception permanently switches that adapter to its current shadow."
  - "Only 400-499 responses other than 408, 425, and 429 are definitive acknowledgement failures; network exceptions, non-confirming 2xx bodies, retryable 4xx, and 5xx remain ambiguous."
  - "Definitive rejection clears only the matching needs_ack UUID and never starts a replacement workbook inside the same submit invocation."

patterns-established:
  - "Browser persistence is treated as an optional acceleration over a current in-memory logical state, not as a prerequisite for form operation."
  - "Acknowledgement retry decisions use an explicit outcome object instead of collapsing every non-success response into one boolean."

requirements-completed: [OPS-05]

duration: 7m
completed: 2026-11-11
---

# Phase 1 Plan 40: Browser Acknowledgement Resilience Summary

**Restricted browser storage and rejected acknowledgement receipts now recover without duplicate workbook preparation, stale UUID traps, or dishonest success claims.**

## Performance

- **Duration:** 7 min
- **Started:** 2026-11-11T17:24:01Z
- **Completed:** 2026-11-11T17:30:35Z
- **Tasks:** 2
- **Files modified:** 4

## Accomplishments

- Added a safe Web Storage adapter whose private shadow is updated by every successful get, set, and remove operation and becomes the permanent source of truth after the first browser storage exception.
- Guarded top-level `window.sessionStorage` acquisition so browser policy failures cannot prevent the form submit handler from being installed.
- Added an explicit acknowledgement status classifier: confirmed 2xx is acknowledged, ordinary 4xx is definitive, and network errors, 408/425/429, 5xx, malformed/non-confirming success responses are retryable.
- Ensured definitive rejection clears only the matching pending receipt, performs no workbook request in that invocation, and allows the next explicit submit to allocate a new UUID.
- Preserved the same pending UUID and acknowledgement-only behavior for ambiguous retries, including storage failures after a successful persistent write.

## Task Commits

Each task was committed atomically through RED/GREEN cycles:

1. **Task 1 RED: Expose browser storage failure gaps** - `f5e4cc5` (test)
2. **Task 1 GREEN: Keep download flow usable without storage** - `1fc407b` (fix)
3. **Task 2 RED: Expose acknowledgement outcome gaps** - `13cb375` (test)
4. **Task 2 GREEN: Classify download acknowledgement outcomes** - `3736606` (fix)

## Files Created/Modified

- `static/download_tracking.js` - Adds the safe storage shadow, status classifier, exact pending-abandon operation, and three-outcome submission transitions.
- `templates/index.html` - Guards storage acquisition, classifies acknowledgement HTTP responses, and presents a truthful retry message after definitive rejection.
- `tests/js/download_tracking.test.mjs` - Covers throwing storage methods, successful-write/later-read failure, 400/403, retryable 4xx, 5xx, network errors, UUID reuse, and duplicate-request prevention.
- `tests/test_app_routes.py` - Locks the guarded initialization, shared classifier wiring, and definitive retry copy into the rendered template contract.

## Decisions Made

- Safe storage preserves Web Storage string/null behavior and stores only the existing opaque UUID, acknowledgement proof, state, and timestamps; it introduces no cookie, identity, telemetry, or remote persistence.
- A definitive acknowledgement result after the browser has already received the workbook clears pending state but retains `downloadStarted: true`; the UI continues to claim only that the download started.
- Unknown or malformed acknowledgement results fail toward retryable ambiguity, preserving the pending UUID and preventing another workbook request.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

- The sandbox initially denied the local PostgreSQL socket connection; rerunning the same supported test command with approved local-database access completed successfully.
- The full suite emitted the existing psycopg connection cleanup `ResourceWarning`; all 419 tests passed and no database test failed.

## User Setup Required

None.

## Verification Results

- Browser download state-machine suite: 16 passed.
- Focused rendered-template contract: 4 passed.
- Focused PostgreSQL acknowledgement route suite: 5 passed.
- Full supported PostgreSQL-backed unittest discovery: 419 passed.
- Python compilation: passed for app, scraper, live smoke, and catalog validator.
- Template contract: `templates/index.html` and `templates/demo.html` exist.
- NCAA catalog validation: 942 selectable teams (D1 308, D2 253, D3 381), zero errors.
- `git diff --check`: passed.

## Known Stubs

None - no placeholder or unwired behavior was introduced.

## Threat Flags

None - browser storage failure and acknowledgement response classification are the planned trust surfaces and are covered by executable regressions.

## Self-Check: PASSED

- All four modified implementation and regression files exist.
- Task commits `f5e4cc5`, `1fc407b`, `13cb375`, and `3736606` resolve in repository history.
- Every acceptance criterion, both focused suites, the full 419-test suite, catalog check, compilation check, template check, and diff hygiene check passed.
- `brag-output/` remains untouched and untracked.

## Next Phase Readiness

- Browser acknowledgement retry safety is complete across persistent, restricted, and failure-prone storage environments.
- Plan 01-41 can proceed; no blocker remains from Plan 01-40.

---
*Phase: 01-canonical-snapshot-foundation*
*Completed: 2026-11-11*
