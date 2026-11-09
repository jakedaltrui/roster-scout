---
phase: 01-canonical-snapshot-foundation
verified: 2026-11-09T22:27:18Z
status: gaps_found
score: 37/57 must-haves verified
overrides_applied: 0
re_verification:
  previous_status: gaps_found
  previous_score: 0/1
  gaps_closed:
    - "The Phase 1 MVP goal is now a valid user story and passes the centralized validator."
  gaps_remaining:
    - "Published snapshot immutability is not serialized against concurrent child or metric-definition mutation."
    - "Retry dispatch has two authorities and no attempt-generation token."
    - "Failed-evidence physical deletion is not operationally guaranteed within 720 hours."
    - "A fact's evidence row is not bound to the same player identity as the fact."
    - "NCAA tables are not validated against section-specific statistic headers."
    - "Unsafe ID-less identity cases are stored as fallback rather than unresolved."
  regressions: []
gaps:
  - truth: "Published snapshots are immutable under concurrent PostgreSQL transactions."
    status: failed
    reason: "Publication locks only the report_snapshots row, while child and metric-definition triggers read status without acquiring a conflicting lock. A mutation can observe draft, pass its trigger, and commit after publication validation."
    artifacts:
      - path: "migrations/versions/0003_snapshot_integrity_hardening.py"
        issue: "Trigger functions use ordinary SELECT/EXISTS checks and no shared parent/advisory lock protocol."
      - path: "roster_scout/snapshots.py"
        issue: "publish_snapshot() locks only the snapshot row; it does not serialize all child or referenced metric-definition mutations."
    missing:
      - "Use one transaction-scoped lock protocol in publication and every mutation that can affect a snapshot."
      - "Add two-connection barrier tests for child and metric-definition mutations racing publication in both interleavings."
  - truth: "Retries are bounded and stale or duplicate deliveries cannot execute the same attempt generation."
    status: failed
    reason: "A retryable failure creates a database outbox intent, then the Celery task also calls self.retry(). Both payloads carry only job_id, so sequential duplicates and stale deliveries are indistinguishable from the next legitimate attempt."
    artifacts:
      - path: "roster_scout/jobs.py"
        issue: "_record_failure() calls ensure_report_dispatch() for retryable failures."
      - path: "roster_scout/tasks.py"
        issue: "build_report_job() separately calls Celery self.retry() and accepts no dispatch/attempt generation."
    missing:
      - "Choose one retry authority and persist bounded backoff in that authority."
      - "Carry and compare-and-set a dispatch/attempt generation so stale deliveries exit without incrementing attempt_count."
      - "Test sequential duplicates, outbox-versus-Celery retry races, and stale generations."
  - truth: "Failed evidence bytes are physically absent no later than 720 hours after collection and purge health gates production readiness."
    status: failed
    reason: "Bytes expire at 696 hours while the maximum purge interval is 24 hours, leaving no outage margin. Purge shares one synchronous scheduler loop with broker/database reconciliation, and health is process-local, logged only, and not wired to deployment readiness."
    artifacts:
      - path: "roster_scout/evidence.py"
        issue: "Eligibility is fixed at 696 hours, relying entirely on the next scheduler cycle."
      - path: "scripts/run_scheduler.py"
        issue: "Purge is not independently supervised; EvidencePurgeHealth is process-local and non-durable."
      - path: "fly.toml"
        issue: "No scheduler/purge readiness check or persisted freshness gate exists."
    missing:
      - "Run purge independently with strict timeouts and meaningful safety margin before 720 hours."
      - "Persist last-success state and expose it to deployment readiness/alerting."
      - "Test purge progress while reconciliation or broker publication is blocked."
  - truth: "Every published fact is bound to evidence for the same canonical player."
    status: failed
    reason: "Publication verifies snapshot ownership and counts but never compares SourceRecord player identity with PlayerTeamSeason -> Player. Swapping two facts' player_team_season_id values preserves all current checks and can publish player A's source statistics as player B's."
    artifacts:
      - path: "roster_scout/models.py"
        issue: "Facts have independent source_record_id and player_team_season_id foreign keys with no matching identity key."
      - path: "roster_scout/snapshots.py"
        issue: "Publication joins ownership only; it does not validate source_player_id or fallback identity equivalence."
      - path: "migrations/versions/0004_publication_traceability.py"
        issue: "Migration makes both links non-null but does not bind them to each other."
    missing:
      - "Persist a durable source-record identity key/link and enforce a matching source/identity pair."
      - "Add a negative publication test that swaps two facts' identity IDs."
  - truth: "Malformed or renamed NCAA batting/pitching schemas cannot become complete or verified-empty canonical sections."
    status: failed
    reason: "The parser requires only one Player column and equal row widths. It accepts Player plus unrelated headers, and it treats a Player-only empty table as verified_empty without knowing whether the section is batting or pitching."
    artifacts:
      - path: "roster_scraper.py"
        issue: "_parse_ncaa_stat_table_result() has no section parameter or minimum versioned batting/pitching header signature."
      - path: "tests/test_ncaa_workflow.py"
        issue: "Corruption tests cover Player renaming and row shape, but not missing/renamed core stat headers by section."
    missing:
      - "Make parsing section-aware and require supported core header signatures before accepting rows or verified_empty."
      - "Test Player-only, unrelated, and renamed core headers for both batting and pitching."
  - truth: "NCAA identities use unresolved status when neither authoritative nor safe fallback identity is available."
    status: failed
    reason: "canonicalize_ncaa_collection() assigns every ID-less group identity_status='fallback'. The ambiguous same-name test proves separation but never assigns unresolved, despite DATA-01 requiring that state."
    artifacts:
      - path: "roster_scout/canonical.py"
        issue: "Identity status is a two-way authoritative/fallback branch; unresolved is unreachable."
      - path: "tests/test_canonical_data.py"
        issue: "Ambiguous ID-less players are asserted only to remain separate, not to be unresolved."
    missing:
      - "Define the safe-fallback threshold and emit unresolved for unsafe/ambiguous evidence."
      - "Add persistence and publication tests for unresolved identities."
deferred:
  - truth: "Snapshot creator attribution is populated from an authenticated operator."
    addressed_in: "Phase 2"
    evidence: "Phase 2 adds passwordless organization identity and authenticated report workflows; Phase 1 has a nullable created_by field but no authenticated principal."
  - truth: "Fielding facts share the same canonical player identity as batting and pitching facts."
    addressed_in: "Phase 3"
    evidence: "Phase 3 success criterion 1 explicitly adds one consistent batting, pitching, fielding, and derived-metric schema."
  - truth: "Excel and Power BI consumers read the same published canonical snapshot facts."
    addressed_in: "Phases 4 and 5"
    evidence: "Phase 4 delivers the detailed workbook from the exact snapshot; Phase 5 delivers the Power BI report from that same snapshot."
---

# Phase 1: Canonical Snapshot Foundation Verification Report

**Phase Goal:** As a Roster Scout operator, I want to publish existing NCAA D1/D2/D3 results through a durable job into an immutable, provenance-rich canonical snapshot, so that every downstream report can rely on one auditable source of truth without breaking today's workbook workflow.
**Verified:** 2026-11-09T22:27:18Z
**Status:** gaps_found
**Re-verification:** Yes — the previous MVP goal-format gap is closed; final implementation was verified after all 13 plans.

## User Flow Coverage

| Step | Expected | Codebase evidence | Status |
| --- | --- | --- | --- |
| Accept a durable D1/D2/D3 build | One accepted job and transactional outbox intent survive broker loss | `accept_report_job()` and `ReportJobDispatch` are wired in `roster_scout/jobs.py:317-343` and `roster_scout/repositories.py:58-80` | ✓ VERIFIED |
| Collect and publish | Structured NCAA batting/pitching evidence becomes one ready snapshot only after validation | `run_report_job()` persists sections, facts, then calls `publish_snapshot()` at `roster_scout/jobs.py:966-1000` | ✗ FAILED — publication can race mutations and accepts stat-less schemas |
| Rely on one auditable source of truth | Each fact is immutable and identifies the player represented by its exact source row | Ownership/count checks exist at `roster_scout/snapshots.py:33-108` | ✗ FAILED — source evidence is not bound to the fact's player identity |
| Preserve today's workbook | Existing selector, D1/D2/D3 scraper/cache behavior, and two-sheet workbook remain operational | Full available suite passed 89 tests; catalog validation passed 942 rows; workbook route tests passed | ✓ VERIFIED |
| Outcome | Downstream reports can rely on one immutable, provenance-rich truth without workbook regression | Workbook compatibility is proven, but the canonical truth is not immutable or identity-safe | ✗ FAILED |

## Goal Achievement

### Roadmap Success Criteria

| # | Truth | Status | Evidence |
| --- | --- | --- | --- |
| R1 | Representative D1, D2, and D3 builds publish immutable canonical player/team/season/source/section facts | ✗ FAILED | Fixture flow and persistence code exist, but concurrent child/metric mutation can commit after publication validation. |
| R2 | Jobs expose durable state, respect browser capacity, and cannot publish failed or half-written snapshots | ✗ FAILED | Durable states and shared capacity exist; malformed stat schemas and concurrent mutations can still publish invalid data. |
| R3 | Idempotent retries do not duplicate jobs/snapshots and cached sections remain schema/version aware | ✓ VERIFIED | Unique job/snapshot relationships and exact-job parser/schema cache matching exist. Retry generation safety is a separate failed plan/OPS contract below. |
| R4 | Innings, missing values, two-way players, transfers, raw values, and metric lineage are unambiguous | ✗ FAILED | Outs/missing/raw/lineage work, but evidence can be attached to the wrong player and unsafe ID-less cases never become unresolved. |
| R5 | Existing selector, scraper, cache, and basic workbook regressions pass | ✓ VERIFIED | 89 available tests passed and the 942-row catalog validator passed. |

### Plan Must-Have Truths

Every PLAN frontmatter truth is resolved below. Compact labels preserve the plan contract while avoiding executor-summary claims as evidence.

| Plan.truth | Must-have | Status | Evidence |
| --- | --- | --- | --- |
| 01-01.1 | Required facts visible only through one atomic draft-to-published transition | ✗ FAILED | Publication/mutation lock race. |
| 01-01.2 | Snapshot readiness independent from artifact readiness | ✓ VERIFIED | Separate snapshot and artifact statuses/models. |
| 01-01.3 | Schema represents authoritative, fallback, unresolved, conflicting, transfer-aware identity | ✓ VERIFIED | Enum/schema supports all states and per-snapshot player-team-season records; runtime unresolved behavior fails separately. |
| 01-01.4 | Exact rows, provenance, retention metadata, sanitized failures have durable homes | ✓ VERIFIED | SourceRecord, FailureEvidence, job/section models and migrations. |
| 01-01.5 | Cross-organization canonical child links are rejected | ✓ VERIFIED | Composite organization/snapshot foreign keys in models and 0001. |
| 01-01.6 | Innings stored as nullable nonnegative outs with raw value retained | ✓ VERIFIED | `innings_to_outs`, PitchingFact constraint, SourceRecord raw JSON. |
| 01-02.1 | Publish only after batting/pitching success or verified empty | ✗ FAILED | Section-unaware parser accepts invalid core schemas as complete/empty. |
| 01-02.2 | Section state distinguishes core failure from supplemental partial | ✓ VERIFIED | critical/status/verified_empty/failure_category fields and publication logic. |
| 01-02.3 | Canonical publication is atomic and independent of artifact readiness | ✗ FAILED | Transaction path exists but concurrent mutation escapes its validation boundary. |
| 01-02.4 | Saved snapshot reads avoid NCAA; explicit successor build is live | ✓ VERIFIED | Published-only repository reads and predecessor/new-job flow. |
| 01-02.5 | Same-build retries reuse only version-compatible accepted-job evidence | ✓ VERIFIED | `_matching_cached_sections()` checks job, source IDs, parser and schema versions. |
| 01-02.6 | Repeated refresh acceptance returns active job without another snapshot | ✓ VERIFIED | Organization/idempotency uniqueness plus create-or-get locking. |
| 01-02.7 | Reused values retain original collection time | ✓ VERIFIED | Section/source reconstruction preserves collected_at; publication time is separate. |
| 01-02.8 | Failed/blocked HTML expires while sanitized metadata remains | ✗ FAILED | Logical expiry exists, but physical deletion by the promised deadline is not enforceable. |
| 01-02.9 | At-least-once delivery cannot create more than one job/snapshot per key | ✓ VERIFIED | Unique job key, one result_snapshot_id, terminal replay behavior. |
| 01-03.1 | NCAA player ID and player URL survive the full canonical path | ✓ VERIFIED | Source rows preserve ID and URL; unit coverage passes. |
| 01-03.2 | Safe fallback matches merge; unsafe ambiguity remains separate and unresolved | ✗ FAILED | Ambiguous rows are separate but all receive `fallback`, never `unresolved`. |
| 01-03.3 | ID-less rows do not auto-link across teams/seasons as transfers | ✓ VERIFIED | Fallback key includes season_team_id and row evidence. |
| 01-03.4 | Conflicting names under one NCAA ID fail with evidence preserved | ✓ VERIFIED | Conflict raises deterministically after exact source rows are committed for same-job recovery. |
| 01-03.5 | Every fact traces to exact row/source/version/checksum | ✗ FAILED | Links exist, but they do not prove the row and player identity match. |
| 01-03.6 | Durable refresh is live while legacy cache/workbook remains unchanged | ✓ VERIFIED | Worker forces structured collection; synchronous route retains legacy cache/tuple flow. |
| 01-03.7 | Selector/failure/two-way/cache/branding/workbook regressions remain green | ✓ VERIFIED | Available suite and catalog validator passed. |
| 01-04.1 | Rows cannot move into/out of a published snapshot | ✗ FAILED | Direct post-publication mutation is blocked, but concurrent draft mutation can commit after publish. |
| 01-04.2 | Published metric definitions cannot be updated/deleted | ✗ FAILED | Trigger has the same unlocked visibility race. |
| 01-04.3 | Revision 0001 is frozen explicit DDL | ✓ VERIFIED | 0001 contains explicit create_table DDL and linear Alembic history. |
| 01-04.4 | Canonical counts reject negatives in parser and PostgreSQL | ✓ VERIFIED | Parser guards and database checks exist; pure tests passed. |
| 01-05.1 | Concurrent shared Player/MetricDefinition upserts avoid unique races | ✓ VERIFIED | PostgreSQL ON CONFLICT plus scoped reselect; integration tests exist but could not run here. |
| 01-05.2 | Authoritative ID reuse blocks material name conflict | ✓ VERIFIED | Normalized stored-name comparison in `_persist_canonical()`. |
| 01-05.3 | Publication requires traceable snapshot-owned source, identity, typed facts | ✗ FAILED | Ownership/counts pass, but row-to-player identity equivalence is absent. |
| 01-06.1 | At most one overlapping worker performs live collection per job | ✓ VERIFIED | Whole-attempt PostgreSQL advisory lock surrounds collection/publication. |
| 01-06.2 | Losing overlapping delivery does not increment attempt or consume browser capacity | ✓ VERIFIED | Advisory-lock loser returns terminal state or job_busy before collector; stale sequential generations fail under 01-07.2. |
| 01-07.1 | Broker-loss accepted job is automatically redispatched | ✓ VERIFIED | Transactional outbox leases and reconciliation are wired. |
| 01-07.2 | Outbox and stale work reconciliation are safe with exclusive execution claim | ✗ FAILED | Outbox and Celery retry independently dispatch the same generation; payload lacks generation. |
| 01-08.1 | Complete same-job evidence can publish after final-attempt crash | ✓ VERIFIED | Evidence-first commit and reconstruction path exist with focused integration coverage. |
| 01-08.2 | Malformed NCAA headers/rows cannot become verified-empty ready snapshots | ✗ FAILED | Row shapes and Player header checked; section-specific core headers are not. |
| 01-08.3 | Partial recovery reuses exact complete section and fetches only missing section | ✓ VERIFIED | Matching signatures and section_names wiring preserve timestamps/checksums. |
| 01-09.1 | Failed/blocked capture stores bounded HTML at 696-hour expiry; success stores none | ✓ VERIFIED | Storage guard, byte cap, expiry calculation, and safe success path. |
| 01-09.2 | Failure state/logs retain only safe metadata | ✓ VERIFIED | Allowlist, sanitized URL/category, safe logging. |
| 01-09.3 | Persisted URLs strip credentials/query/fragment/invalid authority | ✓ VERIFIED | `_safe_source_url()` plus passing negative unit test. |
| 01-10.1 | App and worker share account-wide Browserless capacity | ✓ VERIFIED | Both acquisition paths enter PostgreSQL advisory slot namespace. |
| 01-10.2 | Local PostgreSQL/Redis publish only on loopback | ✓ VERIFIED | Compose binds 127.0.0.1:5432 and :6379; config tests pass. |
| 01-11.1 | Corrected MVP goal validates unchanged | ✓ VERIFIED | Central validator returned `valid: true`. |
| 01-11.2 | D1/D2/D3 durable flow yields immutable provenance snapshot and legacy workbook | ✗ FAILED | Fixture/release code exists, but immutable outcome is false under concurrency. |
| 01-11.3 | Core failures leave no ready snapshot and retain only allowed evidence | ✗ FAILED | A stat-less or renamed core schema is not treated as failure and may publish. |
| 01-11.4 | All owner-plan regressions, migration and deployment gates pass together | ? UNCERTAIN | Official release validator exits 1 without DATABASE_URL; PostgreSQL classes were skipped. Existing tests also omit the six gaps. |
| 01-12.1 | One scheduler continuously invokes dispatch reconciliation | ✓ VERIFIED | Separate Compose/Fly scheduler calls reconcile and drain in bounded loop. |
| 01-12.2 | Scheduler is least-privilege and owns no HTTP/mount/Browserless secret | ✓ VERIFIED | Fly env filtering and app-only HTTP/mount scoping. |
| 01-12.3 | Production launch remains blocked pending managed services/capacity | ✓ VERIFIED | Deployment runbook explicitly retains this gate. |
| 01-13.1 | Evidence bytes are absent by 720 hours while audit metadata remains | ✗ FAILED | Happy-path purge works; topology cannot guarantee the deadline. |
| 01-13.2 | Purge runs independently from dispatch at least every 24 hours | ✗ FAILED | It is scheduled separately in one loop but can be delayed indefinitely by synchronous reconciliation/broker calls. |
| 01-13.3 | Never/stale purge health blocks production readiness with safe alert | ✗ FAILED | Health is process-local/log-only and is not connected to Fly/Compose readiness. |
| 01-13.4 | Operator docs explain 696-hour expiry and 24-hour reserve | ✓ VERIFIED | DEPLOYMENT.md documents it, though the guarantee and one setting are inaccurate. |

**Score:** 37/57 merged roadmap and PLAN truths verified; 19 failed; 1 uncertain.

### Deferred Items

| Item | Addressed In | Evidence |
| --- | --- | --- |
| Authenticated snapshot creator attribution | Phase 2 | Identity and authenticated report workflows are Phase 2 goals. |
| Fielding facts share canonical identity | Phase 3 | Phase 3 explicitly adds a common fielding schema. |
| Excel and Power BI consume the exact same snapshot | Phases 4-5 | Phase 4 builds the exact-snapshot workbook; Phase 5 embeds that same snapshot. |

## Required Artifacts

All PLAN artifacts exist and passed basic substance checks. The failures are Level 3/4 correctness and wiring failures, not missing files.

| Artifact | Expected | Status | Details |
| --- | --- | --- | --- |
| `migrations/versions/0001_canonical_snapshot_foundation.py` | Explicit canonical schema | ✓ VERIFIED | Explicit DDL; linear 0001→0005 migration graph. |
| `migrations/versions/0003_snapshot_integrity_hardening.py` | Published immutability | ✗ HOLLOW | Direct mutations blocked, concurrent visibility not serialized. |
| `migrations/versions/0004_publication_traceability.py` | Fact/source/identity traceability | ⚠ PARTIAL | Non-null ownership links, no evidence-to-player binding. |
| `roster_scout/models.py` | Durable canonical/job/evidence models | ⚠ PARTIAL | Substantive and wired; unresolved/creator pathways are not populated. |
| `roster_scout/snapshots.py` | Atomic validated publication | ✗ HOLLOW | Wired, but unsafe concurrency and identity validation. |
| `roster_scout/jobs.py` | Idempotent durable orchestration | ✗ HOLLOW | Wired, but creates an outbox retry alongside Celery retry. |
| `roster_scout/tasks.py` | Opaque job worker entry | ⚠ PARTIAL | UUID-only payload works but has no attempt generation. |
| `roster_scout/repositories.py` | Published reads and outbox leases | ✓ VERIFIED | Substantive, used by jobs/scheduler. |
| `roster_scout/canonical.py` | Canonical transformation | ⚠ PARTIAL | Outs/missing/two-way/lineage work; unresolved status unreachable. |
| `roster_scraper.py` | Section-aware NCAA acquisition | ✗ HOLLOW | Structured flow is wired; core stat-header contract is absent. |
| `roster_scout/evidence.py` | Bounded evidence and purge | ⚠ PARTIAL | Storage/purge implementation works; deployment deadline not guaranteed. |
| `scripts/run_scheduler.py` | Reconciliation and purge owner | ✗ HOLLOW | Runs both, but purge health is non-durable and loop can be blocked. |
| `usage_limits.py` | Global Browserless capacity | ✓ VERIFIED | PostgreSQL advisory slots are wired into browser acquisition. |
| `compose.yaml`, `fly.toml`, `Dockerfile` | Least-privilege process topology | ⚠ WARNING | Process separation exists; background Compose containers inherit an impossible web health check. |
| Phase test modules and release validator | Regression/release proof | ⚠ PARTIAL | Pure tests pass; PostgreSQL gate unavailable and missing blocker cases. |

## Key Link Verification

| From | To | Via | Status | Details |
| --- | --- | --- | --- | --- |
| Job acceptance | Outbox | One transaction | ✓ WIRED | Intent is committed with accepted job. |
| Outbox | Celery task | UUID dispatch | ⚠ PARTIAL | Wired, but no message/generation token reaches the task. |
| Celery task | Durable runner | `run_report_job(job_id)` | ✗ UNSAFE | Task also invokes Celery retry after runner creates outbox retry. |
| Durable runner | Snapshot publication | Caller transaction | ✗ UNSAFE | Functional link exists; publication lock protocol is incomplete. |
| Parser | Section state | parse result/verified_empty | ✗ UNSAFE | No batting/pitching header contract. |
| SourceRecord | Fact | Composite snapshot/org FK | ✓ WIRED | Exact row ownership enforced. |
| Fact | PlayerTeamSeason | Composite snapshot/org FK | ✓ WIRED | Snapshot identity ownership enforced. |
| SourceRecord ↔ PlayerTeamSeason | Same player | Identity equivalence | ✗ NOT WIRED | Independent links can be mismatched. |
| Same-job cache | Structured collector | Source/parser/schema signature | ✓ WIRED | Exact-job version-aware reconstruction. |
| Scheduler | Dispatch reconciliation | Bounded polling | ✓ WIRED | Separate process invokes services. |
| Scheduler | Evidence purge readiness | Durable readiness gate | ✗ NOT WIRED | Local object/log only; no deployment check. |
| App and worker | Browserless capacity | Shared PostgreSQL namespace | ✓ WIRED | Same advisory slot authority. |
| Legacy route | Two-sheet workbook | Existing scraper tuple | ✓ WIRED | Route regression tests pass. |

## Data-Flow Trace (Level 4)

| Artifact / flow | Data | Source | Produces real data | Status |
| --- | --- | --- | --- | --- |
| Durable canonical build | job → sections → SourceRecord/facts → published reader | Live structured NCAA collector or exact same-job evidence | Yes for fixtures/live boundary | ⚠ FLOWING BUT UNSAFE |
| Fact identity audit | fact source row + player identity | Two independent foreign keys | Data exists but semantic match not enforced | ✗ DISCONNECTED IDENTITY |
| Retry delivery | outbox/Celery → job attempt | Two independent retry authorities | Both can produce execution | ✗ DUPLICATE AUTHORITY |
| Evidence purge | FailureEvidence bytes → purge → readiness | Singleton synchronous scheduler | Purge mutates real rows; health is not durable/exposed | ⚠ OPERATIONALLY HOLLOW |
| Legacy workbook | request → scraper/cache → hitters/pitchers → XLSX | Existing synchronous route | Yes | ✓ FLOWING |

## Behavioral Spot-Checks

| Behavior | Command | Result | Status |
| --- | --- | --- | --- |
| MVP goal contract | `gsd-sdk query user-story.validate ... --raw` | `valid: true` | ✓ PASS |
| Available repository suite | `.venv/bin/python -m unittest discover -s tests -v` | 89 ran, 89 passed, 23 PostgreSQL-backed skips | ⚠ PARTIAL |
| NCAA catalog | `.venv/bin/python scripts/validate_ncaa_catalog.py` | 942/942 selectable; valid=true | ✓ PASS |
| Static compilation | `.venv/bin/python -m py_compile ...` | exit 0 | ✓ PASS |
| Full release gate | `.venv/bin/python scripts/validate_phase1_release.py` | exit 1: DATABASE_URL required | ? SKIP/UNAVAILABLE |
| Section header rejection | Direct parser call with `Player, Unrelated` | Accepted one row | ✗ FAIL |
| Verified-empty schema guard | Direct parser call with Player-only empty table | Returned `verified_empty=True` | ✗ FAIL |
| Unresolved identity | Canonicalize three ambiguous same-name ID-less rows | All three returned `identity_status='fallback'` | ✗ FAIL |
| Migration graph | `.venv/bin/alembic heads` | One head: 0005 | ✓ PASS |
| Diff hygiene | `git diff --check` | exit 0 | ✓ PASS |

## Probe Execution

No PLAN-declared or conventional `scripts/*/tests/probe-*.sh` probes exist. The direct negative probes above were run in the verifier's own process.

## Requirements Coverage

| Requirement | Source plans | Status | Evidence |
| --- | --- | --- | --- |
| SNAP-01 | 01, 03, 11 | ✓ SATISFIED | Report → organization/team-season → team/division/season schema. |
| SNAP-02 | 02, 03, 06, 07, 09-12 | ✓ SATISFIED | Background Celery job and queued/running/retrying/partial/ready/failed states. |
| SNAP-03 | 01-08, 11 | ✗ BLOCKED | Schema/timestamps/predecessor exist, but immutability race remains; creator is deferred to Phase 2. |
| SNAP-04 | 02, 03, 05, 08, 09, 11 | ✓ SATISFIED | Per-section status/count/time/failure category persisted. |
| DATA-01 | 03, 05, 11 | ✗ BLOCKED | Stable and fallback IDs exist; unresolved runtime status is unreachable. |
| DATA-02 | 01, 03, 08, 11 | ✓ SATISFIED | Source/team/season/category IDs, URLs, timestamps persist. |
| DATA-03 | 03, 05, 11 | ✗ BLOCKED | Two-way rows share identity, but evidence can be bound to the wrong player; fielding is deferred to Phase 3. |
| DATA-04 | 01, 11 | ✓ SATISFIED | Global authoritative Player plus distinct snapshot/team-season identities represent transfers. |
| DATA-05 | 01, 03, 04, 11 | ✓ SATISFIED | Nullable values distinguish zero/missing; tests pass. |
| DATA-06 | 01, 03, 11 | ✓ SATISFIED | Innings stored as integer outs with raw value retained. |
| DATA-07 | 02, 03, 05, 08, 09, 11, 13 | ✓ SATISFIED | Exact raw JSON/checksum/provenance retained with facts; failure evidence is separately bounded. |
| DATA-08 | 01, 03, 04, 11 | ✓ SATISFIED | Metric definition version/provenance stored; no weight set is used by current AVG/ERA facts. |
| DATA-09 | 01, 05, 11 | ✓ SATISFIED | Composite organization/snapshot foreign keys reject cross-tenant fact ownership. |
| DATA-10 | 01, 03, 05, 11 | ↪ DEFERRED | Current legacy workbook still reads scraper tuples; exact canonical Excel/PBI consumption is explicitly Phase 4/5. |
| OPS-01 | 02, 03, 06, 07, 10-12 | ✗ BLOCKED | Browser capacity is shared, but retry attempts are not bounded safely across two authorities. |
| OPS-02 | 02, 03, 08, 11 | ✓ SATISFIED | Successful sections use job/source/parser/schema-aware cache keys. |
| OPS-04 | 01, 02, 04, 07, 10-13 | ? NEEDS POSTGRESQL | Linear explicit migrations and release command exist; upgrade/check integration could not run without DATABASE_URL. |
| OPS-05 | 03, 08, 10, 11 | ✓ SATISFIED | Selector, D1/D2/D3 scraper/cache and workbook tests passed. |

No Phase 1 requirement IDs are orphaned from PLAN frontmatter.

## Review-Finding Adjudication

| Finding | Verdict | Independent evidence |
| --- | --- | --- |
| CR-01 PostgreSQL concurrency visibility | CONFIRMED BLOCKER | 0003 trigger SELECTs are unlocked; publisher locks only parent row. |
| CR-02 retry authority/generation | CONFIRMED BLOCKER | `_record_failure()` enqueues outbox at jobs.py:879-882 and task calls `self.retry()` at tasks.py:43-44; payload is job UUID only. |
| CR-03 720-hour physical deletion | CONFIRMED BLOCKER | 696h + max 24h has zero margin; same blocking scheduler loop; no persisted/readiness integration. |
| CR-04 evidence-to-player binding | CONFIRMED BLOCKER | Fact links independently satisfy ownership; publication never compares source identity with Player. |
| CR-05 section-aware headers | CONFIRMED BLOCKER | Direct probe accepted `Player, Unrelated` and Player-only verified-empty. |
| WR-01 background health checks | CONFIRMED WARNING | Dockerfile HTTP health check is inherited by Compose worker/scheduler, which expose no port 5000. |
| WR-02 unused retention setting | CONFIRMED WARNING | `.env.example`, Compose, and DEPLOYMENT document `FAILURE_EVIDENCE_RETENTION_DAYS`; production code never reads it. |

## Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
| --- | --- | --- | --- | --- |
| `Dockerfile` / `compose.yaml` | 41-42 / 24-63 | Web-only health check inherited by background services | ⚠ WARNING | Healthy worker/scheduler appear unhealthy in Compose. |
| `.env.example` / `compose.yaml` / `DEPLOYMENT.md` | 17 / 36 / 195 | Documented retention variable unused | ⚠ WARNING | Operator changes silently have no effect. |
| Phase-modified source set | — | TBD/FIXME/XXX debt markers | ✓ NONE | No blocker debt markers found. |

Empty-list/dict returns found in the legacy scraper were inspected as lookup/error fallbacks and do not feed a phase placeholder UI or stubbed canonical path.

## Human / External Verification Required

These do not supersede the code blockers above.

### 1. Managed production topology

**Test:** Provision/inspect managed PostgreSQL and Redis, attach secrets, run the Fly release migration, and confirm exactly one intended worker and scheduler process.
**Expected:** No secrets in logs/config, migration succeeds, and managed services—not development Compose credentials—back production.
**Why human/external:** Requires the Fly account, billing, and managed service state.

### 2. Live Browserless capacity and NCAA smoke

**Test:** Run concurrent web and worker NCAA requests against the production account limit, then one representative live D1/D2/D3 smoke build.
**Expected:** Aggregate sessions never exceed the account limit and successful builds produce expected live evidence.
**Why human/external:** Depends on Browserless account limits and NCAA/Akamai behavior.

### 3. PostgreSQL release and concurrency gate

**Test:** Supply an isolated PostgreSQL `DATABASE_URL`, run `scripts/validate_phase1_release.py`, then run new two-connection publication-race tests after the lock protocol is fixed.
**Expected:** Migration check and all integration tests pass, and neither transaction interleaving permits post-publication mutation.
**Why human/external:** No PostgreSQL test URL or Docker daemon access was available in this verification environment.

## Gaps Summary

The prior goal-format gap is closed, and meaningful portions of the foundation are real: D1/D2/D3 parsing, canonical numeric handling, durable models, outbox recovery, same-job versioned caching, global Browserless capacity, loopback development bindings, and the legacy workbook regressions all have direct evidence.

The phase goal is nevertheless not achieved. The promised source of truth can change after publication, can associate evidence with the wrong player, and can accept a stat-less NCAA schema. Retry delivery is not generation-safe, unsafe ID-less identities are mislabeled, and the physical evidence-deletion promise is not enforceable by the deployed topology. These are must-have failures, so the escalation gate must remain closed until fixed or explicitly overridden by the developer.

---

_Verified: 2026-11-09T22:27:18Z_
_Verifier: the agent (gsd-verifier)_
