---
phase: 01-canonical-snapshot-foundation
plan: "35"
subsystem: browser-download-acknowledgement
tags: [javascript, sessionStorage, node-test, flask-template, usage-metrics]

requires:
  - phase: 01-canonical-snapshot-foundation
    provides: Stateless UUID-bound workbook acknowledgement receipts from Plan 01-34
provides:
  - Retry-safe browser UUID lifecycle with requesting, needs_ack, and acknowledged transitions
  - Blob-read and anchor-initiation ordering before successful-download acknowledgement
  - Fifteen-minute bounded session state containing only opaque receipt material
affects: [usage-analytics, native-workbooks, browser-download-ux, final-release-gate]

tech-stack:
  added: []
  patterns: [dependency-free browser state machine, session-scoped idempotency, callback-tested download ordering]

key-files:
  created:
    - static/download_tracking.js
    - tests/js/download_tracking.test.mjs
  modified:
    - templates/index.html
    - tests/test_app_routes.py

key-decisions:
  - "One session-scoped opaque UUID survives ambiguous scrape, blob, and acknowledgement failures; definitive HTTP failure or acknowledged success ends that attempt."
  - "The browser records needs_ack only after readable workbook bytes and synchronous anchor initiation, so acknowledgement never claims filesystem-save proof."
  - "Only UUID, lifecycle state, signed proof, and bounded timestamps enter sessionStorage; acknowledged is an ephemeral terminal transition and not persistent identity."

patterns-established:
  - "Pending-first submit: retry one saved acknowledgement before allocating a UUID for a later intentional report."
  - "Prepared workbook UX: a started download remains successful even when the acknowledgement response is ambiguous."

requirements-completed: [OPS-05]

duration: 8 min
completed: 2026-11-11
---

# Phase 1 Plan 35: Browser Download Receipt State Machine Summary

**A short-lived browser state machine now preserves one download UUID across ambiguous failures and acknowledges only after workbook bytes are readable and the anchor download has started.**

## Performance

- **Duration:** 8 min
- **Started:** 2026-11-11T05:53:37Z
- **Completed:** 2026-11-11T06:01:32Z
- **Tasks:** 2
- **Files modified:** 4

## Accomplishments

- Added a dependency-free, Node-tested browser state machine with explicit `requesting`, `needs_ack`, and ephemeral `acknowledged` transitions.
- Preserved one opaque UUID through lost scrape responses, blob failures, acknowledgement timeouts, reloads, and duplicate callbacks while expiring state after 15 minutes.
- Wired `/scrape` to read the blob and initiate the anchor download before posting the UUID/proof to `/api/downloads/ack`.
- Kept a started workbook successful when acknowledgement is ambiguous, then retried that acknowledgement before a later intentional report.
- Reworded success copy to claim only that the download started, never that the browser saved a file to disk.

## Task Commits

Each task was committed atomically:

1. **Task 1 RED: Expose browser UUID lifecycle and retry gaps** - `d771a38` (test)
2. **Task 1 GREEN: Add bounded retry-safe browser state machine** - `45d6a09` (feat)
3. **Task 2 RED: Require state-machine template and acknowledgement wiring** - `f3f46f8` (test)
4. **Task 2 GREEN: Acknowledge after blob read and anchor initiation** - `353cf97` (feat)
5. **Acceptance correction: Return explicit terminal acknowledged transition** - `4f5088a` (fix)

## Files Created/Modified

- `static/download_tracking.js` - Owns bounded session state, UUID transitions, pending acknowledgement retry, and ordered download orchestration.
- `tests/js/download_tracking.test.mjs` - Covers lost scrape/blob/ack responses, definitive failure, duplicate acknowledgement, expiry, ordering, and later submissions.
- `templates/index.html` - Loads the same-origin module and integrates receipt verification, blob download, acknowledgement retry, and honest success/error copy.
- `tests/test_app_routes.py` - Replaces the obsolete unconditional-finally UUID assertions with the new template and privacy contract.

## Decisions Made

- A pending acknowledgement blocks a later scrape until it is acknowledged or expires; silently allocating a second UUID would make one browser outcome count twice.
- The browser stores only one attempt in `sessionStorage`; no team, report, email, account, network, device, cookie, or fingerprint material is persisted.
- Network, blob, receipt-header, and acknowledgement ambiguity retain the active UUID, while a definitive non-2xx scrape response abandons it.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Test Contract] Replaced obsolete historical UUID assertions**
- **Found during:** Task 2 (Acknowledge after blob read and download initiation)
- **Issue:** `DownloadEventContractTests` required a fresh inline UUID and unconditional `finally` clearing, the exact behavior this plan removes, but the test file was omitted from `files_modified`.
- **Fix:** Converted the class to assert same-origin module wiring, session-only state, protected acknowledgement routing, no identity storage, and honest download-start copy.
- **Files modified:** `tests/test_app_routes.py`
- **Verification:** The focused Python class passes 3/3 and full discovery passes 387/387.
- **Committed in:** `f3f46f8`, `353cf97`

**2. [Rule 2 - Missing Critical] Exposed the planned acknowledged terminal state**
- **Found during:** Plan acceptance audit after Task 2
- **Issue:** Successful acknowledgement cleared storage correctly but returned a bare boolean, leaving the plan's explicit `acknowledged` transition unobservable to tests and callers.
- **Fix:** Return an ephemeral `{downloadEventId, state: "acknowledged"}` transition while keeping completed state out of storage.
- **Files modified:** `static/download_tracking.js`, `tests/js/download_tracking.test.mjs`
- **Verification:** All 8 Node state-machine tests and 3 template contract tests pass.
- **Committed in:** `4f5088a`

---

**Total deviations:** 2 auto-fixed (1 test-contract bug, 1 missing correctness contract).
**Impact on plan:** Both corrections were required to verify the locked lifecycle semantics; neither expands product scope.

## Issues Encountered

- The first template test assumed the acknowledgement helper would appear textually after the scrape payload. Call ordering belongs to the executable Node suite, so the source test was corrected to verify wiring without inferring runtime order from function placement.
- The full suite continues to emit the pre-existing psycopg cleanup `ResourceWarning` already recorded in `deferred-items.md`; all tests pass.

## User Setup Required

None - this plan adds no new environment variables or external services. The shared acknowledgement secret requirement remains documented by Plan 01-34.

## Verification Results

- Browser state-machine contract: 8 passed with Node `node:test`.
- Flask template download contract: 3 passed.
- Full PostgreSQL-backed discovery: 387 passed.
- Python compilation for app, scraper, and required scripts: passed.
- Required templates: present.
- NCAA catalog validation: 942 selectable teams, zero errors.
- `git diff --check`: passed.

## Known Stubs

None. The acknowledgement retry and storage expiry paths are fully wired.

## Threat Flags

None - browser storage and acknowledgement ordering are the planned surfaces covered by T-01-35-01 through T-01-35-05.

## TDD Gate Compliance

PASSED - both tasks have failing RED commits followed by passing GREEN commits, with one acceptance correction committed separately.

## Self-Check: PASSED

- `static/download_tracking.js`, `tests/js/download_tracking.test.mjs`, `templates/index.html`, `tests/test_app_routes.py`, and this summary exist.
- Task commits `d771a38`, `45d6a09`, `f3f46f8`, `353cf97`, and `4f5088a` resolve in repository history.
- Every acceptance criterion, focused suite, complete 387-test discovery, compilation, templates, catalog, and diff check passed.
- `brag-output/` remains untouched and untracked.

## Next Phase Readiness

- Plan 01-37 can run the focused browser counterexamples before the complete final release gate.
- No implementation blockers remain for browser receipt semantics.

---
*Phase: 01-canonical-snapshot-foundation*
*Completed: 2026-11-11*
