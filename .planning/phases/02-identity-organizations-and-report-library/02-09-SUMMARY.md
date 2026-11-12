---
phase: 02-identity-organizations-and-report-library
plan: 09
subsystem: reports
tags: [flask, postgresql, jinja, retries, artifacts, multi-tenant, signed-proofs]

requires:
  - phase: 02-identity-organizations-and-report-library
    provides: saved-snapshot-first detail, immutable history, durable job/outbox acceptance, Phase 1 receipt/proof signing
provides:
  - immutable failed-attempt history with CSRF-protected durable retry convergence
  - useful partial-snapshot review with exact ready and unavailable sections
  - composite organization/report/snapshot/artifact authorization for historical workbooks
  - SHA-256-verified stored-byte streaming with bounded scope-bound stateless preparation proofs
  - truthful unavailable-artifact and sanitized source/database/queue/storage failure states
affects: [02-16-download-acknowledgement, report-detail, snapshot-history, artifact-storage]

tech-stack:
  added: []
  patterns: [terminal-predecessor retry key, composite artifact loader, injected artifact reader, opaque scope-bound timed proof]

key-files:
  created: [.planning/phases/02-identity-organizations-and-report-library/02-09-SUMMARY.md]
  modified: [app.py, roster_scout/repositories.py, roster_scout/reports.py, roster_scout/report_routes.py, templates/report_detail.html, tests/test_report_library.py, tests/test_identity.py]

key-decisions:
  - "Retry preserves the selected failed or blocked job and durably links its successor through retry:<failed-job-id>:<action-id> while report locking converges concurrent clicks."
  - "Historical download authorization repeats organization, report, snapshot, and artifact predicates and streams only bytes returned for the stored object key after SHA-256 verification."
  - "Authenticated preparation proofs contain an opaque receipt and a digest of canonical actor/organization/report/snapshot/artifact scope, never raw identity or report identifiers."
  - "Artifact preparation records a fixed pending audit event but no workbook-download usage event; Plan 02-16 owns browser acknowledgement and successful-download accounting."

patterns-established:
  - "Retry acceptance: active membership lock -> tenant report lock -> terminal predecessor validation -> active-job convergence/new linked job -> usage/audit -> commit -> outbox drain."
  - "Historical artifact preparation: composite scoped metadata load -> injected stored-byte read -> size/hash verification -> scope-bound receipt/proof -> audit -> no-store XLSX response."

requirements-completed: [AUTH-01, SNAP-06, SNAP-08, ORG-06, SECU-01, SECU-02, SECU-04, SECU-05]

duration: 18min
completed: 2026-11-12
---

# Phase 2 Plan 9: Safe Retry, Partial Review, and Exact Artifact Preparation Summary

**Immutable failed-attempt retry, useful partial snapshots, and exact tenant-bound historical workbook preparation with scoped stateless proofs**

## Performance

- **Duration:** 18 min
- **Started:** 2026-11-12T16:42:02Z
- **Completed:** 2026-11-12T17:00:12Z
- **Tasks:** 2
- **Files modified:** 7

## Accomplishments

- Added a CSRF-protected retry action that validates the selected failed attempt inside the active organization, preserves it unchanged, creates one linked durable successor, and converges concurrent retries through the existing report lock, job, usage, audit, and dispatch-outbox boundaries.
- Kept Ready sections reviewable in Partial snapshots while naming exact unavailable sections with fixed safe explanations; failed builds remain separate from published snapshot history.
- Added exact historical workbook authorization across organization, report, snapshot, and artifact IDs, with identical absent/foreign/substituted 404 behavior and no scraper or rebuild path.
- Added stored-byte size and SHA-256 verification, fixed storage-failure responses, no-store delivery, and a short-lived receipt/proof bound opaquely to the canonical actor and selected artifact scope.
- Preserved honest download analytics: preparation writes an audit event only, while `workbook_download_served` remains absent until Plan 02-16 browser acknowledgement.

## Task Commits

Each task was committed atomically:

1. **Task 1: Specify failed retry, partial snapshots, and historical artifacts (RED)** - `07410d5` (test)
2. **Task 2: Implement safe retry, partial review, and exact artifact preparation (GREEN)** - `3ec5ab5` (feat)

## Files Created/Modified

- `app.py` - Reuses the Phase 1 timed serializer for an opaque authenticated artifact-scope binding and injects that signer into report routes.
- `roster_scout/repositories.py` - Resolves a ready workbook only through the exact organization/report/snapshot/artifact composite scope.
- `roster_scout/reports.py` - Adds immutable failed-job retry acceptance, exact artifact metadata resolution, artifact IDs in history, and distinct fixed queue failure copy.
- `roster_scout/report_routes.py` - Adds protected retry and artifact preparation/stream routes, dispatch recovery, stored-byte integrity checks, fixed failures, and preparation audit evidence.
- `templates/report_detail.html` - Adds retry controls, partial/unavailable explanations, exact historical download forms, and desktop/mobile non-rebuild copy.
- `tests/test_report_library.py` - Covers duplicate retry, predecessor preservation, partial sections, foreign/substituted artifacts, scoped proofs, no premature usage, and sanitized storage failures.
- `tests/test_identity.py` - Keeps the exhaustive route inventory fail-closed with both new POST routes classified as protected.

## Decisions Made

- Used the existing `ReportJob.idempotency_key` as an equivalent durable predecessor link instead of adding a new schema column: `retry:<failed-job-id>:<action-id>` preserves terminal history and remains compatible with the Phase 1 worker/outbox model.
- Kept the artifact storage implementation behind an injected reader. Phase 1 intentionally deferred managed object storage, so an unconfigured runtime returns a sanitized 503 rather than reading local paths, contacting NCAA, or fabricating a historical workbook.
- Bound the preparation proof to a SHA-256 digest of canonical account, organization, report, snapshot, and artifact UUIDs. The signed proof is verifiable by Plan 02-16 without exposing those raw identifiers in its payload.
- Treated preparation as pending audit evidence only. Successful-download usage remains exclusively acknowledgement-based.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Route security] Extended the exhaustive protected-route inventory**
- **Found during:** Task 2 full Python regression
- **Issue:** The new retry and artifact POST routes made the fail-closed route classification matrix stale.
- **Fix:** Added both routes to the protected inventory without widening public or account-only allowlists.
- **Files modified:** `tests/test_identity.py`
- **Verification:** Route matrix test and full 519-test Python suite pass.
- **Committed in:** `3ec5ab5`

**2. [Rule 2 - Proof scope] Added an opaque canonical artifact-scope binding to the existing timed proof boundary**
- **Found during:** Task 2 authenticated preparation implementation
- **Issue:** A receipt-only proof could not let Plan 02-16 reject a proof replayed across actors, organizations, snapshots, or artifacts without introducing server-side preparation state.
- **Fix:** Reused the Phase 1 serializer and expiry while signing a digest of canonical actor/organization/report/snapshot/artifact scope; no raw scoped UUID enters the proof payload.
- **Files modified:** `app.py`, `roster_scout/report_routes.py`, `tests/test_report_library.py`
- **Verification:** Exact-scope verification succeeds, substituted-artifact verification fails, legacy Phase 1 receipt suites remain green.
- **Committed in:** `3ec5ab5`

**3. [Rule 2 - Artifact integrity] Added bounded size and checksum verification before issuing a receipt**
- **Found during:** Task 2 historical artifact streaming
- **Issue:** Trusting arbitrary reader output could serve substituted or corrupted bytes under valid artifact metadata.
- **Fix:** Require nonempty bytes no larger than 64 MiB and constant-time compare their SHA-256 digest to the stored artifact checksum before proof or audit creation.
- **Files modified:** `roster_scout/report_routes.py`, `tests/test_report_library.py`
- **Verification:** Exact bytes stream; storage/integrity failures return fixed 503 with no receipt, usage, audit, or raw exception content.
- **Committed in:** `3ec5ab5`

---

**Total deviations:** 3 auto-fixed (3 missing critical/security/correctness)
**Impact on plan:** The fixes close route-classification, proof-replay, and stored-byte substitution gaps without adding dependencies or changing the Plan 02-16 acknowledgement boundary.

## Issues Encountered

- The first full discovery run passed 518 tests and failed only because the exhaustive route inventory did not yet name the two newly added protected routes. The inventory was updated and the clean full suite then passed all 519 tests.
- The sandbox initially blocked local PostgreSQL and Git index access; the approved local test and atomic-commit paths were used without touching production or deployment state.

## Verification

- Focused retry/partial/artifact plus snapshot-history suite: **10 passed**.
- Focused authenticated preparation and legacy receipt/acknowledgement regression: **19 passed**.
- Report-library plus Phase 1 job suites: **77 passed**.
- Full Python discovery: **519 passed**.
- Node browser suites: **23 passed**.
- Python compile checks: passed.
- NCAA catalog validation: passed, **942 selectable teams**.
- Required templates present and `git diff --check`: passed.

## Known Stubs

- `roster_scout/report_routes.py` intentionally defaults the injected artifact reader to a fixed sanitized failure because managed object storage and actual artifact byte persistence were deferred beyond Phase 1. The exact authorization, integrity, streaming, and proof contract is complete and tested with an injected stored-byte reader; production storage configuration remains a later deployment prerequisite.

## User Setup Required

None - no external service configuration was performed in this plan.

## Next Phase Readiness

- Plan 02-16 can consume the scope-bound receipt/proof headers, add ordered browser blob-read/anchor/acknowledgement behavior, and record exactly one canonical `workbook_download_served` event without changing artifact authorization.
- Retry, partial review, fixed failure states, exact historical artifact selection, legacy receipt behavior, and full regressions are green.
- Production use of historical artifact streaming still depends on the separately planned managed object-storage adapter and actual stored workbook bytes; until configured, the route fails safely with `artifact_unavailable`.

## Self-Check: PASSED

- Created summary exists: `.planning/phases/02-identity-organizations-and-report-library/02-09-SUMMARY.md`.
- Task commits exist: `07410d5`, `3ec5ab5`.
- Focused/full Python, Node, compile, catalog, template, and diff-hygiene checks pass.

---
*Phase: 02-identity-organizations-and-report-library*
*Completed: 2026-11-12*
