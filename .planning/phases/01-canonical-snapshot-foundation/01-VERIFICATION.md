---
phase: 01-canonical-snapshot-foundation
verified: 2026-11-11T01:30:39Z
status: gaps_found
score: 100/119 must-haves verified
overrides_applied: 0
re_verification:
  previous_status: gaps_found
  previous_score: 63/85
  gaps_closed:
    - "Required NCAA core headers are unique and current durable job sections carry the bumped parser contract."
    - "ID-less batting/pitching rows require nonempty corroboration before merging."
    - "Failure-evidence retention is database-enforced and invalid configuration fails readiness/worker startup without losing terminal job state."
    - "The purge deletes unlocked expired evidence in bounded SKIP LOCKED batches."
    - "Sequential authoritative source/player mismatches are rejected by PostgreSQL and publication validation."
  gaps_remaining:
    - "Authoritative source/player equivalence is not serialized across concurrent identity mutations."
    - "The documented database-optional native workbook workflow now returns 503 after constructing a workbook."
    - "Unique authenticated users are counted from actor-bearing download events, not only authenticated_use events."
    - "The legacy NCAA result cache is not tied to parser/schema identity."
    - "Workbook events are committed before client receipt and an ambiguous retry receives a new UUID."
    - "Purge success finalization is not monotonic or serialized."
  regressions:
    - "Plans 01-28/01-29 made usage persistence mandatory for the legacy workbook route."
    - "Plans 01-28/01-29 introduced analytics whose aggregate and delivery semantics do not match the accepted contract."
gaps:
  - truth: "Every authoritative source row continues to identify the same canonical Player under concurrent writes and publication."
    status: failed
    reason: "Revision 0010 uses ordinary READ COMMITTED trigger reads without a shared row/advisory lock. A two-connection counterexample committed SourceRecord.source_player_id=42 while the linked Player committed source_player_id=99."
    artifacts:
      - path: "migrations/versions/0010_authoritative_source_player_binding.py"
        issue: "The SourceRecord, Player, and PlayerTeamSeason trigger functions do not share a serialization protocol."
      - path: "roster_scout/snapshots.py"
        issue: "Publication rechecks one snapshot but cannot prevent a later concurrent identity mutation."
    missing:
      - "Acquire one deterministic transaction lock for all three mutation directions and publication."
      - "Add two-connection source-insert/Player-update and source-insert/PlayerTeamSeason-reassignment tests."
  - truth: "Today's documented native workbook workflow still returns the workbook without an undeclared PostgreSQL prerequisite."
    status: failed
    reason: "Every successful scrape now opens usage persistence; without DATABASE_URL the route discards the prepared workbook and returns sanitized 503, while scripts/run_local.sh and the native-runner guide do not configure PostgreSQL."
    artifacts:
      - path: "app.py"
        issue: "Download analytics are mandatory after workbook construction."
      - path: "scripts/run_local.sh"
        issue: "The documented runner has no database preflight or provisioning."
      - path: "DEPLOYMENT.md"
        issue: "Native setup documents only environment, Browserless token, and run_local.sh."
    missing:
      - "Either make legacy-route analytics best-effort with observable loss, or make PostgreSQL an explicit startup prerequisite and provision/check it in the native workflow."
      - "Add an unmocked native-runner route contract test."
  - truth: "Unique authenticated users are distinct canonical accounts that performed an authorized accepted report/job action."
    status: failed
    reason: "query_usage_metrics counts every nonnull actor_account_id regardless of event_type. An actor-bearing workbook_download_served event with no authenticated_use event produced unique_authenticated_users=1."
    artifacts:
      - path: "roster_scout/usage.py"
        issue: "The distinct-actor aggregate lacks event_type == authenticated_use."
      - path: "tests/test_usage_metrics.py"
        issue: "Existing fixtures pair actor-bearing downloads with authenticated_use and miss the counterexample."
    missing:
      - "Filter the unique-user aggregate to authenticated_use."
      - "Test global and organization-scoped windows containing only actor-bearing download events."
  - truth: "Every successful NCAA result cache entry is invalidated when the parser/header/schema contract changes."
    status: failed
    reason: "The legacy workbook result cache still uses cache_version=2 and ncaa:{year}:{season_team_id}; payloads contain no parser or schema identity. A pre-contract cached player was returned without invoking collection."
    artifacts:
      - path: "roster_scraper.py"
        issue: "_read_json_cache validates only the unchanged integer cache version."
    missing:
      - "Persist and compare resolved parser/schema identities and bump the legacy cache format/key."
      - "Add a pre-contract successful-result cache invalidation test."
  - truth: "Successful workbook downloads are counted exactly once according to client-receipt semantics."
    status: failed
    reason: "The server commits the event before returning the response; a lost connection can count zero received workbooks. The browser then clears the UUID in finally, so a retry uses a new key and can count a second event."
    artifacts:
      - path: "app.py"
        issue: "record_workbook_download_served commits before any client acknowledgement."
      - path: "templates/index.html"
        issue: "downloadEventId is regenerated for every submit and cleared after ambiguous network failure."
      - path: "DEPLOYMENT.md"
        issue: "It labels a committed event as a successful download without defining the weaker prepared-response semantic."
    missing:
      - "Choose and document received-download versus prepared-response semantics."
      - "For received-download semantics, preserve the UUID across ambiguous retries and record an idempotent acknowledgement only after resp.blob() succeeds."
      - "Add a lost-response/retry browser-level regression."
  - truth: "A purge success heartbeat advances monotonically and represents a serialized complete eligibility scan."
    status: partial
    reason: "The upsert unconditionally overwrites last_success_at, so an older invocation can commit after a newer one and move time backward. The final count and upsert are not serialized against concurrent eligible-row insertion. Readiness independently recounts expired bytes, so this does not by itself prove a sustained false-ready response, but it violates the Plan 01-27 heartbeat contract and can cause false unavailability."
    artifacts:
      - path: "roster_scout/evidence.py"
        issue: "Finalization has neither a monotonic GREATEST update nor a common advisory/serializable protocol."
    missing:
      - "Make the heartbeat monotonic and serialize/retry final scan finalization."
      - "Add forced out-of-order completion and count-to-upsert insertion tests."
deferred:
  - truth: "Authenticated-use production wiring and actor-account foreign key exist."
    addressed_in: "Phase 2"
    evidence: "ROADMAP Phase 2 success criterion 6 and Plan 02-04 explicitly require membership-authorized accepted report/job events and the account FK."
  - truth: "Fielding facts share the same canonical player identity."
    addressed_in: "Phase 3"
    evidence: "Phase 3 adds fielding to the canonical schema and consistent player identity."
  - truth: "Excel and Power BI read the same exact published snapshot facts."
    addressed_in: "Phases 4 and 5"
    evidence: "Phase 4 delivers exact-snapshot Excel and Phase 5 delivers the corresponding Power BI surface."
---

# Phase 1: Canonical Snapshot Foundation Verification Report

**Phase Goal:** As a Roster Scout operator, I want to publish existing NCAA D1/D2/D3 results through a durable job into an immutable, provenance-rich canonical snapshot, so that every downstream report can rely on one auditable source of truth without breaking today's workbook workflow.
**Verified:** 2026-11-11T01:30:39Z
**Status:** gaps_found
**Re-verification:** Yes — after Plans 01-22 through 01-30.

## User Flow Coverage

| Step | Expected | Evidence | Status |
| --- | --- | --- | --- |
| Accept build | Durable job/idempotency and generation-bound retry | Release gate and PostgreSQL tests pass | ✓ VERIFIED |
| Collect/publish | D1/D2/D3 evidence becomes one immutable auditable snapshot | Representative builds pass, but concurrent identity writes can commit mismatched source/Player IDs | ✗ FAILED |
| Rely on source of truth | Exact source row, fact identity, and canonical Player remain equivalent | Two-connection counterexample committed `42 != 99` | ✗ FAILED |
| Preserve workbook | The documented native runner still downloads the two-sheet workbook | Unmocked no-`DATABASE_URL` route returned HTTP 503 after workbook creation | ✗ FAILED |
| Measure usage | Download and authenticated-user metrics match the documented meanings | Privacy and anonymous totals pass; event-type and receipt/retry semantics fail | ✗ FAILED |
| Outcome | Downstream reports can rely on one auditable truth without breaking today's workflow | Three core outcome conditions remain false | ✗ FAILED |

## Goal Achievement

### Roadmap Success Criteria

| # | Truth | Status | Evidence |
| --- | --- | --- | --- |
| R1 | Representative D1/D2/D3 builds publish canonical immutable facts | ✓ VERIFIED | D1/D2/D3 publication and mutation-regression tests pass sequentially. |
| R2 | Durable state/capacity prevent failed or half-written ready snapshots | ✗ FAILED | Concurrent Player/source mutation can leave a ready snapshot whose provenance no longer names its canonical Player. |
| R3 | Retries are idempotent and caches are schema/version aware | ✗ FAILED | Durable section cache is version-aware; legacy successful-result cache is not. |
| R4 | Numeric, identity, raw, and metric lineage are unambiguous | ✗ FAILED | Numeric/metric paths pass, but concurrent authoritative identity equivalence fails. |
| R5 | Existing selector/scraper/cache/basic workbook behavior remains working | ✗ FAILED | The documented native workbook route now requires undeclared PostgreSQL and returns 503 without it. |

### Plan Must-Have Truths

All 114 PLAN truths were resolved against code and behavior. Plans 01-01 through 01-21 contribute 80 truths; Plans 01-22 through 01-30 add 34.

| Plan truths | Status | Evidence / failed truth IDs |
| --- | --- | --- |
| 01-01.1–.6 | 6/6 | Canonical schema, ownership, provenance homes, and innings representation. |
| 01-02.1–.9 | 9/9 | Durable job/section/idempotency and same-job versioned evidence. |
| 01-03.1–.7 | 4/7 | ✗ .5 concurrent canonical-player trace; ✗ .6/.7 legacy/native workbook compatibility. |
| 01-04.1–.4 | 4/4 | Published child/metric immutability, frozen migration, counts. |
| 01-05.1–.3 | 2/3 | ✗ .3 traceability can be invalidated by concurrent Player mutation. |
| 01-06.1–01-10.2 | 12/12 | Exclusive claims, outbox, recovery, evidence safety, capacity/network binding. |
| 01-11.1–.4 | 2/4 | ✗ .2 native workbook path; ✗ .3 invalid ready identity race. The named owner-plan regressions themselves pass. |
| 01-12.1–01-21.4 | 35/35 | Scheduler, retention, snapshot locks, retry generations, corroboration, bounded purge, readiness. Later batching safely supersedes the former single-transaction purge shape. |
| 01-22.1–.4 | 3/4 | ✗ .2 persisted parser identity does not cover the legacy successful-result cache. |
| 01-23.1–.3 | 3/3 | No-support/one-sided rows unresolved; supported two-way rows merge. |
| 01-24.1–.4 | 1/4 | ✗ .1/.2 concurrent reciprocal invariant; ✗ .3 publication is not serialized with identity mutation; migration lifecycle passes. |
| 01-25.1–.3 | 3/3 | Exact PostgreSQL 28-day constraint and migration lifecycle. |
| 01-26.1–.3 | 3/3 | Readiness/worker validation and terminal failure durability. |
| 01-27.1–.4 | 3/4 | Batching/contention/retry pass; ✗ .3 heartbeat can move backward and finalization is not serialized. |
| 01-28.1–.5 | 4/5 | Privacy, idempotency, event contract, and Phase 2 boundary pass; ✗ .3 aggregate counts actor IDs from wrong event type. |
| 01-29.1–.4 | 2/4 | Server-key dedupe and anonymous behavior pass; ✗ .1/.3 receipt/retry semantics. |
| 01-30.1–.4 | 3/4 | The command runs every named new regression and the migration/runbook contract passes; ✗ .4 native workflow is not green. The manifest still omits the verifier's adversarial counterexamples. |

**Score:** 100/119 merged roadmap and PLAN truths verified.

### Required Artifacts

| Artifact | Expected | Status | Details |
| --- | --- | --- | --- |
| `roster_scraper.py` | Unique schema validation and version-aware cache | ⚠ PARTIAL | Durable job parser contract works; legacy result payload/key omit parser/schema identity. |
| `roster_scout/canonical.py` | Safe ID-less canonical matching | ✓ VERIFIED | No-support and one-sided-support rows remain unresolved; corroborated pairs merge. |
| `0010_authoritative_source_player_binding.py` | Reciprocal source/Player invariant | ✗ HOLLOW UNDER CONCURRENCY | Sequential triggers work, but ordinary reads permit write skew. |
| `0011_failure_evidence_retention_constraint.py` | Database retention invariant | ✓ VERIFIED | Exact positive-to-28-day checks, history validation, reversible lifecycle. |
| `roster_scout/evidence.py` | Bounded purge and durable readiness | ⚠ PARTIAL | SKIP LOCKED forward progress works; heartbeat finalization is non-monotonic/unserialized. |
| `0012_product_usage_events.py` | Minimal privacy-safe usage ledger | ✓ VERIFIED | No substitute identity fields; named type/scope/key constraints. |
| `roster_scout/usage.py` | Exact aggregate semantics | ✗ HOLLOW AGGREGATE | Download dedupe works; distinct actor count is not filtered to authenticated_use. |
| `app.py` + `templates/index.html` | Workbook delivery and exactly-once download event | ✗ PARTIAL | Event precedes receipt, retries get new IDs, and DB failure blocks workbook. |
| `scripts/validate_phase1_release.py` | Complete final gate | ⚠ PARTIAL | Exits 0 over 344 tests but does not exercise the confirmed counterexamples. |

### Key Link Verification

| From | To | Via | Status | Details |
| --- | --- | --- | --- | --- |
| NCAA headers | durable ReportSection cache | parser/header identity equality | ✓ WIRED | Current same-job stale sections recollect. |
| NCAA parser contract | legacy result cache | payload/key parser/schema identity | ✗ NOT WIRED | Only integer cache version 2 is checked. |
| SourceRecord ID | canonical Player ID | reciprocal trigger protocol | ✗ NOT SERIALIZED | Sequential validation only; concurrent write skew commits mismatch. |
| FailureEvidence dates | retention cap | named PostgreSQL check | ✓ WIRED | Direct SQL beyond 672h rejected. |
| Expired evidence | purge progress | bounded SKIP LOCKED batches | ✓ WIRED | Locked row does not undo unlocked deletions. |
| Complete scan | purge heartbeat | count then upsert | ⚠ PARTIAL | No monotonic or common transaction-serialization guard. |
| Browser event UUID | download total | unique event key | ✓ WIRED | Repeated same key deduplicates. |
| Authorized accepted action | unique authenticated users | authenticated_use-only distinct actor query | ✗ NOT WIRED | Aggregate includes every actor-bearing event. |
| Prepared response | received workbook metric | client acknowledgement/retry identity | ✗ NOT WIRED | Commit happens before receipt; retry ID is replaced. |
| Native runner | workbook route | declared runtime dependencies | ✗ NOT WIRED | PostgreSQL is required in code but absent from native setup. |

### Data-Flow Trace (Level 4)

| Artifact / flow | Data source | Produces real data | Status |
| --- | --- | --- | --- |
| Durable job → snapshot | PostgreSQL job/sections/source/facts | Yes | ⚠ FLOWING, concurrent identity mutation unsafe |
| Legacy cache → workbook | JSON successful-result cache | Yes | ⚠ FLOWING, stale parser contract accepted |
| Usage event → download total | ProductUsageEvent filtered by workbook event type | Yes | ✓ FLOWING, per-key idempotent |
| Usage event → unique users | ProductUsageEvent distinct actor | Yes | ✗ WRONG SEMANTIC FILTER |
| Browser response → successful-download event | Server commits before response transfer | Event yes; receipt unknown | ✗ DISCONNECTED FROM RECEIPT |
| Purge scan → readiness | PostgreSQL state plus independent expired-byte count | Yes | ⚠ FLOWING, timestamp can regress; outstanding-byte recount remains fail-closed |

### Review-Finding Adjudication

| Finding | Verdict | Evidence |
| --- | --- | --- |
| CR-01 concurrent authoritative identity write skew | **CONFIRMED BLOCKER** | Two real PostgreSQL transactions committed SourceRecord `42` and linked Player `99`; trigger code has no shared lock. |
| CR-02 native workflow now needs PostgreSQL | **CONFIRMED BLOCKER** | Unmocked `/scrape` without `DATABASE_URL` returned 503; run_local.sh has no DB setup/preflight. |
| CR-03 wrong unique-user event type | **CONFIRMED BLOCKER** | One actor-bearing download and no authenticated_use returned downloads=1, unique users=1. |
| CR-04 legacy cache survives parser bump | **CONFIRMED BLOCKER** | Pre-contract cache_version=2 payload returned its player with collector call count 0. |
| CR-05 counted before receipt/new retry UUID | **CONFIRMED BLOCKER** | Code ordering commits at app.py 341–348 before browser `resp.blob()`; finally clears the ID and the next submit generates a new UUID. |
| WR-01 purge finalization race | **CONFIRMED WARNING / PLAN FAILURE** | Older heartbeat overwrote newer timestamp in PostgreSQL. Count-to-upsert is unguarded. Independent readiness recount prevents this fact alone from establishing sustained false-ready state, but false stale/unavailability and stale heartbeat certification remain. |

### Analytics Contract

| Contract | Status | Evidence |
| --- | --- | --- |
| Successful downloads counted exactly once under documented product meaning | ✗ FAILED | Exactly once per committed key works; actual client receipt and ambiguous retry do not. |
| Anonymous successful downloads count in totals, never authenticated uniques | ✓ VERIFIED for Phase 1 producer | App writes null actor/org and download total includes it. |
| Unique users are canonical accounts performing authorized accepted report/job action | ✗ FAILED in aggregate | Producer contract uses authenticated_use, but query counts actor-bearing downloads too. |
| Phase 2 wiring is explicitly deferred and guaranteed | ✓ VERIFIED / DEFERRED | ROADMAP criterion 2.6 and Plan 02-04 specify membership authorization, accepted ReportJob UUID, canonical IDs, and FK. |
| No IP/cookie/email/user-agent/fingerprint substitute identity is collected | ✓ VERIFIED | Usage table/service have no such columns; migration tests and runbook forbid substitutes. Request rate limiting uses IP separately and does not store it as usage identity. |

### Behavioral Spot-Checks

| Behavior | Command / probe | Result | Status |
| --- | --- | --- | --- |
| Full release validator | `DATABASE_URL=...roster_scout_test .venv/bin/python scripts/validate_phase1_release.py` | 344 tests; migrations through 0012; drift; 942 catalog; Compose; diff; PASS | ✓ PASS |
| Concurrent source/Player mutation | Two READ COMMITTED connections: insert source 42, update Player 99, commit both | `mismatch_committed: True` | ✗ FAIL |
| Native workbook without DB | Unset DATABASE_URL; patched local scraper; POST `/scrape` | HTTP 503 JSON after valid workbook construction path | ✗ FAIL |
| Unique users, wrong event type | Insert only actor-bearing workbook_download_served; query window | downloads=1, unique_authenticated_users=1 | ✗ FAIL |
| Legacy cache parser bump | Write current-format pre-contract payload; call team scraper | cached player returned; collector_calls=0 | ✗ FAIL |
| Purge timestamp monotonicity | Upsert newer heartbeat then older heartbeat | stored older time; regressed=True | ✗ FAIL |
| Download lost-response boundary | Static order trace: event commit → return response → browser blob → finally clear ID | No receipt acknowledgement; retry gets new UUID | ✗ FAIL |

### Probe Execution

No PLAN-declared `probe-*.sh` files exist. The release validator and focused disposable-PostgreSQL counterexamples above were executed directly by this verifier.

### Requirements Coverage

| Requirement | Status | Evidence |
| --- | --- | --- |
| SNAP-01 | ✓ SATISFIED | Report stores organization, opponent, division, and season. |
| SNAP-02 | ✓ SATISFIED | Durable job states, outbox/generation retry, worker, and capacity gate. |
| SNAP-03 | ✗ BLOCKED | Concurrent global Player mutation can invalidate authoritative provenance behind a published snapshot. |
| SNAP-04 | ✓ SATISFIED | Per-section state/count/source time/sanitized failure category. |
| DATA-01 | ✓ SATISFIED | Authoritative, corroborated fallback, and unresolved states behave safely sequentially. |
| DATA-02 | ✓ SATISFIED | Team/season/category/source URL/time provenance persists. |
| DATA-03 | ✗ BLOCKED | Concurrent authoritative identity mismatch remains; fielding is explicitly Phase 3. |
| DATA-04 | ✓ SATISFIED | Transfer-aware player/team-season schema. |
| DATA-05 | ✓ SATISFIED | Zero and unavailable remain distinct. |
| DATA-06 | ✓ SATISFIED | Innings stored as outs with raw value. |
| DATA-07 | ✗ BLOCKED | Raw row exists, but concurrent source/Player mismatch can make its canonical audit association false. |
| DATA-08 | ✓ SATISFIED | Versioned immutable metric definition/value linkage. |
| DATA-09 | ✓ SATISFIED | Composite tenant ownership constraints. |
| DATA-10 | ↪ DEFERRED | Phase 4/5 exact-snapshot Excel and Power BI consumers. |
| OPS-01 | ✓ SATISFIED | Bounded retry and shared Browserless capacity. |
| OPS-02 | ✗ BLOCKED | Legacy successful-result cache lacks parser/schema identity. |
| OPS-04 | ✓ SATISFIED | 0012 fresh install, full downgrade/re-upgrade, and drift pass. |
| OPS-05 | ✗ BLOCKED | Mocked regressions pass, but the documented native basic-workbook workflow returns 503 without PostgreSQL. |

All 18 Phase 1 requirement IDs appear in Phase plans; none are orphaned.

### Anti-Patterns Found

No unreferenced `TBD`, `FIXME`, or `XXX` markers were found in phase-modified runtime files. No placeholder implementation was used as evidence. `git diff --check` passed.

### Human Verification Required

None. The blocking failures are deterministic code/data-flow defects or were reproduced against the disposable PostgreSQL database; visual judgment is not needed to decide this gate.

### Deferred Items

| Item | Addressed In | Evidence |
| --- | --- | --- |
| Authenticated producer wiring/account FK | Phase 2 | ROADMAP criterion 6 and Plan 02-04. |
| Fielding canonical identity | Phase 3 | Detailed NCAA Intelligence scope. |
| Exact-snapshot Excel/Power BI consumption | Phases 4–5 | Detailed Excel and embedded dashboard goals. |

### Gaps Summary

Five blockers prevent Phase 1's user-story outcome: the canonical authoritative identity can be corrupted by concurrent writes; the existing native workbook flow is broken without an undeclared database; unique users use the wrong event-type population; legacy result cache entries survive parser-contract changes; and “successful download” events are disconnected from client receipt and ambiguous retry identity. Purge finalization adds one plan-level concurrency gap: the timestamp can regress and the final scan is not serialized, although the separate expired-byte readiness query remains fail-closed.

The 344-test release gate is valuable evidence for the happy path, migrations, and earlier gap closures, but its green result does not cover these counterexamples.

---

_Verified: 2026-11-11T01:30:39Z_
_Verifier: the agent (gsd-verifier)_
