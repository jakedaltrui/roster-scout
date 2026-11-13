---
phase: 02-identity-organizations-and-report-library
plan: "16"
subsystem: auth
tags: [flask, javascript, csrf, signed-proof, postgres, idempotency]

# Dependency graph
requires:
  - phase: 02-09
    provides: Exact stored-artifact preparation, opaque receipts, and short-lived purpose-bound proofs
  - phase: 01-34
    provides: Stateless browser acknowledgement protocol and privacy-safe download usage ledger
provides:
  - Ordered authenticated prepare, blob-read, anchor-initiation, and same-origin acknowledgement flow
  - Stateless current-membership validation for prepared-artifact acknowledgements
  - Exactly-once canonical account and organization workbook download usage with one success audit
affects: [usage-analytics, report-library, security-audit, authenticated-downloads]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - Signed principal digest layered onto the existing full artifact scope digest
    - Receipt UUID as the durable browser retry key and database idempotency key
    - Usage insertion outcome gates the corresponding success audit

key-files:
  created:
    - .planning/phases/02-identity-organizations-and-report-library/02-16-SUMMARY.md
  modified:
    - app.py
    - roster_scout/usage.py
    - static/download_tracking.js
    - templates/report_detail.html
    - tests/test_app_routes.py
    - tests/js/download_tracking.test.mjs

key-decisions:
  - "Add a hashed principal binding to the signed preparation proof so the acknowledgement can validate the current account and organization statelessly without exposing raw identifiers."
  - "Use the server-issued receipt UUID as both the persisted retry identity and canonical usage event key; a replay returns success but cannot insert a second event or audit."
  - "Keep authenticated download retry state separate from anonymous workbook tracking and retain only opaque receipt, proof, and timestamps in browser storage."
  - "Measure browser blob-read plus synchronous anchor initiation and acknowledgement; never claim proof that the browser saved a file to the filesystem."

patterns-established:
  - "Prepared-artifact acknowledgement: verify origin, CSRF, signed purpose, receipt, current membership, and principal binding before any ledger write."
  - "Ambiguous browser retry: persist needs_ack and retry acknowledgement only across reloads, never repeat blob reading or anchor initiation."

requirements-completed: [AUTH-01, ORG-06, SECU-01, SECU-02, SECU-04, SECU-05]

# Metrics
duration: 30min
completed: 2026-11-13
---

# Phase 2 Plan 16: Authenticated Download Acknowledgement Summary

**Stateless prepared-artifact proofs now drive an ordered browser initiation flow whose same-origin acknowledgement records one canonical tenant download event and one audit without asserting filesystem-save proof.**

## Performance

- **Duration:** 30 minutes
- **Started:** 2026-11-13T06:00:58Z
- **Completed:** 2026-11-13T06:30:46Z
- **Tasks:** 2
- **Files modified:** 7

## Accomplishments

- Extended the existing acknowledgement route to preserve anonymous behavior while authenticating prepared historical-report receipts with exact origin, CSRF, signed-purpose, receipt, current-membership, and tenant-binding checks.
- Added an ambiguity-safe browser state machine that reads the authorized blob, initiates a hidden anchor, then acknowledges; reloads retry acknowledgement only.
- Recorded `workbook_download_served` exactly once with canonical account and organization IDs and emitted the corresponding successful security audit only for the first insert.
- Wired every authenticated report-detail download form to the state machine while retaining the original form POST as progressive enhancement fallback.

## Task Commits

Each task was committed atomically:

1. **Task 1: Specify authenticated initiation, acknowledgement, and ambiguous retry** - `5df0821` (test)
2. **Task 2: Implement same-origin acknowledgement and exactly-once usage tracking** - `da9cd67` (feat)

**Plan metadata:** recorded in the final documentation commit.

## Files Created/Modified

- `.planning/phases/02-identity-organizations-and-report-library/02-16-SUMMARY.md` - Execution evidence, decisions, deviations, and verification record.
- `app.py` - Stateless authenticated proof verification, current-membership enforcement, idempotent ledger write, and first-insert audit.
- `roster_scout/usage.py` - Canonical authenticated workbook event producer that reports whether the unique insert occurred.
- `static/download_tracking.js` - Ordered authenticated preparation/initiation/acknowledgement flow with durable acknowledgement-only retry.
- `templates/report_detail.html` - Actual historical-report forms opt into the authenticated tracker while retaining native POST fallback.
- `tests/test_app_routes.py` - Positive, replay, origin/CSRF, proof, membership, secrecy, and template-wiring coverage.
- `tests/js/download_tracking.test.mjs` - Ordering, rejection, and reload ambiguity coverage for the browser protocol.

## Decisions Made

- The signed payload retains Plan 02-09's full artifact scope digest and adds a separate SHA-256 principal binding. The server can therefore compare the proof to the current canonical account and organization without embedding raw tenant identifiers or introducing receipt storage.
- Replay semantics are deliberately successful and idempotent. The unique receipt event key prevents a second usage row, while the insertion result prevents a duplicate success audit.
- Authenticated retries use a distinct storage key and adopt the server-issued receipt. A pending receipt always takes the acknowledgement-only path before any new preparation can begin.
- The metric represents an authorized prepared blob read followed by browser anchor initiation and acknowledgement. Neither UI copy nor server behavior claims that a filesystem save completed.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing Critical] Wired the real report-detail download forms**
- **Found during:** Task 2 (browser state-machine integration)
- **Issue:** The plan listed the tracker but not the template that owns authenticated historical-download forms. Without form wiring, the completed state machine would be unreachable in the product.
- **Fix:** Loaded the existing tracker on `report_detail.html` and marked all desktop and mobile historical-artifact forms for interception, preserving their original form action and server fallback.
- **Files modified:** `templates/report_detail.html`, `tests/test_app_routes.py`
- **Verification:** Template contract test asserts the script and all three form hooks; the full Python suite passes.
- **Committed in:** `da9cd67`

**2. [Rule 2 - Missing Critical] Added a privacy-safe principal binding to prepared proofs**
- **Found during:** Task 2 (stateless membership validation)
- **Issue:** Plan 02-09 signed only a one-way digest of account, organization, report, snapshot, and artifact together. The acknowledgement had no raw scope fields and could not prove that the signed preparation belonged to the current tenant without storing server receipt state.
- **Fix:** Added a separate hashed account/organization binding to the already signed payload, retained the original full artifact scope digest, and compared the principal digest to current active membership in constant time.
- **Files modified:** `app.py`, `tests/test_app_routes.py`
- **Verification:** Foreign/inactive membership, tampering, expiration, missing proof, and replay tests pass; raw account, organization, report, snapshot, and artifact IDs remain absent from the proof payload.
- **Committed in:** `da9cd67`

**3. [Rule 2 - Missing Critical] Gated success audit emission on first usage insertion**
- **Found during:** Task 2 (ambiguous acknowledgement replay)
- **Issue:** Database uniqueness prevented duplicate usage rows, but the existing producer did not expose whether it inserted, so an ambiguous retry could still duplicate the success audit.
- **Fix:** Added an insertion-result producer and emitted `artifact.download` only when the canonical event was newly created.
- **Files modified:** `roster_scout/usage.py`, `app.py`, `tests/test_app_routes.py`
- **Verification:** Valid acknowledgement plus replay produces one usage event and one success audit; concurrent/idempotent usage tests remain green.
- **Committed in:** `da9cd67`

---

**Total deviations:** 3 auto-fixed (3 Rule 2 missing-critical corrections)
**Impact on plan:** All changes are narrow correctness and security requirements for making the planned protocol reachable, tenant-bound, and exactly-once. No new dependency, schema, infrastructure, or deployment change was introduced.

## Issues Encountered

- The sandbox initially blocked access to the local PostgreSQL test service and Git index. The same scoped test and commit operations were rerun with approved workspace escalation; no production service or deployment was touched.
- The full Python suite emits pre-existing psycopg connection `ResourceWarning` messages but completes successfully.

## Verification

- `524` Python tests passed via full `unittest` discovery.
- `26` Node tests passed across `tests/js/*.test.mjs`.
- Python bytecode compilation passed for the application, scraper, validation scripts, and usage module.
- NCAA catalog validation passed with `942` selectable teams and no errors.
- `git diff --check` passed.

## Known Stubs

None introduced. The authenticated report forms are connected to real preparation and acknowledgement routes, and no empty or mock data path was added.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Authenticated historical-report usage is ready for aggregate product analytics and security review.
- No blockers remain. The protocol remains stateless and does not claim browser filesystem-save verification.

## Self-Check: PASSED

- Summary file exists.
- Task commits `5df0821` and `da9cd67` exist in repository history.
- No stub patterns or unplanned threat surfaces were introduced in the changed files.

---
*Phase: 02-identity-organizations-and-report-library*
*Completed: 2026-11-13*
