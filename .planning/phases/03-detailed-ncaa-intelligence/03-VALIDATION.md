---
phase: 03
slug: detailed-ncaa-intelligence
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-11-15
---

# Phase 03 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | Python stdlib `unittest`; PostgreSQL integration tests through the existing session fixtures |
| **Config file** | None; tests are discovered from `tests/` |
| **Quick run command** | `uv run python -m unittest tests.test_detailed_stats tests.test_ncaa_detail tests.test_scoped_stats tests.test_player_history -v` |
| **Full suite command** | `uv run python -m unittest discover -s tests -v` |
| **Phase gate command** | `PHASE2_DESTRUCTIVE_DATABASE=local-disposable DATABASE_URL=postgresql+psycopg://..._test uv run python scripts/validate_phase3_release.py` |
| **Estimated runtime** | Quick target: under 120 seconds; full/release target: under 15 minutes |

---

## Sampling Rate

- **After every task commit:** Run the focused test class for that task plus `tests.test_canonical_data`
- **After every plan wave:** Run the four Phase 3 focused modules plus `tests.test_report_jobs`, `tests.test_snapshot_persistence`, and `tests.test_ncaa_workflow`
- **Before `$gsd-verify-work`:** Run the disposable-PostgreSQL release script, full unittest discovery, catalog validation, root `CONTEXT.md` compile checks, and `git diff --check`
- **Max feedback latency:** 120 seconds for focused checks; long-running release checks run only at wave and phase gates

---

## Requirement Verification Map

Plan/task and threat references are assigned once the executable plans are written. Every requirement below must be mapped to at least one concrete task before execution begins.

| Requirement | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|-------------|-----------------|-----------|-------------------|-------------|--------|
| STAT-01 | Metric inputs remain snapshot- and tenant-owned | unit + DB integration | `uv run python -m unittest tests.test_detailed_stats.HitterMetricTests -v` | ❌ W0 | ⬜ pending |
| STAT-02 | Metric inputs remain snapshot- and tenant-owned | unit + DB integration | `uv run python -m unittest tests.test_detailed_stats.PitcherMetricTests -v` | ❌ W0 | ⬜ pending |
| STAT-03 | Untrusted fielding/catcher HTML is validated before persistence | parser + DB integration | `uv run python -m unittest tests.test_ncaa_detail.FieldingTests -v` | ❌ W0 | ⬜ pending |
| STAT-04 | Zero, missing, and undefined values never collapse into one state | unit/property table | `uv run python -m unittest tests.test_detailed_stats.DenominatorSemanticsTests -v` | ❌ W0 | ⬜ pending |
| STAT-05 | wOBA is unavailable without verified season/division constants | unit + DB integration | `uv run python -m unittest tests.test_detailed_stats.ConstantMetricTests.test_woba -v` | ❌ W0 | ⬜ pending |
| STAT-06 | FIP is unavailable without verified season/division constants | unit + DB integration | `uv run python -m unittest tests.test_detailed_stats.ConstantMetricTests.test_fip -v` | ❌ W0 | ⬜ pending |
| STAT-07 | Method version, exact inputs, samples, and source evidence are immutable | DB integration | `uv run python -m unittest tests.test_detailed_stats.MethodologyPersistenceTests -v` | ❌ W0 | ⬜ pending |
| STAT-08 | D1/D2/D3 use one validated schema and availability vocabulary | fixture contract | `uv run python -m unittest tests.test_phase3_release.DivisionParityTests -v` | ❌ W0 | ⬜ pending |
| SPLT-01 | Batting split identifiers and labels are discovered and contract-checked | parser + canonical fixture | `uv run python -m unittest tests.test_ncaa_detail.BattingSplitTests -v` | ❌ W0 | ⬜ pending |
| SPLT-02 | Pitching splits publish exact facts or explicit unavailability | parser + canonical fixture | `uv run python -m unittest tests.test_ncaa_detail.PitchingSplitTests -v` | ❌ W0 | ⬜ pending |
| SPLT-03 | Split code, label, type, source, and status are immutable and tenant-bound | DB integration | `uv run python -m unittest tests.test_ncaa_detail.SplitProvenanceTests -v` | ❌ W0 | ⬜ pending |
| SPLT-04 | Stale or wrong-season split identifiers are rejected | parser contract | `uv run python -m unittest tests.test_ncaa_detail.SplitDiscoveryTests -v` | ❌ W0 | ⬜ pending |
| SPLT-05 | Missing splits are unavailable or partial, never fabricated as zero | unit + DB integration | `uv run python -m unittest tests.test_ncaa_detail.SplitAvailabilityTests -v` | ❌ W0 | ⬜ pending |
| HIST-01 | Transfer history preserves exact school, team, division, season, and source | parser + DB integration | `uv run python -m unittest tests.test_player_history.CareerSeasonTests -v` | ❌ W0 | ⬜ pending |
| HIST-02 | Career aggregates include only compatible available rows | unit | `uv run python -m unittest tests.test_player_history.CareerAggregateTests -v` | ❌ W0 | ⬜ pending |
| HIST-03 | Current-season typed game logs are linked to the stable player identity | parser + DB integration | `uv run python -m unittest tests.test_player_history.GameLogTests -v` | ❌ W0 | ⬜ pending |
| HIST-04 | Missing history or games disclose exact completeness state | unit + DB integration | `uv run python -m unittest tests.test_player_history.HistoryCompletenessTests -v` | ❌ W0 | ⬜ pending |
| HIST-05 | Request ledger, cache, concurrency, retry, and budget controls prevent unbounded collection | job integration | `uv run python -m unittest tests.test_report_jobs tests.test_player_history.CollectionBudgetTests -v` | Partial + ❌ W0 detail | ⬜ pending |
| HIST-06 | Every drill path is tenant-isolated and resolves within one immutable snapshot | DB integration | `uv run python -m unittest tests.test_player_history.SnapshotDrillPathTests -v` | ❌ W0 | ⬜ pending |
| OPS-03 | Diagnostics remain sanitized while exposing actionable stuck/failure/capacity state | unit + integration | `uv run python -m unittest tests.test_phase3_release.OperatorDiagnosticsTests -v` | ❌ W0 | ⬜ pending |

### Required Edge-Case Matrix

| Case | Expected assertion |
|------|--------------------|
| D1, D2, D3 representative pages | Same canonical columns, scope codes, metric definitions, and availability vocabulary |
| Two-way transfer | One global stable player; distinct team-season rows; typed role facts never collapse |
| Fewer than ten completed games | Complete last-10 scope with actual count 0–9 only when the full schedule set is known |
| Completed doubleheader | Two exact game IDs and two membership rows |
| Cancellation, postponement, or no-contest | Excluded from completed-game membership |
| Conference realignment | Classification uses report-season evidence, not current affiliation |
| Missing opponent or conference ID | Conference scope partial/unavailable, never guessed |
| Valid zero numerator | Numeric zero only when the denominator is positive |
| Zero denominator | `undefined_denominator`, never infinity or zero |
| Missing input | `missing_input`, distinct from zero |
| Missing wOBA/FIP constant | `unavailable_constant` with season/division context |
| Split ID drift or wrong header | Source identifier rejected; split unavailable/failed with context |
| Budget exhausted mid-player family | Core can publish; supplemental section is partial with exact completed/remaining counts |
| Same-build retry | Reuses only version- and source-matched completed evidence |
| Explicit refresh | Performs live source collection and creates a successor snapshot |
| Cross-organization substitution | Every new fact, scope, game, and source foreign key rejects foreign snapshot ownership |

---

## Wave 0 Requirements

- [ ] `tests/fixtures/ncaa_detail/{d1,d2,d3}/` — sanitized current-layout team fielding, split discovery/results, player career/game logs, and schedule pages
- [ ] `tests/test_detailed_stats.py` — formula table, input lineage, constants, and zero/missing/undefined semantics
- [ ] `tests/test_ncaa_detail.py` — parser/source contracts and all split availability states
- [ ] `tests/test_scoped_stats.py` — exact last-10 and conference membership plus aggregation
- [ ] `tests/test_player_history.py` — stable-ID fan-out, transfers, game links, and collection budgets
- [ ] `tests/test_phase3_release.py` and `scripts/validate_phase3_release.py` — D1/D2/D3, tenancy, immutability, and operator gates
- [ ] Migration fixture proving upgrade from Alembic head `0024` preserves all published Phase 1/2 snapshots unchanged

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Live NCAA split-ID discovery | SPLT-04 | Live identifiers are upstream-owned and may drift independently of fixtures | Run the approved live capture against one D1, D2, and D3 team; confirm discovered identifiers resolve to the expected typed header and store sanitized fixtures before enabling the parser contract |

All persisted behavior, metric semantics, scope membership, tenancy, and release gates otherwise require automated verification.

---

## Validation Sign-Off

- [ ] Every planned task has an automated verify command or explicit Wave 0 dependency
- [ ] Sampling continuity: no three consecutive tasks without automated verification
- [ ] Wave 0 covers every missing test and fixture reference
- [ ] No watch-mode flags
- [ ] Focused feedback latency remains under 120 seconds
- [ ] Every Phase 3 requirement is represented in a plan frontmatter `requirements` field
- [ ] `nyquist_compliant: true` is set after final plan-to-test mapping

**Approval:** pending executable-plan mapping
