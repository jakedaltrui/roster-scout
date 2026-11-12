---
status: resolved
trigger: "Fix the 11 full-suite time-sensitive session test failures surfaced after Plan 02-08."
created: 2026-11-12T16:33:00Z
updated: 2026-11-12T16:56:00Z
---

## Current Focus

reasoning_checkpoint:
  hypothesis: "The two shared PostgreSQL test bases freeze `self.now` at 2026-11-11T19:00Z; browser-route helpers create sessions at that instant, but Flask authenticates against real UTC, so exactly those sessions expire after 12 hours and the routes redirect before their intended assertions."
  confirming_evidence:
    - "A clean focused run reproduces exactly 11 failures: every failure is an authenticated browser route returning 302 instead of its post-auth status."
    - "All direct service/lifecycle tests remain green because they inject explicit relative `now` values; production `SESSION_IDLE_LIFETIME` remains correct."
    - "The only fixed 2026-11-11 session clock definitions are the two shared bases in `tests/test_identity.py` and `tests/test_organizations.py`."
  falsification_test: "If replacing only those shared base clocks with current UTC does not turn all 57 focused tests green, or changes an exact session-boundary assertion, the hypothesis is wrong."
  fix_rationale: "A current per-test reference clock keeps route credentials valid against wall time while every exact expiry, invitation, and audit assertion still uses deterministic deltas from the same captured instant."
  blind_spots: "The full 513+ test suite may reveal another fixed-clock fixture or migration-order interaction not present in the focused modules."
next_action: Resolved by deterministic full-suite verification; continue Phase 2.

## Symptoms

expected: Identity, organization, and report route tests authenticate deterministically while exact session-expiry boundary tests continue to validate the 12-hour idle policy.
actual: Eleven route tests redirect to login on 2026-11-12 because their shared sessions were created at 2026-11-11T19:00:00Z and request authentication uses the real current time.
errors: Full discovery ran 513 tests with 11 failures; affected authenticated routes received login redirects after the fixed fixture timestamp exceeded the idle lifetime.
reproduction: Run the PostgreSQL-backed full unittest discovery on 2026-11-12, or run an affected route fixture while request-time resolution uses the real clock.
started: Became date-dependent after the wall clock moved more than 12 hours beyond the fixed 2026-11-11T19:00:00Z route fixture time.

## Eliminated


## Evidence

- timestamp: 2026-11-12T16:36:00Z
  checked: `roster_scout/sessions.py`, `roster_scout/authorization.py`, and `app.py` request gate
  found: Production session loading correctly defaults to wall-clock UTC when no clock is injected; the Flask gate calls it without `now`, while route fixtures create credentials at fixed `self.now = 2026-11-11T19:00:00Z`.
  implication: A route fixture using that credential becomes invalid as soon as the real clock crosses its 12-hour idle expiry; production logic is not the defect.
- timestamp: 2026-11-12T16:36:30Z
  checked: direct session lifecycle tests in `tests/test_identity.py`
  found: Exact idle/absolute boundary tests explicitly pass `now=self.now + ...` to service functions.
  implication: Route fixture time can be made relative to wall clock without weakening the separate exact-boundary coverage.
- timestamp: 2026-11-12T16:39:00Z
  checked: Focused `tests.test_identity tests.test_organizations` reproduction
  found: Exactly 11 failures reproduce; all are authenticated browser routes returning 302, while 46 direct identity, invitation, role, schema, and exact session-boundary tests pass.
  implication: The failure set matches the fixed-fixture/wall-clock hypothesis and excludes production session expiry logic, database schema, and authorization rules.
- timestamp: 2026-11-12T16:44:00Z
  checked: Exact 57-test identity/organization reproduction after the two-line fixture-clock change
  found: All 57 tests pass; the same 11 browser-route tests that previously returned 302 now reach their intended assertions, and every explicit session boundary test remains green.
  implication: The hypothesis is confirmed and the minimal fixture-only fix corrects the original issue without changing production session behavior.
- timestamp: 2026-11-12T16:46:00Z
  checked: Adjacent `tests.test_report_library` suite
  found: All 22 report-library, refresh, history, status, and report-creation tests pass.
  implication: The test-clock change does not regress the adjacent customer workflow that depends on the same Flask authorization gate.
- timestamp: 2026-11-12T16:50:00Z
  checked: Complete PostgreSQL-backed unittest discovery
  found: All 513 tests pass in one process, including the original 11 failures and migration-heavy lifecycle coverage.
  implication: The original full-suite failure is resolved and no additional fixed-clock or ordering regression remains in Python coverage.
- timestamp: 2026-11-12T16:54:00Z
  checked: Node browser suite, Python compile checks, required templates, NCAA catalog validation, and `git diff --check`
  found: All 23 Node tests pass; every named Python module compiles; both templates exist; all 942 catalog rows are selectable with only the expected 25 stable-ID warning; diff hygiene passes.
  implication: The fixture-only change is clean across the repository handoff gates and does not affect browser, scraper, catalog, or template behavior.
- timestamp: 2026-11-12T16:56:00Z
  checked: Plan 02-08 summary and deferred-item record
  found: The summary now reports the clean 513-test result and the focused regression evidence, and the single-item deferred file was removed because its only debt is resolved.
  implication: Phase records no longer preserve a false unresolved failure or stale readiness caveat.

## Resolution

root_cause: The identity and organization PostgreSQL base fixtures used a calendar-fixed `self.now` for both deterministic domain assertions and browser session creation, but Flask route authentication correctly resolves the real UTC clock. Once wall time passed the 12-hour idle expiry, every route credential from those fixtures became stale.
fix: Capture a fresh UTC-second reference in each shared PostgreSQL test setup instead of freezing all identity and organization fixtures to a calendar timestamp; all direct boundary tests continue to assert explicit deltas from that captured instant.
verification: The original 57-test identity/organization reproduction passes, including exact expiry boundaries; the adjacent 22-test report-library suite passes; clean PostgreSQL discovery passes all 513 tests; all 23 Node tests, compile checks, template checks, catalog validation, and diff hygiene pass.
files_changed: [tests/test_identity.py, tests/test_organizations.py, .planning/phases/02-identity-organizations-and-report-library/02-08-SUMMARY.md, .planning/phases/02-identity-organizations-and-report-library/deferred-items.md]
