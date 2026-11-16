---
phase: 03-detailed-ncaa-intelligence
reviewed: 2026-11-16T11:15:51Z
depth: standard
files_reviewed: 48
files_reviewed_list:
  - migrations/versions/0025_detailed_ncaa_intelligence.py
  - roster_scout/canonical.py
  - roster_scout/enums.py
  - roster_scout/jobs.py
  - roster_scout/metrics.py
  - roster_scout/models.py
  - roster_scout/ncaa_detail.py
  - roster_scout/player_detail.py
  - roster_scout/report_routes.py
  - roster_scout/scopes.py
  - roster_scout/snapshots.py
  - scripts/capture_phase3_ncaa_fixtures.py
  - scripts/inspect_report_jobs.py
  - scripts/prepare_phase3_test_database.py
  - scripts/validate_phase3_release.py
  - tests/fixtures/ncaa_detail/d1/batting_split_result.html
  - tests/fixtures/ncaa_detail/d1/fielding.html
  - tests/fixtures/ncaa_detail/d1/fixture_manifest.json
  - tests/fixtures/ncaa_detail/d1/pitching_split_result.html
  - tests/fixtures/ncaa_detail/d1/player_detail.html
  - tests/fixtures/ncaa_detail/d1/schedule.html
  - tests/fixtures/ncaa_detail/d1/split_controls.html
  - tests/fixtures/ncaa_detail/d2/batting_split_result.html
  - tests/fixtures/ncaa_detail/d2/fielding.html
  - tests/fixtures/ncaa_detail/d2/fixture_manifest.json
  - tests/fixtures/ncaa_detail/d2/pitching_split_result.html
  - tests/fixtures/ncaa_detail/d2/player_detail.html
  - tests/fixtures/ncaa_detail/d2/schedule.html
  - tests/fixtures/ncaa_detail/d2/split_controls.html
  - tests/fixtures/ncaa_detail/d3/batting_split_result.html
  - tests/fixtures/ncaa_detail/d3/fielding.html
  - tests/fixtures/ncaa_detail/d3/fixture_manifest.json
  - tests/fixtures/ncaa_detail/d3/pitching_split_result.html
  - tests/fixtures/ncaa_detail/d3/player_detail.html
  - tests/fixtures/ncaa_detail/d3/schedule.html
  - tests/fixtures/ncaa_detail/d3/split_controls.html
  - tests/test_detailed_stats.py
  - tests/test_identity.py
  - tests/test_ncaa_detail.py
  - tests/test_ncaa_workflow.py
  - tests/test_phase2_tenant_isolation.py
  - tests/test_phase3_release.py
  - tests/test_player_history.py
  - tests/test_report_jobs.py
  - tests/test_report_library.py
  - tests/test_scoped_stats.py
  - tests/test_snapshot_persistence.py
  - AGENTS.md
findings:
  critical: 5
  warning: 3
  info: 0
  total: 8
status: issues_found
---

# Phase 3: Code Review Report

**Reviewed:** 2026-11-16T11:15:51Z
**Depth:** standard
**Files Reviewed:** 48
**Status:** issues_found

## Summary

The schema and isolated parser/metric contracts are extensive, but the submitted production collector does not connect several Phase 3 capabilities to a real report build. A normal job can publish `ready` without schedule scopes, game logs, career history, or (depending on configuration) split evidence. The source-contract review also found two fail-open empty/completeness decisions that can turn truncated NCAA responses into authoritative facts.

The prescribed Phase 3 release gate passed, as did compilation, 91 focused non-database tests, and `git diff --check`. Those results do not cover the missing production call paths described below.

## Narrative Findings (AI reviewer)

## Critical Issues

### CR-01: Live report jobs never collect schedule or game-log evidence

**Classification:** BLOCKER
**File:** `roster_scout/jobs.py:2420-2757`
**Issue:** `collect_ncaa_sections()` plans and returns only core batting/pitching, optional fielding, and optional split sections. It never calls `parse_schedule_fixture()`, a live schedule collector, or `to_collected_schedule_sections()`. Consequently `_persist_schedule_scopes()` at lines 1836-2090 is dead in the real worker path: no `Game`, computed last-10/conference scope, or typed game-log data is created. Because publication only validates computed scopes when their sections already exist (`snapshots.py:671-717`), the core-only result can still be marked `ready`. The computed-scope tests inject synthetic schedule sections directly and therefore do not prove the live job flow.

**Fix:** Add bounded schedule and current-season game-log collection to `collect_ncaa_sections()`, ledger each family, adapt it through `to_collected_schedule_sections()`, and always return explicit unavailable/partial sections when collection cannot complete. Make publication require the schedule/game-log family for a Phase 3 snapshot before `ready` is possible.

### CR-02: Player-history parsing has no production persistence path

**Classification:** BLOCKER
**File:** `roster_scout/jobs.py:1340-1775`
**Issue:** `_persist_canonical()` persists season batting/pitching/fielding facts and split facts, then proceeds directly to schedule scopes. It never fetches player detail, calls `canonicalize_player_history()` (`canonical.py:605-649`), or inserts `CareerSeasonFact`, `CareerAggregate`, and typed game-log rows. The player-history tests exercise the pure parser/canonicalizer, while `test_report_library.py:936-1034` manually seeds history rows. In production the authorized career/game drill views therefore return empty arrays even when the snapshot is `ready`.

**Fix:** Plan one bounded detail collection per authoritative player/stat type, persist exact `SourceRecord` evidence, canonicalize it against the same snapshot's schedule, and insert all history/aggregate/game-log rows with the existing composite ownership keys. Represent idless players, exhausted budgets, and unavailable history as explicit supplemental sections, and require those sections in Phase 3 publication readiness.

### CR-03: Split evidence disappears silently when the browser provider is not exactly `browserless`

**Classification:** BLOCKER
**File:** `roster_scout/jobs.py:2471-2477`
**Issue:** `splits_requested` is derived from `NCAA_BROWSER_PROVIDER == "browserless"`. If the variable is absent, misspelled, or another supported transport is selected, the collector does not create split request-ledger rows or unavailable split sections. `publish_snapshot()` treats an absent split family as valid (`snapshots.py:541-545`) and can publish `ready`, making configuration failure indistinguishable from a fully collected report.

**Fix:** Treat both split families as part of the Phase 3 report contract regardless of transport. Provider configuration should select the fetch implementation, not whether data exists. If the transport is unavailable, persist both split sections/scopes with a safe unavailable reason and publish `partial`; also reject `ready` when the required family is absent.

### CR-04: A header-only fielding response is certified as authoritative empty data

**Classification:** BLOCKER
**File:** `roster_scout/ncaa_detail.py:1519-1566`
**Issue:** `parse_fielding_page()` sets `verified_empty=not rows` after merely finding a valid header. It does not require an NCAA empty-state marker. A truncated response, a table whose body failed to load, or a transient empty `<tbody>` is therefore published as a successful zero-row fielding section. `tests/test_ncaa_detail.py:132-141` explicitly encodes this fail-open behavior by deleting the only player row and expecting `verified_empty=True`.

**Fix:** Track whether a recognized, source-authored empty marker was observed. Return `verified_empty=True` only when that marker is present and no player rows exist; otherwise raise `FieldingParseError("schema_validation")` or return an unavailable section. Replace the existing test with separate explicit-empty and header-only/truncated cases.

### CR-05: Fixture capture unconditionally certifies partial schedules as complete

**Classification:** BLOCKER
**File:** `scripts/capture_phase3_ncaa_fixtures.py:1308-1378`
**Issue:** The sanitizer skips malformed/short rows (`continue` at lines 1311-1317), classifies every unknown non-empty result as `scheduled`, and then always emits `data-schedule-complete="true"`. It does not verify pagination, expected row totals, season bounds, or an explicit source completeness marker. Downstream parsing trusts this flag, so a partial capture can make fewer-than-ten results and conference scopes look complete. Neutral-site games are also reduced to home unless the text starts with `@` (lines 1345-1346).

**Fix:** Fail closed on malformed game rows and unrecognized result text; derive completeness from an independently validated full-season/pagination contract and emit `false` when that proof is absent. Parse explicit home/away/neutral source semantics. Add truncated-page, pagination, unknown-result, and neutral-site fixtures before accepting `schedule_complete=True`.

## Warnings

### WR-01: Live split discovery can select an unrelated numeric DOM attribute

**Classification:** WARNING
**File:** `roster_scout/ncaa_detail.py:986-1008`
**Issue:** After matching a visible split label, discovery gathers values from every non-event attribute and accepts the first numeric value. For example, a button with `tabindex="7" data-stat-seq="123"` is discovered as split ID `7`. The capture utility repeats the same pattern in `_candidate_identifier()` (`scripts/capture_phase3_ncaa_fixtures.py:214-235`). This makes harmless NCAA markup changes redirect requests to the wrong split or mark a valid scope unavailable.

**Fix:** Allowlist documented identifier-bearing attribute names and recognized query-string keys, require all discovered candidates to agree, and reject ambiguous controls. Add noisy numeric attributes before and after the real identifier in tests.

### WR-02: Duplicate live split rows overwrite earlier evidence silently

**Classification:** WARNING
**File:** `roster_scout/ncaa_detail.py:1228-1264`
**Issue:** Live result parsing stores rows in a dictionary keyed by player path. If responsive markup, duplicated grids, or source corruption produces two rows for one player, the later count replaces the first without validation. The capture script's `_result_rows()` uses the same overwrite behavior. The resulting fixture/fact appears unique and traceable even though conflicting source evidence was discarded.

**Fix:** Detect a repeated player path. Accept it only if the normalized counts are identical (and record a deterministic deduplication rule), otherwise raise `SplitContractError("schema_validation")`. Add identical-duplicate and conflicting-duplicate tests.

### WR-03: The authoritative release gate does not exercise the real Phase 3 collector end to end

**Classification:** WARNING
**File:** `scripts/validate_phase3_release.py:24-52`
**Issue:** The focused gate selects parser, synthetic publication, and projection tests, but no test runs `collect_ncaa_sections()` with controlled live responses and asserts that schedule, all three game-log families, history, and both split families reach the database. Full discovery includes a real-collector test, but that test only asserts batting/pitching facts (`tests/test_report_jobs.py:2828-2854`). This is why CR-01 through CR-03 pass the advertised authoritative gate.

**Fix:** Add a PostgreSQL integration test that runs the actual collector/job entry point with all network/browser calls stubbed at the transport boundary, then asserts the required section set, request ledgers, typed tables, player drill response, and `ready`/`partial` semantics. Include that test class explicitly in `FOCUSED_TESTS`.

---

_Reviewed: 2026-11-16T11:15:51Z_
_Reviewer: the agent (gsd-code-reviewer)_
_Depth: standard_
