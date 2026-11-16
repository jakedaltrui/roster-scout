---
phase: 03
slug: detailed-ncaa-intelligence
status: approved
nyquist_compliant: true
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
| **Quick run command** | `uv run python -m unittest tests.test_detailed_stats tests.test_ncaa_detail tests.test_scoped_stats tests.test_player_history tests.test_report_library.SnapshotPlayerDetailRouteTests -v` |
| **Full suite command** | `uv run python -m unittest discover -s tests -v` |
| **Phase gate command** | `uv run python scripts/prepare_phase3_test_database.py --ensure && uv run python scripts/validate_phase3_release.py` |
| **Estimated runtime** | Quick target: under 120 seconds; full/release target: under 15 minutes |

---

## Sampling Rate

- **After every task commit:** Run the focused test class for that task plus `tests.test_canonical_data`
- **After every plan wave:** Run the four Phase 3 focused modules plus `tests.test_report_jobs`, `tests.test_snapshot_persistence`, and `tests.test_ncaa_workflow`
- **Before `$gsd-verify-work`:** Run the disposable-PostgreSQL release script, full unittest discovery, catalog validation, root `CONTEXT.md` compile checks, and `git diff --check`
- **Max feedback latency:** 120 seconds for focused checks; long-running release checks run only at wave and phase gates

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 03-01-03 | 01 | 1 | STAT-01 | T-03-01-01/02/04 | Hitter facts and metrics persist with inputs/sample/definition lineage through a published report build | unit + job + DB integration | `uv run python -m unittest tests.test_report_jobs.FullSeasonMetricPublicationTests tests.test_detailed_stats.HitterMetricTests -v` | ❌ W0 | ⬜ pending |
| 03-01-03 | 01 | 1 | STAT-02 | T-03-01-01/02/04 | Pitcher facts and metrics persist with inputs/sample/definition lineage through a published report build | unit + job + DB integration | `uv run python -m unittest tests.test_report_jobs.FullSeasonMetricPublicationTests tests.test_detailed_stats.PitcherMetricTests -v` | ❌ W0 | ⬜ pending |
| 03-02-02 | 02 | 2 | STAT-03 | T-03-02-01/03 | Untrusted fielding/catcher HTML is validated before tenant-bound persistence | parser + DB integration | `uv run python -m unittest tests.test_ncaa_detail.FieldingTests -v` | ❌ W0 | ⬜ pending |
| 03-01-02 | 01 | 1 | STAT-04 | T-03-01-01 | Zero, missing, undefined, and incompatible states never collapse | unit/property table | `uv run python -m unittest tests.test_detailed_stats.DenominatorSemanticsTests -v` | ❌ W0 | ⬜ pending |
| 03-01-02 | 01 | 1 | STAT-05 | T-03-01-03 | wOBA remains unavailable without versioned verified NCAA weights | unit + DB integration | `uv run python -m unittest tests.test_detailed_stats.ConstantMetricTests.test_woba -v` | ❌ W0 | ⬜ pending |
| 03-01-02 | 01 | 1 | STAT-06 | T-03-01-03 | FIP remains unavailable without a versioned verified NCAA constant | unit + DB integration | `uv run python -m unittest tests.test_detailed_stats.ConstantMetricTests.test_fip -v` | ❌ W0 | ⬜ pending |
| 03-01-03 | 01 | 1 | STAT-07 | T-03-01-03/04 | Method version, exact inputs/sample/source, and verified constant-set lineage persist immutably | job + DB integration | `uv run python -m unittest tests.test_report_jobs.FullSeasonMetricPublicationTests tests.test_detailed_stats.MethodologyPersistenceTests -v` | ❌ W0 | ⬜ pending |
| 03-08-03 | 08 | 9 | STAT-08 | T-03-08-01/02 | D1/D2/D3 share one schema, status vocabulary, and isolation contract | fixture + release contract | `uv run python -m unittest tests.test_phase3_release.DivisionParityTests -v` | ❌ W0 | ⬜ pending |
| 03-04-02 | 04 | 4 | SPLT-01 | T-03-04-01/02 | Seven batting split codes use discovered source identity and typed ownership | parser + canonical fixture | `uv run python -m unittest tests.test_ncaa_detail.BattingSplitTests -v` | ❌ W0 | ⬜ pending |
| 03-04-02 | 04 | 4 | SPLT-02 | T-03-04-01/02 | Seven pitching split codes publish exact facts or explicit unavailability | parser + canonical fixture | `uv run python -m unittest tests.test_ncaa_detail.PitchingSplitTests -v` | ❌ W0 | ⬜ pending |
| 03-04-02 | 04 | 4 | SPLT-03 | T-03-04-02/03 | Split code/label/type/source/status are immutable and tenant-bound | DB integration | `uv run python -m unittest tests.test_ncaa_detail.SplitProvenanceTests -v` | ❌ W0 | ⬜ pending |
| 03-03-03 | 03 | 3 | SPLT-04 | T-03-03-02 | Per-season/type control plus result-grid discovery rejects stale/mismatched identifiers and records explicit source absence | live capture + parser contract | `uv run python -m unittest tests.test_ncaa_detail.SplitDiscoveryTests.test_certified_result_grid_contract -v` | ❌ W0 | ⬜ pending |
| 03-04-03 | 04 | 4 | SPLT-05 | T-03-04-03 | Missing split is an unavailable/partial scope with no synthetic zero facts | unit + DB integration | `uv run python -m unittest tests.test_ncaa_detail.SplitAvailabilityTests -v` | ❌ W0 | ⬜ pending |
| 03-05-02 | 05 | 6 | HIST-01 | T-03-05-01/02 | Transfer history preserves exact school/team/division/season/source | parser + DB integration | `uv run python -m unittest tests.test_player_history.CareerSeasonTests -v` | ❌ W0 | ⬜ pending |
| 03-05-02 | 05 | 6 | HIST-02 | T-03-05-05 | Career aggregate includes only exact compatible available rows | unit + DB integration | `uv run python -m unittest tests.test_player_history.CareerAggregateTests -v` | ❌ W0 | ⬜ pending |
| 03-05-02 | 05 | 6 | HIST-03 | T-03-05-02/03 | Current-season typed game logs link through the one schedule-derived canonical Game | parser + DB integration | `uv run python -m unittest tests.test_player_history.GameLogTests -v` | ❌ W0 | ⬜ pending |
| 03-05-02 | 05 | 6 | HIST-04 | T-03-05-05 | Missing history/games disclose exact section completeness | unit + DB integration | `uv run python -m unittest tests.test_player_history.HistoryCompletenessTests -v` | ❌ W0 | ⬜ pending |
| 03-07-02 | 07 | 8 | HIST-05 | T-03-07-01/04 | One durable ledger bounds cache/live requests, concurrency, retry, and budget | job integration | `uv run python -m unittest tests.test_report_jobs tests.test_player_history.CollectionBudgetTests -v` | Existing base + ❌ W0 detail | ⬜ pending |
| 03-09-02 | 09 | 7 | HIST-06 | T-03-09-01/02/04 | Protected JSON drill paths authorize report/snapshot/player and resolve immutable season/split/career/game views | route + DB integration | `uv run python -m unittest tests.test_report_library.SnapshotPlayerDetailRouteTests tests.test_player_history.SnapshotDrillPathTests -v` | ❌ W0 | ⬜ pending |
| 03-07-03 | 07 | 8 | OPS-03 | T-03-07-02/05 | Diagnostics expose safe lifecycle/budget/capacity and seeded failed-Power-BI attempt metadata without customer data | unit + integration | `uv run python -m unittest tests.test_phase3_release.OperatorDiagnosticsTests tests.test_phase3_release.ExternalRefreshDiagnosticTests -v` | ❌ W0 | ⬜ pending |

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

- [ ] `tests/fixtures/ncaa_detail/{d1,d2,d3}/` — sanitized current-layout team fielding, split controls plus batting/pitching result-or-absence evidence, player career/game logs, and schedule pages
- [ ] `scripts/capture_phase3_ncaa_fixtures.py --preflight` — secret-safe configured Browserless endpoint/token readiness with fixed `phase3_browserless_unavailable`
- [ ] `scripts/prepare_phase3_test_database.py --ensure` — executor-owned `_test` PostgreSQL bootstrap/readiness with migration permission and fixed `phase3_test_database_unavailable`
- [ ] `tests/test_detailed_stats.py` — formula table, input lineage, constants, and zero/missing/undefined semantics
- [ ] `tests/test_ncaa_detail.py` — parser/source contracts and all split availability states
- [ ] `tests/test_scoped_stats.py` — exact last-10 and conference membership plus aggregation
- [ ] `tests/test_player_history.py` — stable-ID fan-out, transfers, game links, and collection budgets
- [ ] `tests/test_report_library.py::SnapshotPlayerDetailRouteTests` — protected same-snapshot customer JSON drill contract and cross-organization denial
- [ ] `tests/test_phase3_release.py` and `scripts/validate_phase3_release.py` — D1/D2/D3, constant-set, external refresh diagnostics, tenancy, immutability, and operator gates
- [ ] Migration fixture proving upgrade from Alembic head `0024` preserves all published Phase 1/2 snapshots unchanged

---

## Manual-Only Verifications

All phase behaviors have automated verification. Plan 03-03 runs the live D1/D2/D3 fixture capture as a blocking automated source-discovery command before split implementation.

---

## Validation Sign-Off

- [x] Every planned task has an automated verify command or explicit Wave 0 dependency
- [x] Sampling continuity: no three consecutive tasks without automated verification
- [x] Wave 0 plans cover every missing test and fixture reference
- [x] No watch-mode flags
- [x] Focused feedback latency target remains under 120 seconds
- [x] Every Phase 3 requirement is represented in a plan frontmatter `requirements` field
- [x] `nyquist_compliant: true` is set after final plan-to-test mapping

**Approval:** approved for execution 2026-11-15; `wave_0_complete` remains false until execution creates and greens the listed assets
