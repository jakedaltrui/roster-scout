---
phase: 03-detailed-ncaa-intelligence
verified: 2026-11-16T19:55:08Z
status: passed
score: 21/21 must-haves verified
overrides_applied: 0
re_verification:
  previous_status: gaps_found
  previous_score: 20/21
  gaps_closed:
    - "Authoritative Phase 3 release command exits zero after committed-range whitespace fix ff5ed24."
  gaps_remaining: []
  regressions: []
---

# Phase 3: Detailed NCAA Intelligence Verification Report

**Phase Goal:** As a baseball staff member, I want to open an opponent snapshot containing auditable season, fielding, derived, split, career, and game-level NCAA intelligence or an explicit unavailable state for every section, so that I can trust the report's detail and understand any missing data before using it to prepare for a series.
**Verified:** 2026-11-16T19:55:08Z
**Status:** passed
**Re-verification:** Yes — after gap closure in commit `ff5ed24`

## User Flow Coverage

| Step | Expected | Evidence | Status |
| --- | --- | --- | --- |
| Open opponent snapshot | Authenticated staff select an organization-owned report and published snapshot | `roster_scout/report_routes.py:861-890` authorizes the protected snapshot/player route from `g.authorization_context` | ✓ VERIFIED |
| Open one roster player | Report, snapshot, team-season, and stable player must form one exact tenant-owned chain | `roster_scout/player_detail.py:493-550`; foreign and mixed IDs passed the audited indistinguishable-404 test | ✓ VERIFIED |
| Review season intelligence | Batting, pitching, fielding, metrics, inputs, sample, and bounded source provenance are projected | `roster_scout/player_detail.py:38-96,163-237` | ✓ VERIFIED |
| Review split intelligence | Seven batting and seven pitching families expose facts or explicit status/reason and source identity | `roster_scout/ncaa_detail.py:31-68`; `roster_scout/player_detail.py:272-325` | ✓ VERIFIED |
| Review career and games | Transfer-aware seasons, separately labeled aggregate, and dated typed game facts share the same stable player | `roster_scout/player_detail.py:328-465` | ✓ VERIFIED |
| Understand missing data | Snapshot/section/scope/metric status distinguishes partial, unavailable, failed, missing, undefined, incompatible, and unavailable constants | `roster_scout/player_detail.py:468-490`; `roster_scout/metrics.py:51-82,345-395` | ✓ VERIFIED |
| Outcome | Staff can trust the stored detail and see why data is missing before use | Same-snapshot immutable projection, provenance, explicit completeness, and focused route/publication tests all passed | ✓ VERIFIED |

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
| --- | --- | --- | --- |
| 1 | Representative D1, D2, and D3 snapshots expose one consistent batting, pitching, fielding, and derived schema. | ✓ VERIFIED | `DivisionParityTests`, fielding parity, split fixtures, and real-collector D1/D2/D3 publication checks passed in the gate. |
| 2 | All seven batting and pitching split families are captured with season-specific source identity or explicit unavailability. | ✓ VERIFIED | Fourteen canonical codes at `ncaa_detail.py:31-40`; fixture manifests bind season `17040`, division/team/category, identifier, checksum, and capture time; all split discovery/availability/publication tests passed. |
| 3 | Player detail links season facts, transfer-aware career seasons, separate aggregates, and dated typed game logs under one stable identity. | ✓ VERIFIED | `player_detail.py:328-465`; career, aggregate, game-log, drill-path, and protected-route suites passed. |
| 4 | wOBA/FIP use verified versioned NCAA season/division inputs or return explanatory unavailable; no MLB constants are substituted. | ✓ VERIFIED | `metrics.py:179-197,245-255,323-374`; database starts with no constants and exact verified-scope/immutability tests passed. |
| 5 | Collection respects request budgets, bounded concurrency, caching, retry/backoff, hard deadlines, and safe diagnostics. | ✓ VERIFIED | `RequestLedger` constraints at `models.py:2039-2127`; 132 focused tests passed including atomic concurrency, retry generations, cache reuse, deadlines, capacity, operator output, and failed `power_bi` records. |
| 6 | One authoritative safe release command proves the complete phase and exits zero. | ✓ VERIFIED | After `ff5ed24`, the safe loopback `_test` gate exited 0: migration lifecycle/preservation, 132 focused Phase 3 tests, inherited 83 Python/37 Node checks, two 774-test full Python runs, full browser suite, catalog/deployment groups, compilation, and diff hygiene all passed. |

**Score:** 21/21 must-haves verified — all 20 Phase 3 requirements and the authoritative release-command must-have pass.

## Required Artifacts

| Artifact | Expected | Status | Details |
| --- | --- | --- | --- |
| `roster_scout/metrics.py` | One Decimal formula registry | ✓ VERIFIED | `MetricResult`, `calculate_metric`, and `aggregate_compatible_counts` are substantive at lines 14, 345, and 398. The SDK's export-list parser produced a false negative because the PLAN uses inline YAML list syntax; direct inspection and execution verify all exports. |
| `roster_scout/ncaa_detail.py` | Fielding, split, schedule, history, and source contracts | ✓ VERIFIED | Fixed NCAA host/ID paths, exact parser signatures, explicit absence, and typed results are implemented and tested. |
| `roster_scout/scopes.py` | Exact last-10/conference selection and count-first aggregation | ✓ VERIFIED | Deterministic membership and same-season conference evidence at lines 71-220. |
| `roster_scout/player_detail.py` | Protected same-snapshot detail projection | ✓ VERIFIED | Fixed view allowlist, tenant/snapshot predicates, career aggregate, and exact Game joins are implemented. |
| `roster_scout/models.py` | Typed, tenant-owned, immutable Phase 3 schema | ✓ VERIFIED | Fielding, Game, scopes, career, logs, constants, metrics, ledgers, and external diagnostics exist with composite ownership. |
| `roster_scout/jobs.py` | Evidence-first persistence, budgets, cache/live settlement, publication | ✓ VERIFIED | `_persist_canonical()` and request-ledger settlement are wired into normal report execution. |
| `roster_scout/snapshots.py` | Publication parity, ownership, completeness, and immutability validation | ✓ VERIFIED | Normal collector publication and negative lineage/parity tests passed. |
| `migrations/versions/0025_detailed_ncaa_intelligence.py` | Additive/reversible Phase 3 boundary | ✓ VERIFIED | Direct run proved 0024 preservation, 0025 downgrade/re-upgrade, fresh head, and no model drift. |
| D1/D2/D3 fixture bundles | Current sanitized fielding/split/history/schedule evidence | ✓ VERIFIED | Every required fixture exists; split manifests have 14 per-division observations with season/type/category/path/checksum provenance. |
| `scripts/inspect_report_jobs.py` | Read-only sanitized operations projection | ✓ VERIFIED | Stuck-job, section, budget/capacity, and failed `power_bi` projection tests passed. |
| `scripts/validate_phase3_release.py` | Safe authoritative release gate | ✓ VERIFIED | Safe loopback `_test` execution exited 0 after proving migrations, focused and inherited suites, full discovery, browser tests, catalog/deployment groups, compilation, and diff hygiene. |

## Key Link Verification

| From | To | Via | Status | Details |
| --- | --- | --- | --- | --- |
| `canonical.py` | `metrics.py` | Expanded count maps to shared registry | ✓ WIRED | SDK pattern check passed; golden and publication tests execute the link. |
| `jobs.py` | `snapshots.py` | Persist lineage/evidence before atomic publication | ✓ WIRED | Normal report-build and publication-rejection tests passed. |
| `roster_scraper.py` | `ncaa_detail.py` | Existing bounded Browserless collection seam | ✓ WIRED | Real collector and deadline-boundary tests passed. |
| `ncaa_detail.py` | D1/D2/D3 fixtures | Per-season/type discovery and parser signatures | ✓ WIRED | Stale season/type/label/header/player-link mutations are rejected. |
| `jobs.py` | `metrics.py` | Split/scoped/career compatible counts through one registry | ✓ WIRED | Count-first aggregate and exact-sample tests passed. |
| `ncaa_detail.py` | `scopes.py` | Schedule/conference evidence to pure selectors | ✓ WIRED | Doubleheader, cancellation, realignment, and incomplete-evidence tests passed. |
| `report_routes.py` | `player_detail.py` | Server-derived organization plus report/snapshot/player filters | ✓ WIRED | Authorized success and foreign/mixed audited 404 tests passed. |
| `jobs.py` | `RequestLedger` | Reserve and settle planned/cache/live work | ✓ WIRED | Atomic concurrency, retry-generation, cache, budget, and terminalization tests passed. |

## Data-Flow Trace (Level 4)

| Artifact | Data | Source | Produces Real Data | Status |
| --- | --- | --- | --- | --- |
| Season detail | Typed season facts and metric lineage | Persisted `BattingFact`, `PitchingFact`, `FieldingFact`, `MetricValue` for the exact published snapshot | Yes; normal report-build integration test queries published rows | ✓ FLOWING |
| Splits | Scope status/facts/metrics | Discovered NCAA controls/results → exact source evidence → `StatisticScope` and scoped facts | Yes, or an explicit zero-fact unavailable scope | ✓ FLOWING |
| Career | Season rows and separate aggregate | Stable NCAA player page → `CareerSeasonFact` and `CareerAggregate` membership | Yes; transfer and compatibility tests passed | ✓ FLOWING |
| Games | Dated typed facts | Schedule-derived canonical `Game` → batting/pitching/fielding game-log rows | Yes; exact contest and current-season tests passed | ✓ FLOWING |
| Last-10/conference | Exact membership and metrics | Audited schedule/conference IDs → `StatisticScopeGame` → count-first formulas | Yes; deterministic selection and publication tests passed | ✓ FLOWING |
| Operations | Budget/cache/live/capacity and refresh metadata | Durable `RequestLedger` and `ExternalRefreshDiagnostic` rows | Yes; seeded failed `power_bi` projection passed | ✓ FLOWING |

## Behavioral Spot-Checks

| Behavior | Command/check | Result | Status |
| --- | --- | --- | --- |
| Safe PostgreSQL readiness | `prepare_phase3_test_database.py --ensure` with documented loopback `_test` URL | Exit 0 | ✓ PASS |
| Migration preservation/lifecycle | Authoritative gate migration group | 2 tests passed; head `0025`; no drift | ✓ PASS |
| Phase 3 functional/security contract | Authoritative gate focused group | 132 tests passed | ✓ PASS |
| Prior customer/security behavior | Inherited Phase 2.1 gate | 83 Python tests and 37 Node tests passed; clean discovery/catalog/deployment/compile stages completed | ✓ PASS |
| Release-range hygiene | `git diff --check fa18b73e5463da4d7e208547dbf4e7f9bc21b06c..HEAD --` and `git diff --check` | Exit 0 after `ff5ed24` | ✓ PASS |
| Authoritative Phase 3 gate | `uv run python scripts/validate_phase3_release.py` with documented loopback `_test` PostgreSQL URL and destructive-test sentinel | Exit 0; final output: `Phase 3 release gate passed.` | ✓ PASS |
| Production unchanged | Gate only targets loopback `_test`; no deploy command exists in manifest | No production/deployment action performed | ✓ PASS |

## Probe Execution

No `scripts/*/tests/probe-*.sh` probes are declared for Phase 3. The explicitly declared executable authority is `scripts/validate_phase3_release.py`; its safe loopback `_test` execution passed with exit code 0.

## Requirements Coverage

| Requirement | Source Plan(s) | Status | Evidence |
| --- | --- | --- | --- |
| STAT-01 | 03-01, 03-06 | ✓ SATISFIED | 23-code metric registry and published hitter facts/metrics; `HitterMetricTests` and full-season publication passed. |
| STAT-02 | 03-01, 03-06 | ✓ SATISFIED | Pitching counts/outs and requested rates/FIP contract; `PitcherMetricTests` and publication passed. |
| STAT-03 | 03-02, 03-06 | ✓ SATISFIED | G/GS/PO/A/E/TC/DP/FPCT plus nullable PB/SBA/CSB; D1/D2/D3 fielding and publication passed. |
| STAT-04 | 03-01, 03-06 | ✓ SATISFIED | Positive-denominator zero, missing, undefined, and incompatible states remain distinct; no infinity/misleading zero. |
| STAT-05 | 03-01 | ✓ SATISFIED | wOBA requires exact verified NCAA season/division weights; otherwise `unavailable_constant`. |
| STAT-06 | 03-01 | ✓ SATISFIED | FIP requires exact verified NCAA season/division constant; otherwise `unavailable_constant`. |
| STAT-07 | 03-01, 03-06 | ✓ SATISFIED | Formula/version, exact inputs, sample, status/reason, and constant lineage persist. |
| STAT-08 | 03-02, 03-03, 03-08 | ✓ SATISFIED | Representative D1/D2/D3 parity test passed over fields/scopes/history/status vocabulary. |
| SPLT-01 | 03-04 | ✓ SATISFIED | Exactly seven batting split codes and source-backed facts/unavailability passed. |
| SPLT-02 | 03-04 | ✓ SATISFIED | Exactly seven pitching split codes and source-backed facts/unavailability passed. |
| SPLT-03 | 03-04 | ✓ SATISFIED | Scope persists code, label, type, source identifier/path/URL, status, time, versions, and checksum. |
| SPLT-04 | 03-03, 03-04 | ✓ SATISFIED | Per-season/type discovery; production hard-code scan and stale/wrong candidate tests passed. |
| SPLT-05 | 03-04 | ✓ SATISFIED | Unavailable scopes contain no facts or synthetic metric zero. |
| HIST-01 | 03-05 | ✓ SATISFIED | Stable-player career rows retain season, school, team ID, division, type, counts, and evidence. |
| HIST-02 | 03-05 | ✓ SATISFIED | Career aggregate is separately labeled with exact included/excluded rows and count-first recomputation. |
| HIST-03 | 03-05, 03-06 | ✓ SATISFIED | Current-season dated Game linkage and typed batting/pitching/fielding logs passed. |
| HIST-04 | 03-05, 03-07 | ✓ SATISFIED | ID-less, missing team/division, ambiguous game, deadline, and budget cases disclose completeness. |
| HIST-05 | 03-07 | ✓ SATISFIED | Bounded defaults/overrides, shared capacity, durable budget, cache, retry generations, and hard deadlines passed. |
| HIST-06 | 03-04, 03-09 | ✓ SATISFIED | Protected same-snapshot season/splits/career/games JSON drill and non-enumerating denial passed. |
| OPS-03 | 03-07, 03-08 | ✓ SATISFIED | Metadata-only operator output identifies stuck/unavailable/exhausted states and failed `power_bi` attempts without customer data. |

No Phase 3 requirement is orphaned: all 20 IDs appear in plan frontmatter and have executable evidence.

## Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
| --- | --- | --- | --- | --- |
| None | — | No blocking anti-pattern remains | — | Commit `ff5ed24` removed the only trailing-whitespace finding; committed-range and working-tree diff hygiene both pass. |

No `TBD`, `FIXME`, or `XXX` debt markers were found in Phase 3 implementation, migration, scripts, or focused tests. Two empty-list returns in the capture script are fail-closed parser outcomes that callers validate; they are not stubs.

## Human Verification Required

None. The phase has `UI hint: no`, and every planned behavior has deterministic route, database, fixture, security, and release-contract coverage. Phase 4 owns customer-facing workbook presentation.

## Disconfirmation Pass

- **Partial requirement search:** no functional Phase 3 requirement or release must-have remains partial.
- **Potentially misleading test search:** several historical tests retain “RED sentinel” docstrings, but their current assertions execute implemented contracts and passed; no vacuous skip was observed.
- **Uncovered error-path search:** direct focused execution covered malformed source tables, stale IDs, missing stable IDs, ambiguous games, missing constants, budget/capacity/deadline exhaustion, cross-tenant substitution, service failure, and failed external refresh diagnostics. No additional observable untested Phase 3 error path was found in the reviewed scope.

## Gaps Summary

All twenty product requirements, all five roadmap success criteria, and the authoritative release-command must-have are implemented and executable. Commit `ff5ed24` closed the sole prior hygiene gap; the complete safe Phase 3 gate now exits zero, and no human-verification item remains.

---

_Verified: 2026-11-16T19:55:08Z_
_Verifier: the agent (gsd-verifier)_
