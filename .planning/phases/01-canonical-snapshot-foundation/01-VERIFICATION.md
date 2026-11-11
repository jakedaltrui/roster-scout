---
phase: 01-canonical-snapshot-foundation
verified: 2026-11-11T16:03:45Z
status: gaps_found
score: "128/143 must-haves verified"
overrides_applied: 0
re_verification:
  previous_status: gaps_found
  previous_score: "100/119"
  gaps_closed:
    - "The native two-sheet workbook succeeds without DATABASE_URL and dropped analytics is sanitized and observable."
    - "Unique authenticated users count distinct actors only from authenticated_use events."
    - "The legacy NCAA result cache is bound to cache format, parser, core-header, and schema identities."
    - "Workbook preparation no longer records success; blob-read/initiation acknowledgement and ambiguous retry reuse are implemented."
    - "Purge certification is serialized against eligible evidence insertion and its heartbeat is monotonic."
    - "The original existing-organization authoritative identity interleavings are serialized."
  gaps_remaining:
    - "Authoritative identity equivalence can still be violated when the organization and its snapshot identity are created in the uncommitted source transaction."
    - "Fly's TLS-terminating proxy causes valid public HTTPS acknowledgements to fail the endpoint's internal-scheme origin comparison."
    - "Definitive acknowledgement 4xx responses remain pending and block later intentional reports until expiry."
    - "Unavailable sessionStorage can break the browser report workflow."
    - "The release diff gate does not inspect committed phase changes."
  regressions: []
gaps:
  - truth: "Every authoritative source row continues to identify the same canonical Player under every concurrent tenant-creation and identity-mutation schedule."
    status: failed
    reason: "Revision 0013 keys the common Player lock by organization plus Player. A direct Player update enumerates only visible organizations; an uncommitted new organization is invisible, so the source transaction and Player update take no common lock and both commit a 42-versus-99 mismatch."
    artifacts:
      - path: "migrations/versions/0013_authoritative_identity_serialization.py"
        issue: "enforce_player_authoritative_source_player cross-joins visible organizations at lines 292-310 instead of taking a visibility-independent global Player lock."
      - path: "tests/test_snapshot_persistence.py"
        issue: "The new identity race creates the organization before both workers and does not cover organization creation inside the uncommitted source transaction."
    missing:
      - "Add one global Player UUID lock acquired by every authoritative source, Player, PlayerTeamSeason, and publication path before tenant-scoped identity locks, without reversing snapshot-first ordering."
      - "Add a real two-connection regression that creates organization, snapshot, PlayerTeamSeason, and SourceRecord in the still-uncommitted source transaction."
  - truth: "A valid same-origin browser acknowledgement works behind the supported Fly HTTPS proxy boundary."
    status: failed
    reason: "The endpoint compares Origin with request.scheme/request.host. Under Fly's HTTPS edge and plain-HTTP internal port, Flask sees http while the browser sends an https Origin; X-Forwarded-Proto is ignored and the valid acknowledgement returns 403."
    artifacts:
      - path: "app.py"
        issue: "acknowledge_workbook_download builds expected_origin from the untrusted/internal WSGI scheme without a trusted external-origin contract."
      - path: "fly.toml"
        issue: "force_https terminates public HTTPS while the app listens on plain HTTP internal_port 5000."
      - path: "tests/test_app_routes.py"
        issue: "Origin tests use matching direct request schemes and do not model the Fly forwarding hop."
    missing:
      - "Configure a trusted one-hop proxy scheme/host boundary or a validated public origin without trusting arbitrary forwarded headers."
      - "Add a route test for internal HTTP plus public HTTPS Origin and trusted Fly forwarded-proto metadata."
  - truth: "A definitive acknowledgement rejection ends the pending attempt so a later intentional submission can receive a fresh UUID."
    status: failed
    reason: "postDownloadAcknowledgement collapses every non-2xx response to acknowledged=false. The state machine treats 400/403 exactly like network/5xx ambiguity, preserves needs_ack, and refuses to start another workbook until the 15-minute client TTL expires."
    artifacts:
      - path: "templates/index.html"
        issue: "The acknowledgement callback discards the HTTP status classification."
      - path: "static/download_tracking.js"
        issue: "runDownloadSubmission has no transition that clears needs_ack after a definitive acknowledgement failure."
      - path: "tests/js/download_tracking.test.mjs"
        issue: "Tests cover ambiguous failure but not 400/403 versus 500/network classification."
    missing:
      - "Return success, retryable ambiguity/5xx, or definitive 4xx from the acknowledgement callback."
      - "Clear the matching pending state on definitive 4xx and allocate a fresh UUID only on a later explicit submit."
      - "Add executable Node and template regressions for 400/403, 500, and network outcomes."
  - truth: "The browser workbook form remains usable when session storage is unavailable or throws."
    status: partial
    reason: "The template passes sessionStorage directly and the state machine does not guard getItem, setItem, or removeItem. A SecurityError or quota error escapes instead of using a current-page fallback."
    artifacts:
      - path: "templates/index.html"
        issue: "Top-level sessionStorage acquisition has no fallback."
      - path: "static/download_tracking.js"
        issue: "All storage methods are called without exception handling."
    missing:
      - "Use a safe storage adapter with an in-memory per-page fallback."
      - "Test throwing storage methods and form initialization/submission."
  - truth: "The release diff-hygiene gate inspects the committed Phase 1 change range."
    status: partial
    reason: "The validator runs only git diff --check after all plan commits, so a clean working tree makes the check inspect none of the 30 committed files changed since the round-four base."
    artifacts:
      - path: "scripts/validate_phase1_release.py"
        issue: "DIFF_LABEL runs git diff --check without an explicit validated base..HEAD range."
      - path: "tests/test_deployment_config.py"
        issue: "Manifest tests assert ordering but not a nonempty release-scoped diff range."
    missing:
      - "Resolve and validate the release base and run git diff --check <base>..HEAD, optionally followed by a working-tree check."
      - "Add a contract test that requires the commit range and proves a committed whitespace defect is detected."
deferred:
  - truth: "Authenticated-use production wiring and the actor-account foreign key exist."
    addressed_in: "Phase 2"
    evidence: "ROADMAP Phase 2 success criterion 6 requires membership-authorized accepted report/job events and the canonical account foreign key."
  - truth: "Fielding facts share the same canonical player identity."
    addressed_in: "Phase 3"
    evidence: "Phase 3 adds fielding to the canonical schema and consistent player identity."
  - truth: "Excel and Power BI read the same exact published snapshot facts."
    addressed_in: "Phases 4 and 5"
    evidence: "Phase 4 delivers exact-snapshot Excel and Phase 5 delivers the corresponding Power BI surface."
---

# Phase 1: Canonical Snapshot Foundation Verification Report

**Phase Goal:** As a Roster Scout operator, I want to publish existing NCAA D1/D2/D3 results through a durable job into an immutable, provenance-rich canonical snapshot, so that every downstream report can rely on one auditable source of truth without breaking today's workbook workflow.
**Verified:** 2026-11-11T16:03:45Z
**Status:** gaps_found
**Re-verification:** Yes — after round-four Plans 01-31 through 01-37.

## User Flow Coverage

User story: “As a Roster Scout operator, I want to publish existing NCAA D1/D2/D3 results through a durable job into an immutable, provenance-rich canonical snapshot, so that every downstream report can rely on one auditable source of truth without breaking today's workbook workflow.”

| Step | Expected | Evidence | Status |
| --- | --- | --- | --- |
| Accept build | A durable idempotent job is accepted and runs outside the request | Full release validator and job/outbox suites pass | ✓ VERIFIED |
| Collect and publish | D1/D2/D3 evidence becomes one immutable provenance-rich snapshot | Representative publication tests pass, but the new-organization race commits mismatched source/Player IDs | ✗ FAILED |
| Rely on source of truth | Every downstream reader sees an authoritative identity consistent with its source row | PostgreSQL probe stored Player `99` and SourceRecord `42` with `mismatch_count=1` | ✗ FAILED |
| Preserve workbook workflow | Native workbook remains available and hosted users can run later reports | Native no-database XLSX passes; Fly-style acknowledgement returns 403 and the browser then blocks later submissions | ✗ FAILED |
| Measure successful downloads | Blob read/initiation produces one retry-safe acknowledged event | Local happy path passes; production proxy and definitive-4xx transitions fail | ✗ FAILED |
| Outcome | Auditable truth is reliable without breaking today's workbook workflow | Three deterministic blockers remain | ✗ FAILED |

## Goal Achievement

### Roadmap Success Criteria

| # | Truth | Status | Evidence |
| --- | --- | --- | --- |
| R1 | Representative D1/D2/D3 builds publish canonical immutable facts | ✓ VERIFIED | Goal-level and division publication tests pass in the full release validator. |
| R2 | Durable state/capacity prevent failed or half-written ready snapshots | ✗ FAILED | A concurrently created organization can commit a ready source identity inconsistent with the global Player. |
| R3 | Retries are idempotent and caches are schema/version aware | ✓ VERIFIED | Job/snapshot retry tests, cache-v3 rejection, coalescing, and force-refresh tests pass. |
| R4 | Numeric, identity, raw, and metric lineage are unambiguous | ✗ FAILED | Numeric/raw/metric paths pass, but authoritative identity is ambiguous under the reproduced tenant-creation race. |
| R5 | Existing selector/scraper/cache/basic workbook regression tests continue to pass | ✓ VERIFIED | Complete release validator exits 0, including catalog, workbook, cache, template, and discovery groups. |

### Round-Four and Prior Must-Have Truths

The previous report established 99/114 Phase 1 PLAN truths plus 1/5 roadmap truths. Re-verification quick-checked prior passes, fully re-tested the six previous gaps, and added the 24 truths from Plans 01-31 through 01-37.

| Scope | Verified | Total | Notes |
| --- | ---: | ---: | --- |
| Prior Plan 01-01 through 01-30 truths | 106 | 114 | Native workbook, cache, aggregate, and purge gaps close; authoritative identity and hosted download semantics remain open. |
| Plan 01-31 authoritative serialization | 1 | 3 | Named errors/order remain; mutation and publication truths fail for an uncommitted new organization. |
| Plan 01-32 cache contract | 3 | 3 | Format/parser/header/schema contract and coalescing verified. |
| Plan 01-33 native workbook and aggregates | 3 | 3 | No-database XLSX and authenticated_use-only aggregate verified. |
| Plan 01-34 protected acknowledgement | 3 | 4 | Preparation, forgery bounds, and shared-secret preflight pass; Fly HTTPS same-origin operation fails. |
| Plan 01-35 browser state machine | 2 | 3 | Ordering and successful completion pass; definitive acknowledgement failure does not reset. |
| Plan 01-36 purge certification | 4 | 4 | Shared advisory lock, monotonic heartbeat, SKIP LOCKED, and fail-closed readiness verified. |
| Plan 01-37 release gate/runbook | 3 | 4 | Ordered groups/lifecycle/runbook pass; committed-range diff coverage and new blocker counterexamples are absent. |
| Roadmap success criteria | 3 | 5 | R2 and R4 fail on authoritative identity. |

**Score:** 128/143 must-haves verified.

## Original Gap Re-test

| Original gap | Result | Evidence |
| --- | --- | --- |
| Authoritative identity write skew | ⚠ PARTIAL | Original existing-organization source/Player, reassignment, and publication schedules pass; a stronger new-organization schedule still commits mismatch. |
| Database-optional native workbook | ✓ CLOSED | `NativeWorkbookDatabaseOptionalTests` returns a readable Hitters/Pitchers XLSX without DATABASE_URL. |
| authenticated_use-only unique users | ✓ CLOSED | Actor-bearing download-only and organization-scoped counterexamples pass. |
| Contract-bound legacy NCAA cache | ✓ CLOSED | Version-2 and individually mismatched contract payloads recollect; current payload reuses; coalescing/refresh pass. |
| Receipt/retry download semantics | ✓ ORIGINAL GAP CLOSED, NEW BLOCKERS | Preparation writes no event; blob/initiation precede ack; ambiguous retry preserves UUID. Fly origin and definitive 4xx handling remain broken. |
| Serialized monotonic purge heartbeat | ✓ CLOSED | Real two-connection insert/finalization and out-of-order completion tests pass. |

Focused execution: 50 Python counterexamples passed in 17.968 seconds; 8 Node browser-state tests passed.

## Required Artifacts

All 19 artifacts declared by Plans 01-31 through 01-37 exist and pass structural artifact checks. Substance and wiring results differ where noted.

| Artifact | Expected | Status | Details |
| --- | --- | --- | --- |
| `0013_authoritative_identity_serialization.py` | Shared authoritative serialization | ✗ HOLLOW FOR NEW TENANT | Substantive triggers/locks exist, but the Player-side common domain depends on visible organizations. |
| `roster_scout/snapshots.py` | Publication identity locking | ⚠ PARTIAL | Wired and ordered for visible snapshot identities; cannot repair the missing global Player domain. |
| `roster_scraper.py` | Cache-v3 semantic contract | ✓ VERIFIED | Exact four-field payload equality and contract-qualified cache/lock key. |
| `roster_scout/usage.py` | Exact aggregate semantics | ✓ VERIFIED | Downloads and authenticated unique users use separate event-type filters. |
| `app.py` | Native workbook and protected acknowledgement | ⚠ PARTIAL | Native/preparation/crypto/idempotency work; hosted external-origin resolution does not. |
| `static/download_tracking.js` | Retry-safe UUID state machine | ⚠ PARTIAL | Ambiguous retries work; definitive acknowledgement 4xx is indistinguishable from ambiguity; storage exceptions escape. |
| `templates/index.html` | Blob/initiation/ack wiring | ⚠ PARTIAL | Call order is correct; non-2xx ack classification and safe storage adapter are absent. |
| `roster_scout/evidence.py` | Serialized monotonic purge | ✓ VERIFIED | Same advisory key guards writer/finalization; SQL GREATEST and conditional count update are wired. |
| `scripts/validate_phase1_release.py` | Complete release gate | ⚠ PARTIAL | All planned groups run in order and pass; diff checks only the clean working tree and new adversarial blockers are absent. |
| Round-four test files and `DEPLOYMENT.md` | Executable contracts/runbook | ⚠ PARTIAL | Planned tests pass; reviewed schedules above remain uncovered. |

## Key Link Verification

| From | To | Via | Status | Details |
| --- | --- | --- | --- | --- |
| Source/Player/identity mutations | authoritative equality | snapshot → tenant Player → identity locks | ✗ NOT UNIVERSAL | New uncommitted organization produces no common tenant Player lock. |
| Publisher | authoritative mismatch query | ordered visible identity locks before validation | ⚠ PARTIAL | Correct for visible identities; vulnerable to the same missing global domain. |
| NCAA result cache key | cache payload validation | format/parser/header/schema contract | ✓ WIRED | Runtime-derived key and exact body equality. |
| Usage events | operator unique-user aggregate | authenticated_use plus non-null actor filter | ✓ WIRED | Global and organization windows share the rule. |
| `/scrape` | `/api/downloads/ack` | UUID/proof after successful XLSX build | ✓ WIRED | No event at preparation; no-store headers and proof bounds exist. |
| Public HTTPS browser | acknowledgement route | exact same-origin validation | ✗ NOT WIRED ON FLY | Internal HTTP scheme is compared to public HTTPS Origin. |
| Browser blob | download initiation → acknowledgement | one session UUID | ⚠ PARTIAL | Happy/ambiguous paths work; definitive acknowledgement failure never ends the attempt. |
| Evidence writer | purge final count/upsert | shared transaction advisory lock | ✓ WIRED | Real two-connection blocking test passes. |
| Purge upsert | heartbeat state | GREATEST plus timestamp-associated count | ✓ WIRED | Out-of-order test preserves newer timestamp/count. |
| Release validator | committed round-four changes | diff hygiene | ✗ NOT WIRED | `git diff --check` sees no committed change range. |

The generic key-link scanner cannot resolve route/behavior labels as file paths; the statuses above come from direct source tracing and executable probes.

## Data-Flow Trace (Level 4)

| Flow | Source | Produces real data | Status |
| --- | --- | --- | --- |
| Durable job → published snapshot | PostgreSQL report/job/section/source/fact rows | Yes | ⚠ FLOWING, but global Player identity can diverge under new-tenant race |
| Legacy NCAA cache → native workbook | Contract-v3 JSON players | Yes | ✓ FLOWING |
| authenticated_use → unique users | PostgreSQL ProductUsageEvent | Yes | ✓ FLOWING |
| Blob/initiation → successful download event | Browser UUID/proof → ack API → PostgreSQL | Yes locally | ✗ BROKEN at Fly proxy; 4xx retry state also blocks later flow |
| Evidence scan → readiness heartbeat | PostgreSQL evidence + purge state | Yes | ✓ FLOWING |

## Review-Finding Adjudication

| Finding | Verdict | Independent evidence |
| --- | --- | --- |
| CR-01 Fly HTTPS acknowledgement rejected | **CONFIRMED BLOCKER** | Flask test client with internal `http://roster-scout.fly.dev`, public `Origin: https://roster-scout.fly.dev`, and `X-Forwarded-Proto: https` returned 403 because app.py compares request.scheme directly. |
| CR-02 definitive acknowledgement 4xx traps browser | **CONFIRMED BLOCKER** | Node probe: first 403 left `needs_ack`; second intentional submission returned `pendingAcknowledgement=true`; workbook request count stayed 1. |
| CR-03 new organization bypasses Player lock | **CONFIRMED BLOCKER** | Real PostgreSQL probe committed both transactions and printed Player `99`, SourceRecord `42`, `mismatch_count: 1`. |
| WR-01 unavailable session storage breaks flow | **CONFIRMED WARNING** | Throwing-storage Node probe propagated `SecurityError`; no safe adapter exists. Top-level sessionStorage access can also throw before form wiring in restricted contexts. |
| WR-02 diff gate checks only clean tree | **CONFIRMED WARNING** | `git diff --name-only 8061e03..HEAD` listed 30 files while plain `git diff --check` inspected the clean working tree and returned no output. |

## Behavioral Spot-Checks

| Behavior | Command / probe | Result | Status |
| --- | --- | --- | --- |
| Round-four focused Python | 10 named Plan 31–36 test classes | 50 passed | ✓ PASS |
| Browser UUID state machine | `node --test tests/js/download_tracking.test.mjs` | 8 passed | ✓ PASS |
| Complete release validator | `DATABASE_URL=... .venv/bin/python scripts/validate_phase1_release.py` | Exit 0; Phase 1 release gate PASS | ✓ PASS, incomplete scenarios |
| Fly proxy origin | Internal HTTP request + public HTTPS Origin + forwarded proto | HTTP 403 | ✗ FAIL |
| Definitive ack 403 | Node two-submit state probe | Second submit blocked; same needs_ack retained | ✗ FAIL |
| Concurrent new-tenant identity | Two PostgreSQL transactions | Both committed; `42 != 99` | ✗ FAIL |
| Throwing session storage | Node tracker probe | `SecurityError` escaped | ⚠ FAIL |
| Release diff range | Compare `8061e03..HEAD` to plain diff check | 30 committed files; plain check sees none | ⚠ FAIL |

## Probe Execution

No PLAN-declared `probe-*.sh` files exist. The required release probe was run independently:

| Probe | Command | Result | Status |
| --- | --- | --- | --- |
| Phase 1 release validator | `DATABASE_URL=...roster_scout_test .venv/bin/python scripts/validate_phase1_release.py` | Story, head 0013, focused Python/Node, migration lifecycle/drift, clean discovery, 942-team catalog, Compose/deployment, workbook, compilation/templates, and current diff groups all passed | ✓ PASS |

## Requirements Coverage

| Requirement | Status | Evidence |
| --- | --- | --- |
| SNAP-01 | ✓ SATISFIED | Report ownership/opponent/division/season schema and D1/D2/D3 publication tests pass. |
| SNAP-02 | ✓ SATISFIED | Durable job/outbox/generation state and Browserless capacity gates pass. |
| SNAP-03 | ✗ BLOCKED | New-tenant concurrency can invalidate authoritative provenance behind a published immutable snapshot. |
| SNAP-04 | ✓ SATISFIED | Per-section status/count/source time/sanitized failure state remains green. |
| DATA-01 | ✗ BLOCKED | Stable NCAA ID exists, but its canonical Player association can diverge under the confirmed race. |
| DATA-02 | ✓ SATISFIED | Team/season/category/source URL/time provenance persists. |
| DATA-03 | ✗ BLOCKED | Batting/pitching facts can point through an identity whose Player ID no longer matches authoritative source evidence; fielding remains Phase 3. |
| DATA-04 | ✓ SATISFIED | Transfer-aware PlayerTeamSeason model and tests pass. |
| DATA-05 | ✓ SATISFIED | Zero and unavailable remain distinct. |
| DATA-06 | ✓ SATISFIED | Innings are nullable integer outs with raw source values. |
| DATA-07 | ✗ BLOCKED | Raw values persist, but the authoritative audit association can be false after the reproduced race. |
| DATA-08 | ✓ SATISFIED | Immutable versioned metric definition/value lineage passes. |
| DATA-09 | ✓ SATISFIED | Composite tenant ownership constraints pass. |
| DATA-10 | ↪ DEFERRED | Exact-snapshot Excel and Power BI consumers are explicit Phase 4/5 work. |
| OPS-01 | ✓ SATISFIED | Bounded retries and shared Browserless capacity pass. |
| OPS-02 | ✓ SATISFIED | Durable sections and legacy result cache now use semantic version identities. |
| OPS-04 | ✓ SATISFIED | Revision 0013 fresh install, downgrade/re-upgrade, head, and drift checks pass. |
| OPS-05 | ✗ BLOCKED | Local regressions pass, but supported Fly acknowledgement and later report behavior lack coverage and fail direct probes. |

All 18 Phase 1 requirement IDs are claimed by phase plans; none are orphaned.

## Anti-Patterns Found

No unreferenced `TBD`, `FIXME`, or `XXX` markers were found in the round-four runtime files. UI `placeholder` matches in `templates/index.html` are ordinary input/CSS attributes, not implementation stubs. No override applies.

## Human Verification Required

None. The release blockers and warnings are deterministic source/data-flow defects reproduced without visual judgment or external customer accounts.

## Deferred Items

| Item | Addressed In | Evidence |
| --- | --- | --- |
| Authenticated producer wiring/account FK | Phase 2 | ROADMAP criterion 2.6 explicitly requires membership-authorized accepted report/job events and canonical account/organization IDs. |
| Fielding canonical identity | Phase 3 | Detailed NCAA Intelligence adds fielding facts and consistent identity. |
| Exact-snapshot Excel/Power BI consumption | Phases 4–5 | Detailed Excel and embedded dashboard goals own those consumers. |

## Gaps Summary

Five earlier gaps are closed and the original authoritative schedules are fixed, but Phase 1 still cannot pass. A visibility-dependent tenant Player lock leaves one real write-skew schedule, the supported Fly proxy rejects valid HTTPS acknowledgements, and the browser treats definitive acknowledgement rejection as indefinitely retryable. Storage-restricted browsers and the clean-tree-only diff gate are additional robustness/release-quality gaps.

The next command should be `$gsd-plan-phase 1 --gaps`; the structured frontmatter above is ready for gap planning.

---

_Verified: 2026-11-11T16:03:45Z_
_Verifier: the agent (gsd-verifier)_
