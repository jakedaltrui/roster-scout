---
phase: 01-canonical-snapshot-foundation
verified: 2026-11-10T04:33:56Z
status: gaps_found
score: 63/85 must-haves verified
overrides_applied: 0
re_verification:
  previous_status: gaps_found
  previous_score: 37/57
  gaps_closed:
    - "Snapshot publication is serialized against child, metric-value, and metric-definition mutations in both transaction interleavings."
    - "PostgreSQL outbox generations are the sole retry authority, stale deliveries are rejected by CAS, and process loss advances N to N+1 once."
  gaps_remaining:
    - "Failed-evidence retention is not database-enforced, invalid retention can report ready, and one contended row can prevent purge progress."
    - "Authoritative source_player_id is not bound to the Player behind PlayerTeamSeason."
    - "The NCAA header contract reuses parser-1 cache identity and accepts duplicate required headers."
    - "A one-to-one ID-less same-name batting/pitching pair without corroborating support is merged as fallback."
  regressions: []
gaps:
  - truth: "Malformed NCAA schemas and stale pre-contract cache evidence cannot publish."
    status: failed
    reason: "The header change still uses parser-1 and ncaa-core-v1 is not persisted in cache metadata. Required non-Player headers use set inclusion, so duplicate AB/H/IP/ERA headers are accepted."
    artifacts:
      - path: "roster_scraper.py"
        issue: "Parser identity remains parser-1 and required headers are not unique."
      - path: "roster_scout/jobs.py"
        issue: "Cache matching cannot distinguish pre-contract parser-1 sections."
    missing:
      - "Persist a bumped parser/header-contract identity and reject old cached sections."
      - "Require exactly one normalized occurrence of every required header and test duplicate row-bearing and empty tables."
  - truth: "Unsafe ID-less matching reaches unresolved rather than merging without supporting evidence."
    status: failed
    reason: "Missing jersey/position are compatible wildcards; reciprocal uniqueness merges a lone batting and pitching row before the missing-support ambiguity check."
    artifacts:
      - path: "roster_scout/canonical.py"
        issue: "The reciprocal branch requires no shared nonempty support attribute."
    missing:
      - "Require corroborating jersey or position evidence for a cross-section fallback merge."
      - "Test no-support and one-sided-support same-name pairs as separate unresolved identities."
  - truth: "Retention is database-enforced and invalid retention blocks readiness without rolling back terminal job state."
    status: failed
    reason: "PostgreSQL has no collected_at/expires_at window constraint. /readyz returned 200 under retention 29, and evidence insertion can raise inside the terminal job transaction."
    artifacts:
      - path: "roster_scout/models.py"
        issue: "FailureEvidence has no <=28-day timestamp constraint."
      - path: "migrations/versions/0009_evidence_purge_health.py"
        issue: "Adds health state only; failure_evidence remains unhardened."
      - path: "app.py"
        issue: "/readyz does not validate retention configuration."
      - path: "roster_scout/jobs.py"
        issue: "Optional evidence storage shares the terminal-state transaction."
    missing:
      - "Add a reversible 0 < expires_at-collected_at <= 28 days database constraint and direct-SQL tests."
      - "Validate startup/readiness configuration and preserve sanitized terminal state if evidence storage fails."
  - truth: "The purge makes bounded forward progress under contention and volume."
    status: failed
    reason: "One all-row UPDATE runs in one transaction. A locked eligible row triggers lock_timeout and rolls back deletion of every other row."
    artifacts:
      - path: "roster_scout/evidence.py"
        issue: "No batching or SKIP LOCKED."
      - path: "tests/test_evidence.py"
        issue: "The contention test proves total rollback, not unrelated-row progress."
    missing:
      - "Use bounded deterministic batches with FOR UPDATE SKIP LOCKED."
      - "Add locked-row and multi-batch backlog progress tests."
  - truth: "Every authoritative source row identifies the same canonical Player as its fact."
    status: failed
    reason: "Fact and SourceRecord share PlayerTeamSeason, but neither the database nor publisher compares SourceRecord.source_player_id with Player.source_player_id."
    artifacts:
      - path: "roster_scout/snapshots.py"
        issue: "Publication never joins Player or compares source IDs."
      - path: "migrations/versions/0008_source_identity_binding.py"
        issue: "Constraints enforce only the chosen PlayerTeamSeason."
    missing:
      - "Database-enforce authoritative source-ID equivalence."
      - "Add ORM/direct-SQL publication negatives with shared PlayerTeamSeason but mismatched authoritative IDs."
deferred:
  - truth: "Snapshot creator attribution is populated from an authenticated operator."
    addressed_in: "Phase 2"
    evidence: "Phase 2 introduces authenticated organization report workflows."
  - truth: "Fielding facts share the same canonical player identity."
    addressed_in: "Phase 3"
    evidence: "Phase 3 adds fielding to the canonical schema."
  - truth: "Excel and Power BI consume exact published canonical facts."
    addressed_in: "Phases 4 and 5"
    evidence: "Those phases deliver exact-snapshot workbook and Power BI surfaces."
---

# Phase 1: Canonical Snapshot Foundation Verification Report

**Phase Goal:** As a Roster Scout operator, I want to publish existing NCAA D1/D2/D3 results through a durable job into an immutable, provenance-rich canonical snapshot, so that every downstream report can rely on one auditable source of truth without breaking today's workbook workflow.
**Verified:** 2026-11-10T04:33:56Z
**Status:** gaps_found
**Re-verification:** Yes — after Plans 01-14 through 01-21.

## User Flow Coverage

| Step | Expected | Evidence | Status |
| --- | --- | --- | --- |
| Accept build | Accepted job and generation outbox survive broker loss | PostgreSQL generation/outbox tests pass | ✓ VERIFIED |
| Collect/publish | Only supported batting/pitching evidence becomes ready | Duplicate headers and old parser-1 evidence remain acceptable | ✗ FAILED |
| Rely on truth | Immutable fact, exact row, and canonical player agree | Concurrency fixed; authoritative source ID can disagree with Player | ✗ FAILED |
| Preserve workbook | Selector, cache, and two-sheet workbook work | Release validator and 942-row catalog pass | ✓ VERIFIED |
| Outcome | One auditable truth supports downstream reports | Schema/cache, identity, and retention invariants remain unsafe | ✗ FAILED |

## Goal Achievement

### Roadmap Success Criteria

| # | Truth | Status | Evidence |
| --- | --- | --- | --- |
| R1 | Representative D1/D2/D3 builds publish immutable canonical facts | ✓ VERIFIED | Representative flows and both mutation race interleavings pass. |
| R2 | Durable jobs cannot publish failed/half-written snapshots | ✗ FAILED | Duplicate-header or stale parser-1 evidence can bypass the new schema contract. |
| R3 | Retries are idempotent and cache is schema/version aware | ✗ FAILED | Generation CAS passes; header-contract version is not persisted. |
| R4 | Numeric, identity, raw, and metric lineage are unambiguous | ✗ FAILED | Numeric/metric paths pass; unsafe ID-less and authoritative ID cases fail. |
| R5 | Existing selector/scraper/cache/workbook regressions pass | ✓ VERIFIED | 273 tests and 942-row catalog pass. |

### Plan Must-Have Truths

All 80 PLAN truths are resolved. “All verified” resolves each truth in that range.

| Plan truths | Status | Evidence / failed truth IDs |
| --- | --- | --- |
| 01-01.1–.6 | ✓ ALL VERIFIED | Atomic lock protocol, schema, ownership, evidence homes, innings. |
| 01-02.1–.9 | 6/9 | ✗ .1 duplicate/stale schemas; ✗ .5 parser cache identity; ✗ .8 deletion guarantee. |
| 01-03.1–.7 | 5/7 | ✗ .2 unsafe fallback; ✗ .5 authoritative evidence/Player equivalence. |
| 01-04.1–.4 | ✓ ALL VERIFIED | Mutation races, metric immutability, migration history, count checks. |
| 01-05.1–.3 | 2/3 | ✗ .3 traceability does not prove source ID equals Player ID. |
| 01-06.1–.2 | ✓ ALL VERIFIED | Whole-attempt claim. |
| 01-07.1–.2 | ✓ ALL VERIFIED | Outbox and exclusive reconciliation. |
| 01-08.1–.3 | 2/3 | ✗ .2 duplicate/stale headers can authorize publication. |
| 01-09.1–.3 | ✓ ALL VERIFIED | 672h is a stronger superseding value; bounded/sanitized capture passes. |
| 01-10.1–.2 | ✓ ALL VERIFIED | Shared capacity and loopback bindings. |
| 01-11.1–.4 | 3/4 | ✗ .3 malformed duplicate/stale evidence may publish. |
| 01-12.1–.3 | ✓ ALL VERIFIED | Scheduler, least privilege, production gate. |
| 01-13.1–.4 | 2/4 | ✗ .1 deadline bypass/contention; ✗ .3 invalid retention reports ready. |
| 01-14.1–.3 | 2/3 | ✗ .1 required headers not unique and contract version not persisted. |
| 01-15.1–.3 | ✓ ALL VERIFIED | Shared locks and four real transaction interleavings. |
| 01-16.1–.5 | ✓ ALL VERIFIED | One retry authority, generation CAS, stale rejection, crash recovery, backoff. |
| 01-17.1–.3 | 2/3 | ✗ .1 shared PlayerTeamSeason does not prove authoritative Player equality. |
| 01-18.1–.3 | 1/3 | ✗ .1 no-support reciprocal merge; ✗ .3 unsafe rows do not remain unresolved. |
| 01-19.1–.4 | 1/4 | ✗ .1 no DB cap; ✗ .2 zero progress under contention; ✗ .4 config omitted from readiness. |
| 01-20.1–.3 | ✓ ALL VERIFIED | Independent process, dispatch isolation, bounded safe retry. |
| 01-21.1–.4 | 2/4 | ✗ .1 invalid retention returns ready; ✗ .3 hard cap is application-only. |

**Score:** 63/85 merged roadmap and PLAN truths verified.

### Deferred Items

| Item | Addressed In | Evidence |
| --- | --- | --- |
| Authenticated creator | Phase 2 | Authentication/report workflows. |
| Fielding identity | Phase 3 | Fielding canonical schema. |
| Canonical Excel/Power BI consumers | Phases 4–5 | Exact-snapshot delivery surfaces. |

## Required Artifacts

All declared artifacts exist and passed SDK substance checks. Manual Level 3/4 results:

| Artifact | Status | Details |
| --- | --- | --- |
| `0006_snapshot_mutation_serialization.py` | ✓ VERIFIED | Shared xact locks; two-connection tests pass. |
| `0007_report_attempt_generations.py` | ✓ VERIFIED | Migration/outbox/task generation path wired. |
| `0008_source_identity_binding.py` | ⚠ PARTIAL | Same PlayerTeamSeason enforced; authoritative ID equivalence absent. |
| `0009_evidence_purge_health.py` | ⚠ PARTIAL | Durable heartbeat exists; no retention-window constraint. |
| `roster_scraper.py` | ✗ HOLLOW EDGE | Main matrix works; duplicate headers and stale cache identity do not. |
| `roster_scout/canonical.py` | ✗ HOLLOW EDGE | Many ambiguity cases work; no-support reciprocal pair merges. |
| `roster_scout/evidence.py` | ✗ HOLLOW EDGE | Real purge works; one lock can cause zero progress. |
| `app.py` | ✗ PARTIAL | Persisted state wired; retention config omitted. |
| purge/deployment config | ✓ VERIFIED | Independent least-privilege process renders. |
| release validator | ✓ VERIFIED | Exits 0, but omits confirmed counterexamples. |

## Key Link Verification

| From | To | Status | Details |
| --- | --- | --- | --- |
| Publisher/mutations | shared DB locks | ✓ WIRED | Both interleavings pass. |
| Outbox | `(job_id,generation)` task | ✓ WIRED | CAS rejects stale/duplicate. |
| Header contract | persisted cache identity | ✗ NOT WIRED | ncaa-core-v1 is in-memory; parser remains parser-1. |
| SourceRecord/fact | PlayerTeamSeason | ✓ WIRED | Swapped PTS rejected. |
| SourceRecord.source_player_id | Player.source_player_id | ✗ NOT WIRED | No constraint or publication comparison. |
| Retention config | FailureEvidence row | ⚠ PARTIAL | Normal insert capped; DB bypass remains. |
| Purge runner | expired rows | ⚠ PARTIAL | Independent from dispatch, not row contention. |
| Purge state | `/readyz` | ⚠ PARTIAL | Freshness/bytes wired; invalid config ignored. |
| Legacy request | workbook | ✓ WIRED | Regressions pass. |

## Data-Flow Trace (Level 4)

| Flow | Produces Real Data | Status |
| --- | --- | --- |
| Job → snapshot | Yes | ⚠ FLOWING, schema-version edge unsafe |
| Source row → fact identity | Yes | ⚠ FLOWING, authoritative ID unchecked |
| Retry generation → attempt | Yes | ✓ FLOWING |
| Expired bytes → purge | Yes when uncontended | ✗ BLOCKABLE |
| Purge state → readiness | Yes | ⚠ FLOWING, config omitted |
| Legacy workbook | Yes | ✓ FLOWING |

## Behavioral Spot-Checks

| Behavior | Result | Status |
| --- | --- | --- |
| Supported suite | 273 tests, OK | ✓ PASS |
| Unified release gate | migrations, 87 focused, 273 full, catalog, Compose, diff pass | ✓ PASS |
| Migration lifecycle/drift | base downgrade/re-upgrade; no new operations | ✓ PASS |
| NCAA catalog | 942/942 selectable | ✓ PASS |
| `Player,AB,AB,H` parser probe | Accepted row with `AB_2` | ✗ FAIL |
| No-support same-name pair | Merged into one fallback | ✗ FAIL |
| Retention 29 + fresh heartbeat | `/readyz` returned 200 fresh | ✗ FAIL |
| Active DB constraints | No failure-evidence time-window check | ✗ FAIL |
| Locked eligible purge row | Entire transaction rolls back, no heartbeat | ✗ FAIL |

## Probe Execution

No PLAN-declared `probe-*.sh` files exist. Counterexamples ran in the verifier process using the supported uv/PostgreSQL environment.

## Requirements Coverage

| Requirement | Status | Evidence |
| --- | --- | --- |
| SNAP-01 | ✓ SATISFIED | Report/team/division/season persisted. |
| SNAP-02 | ✓ SATISFIED | Durable states, worker, scheduler, generation recovery. |
| SNAP-03 | ✗ BLOCKED | Provenance may identify the wrong authoritative Player. |
| SNAP-04 | ✓ SATISFIED | Section state/count/time/safe failure metadata. |
| DATA-01 | ✗ BLOCKED | Missing-support pair becomes fallback. |
| DATA-02 | ✓ SATISFIED | Source/team/season/category provenance. |
| DATA-03 | ✗ BLOCKED | Source ID may disagree with canonical Player; fielding deferred. |
| DATA-04 | ✓ SATISFIED | Transfer-aware identities. |
| DATA-05 | ✓ SATISFIED | Null versus zero. |
| DATA-06 | ✓ SATISFIED | Innings outs plus raw value. |
| DATA-07 | ✗ BLOCKED | Authoritative ownership and hard retention are not durable invariants. |
| DATA-08 | ✓ SATISFIED | Versioned immutable metric lineage. |
| DATA-09 | ✓ SATISFIED | Cross-organization FKs. |
| DATA-10 | ↪ DEFERRED | Phase 4/5 consumers. |
| OPS-01 | ✓ SATISFIED | Bounded retries and shared capacity. |
| OPS-02 | ✗ BLOCKED | Header-contract change absent from cache identity. |
| OPS-04 | ✓ SATISFIED | Migration lifecycle/drift pass. |
| OPS-05 | ✓ SATISFIED | D1/D2/D3 and workbook regressions pass. |

No Phase 1 requirement IDs are orphaned from PLAN frontmatter.

## Anti-Patterns Found

| File | Pattern | Severity | Impact |
| --- | --- | --- | --- |
| Phase source set | TBD/FIXME/XXX | ✓ NONE | No blocker debt markers. |
| `roster_scout/evidence.py:228` | all-row UPDATE | 🛑 BLOCKER | One timeout rolls back all progress. |
| `roster_scraper.py:2096` | set header validation | 🛑 BLOCKER | Duplicate core fields are accepted. |

Legacy empty returns are lookup/error fallbacks, not canonical stubs.

## Human / External Verification Required

### 1. Managed production topology

**Test:** Provision managed services, run release migration, and confirm one scheduler and purge process.
**Expected:** No secrets leak and readiness remains closed until persisted purge success.
**Why human:** Requires Fly account, billing, and managed state.

### 2. Live Browserless/NCAA boundary

**Test:** Exercise concurrent web/worker collection and live D1/D2/D3 builds.
**Expected:** Account capacity is respected and live evidence matches supported schema.
**Why human:** Depends on external limits and NCAA/Akamai.

## Gaps Summary

Plans 01-15 and 01-16 close the concurrency and retry gaps with real PostgreSQL proof, and the workbook/release gates are green. The phase goal is still not achieved: stale or duplicate-schema evidence can publish, unsafe ID-less rows can merge, authoritative IDs can disagree with canonical Players, and the 720-hour deletion/readiness promise is not a durable forward-progress-safe invariant.

---

_Verified: 2026-11-10T04:33:56Z_
_Verifier: the agent (gsd-verifier)_
