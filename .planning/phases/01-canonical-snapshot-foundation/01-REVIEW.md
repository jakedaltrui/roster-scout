---
phase: 01-canonical-snapshot-foundation
reviewed: 2026-11-11T01:20:24Z
depth: standard
files_reviewed: 27
files_reviewed_list:
  - app.py
  - roster_scraper.py
  - roster_scout/canonical.py
  - roster_scout/evidence.py
  - roster_scout/jobs.py
  - roster_scout/models.py
  - roster_scout/snapshots.py
  - roster_scout/usage.py
  - migrations/versions/0010_authoritative_source_player_binding.py
  - migrations/versions/0011_failure_evidence_retention_constraint.py
  - migrations/versions/0012_product_usage_events.py
  - scripts/report_usage.py
  - scripts/run_purge.py
  - scripts/run_worker.py
  - scripts/validate_phase1_release.py
  - templates/index.html
  - tests/test_app_routes.py
  - tests/test_canonical_data.py
  - tests/test_canonical_publish.py
  - tests/test_deployment_config.py
  - tests/test_evidence.py
  - tests/test_ncaa_workflow.py
  - tests/test_report_jobs.py
  - tests/test_snapshot_persistence.py
  - tests/test_usage_metrics.py
  - DEPLOYMENT.md
  - .env.example
findings:
  critical: 5
  warning: 1
  info: 0
  total: 6
status: issues_found
---

# Phase 1: Code Review Report

**Reviewed:** 2026-11-11T01:20:24Z
**Depth:** standard
**Files Reviewed:** 27
**Status:** issues_found

## Summary

The round-three gap work closes the previously demonstrated parser-cardinality, ID-less corroboration, retention-constraint, bounded-purge, and sequential source/player mismatch cases, but it is not safe to ship yet. The authoritative identity triggers can be defeated by concurrent transactions, the documented native workbook workflow now fails without PostgreSQL, and the new usage layer misclassifies actor-bearing download events as authenticated use. The legacy NCAA result cache also remains independent of the parser contract, while “successful” download events are committed before any workbook bytes reach the browser. The purge heartbeat has a smaller concurrency defect that can regress readiness timestamps.

The reported 344-test release pass does not exercise the concrete counterexamples below: the identity tests mutate rows sequentially, the route suite mocks usage persistence for the legacy path, the usage aggregate tests always pair actor-bearing downloads with authenticated-use events, and the browser contract test inspects source ordering rather than a lost-response retry. No source files were modified by this review.

## Narrative Findings (AI reviewer)

## Critical Issues

### CR-01 [BLOCKER]: The authoritative source/player invariant is vulnerable to concurrent write skew

**File:** `migrations/versions/0010_authoritative_source_player_binding.py:49-79,101-123,141-163`

**Issue:** All three trigger functions validate related rows with ordinary snapshot reads. None locks the `players` or `player_team_seasons` row, and none uses a common advisory-lock protocol. A source-record transaction can read a matching Player at lines 60-66 while a concurrent transaction reads the pre-insert source set at lines 104-117 and changes that Player's authoritative ID. Both `BEFORE` triggers can pass under PostgreSQL `READ COMMITTED`, then both transactions commit a source ID that no longer matches its canonical Player. The same race exists between source insertion and `PlayerTeamSeason.player_id` reassignment at lines 144-157. The publication check in `roster_scout/snapshots.py:106-136` does not repair this: it is another snapshot read and cannot serialize a later concurrent mutation.

**Fix:** Make every source insert/update, Player authoritative-ID update, and PlayerTeamSeason reassignment acquire the same deterministic transaction lock before validation. For example, lock the relevant Player and PlayerTeamSeason rows with `SELECT ... FOR UPDATE` in a consistent order, or use a transaction advisory lock keyed by the canonical identity. Add two-connection tests that pause a source insert after its trigger read while changing the Player or reassigning the PlayerTeamSeason, then assert that one transaction fails and the committed database state still satisfies the invariant.

### CR-02 [BLOCKER]: Adding download metrics breaks the documented native workbook workflow

**Files:** `app.py:151-165,331-348`; `DEPLOYMENT.md:5-15`

**Issue:** Every otherwise successful `/scrape` request now calls `_usage_session_factory()`, which requires `DATABASE_URL`, and converts any persistence failure into HTTP 503. The documented native runner still tells an operator to run only setup, Browserless-token, and `scripts/run_local.sh`; that runner does not configure or start PostgreSQL. Therefore the supported native flow can scrape and construct the Excel file but will always discard it and return 503 when no database URL is present. The regression is hidden because `tests/test_app_routes.py:359-370` patches `_record_workbook_download` for the legacy route suite, while only the database-specific metric tests exercise the real producer.

**Fix:** Either keep analytics best-effort for the database-optional legacy route (with explicit health/telemetry for dropped events), or formally make PostgreSQL a prerequisite for all workbook delivery and update `scripts/run_local.sh`, setup, and the deployment guide to validate/provision it. Add an unmocked native-runner contract test with the documented environment that proves `/scrape` still returns a workbook, or fails at startup with a clear prerequisite error rather than after doing the scrape.

### CR-03 [BLOCKER]: The “unique authenticated users” query counts actors from the wrong event type

**Files:** `roster_scout/usage.py:155-176,195-205`; `migrations/versions/0012_product_usage_events.py:31-43`

**Issue:** The producer contract says an authenticated use is the `authenticated_use` event emitted only after authorization and durable job acceptance. The aggregate, however, counts every distinct non-null `actor_account_id` without filtering by event type. Revision 0012 deliberately permits `workbook_download_served` to carry an actor/organization pair, and `record_workbook_download_served()` exposes those arguments. A single actor-bearing download event with no accepted authenticated-use event therefore reports one “unique authenticated user.” Existing tests miss this because every actor-bearing download is paired with an authenticated-use event for the same actor.

**Fix:** Apply `ProductUsageEvent.event_type == AUTHENTICATED_USE` to the distinct-actor aggregate as well as the non-null check. Add global and organization-scoped tests containing only actor-bearing download events and assert that downloads increase while unique authenticated users remain zero.

### CR-04 [BLOCKER]: The legacy NCAA result cache is not invalidated by the parser-contract bump

**File:** `roster_scraper.py:77-90,1092-1112,2413-2426`

**Issue:** The durable section cache now uses `parser-2+ncaa-core-v2`, but the legacy workbook cache key remains only `ncaa:{year}:{season_team_id}` and cached payloads contain only the unchanged integer `_NCAA_CACHE_VERSION = 2`. A result written by the pre-fix parser therefore remains valid after deployment and is returned at lines 2416-2426 without running the new duplicate-header contract. The configurable `NCAA_PARSER_VERSION` does not participate in this cache at all. This preserves exactly the stale parser evidence that the version bump was intended to reject, for the default result-cache TTL after rollout.

**Fix:** Persist the resolved parser and schema identities in every legacy result payload and reject reads unless both equal the current build; preferably include them in the cache key as well. Bump the legacy cache format version for this release. Add a test that writes a cache entry with the former parser identity, advances the parser contract, and proves the request recollects instead of returning those players.

### CR-05 [BLOCKER]: A download is counted before the browser receives the workbook, and retries are not idempotent

**Files:** `app.py:331-348`; `templates/index.html:1299-1357`

**Issue:** The server commits `workbook_download_served` before returning the `send_file` response, not after transmission or client receipt. If the connection or proxy fails after the event commit but before `resp.blob()` completes, no workbook is delivered, yet the event is permanently counted. The browser's `finally` block then clears `downloadEventId`; the user's retry generates a new UUID and records a second event. Thus one eventual download can count twice, and a download that never reaches the browser can count once. Server-side duplicate-key tests do not cover this lost-response boundary, and the documented definition of a “successful download” becomes a prepared response rather than a received workbook.

**Fix:** Separate workbook generation from delivery acknowledgement. Preserve the same request UUID across ambiguous network retries, and record/confirm the event from a bounded acknowledgement endpoint only after the client has successfully read the workbook blob (or rename the metric explicitly to “workbook responses prepared” if that weaker semantic is acceptable). Protect the acknowledgement against cross-origin abuse and keep its insert idempotent. Add a browser-level test that simulates a connection loss after server commit, retries, and verifies the chosen success definition produces exactly one event.

## Warnings

### WR-01 [WARNING]: Concurrent or out-of-order purges can move the success heartbeat backwards

**File:** `roster_scout/evidence.py:236-251,287-308`

**Issue:** `_upsert_evidence_purge_state()` unconditionally overwrites `last_success_at` with the caller's `now`. Two overlapping scheduler/manual purge runs can complete out of order, allowing an older scan to commit after a newer one and replace the newer timestamp. Readiness can then become stale early and take the app out of service even though a more recent complete purge succeeded. The same lack of serialization leaves a check-to-upsert window where a concurrent eligible-row insertion can make the recorded “complete scan” stale at commit time.

**Fix:** Serialize complete-scan finalization with a singleton advisory lock, or make the upsert monotonic with `GREATEST(existing.last_success_at, excluded.last_success_at)` and use `SERIALIZABLE`/retry (or a shared writer protocol) for the final eligibility check. Add overlapping-purge tests that force the older invocation to commit last and a test that inserts an eligible evidence row between the final count and heartbeat write.

---

_Reviewed: 2026-11-11T01:20:24Z_
_Reviewer: the agent (gsd-code-reviewer)_
_Depth: standard_
