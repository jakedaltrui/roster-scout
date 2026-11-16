# Phase 3: Detailed NCAA Intelligence - Research

**Researched:** 2026-11-15
**Domain:** NCAA baseball collection, canonical statistics, scoped aggregates, history, and provenance
**Confidence:** HIGH for internal architecture and official NCAA formulas; MEDIUM for current NCAA page-discovery details; LOW for unverified NCAA split identifiers and production request-budget tuning

<user_constraints>
## User Constraints (from CONTEXT.md)

**Provenance:** Copied verbatim from `03-CONTEXT.md`. [VERIFIED: codebase grep]

### Locked Decisions

### Career and Game-Log Depth
- **D-01:** For every current-roster player with a stable NCAA identity, collect every available compatible career season total, including the school, team identity, division, season, and source evidence for each row.
- **D-02:** Detailed game-log acquisition is limited to the active report season. Older seasons appear as season-level career facts, not historical per-game rows.
- **D-03:** Career aggregates remain separately labeled derived records and may include only compatible available season facts. They must never blur transfers or silently treat a missing season as zero.
- **D-04:** Two-way players retain one stable player identity while their batting, pitching, fielding, split, career, and game-log facts remain separately typed and traceable.

### Last-10 and Conference Scopes
- **D-05:** `last_10_completed_games` and `conference_only` are first-class canonical statistic scopes for batting, pitching, and fielding when the necessary source facts exist.
- **D-06:** When NCAA does not publish a ready-made aggregate, Roster Scout may compute these scopes from exact audited current-season game logs using the same canonical definitions as full-season statistics.
- **D-07:** A last-10 scope uses the team's ten most recent completed games as of the snapshot collection time. It records the exact included game identifiers, dates, date range, and actual game count; cancellations and postponements are excluded, while both games of a completed doubleheader count separately.
- **D-08:** Conference-only classification requires season-specific conference identity and evidence sufficient to establish that each included opponent was a conference opponent for that season. It must never infer conference status from a current conference name, school name, or schedule position alone.
- **D-09:** If the game set, conference identity, or classification evidence is incomplete, the affected scope is explicitly unavailable or partial. It is never emitted as a zero-stat scope.

### Small Samples and Metric Availability
- **D-10:** A mathematically valid metric is shown regardless of sample size. Every scoped or split value carries the relevant PA, AB, batters faced, innings/outs, opportunities, or game count so coaches can judge the sample directly.
- **D-11:** Roster Scout does not suppress values behind arbitrary minimum thresholds. It does distinguish a valid zero from a missing input, an undefined denominator, and an unavailable source.
- **D-12:** Every derived value records its versioned definition and inputs. wOBA and FIP remain unavailable with an explanatory reason unless verified NCAA season/division weights or constants exist; MLB constants are never substituted.

### Publication and Completeness
- **D-13:** Preserve the Phase 1 publication rule: season batting and pitching remain publication-critical core sections. Fielding, splits, career history, game logs, last-10, conference-only, and NCAA-constant-dependent metrics are supplemental sections.
- **D-14:** A valid core collection may publish a clearly labeled `partial` snapshot when a supplemental section is unavailable or fails within its bounded request budget. The snapshot records exact section status, failure category, source time, row count, and availability reason.
- **D-15:** Supplemental facts are collected before immutable publication. A published snapshot is never mutated later to fill missing detail; an explicit refresh produces a linked successor snapshot.
- **D-16:** Same-build retries may reuse validated section evidence under the existing parser/schema/source identity contract. A new explicit refresh must collect live source data and cannot reuse an older build's facts.

### the agent's Discretion
- Choose the exact normalized table layout, migration split, request-budget values, bounded concurrency, retry cadence, and cache keys while preserving the locked provenance, immutability, availability, and D1/D2/D3 parity rules.
- Choose whether last-10 and conference calculations share a generic scoped-fact representation with source-provided splits or use a closely related typed model, provided both retain exact scope membership and never duplicate metric formulas.
- Choose safe display labels and machine codes for section/scope availability. Phase 3 does not implement the customer-facing workbook or dashboard layout.

### Deferred Ideas (OUT OF SCOPE)

- Detailed workbook tabs, formatting, precision, and presentation remain Phase 4.
- Embedded opponent dashboard pages, Power BI semantic delivery, filters, and embed-token behavior remain Phase 5.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| STAT-01 | Hitter season facts include the available NCAA counting statistics needed to report PA, AVG, OBP, SLG, OPS, ISO, BB%, K%, BABIP, singles, and total bases. | Expanded typed counts plus one versioned metric engine; official NCAA AVG/OBP/SLG formulas are documented below. [CITED: https://s3.amazonaws.com/fs.ncaa.org/Docs/stats/Stats_Manuals/Baseball.pdf] |
| STAT-02 | Pitcher season facts include the available NCAA counting statistics needed to report ERA, WHIP, K/9, BB/9, H/9, HR/9, K/BB, K%, BB%, BABIP allowed, and FIP. | Store outs, BF, opponent AB, H, HR, BB, HBP, SO, ER, and source values; calculate all rates from outs/BF and make FIP constant-dependent. [VERIFIED: codebase grep] |
| STAT-03 | Fielding facts include games/starts when available, PO, A, E, total chances, double plays, fielding percentage, and available catcher PB/SBA/CSB values. | Add fielding as a third season category using the same NCAA `#stat_grid` collector, then type G/GS/PO/A/E/TC/DP/FPCT/PB/SBA/CSB when present. [CITED: https://github.com/billpetti/baseballr/blob/master/R/ncaa_team_player_stats.R] |
| STAT-04 | Every derived metric handles a zero or unavailable denominator explicitly without emitting infinity or a misleading zero. | Centralize calculations around `value_status` and never coerce missing/undefined to zero. [VERIFIED: codebase grep] |
| STAT-05 | wOBA uses a versioned NCAA season/division weight set and is unavailable with an explanatory reason when no verified weight set exists. | Default to `unavailable_constant` until an approved, versioned NCAA season/division weight set is persisted; do not adopt third-party fitted constants silently. [CITED: https://www.ncaa.org/championships/statistics-and-records/baseball/] |
| STAT-06 | FIP uses a versioned NCAA season/division constant and is unavailable with an explanatory reason when no verified constant exists. | Apply the same constant registry and unavailable rule to FIP. [VERIFIED: codebase grep] |
| STAT-07 | Metric definitions and formulas are available in report methodology metadata. | Extend metric definitions with formula version, input codes, denominator/sample code, and optional constant-set version. [VERIFIED: codebase grep] |
| STAT-08 | Representative D1, D2, and D3 fixtures produce the same metric schema and classification behavior. | Reuse one schema/parser contract and add division fixtures for fielding, splits, history, metrics, and unavailability. [VERIFIED: codebase grep] |
| SPLT-01 | Batting split facts support vs LHP, vs RHP, runners on, bases empty, bases loaded, runners in scoring position, and two-outs contexts. | Model the seven locked codes as data-discovered source scopes; do not hard-code source IDs. [VERIFIED: codebase grep] |
| SPLT-02 | Pitching split facts support vs LHB, vs RHB, runners on, bases empty, bases loaded, runners in scoring position, and two-outs contexts when the NCAA source provides them. | Use the same scope registry with pitching-specific handedness labels and per-scope availability. [VERIFIED: codebase grep] |
| SPLT-03 | Each split record retains canonical split code, display label, season/stat type, source identifier, source URL, and availability status. | `StatisticScope` owns canonical code/label/stat type/source ID/source URL/status; scoped facts reference it. [ASSUMED] |
| SPLT-04 | Split source identifiers are discovered or validated per season and stat type rather than assumed permanent. | Capture current page controls/links and validate the returned table/header before accepting an ID. [ASSUMED] |
| SPLT-05 | A missing split is displayed as unavailable and never as a zero-stat split. | Persist a requested scope row with no facts and an availability reason; never synthesize zeros. [VERIFIED: codebase grep] |
| HIST-01 | A player's career view lists each available season with school, team ID, division, and season statistics under the stable NCAA player identity. | Read career totals from stable player pages and preserve separate school/team/season/division evidence per row. [CITED: https://github.com/billpetti/baseballr/blob/master/R/ncaa_game_logs.R] |
| HIST-02 | A career aggregate is labeled separately from individual season rows and is recomputed only from available compatible facts. | Aggregate only compatible, available season facts and retain the included career-row IDs. [VERIFIED: codebase grep] |
| HIST-03 | Player game logs identify date, opponent, opponent ID when available, and available batting, pitching, and fielding lines. | Parse current player `#game_log_{player_id}_player` grids for batting/pitching/fielding and link them to an audited team schedule game when the match is unique. [CITED: https://github.com/billpetti/baseballr/blob/master/R/ncaa_game_logs.R] |
| HIST-04 | Missing historical seasons or games are disclosed in section completeness instead of being silently omitted. | Represent missing stable ID, missing table, ambiguous team/game link, and budget exhaustion as explicit section/scope state. [VERIFIED: codebase grep] |
| HIST-05 | Career and game-log collection uses bounded concurrency, source caching, request budgets, and retry/backoff controls. | Reuse the Browserless slot, job retry, section cache, and source-contract mechanisms; add a per-build request ledger. [VERIFIED: codebase grep] |
| HIST-06 | A customer can drill from a roster player into season, split, career, and game-log views for the same snapshot. | Every fact path begins at snapshot + stable Player and reaches season/scope/game facts without a second identity system. [VERIFIED: codebase grep] |
| OPS-03 | Operators can identify stuck jobs, failed Power BI refreshes, unavailable source sections, and capacity exhaustion without reading customer data. | Add metadata-only diagnostics for job age, request budget, section status/reason, upstream category, and capacity exhaustion; Power BI refresh remains Phase 5 but the diagnostics model must reserve its category. [VERIFIED: codebase grep] |
</phase_requirements>

## Summary

Phase 3 should extend the existing canonical and job seams rather than add a second runtime. The repository already persists exact NCAA rows before typed facts, uses stable NCAA player IDs, publishes batting and pitching atomically, allows supplemental failures to produce a partial snapshot, and restricts cache reuse to a version-matched same build. [VERIFIED: codebase grep] The implementation should therefore add typed detail and independently statused supplemental sections to `roster_scout/models.py`, `roster_scout/canonical.py`, `roster_scout/jobs.py`, and the existing NCAA acquisition functions. [VERIFIED: codebase grep]

Current upstream behavior supports the required data shapes: team season batting/pitching/fielding use the season-team `season_to_date_stats` page keyed by a per-season category ID, while a stable player page carries both career-total and game-log grids for batting, pitching, and fielding. [CITED: https://github.com/billpetti/baseballr/blob/master/R/ncaa_team_player_stats.R] [CITED: https://github.com/billpetti/baseballr/blob/master/R/ncaa_game_logs.R] NCAA pages are currently Akamai-protected and rate-sensitive, so every new fetch must stay behind the proven Browserless capacity boundary and a strict per-build request ledger. [CITED: https://github.com/BillPetti/baseballr/blob/master/NEWS.md]

The main schema decision is to model splits, last-10, and conference-only as one canonical `StatisticScope` concept with exact membership/evidence, while retaining separately typed batting, pitching, and fielding facts. [ASSUMED] The same metric engine must run against full-season and scoped count sets, persist input values and sample size, and return a status instead of a number for missing inputs, zero denominators, unavailable sources, or missing constants. [ASSUMED] The NCAA manual verifies AVG, OBP, SLG, ERA, and fielding-percentage formulas; wOBA weights and the FIP constant should remain explicitly unavailable unless the project later approves a season/division constant set. [CITED: https://s3.amazonaws.com/fs.ncaa.org/Docs/stats/Stats_Manuals/Baseball.pdf]

**Primary recommendation:** Implement Phase 3 as four ordered slices—schema/metric contract, team detail and split discovery, player history/game-log acquisition, then audited scopes/operations/release validation—using one shared scope model and one shared metric engine. [ASSUMED]

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| NCAA page acquisition and split/category discovery | API / Backend worker | External NCAA/Browserless | Existing durable workers own source access and Browserless capacity. [VERIFIED: codebase grep] |
| Raw row preservation and canonicalization | API / Backend worker | Database / Storage | Transformation occurs before publication; exact source evidence is persisted first. [VERIFIED: codebase grep] |
| Snapshot facts, scopes, memberships, methods, and availability | Database / Storage | API / Backend | Immutable PostgreSQL state is the downstream source of truth. [VERIFIED: codebase grep] |
| Derived metrics | API / Backend | Database / Storage | One deterministic service computes values; the database persists definitions, inputs, and results. [ASSUMED] |
| Last-10 and conference game selection | API / Backend | Database / Storage | Selection is deterministic from audited schedule/game and conference evidence, then persisted as exact membership. [ASSUMED] |
| Request budgets, retries, and operator diagnostics | API / Backend worker | Database / Storage | Durable job/section state already owns attempts, cache evidence, and safe failure categories. [VERIFIED: codebase grep] |
| Workbook/dashboard presentation | — | — | Explicitly deferred to Phases 4 and 5. [VERIFIED: codebase grep] |

## Project Constraints (from AGENTS.md)

- Treat this repository as independent of the surrounding Axis Baseball workspace. [VERIFIED: codebase grep]
- Do not read or commit `credentials.json`, `oauth_credentials.json`, or `token.json`; they are unused local secrets. [VERIFIED: codebase grep]
- For NCAA selection/scraping changes, inspect `roster_scraper.py`, `app.py` dispatch, `templates/index.html` request contract only when needed, and `scripts/validate_ncaa_catalog.py`. [VERIFIED: codebase grep]
- For workbook columns, inspect `roster_scraper.py` exports and `app.py`/workbook writer, but presentation changes are out of Phase 3 scope. [VERIFIED: codebase grep]
- Before handoff, run the checks in root `CONTEXT.md`. [VERIFIED: codebase grep]

No project-defined `.codex/skills/` or `.agents/skills/` were present. [VERIFIED: filesystem probe]

## Standard Stack

### Core

| Library / component | Version | Purpose | Why Standard |
|---------------------|---------|---------|--------------|
| Python | 3.14.7 available | Collectors, canonicalization, workers, tests | Existing application runtime. [VERIFIED: environment probe] |
| Flask | 3.1.3 | Existing application boundary | Already pinned; Phase 3 does not add customer-facing routes. [VERIFIED: PyPI registry] |
| SQLAlchemy | 2.1.6 pinned | Typed models and caller-owned transactions | Existing canonical persistence pattern; PyPI showed 2.1.8 as latest during research, but this phase should not mix a framework upgrade into schema work. [VERIFIED: PyPI registry] |
| Alembic | 1.20.1 | Additive schema migrations | Existing migration head is `0024`. [VERIFIED: environment probe] |
| PostgreSQL + psycopg | psycopg 3.3.6 pinned | Immutable canonical data and constraints | Existing production authority and composite ownership enforcement. [VERIFIED: codebase grep] |
| Celery + Redis | Celery 5.6.3 pinned | Durable report execution and retry | Existing outbox/job boundary. [VERIFIED: codebase grep] |
| requests + BeautifulSoup | requests 2.34.2; bs4 4.15.0 | HTTP and resilient HTML parsing | Already used by the NCAA collector. [VERIFIED: PyPI registry] |
| Browserless + Patchright | Patchright 1.64.3 pinned | NCAA browser/Akamai access | Existing shared sticky-browser and capacity boundary. [VERIFIED: codebase grep] |

### Supporting

| Component | Version | Purpose | When to Use |
|-----------|---------|---------|-------------|
| `Decimal` | Python stdlib | Deterministic rate computation | Use for every derived metric; do not compute canonical values with binary floats. [VERIFIED: codebase grep] |
| JSONB raw evidence | PostgreSQL | Preserve exact source rows and metric input snapshots | Keep raw source labels/values and explicit input maps while typed columns serve normal queries. [VERIFIED: codebase grep] |
| `unittest` | Python stdlib | Unit/integration/release tests | Existing suite and release gates use it. [VERIFIED: codebase grep] |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Extend existing collector | Adopt `baseballr`, `collegebaseball`, or `ncaa_bbStats` | These projects confirm useful upstream shapes, but introducing R or a second Python scraper would duplicate the Browserless, cache, schema, and failure contracts. Do not add one. [VERIFIED: codebase grep] |
| Explicitly unavailable wOBA/FIP | Import third-party fitted NCAA constants | A third-party package documents fitted NCAA constants, but those are not NCAA-published constants and cannot silently satisfy the locked verification rule. [CITED: https://collegebaseballstatspackage.readthedocs.io/en/latest/advanced_stats.html] |
| One generic JSON fact table | Typed batting/pitching/fielding tables | Generic JSON is flexible but weakens constraints and downstream parity; keep typed facts with raw JSON alongside them. [ASSUMED] |

**Installation:** No new package is required or recommended. [VERIFIED: codebase grep]

**Version verification:** Existing pins were compared with PyPI on 2026-11-15; Flask, requests, BeautifulSoup, and Alembic matched the latest registry version, while SQLAlchemy, psycopg, Celery, and Patchright had newer releases. [VERIFIED: PyPI registry] Preserve pins during Phase 3 unless a separate dependency-upgrade task is authorized. [ASSUMED]

## Package Legitimacy Audit

This phase installs no external packages, so the package-legitimacy gate is not applicable. [VERIFIED: codebase grep]

## Architecture Patterns

### System Architecture Diagram

```text
Accepted ReportJob
    |
    v
Existing NCAA/Browserless slot + per-build request ledger
    |
    +--> team season pages: batting | pitching | fielding
    +--> split discovery --> validated split pages (7 x batting/pitching)
    +--> team schedule/results + season conference evidence
    +--> stable player pages --> career grids + active-season game-log grids
    |
    v
Exact SourceRecords first (URL, source IDs, time, checksum, raw row)
    |
    v
Canonical identity resolution (one Player; typed role facts)
    |
    +--> full-season typed facts
    +--> source-provided split scopes
    +--> exact Game facts and career-season facts
    +--> last-10/conference scope membership
    |
    v
One versioned metric engine + optional constant registry
    |
    +--> numeric value + inputs + sample size
    +--> missing_input | undefined_denominator | unavailable_constant
    |
    v
Atomic draft publication
    +--> ready: every requested section complete
    `--> partial: core complete, one or more supplemental sections partial/unavailable/failed
```

The flow preserves the existing evidence-before-facts and atomic-publication boundaries. [VERIFIED: codebase grep]

### Recommended Project Structure

```text
roster_scout/
├── models.py                 # additive fact/scope/game/constant models
├── canonical.py              # source rows -> typed counts and identities
├── metrics.py                # one Decimal-based metric registry/engine
├── ncaa_detail.py            # category/split/player/schedule parsing contracts
├── scopes.py                 # last-10/conference selection and aggregation
├── jobs.py                   # section orchestration, budget, cache, publication
└── snapshots.py              # extended integrity and completeness checks
migrations/versions/
└── 0025_detailed_ncaa_intelligence.py
scripts/
├── validate_phase3_release.py
└── inspect_report_jobs.py    # metadata-only OPS-03 diagnostics
tests/
├── fixtures/ncaa_detail/     # D1/D2/D3 source HTML/rows, no secrets
├── test_detailed_stats.py
├── test_ncaa_detail.py
├── test_scoped_stats.py
├── test_player_history.py
└── test_phase3_release.py
```

New module names are recommendations; the ownership boundaries are the important contract. [ASSUMED]

### Pattern 1: One Scope Registry, Typed Facts

**What:** Add a snapshot-owned `StatisticScope` with fields for `scope_code`, `scope_kind`, `stat_type`, display label, source identifier/URL, status, availability reason, collected time, sample counts, and optional date/conference metadata. Typed batting, pitching, and fielding facts reference the scope; exact included games use a normalized scope-game join. [ASSUMED]

**When to use:** Use it for all seven source splits plus `last_10_completed_games` and `conference_only`. Full-season facts may remain in the existing tables, but their metric inputs must pass through the same metric engine. [ASSUMED]

**Why:** This keeps availability and provenance at scope granularity without turning unlike batting/pitching/fielding rows into one JSON blob. [ASSUMED]

### Pattern 2: Requested Scope Row Even When No Facts Exist

**What:** Materialize every requested section/scope as `complete`, `partial`, `unavailable`, or `failed`; absence of a row is not an availability state. [VERIFIED: codebase grep]

**Important migration:** `roster_scout.enums.SECTION_STATUSES` currently has `pending`, `complete`, `unavailable`, and `failed`, so Phase 3 must add `partial` and migrate the database check constraint before emitting partial sections. [VERIFIED: codebase grep]

### Pattern 3: Source Discovery Before Fetch

**What:** Discover fielding/split identifiers from season lookup data or source-page controls, validate that the response belongs to the requested season/stat type/scope, then persist the discovered ID with the source row. [ASSUMED]

**When to use:** Every season and each batting/pitching split family. Source IDs are not permanent application constants. [VERIFIED: codebase grep]

**Acceptance rule:** A discovered page is usable only if its table selector, normalized header signature, requested scope label, season identity, and player-link pattern match the parser contract. [ASSUMED]

### Pattern 4: Player-Page Fan-Out by Stable Identity and Type

**What:** For each current-roster stable NCAA player ID, request only applicable batting/pitching/fielding player pages. Parse both career and active-season game grids from that same page. [CITED: https://github.com/billpetti/baseballr/blob/master/R/ncaa_game_logs.R]

**When to use:** History and game logs. ID-less players get an explicit unavailable history/game-log state and are never cross-season linked by name. [VERIFIED: codebase grep]

**Transfer rule:** Career rows remain separate by season/team/division. Resolve team identity from source links or exact season-specific lookup evidence; a text-only ambiguous team match makes that row partial/unavailable rather than guessed. [ASSUMED]

### Pattern 5: Schedule-First Game Identity

**What:** Build one snapshot-owned Game list from the team's season schedule/results, retaining contest/game ID, date, opponent identity, result, completion state, source URL, and source row. NCAA schedule tooling exposes contest IDs and season-specific home/away team and conference IDs. [CITED: https://github.com/BillPetti/baseballr/blob/master/R/ncaa_schedule_info.R]

**When to use:** Current-season player game-log linkage, last-10 membership, and conference-only classification. [ASSUMED]

**Join rule:** Link player log rows to a Game only when date/opponent/result and doubleheader ordinal (or a direct source game ID) resolve uniquely. Ambiguous same-day doubleheader rows make the affected log/scope partial; never arbitrarily pair by row order alone. [ASSUMED]

### Pattern 6: Metric Result Is a Tagged Value

**What:** Return `(value, status, reason, inputs, sample)` from every formula. Persist definition version, exact input values/codes, optional constant-set version, and sample counts. [ASSUMED]

**States:** `available`, `missing_input`, `undefined_denominator`, `unavailable_source`, `unavailable_constant`, and `incompatible_input`. [ASSUMED]

**Database change:** Existing `MetricValue` stores only value or unavailable reason; it needs input lineage and sample metadata (JSONB or normalized input rows) to satisfy D-10/D-12. [VERIFIED: codebase grep]

### Pattern 7: Per-Build Request Ledger

**What:** Persist request budget use by section/family, not only attempt count: planned URL identity, cache hit/live fetch, start/end time, outcome category, and remaining budget. Do not store customer data or secret-bearing URLs in operator output. [ASSUMED]

**Recommended initial limits:** one existing Browserless account slot per report job; at most 2 in-session page navigations at once; 150 live page navigations per build; 45 seconds per navigation; 12 minutes collection wall time; existing 3 job attempts with capped exponential backoff. [ASSUMED]

These values are conservative starting points because current upstream maintainers describe NCAA capture as slow and rate-sensitive; production telemetry should tune them without changing canonical semantics. [CITED: https://github.com/sportsdataverse/baseballr-data] The planner should make them validated configuration with hard min/max bounds and deterministic fixture overrides. [ASSUMED]

### Anti-Patterns to Avoid

- **One giant “details” section:** A single failure would hide which family is unavailable and prevent same-build reuse. Use bounded sections/families. [ASSUMED]
- **Per-player browser sessions:** This bypasses the shared capacity design and multiplies challenge/terms overhead. Reuse the existing sticky Browserless session within one slot. [VERIFIED: codebase grep]
- **Name-based cross-season linkage:** It merges transfers and same-name players incorrectly. Only stable NCAA identity may cross seasons. [VERIFIED: codebase grep]
- **Hard-coded split IDs:** IDs must be discovered/validated per season and stat type. [VERIFIED: codebase grep]
- **Recomputing scope membership downstream:** Excel and Power BI must consume persisted exact membership, not rerun “last 10” later. [VERIFIED: codebase grep]
- **Treating unavailable as an all-zero stat line:** Zero is a value only when all required source inputs exist and the denominator semantics permit it. [VERIFIED: codebase grep]
- **Mixing formula implementations:** Full-season, split, last-10, conference, workbook, and dashboard calculations must call one metric registry or consume its persisted values. [VERIFIED: codebase grep]
- **Backfilling published snapshots with newly fetched detail:** Phase 3 facts are collected before publication; later detail requires a successor refresh. [VERIFIED: codebase grep]

## Canonical Data Shape

### Expanded Count Inputs

| Family | Minimum typed inputs | Notes |
|--------|----------------------|-------|
| Batting | G, GS, PA if source-published, AB, R, H, 2B, 3B, HR, RBI, BB, HBP, SO/K, SF, SH, SB, CS, TB if source-published | Preserve source-provided rates, but derive canonical rates from counts under a versioned definition. [ASSUMED] |
| Pitching | G/App, GS, outs, H, R, ER, BB, SO/K, HR, HBP, BF, opponent AB, opponent SF when present | Outs remain canonical; never use NCAA decimal-looking innings as base-10. [CITED: https://s3.amazonaws.com/fs.ncaa.org/Docs/stats/Stats_Manuals/Baseball.pdf] |
| Fielding | G, GS, PO, A, E, TC, DP, FPCT; catcher PB, SBA, CSB when present | Store absent catcher columns as missing, not zero. [ASSUMED] |

### Metric Registry

| Metric | Versioned formula | Required denominator behavior |
|--------|-------------------|-------------------------------|
| AVG | `H / AB` | AB=0 -> undefined denominator. [CITED: https://s3.amazonaws.com/fs.ncaa.org/Docs/stats/Stats_Manuals/Baseball.pdf] |
| OBP | `(H + BB + HBP) / (AB + BB + HBP + SF)` | Denominator=0 -> undefined. [CITED: https://s3.amazonaws.com/fs.ncaa.org/Docs/stats/Stats_Manuals/Baseball.pdf] |
| SLG | `TB / AB` | AB=0 -> undefined. [CITED: https://s3.amazonaws.com/fs.ncaa.org/Docs/stats/Stats_Manuals/Baseball.pdf] |
| OPS | `OBP + SLG` | Either component unavailable -> unavailable. [ASSUMED] |
| ISO | `SLG - AVG` (equivalent to extra bases per AB with compatible inputs) | AB=0 -> undefined. [ASSUMED] |
| 1B | `H - 2B - 3B - HR` | Negative result -> incompatible source inputs, not clamped. [ASSUMED] |
| TB | `1B + 2*2B + 3*3B + 4*HR` | Any missing hit component -> missing input. [ASSUMED] |
| BB% / K% (batting) | `BB / PA`, `SO / PA` | PA=0 -> undefined; retain PA. [ASSUMED] |
| BABIP | `(H - HR) / (AB - SO - HR + SF)` | Denominator<=0 -> undefined/incompatible as appropriate. [ASSUMED] |
| ERA | `ER * 27 / outs` | outs=0 -> undefined. [CITED: https://s3.amazonaws.com/fs.ncaa.org/Docs/stats/Stats_Manuals/Baseball.pdf] |
| WHIP | `(BB + H) * 3 / outs` | outs=0 -> undefined. [ASSUMED] |
| K/9, BB/9, H/9, HR/9 | `count * 27 / outs` | outs=0 -> undefined. [ASSUMED] |
| K/BB | `SO / BB` | BB=0 -> undefined, not infinity. [ASSUMED] |
| K% / BB% (pitching) | `SO / BF`, `BB / BF` | BF=0 -> undefined; retain BF. [ASSUMED] |
| BABIP allowed | `(H - HR) / (opponent_AB - SO - HR + opponent_SF)` | Missing opponent AB/SF -> missing input; nonpositive denominator -> undefined. [ASSUMED] |
| FPCT | `(PO + A) / (PO + A + E)` | Total chances=0 -> undefined. [CITED: https://s3.amazonaws.com/fs.ncaa.org/Docs/stats/Stats_Manuals/Baseball.pdf] |
| wOBA | Approved NCAA season/division weight-set formula only | No approved set -> `unavailable_constant`. [VERIFIED: codebase grep] |
| FIP | Approved NCAA season/division constant-set formula only | No approved set -> `unavailable_constant`; never use an MLB constant. [VERIFIED: codebase grep] |

The formula table contains several conventional formulas marked `[ASSUMED]`; lock their exact definitions and rounding in a metric-registry test before implementation. [ASSUMED]

### Availability and Failure Codes

| Layer | Machine code | Meaning |
|-------|--------------|---------|
| Section/scope | `complete` | Requested membership and rows are fully known. [ASSUMED] |
| Section/scope | `partial` | Some rows/membership were verified, but completeness is known to be incomplete. [ASSUMED] |
| Section/scope | `unavailable` | Source does not publish it, stable identity is absent, or required evidence is unavailable. [ASSUMED] |
| Section/scope | `failed` | Network, access, schema, or retry/budget failure prevented a determination. [ASSUMED] |
| Reason | `source_not_published` | Requested grid/control is absent after a valid page load. [ASSUMED] |
| Reason | `source_identifier_not_found` | Category/split ID could not be discovered or validated. [ASSUMED] |
| Reason | `missing_stable_player_id` | History/game log was intentionally not linked across seasons. [VERIFIED: codebase grep] |
| Reason | `ambiguous_game_link` | Player log row could not be mapped uniquely to schedule game. [ASSUMED] |
| Reason | `conference_evidence_incomplete` | Season-specific membership/opponent proof is insufficient. [VERIFIED: codebase grep] |
| Reason | `request_budget_exhausted` | Bounded work stopped before the family completed. [ASSUMED] |
| Reason | `upstream_blocked` / `network` / `schema_validation` | Preserve existing safe external-failure categories. [VERIFIED: codebase grep] |
| Metric | `missing_input` / `undefined_denominator` / `unavailable_constant` | Distinguish data absence, math undefinedness, and missing verified constants. [VERIFIED: codebase grep] |

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Browser/session capacity | A new semaphore or player-page browser pool | Existing `usage_limits.browserless_slot()` and sticky Browserless collector | Capacity and cleanup behavior are already release-tested. [VERIFIED: codebase grep] |
| Durable retries | Ad hoc loops inside parsers | Existing ReportJob generation, outbox, `bounded_retry_delay`, and same-build section cache | Preserves idempotency and crash recovery. [VERIFIED: codebase grep] |
| Identity | Name/year transfer matching | Existing stable `Player` + snapshot `PlayerTeamSeason` pattern | Name linkage cannot safely cross teams/seasons. [VERIFIED: codebase grep] |
| Numeric parsing | `float()` and truthiness defaults | Existing Decimal/null helpers plus outs conversion | Float and `or 0` destroy missing/zero and innings semantics. [VERIFIED: codebase grep] |
| wOBA/FIP constants | MLB constants or a locally invented calibration | Versioned approved NCAA constant registry or explicit unavailable | Constants are season/division-dependent under the locked contract. [VERIFIED: codebase grep] |
| Last-10 membership | `ORDER BY date LIMIT 10` over player rows | Exact completed team Game list + normalized scope membership | Doubleheaders, postponements, and incomplete links need explicit treatment. [VERIFIED: codebase grep] |
| Conference classification | Current school conference or string comparison | Season-specific team/opponent conference IDs with source evidence | Conference alignment changes by season. [VERIFIED: codebase grep] |
| Downstream metrics | Excel or Power BI formulas | Persisted canonical MetricDefinition/MetricValue outputs | Prevents consumer drift. [VERIFIED: codebase grep] |

**Key insight:** The hard part is not arithmetic; it is proving which source rows, games, seasons, and constants belong in each immutable result. [ASSUMED]

## Common Pitfalls

### Pitfall 1: Treating “No Table” as an Empty Table

**What goes wrong:** A challenged, redesigned, or non-published page becomes a zero-row scope. [VERIFIED: codebase grep]

**Why it happens:** HTML parsers often return an empty selection for both valid absence and schema failure. [ASSUMED]

**How to avoid:** Require the expected page identity/header and an explicit empty marker before `verified_empty`; otherwise emit unavailable or failed with reason. [VERIFIED: codebase grep]

**Warning signs:** `row_count=0`, `verified_empty=false`, missing checksum, or a source page containing Akamai markers. [VERIFIED: codebase grep]

### Pitfall 2: Assuming Category or Split IDs Are Stable

**What goes wrong:** A prior-season identifier returns the wrong grid or no grid. [VERIFIED: codebase grep]

**Why it happens:** NCAA category IDs are season-specific, and split IDs have not yet been validated for the current source layout. [VERIFIED: codebase grep]

**How to avoid:** Discover/validate per season + stat type + canonical split code and cache only inside the same build/source contract. [ASSUMED]

**Warning signs:** Returned heading/headers do not match requested stat type or split label. [ASSUMED]

### Pitfall 3: Partial Status Exists at Snapshot but Not Section

**What goes wrong:** D-14 cannot express “some player logs collected” without calling the whole section complete or failed. [VERIFIED: codebase grep]

**Why it happens:** Current `SECTION_STATUSES` omits `partial`. [VERIFIED: codebase grep]

**How to avoid:** Migrate enum/check constraints, define partial row-count semantics, and update publication/cache/reconstruction tests together. [ASSUMED]

**Warning signs:** `ReportSection.status` uses a free-form value rejected by PostgreSQL or cache reconstruction treats a partial section as reusable-complete. [VERIFIED: codebase grep]

### Pitfall 4: Duplicate or Mislinked Doubleheaders

**What goes wrong:** Two games with the same date/opponent collapse or player lines attach to the wrong contest. [ASSUMED]

**Why it happens:** Player game-log grids expose date/opponent/result, while exact contest identity comes from schedule evidence. [CITED: https://github.com/billpetti/baseballr/blob/master/R/ncaa_game_logs.R]

**How to avoid:** Keep Game identity from schedule first, preserve doubleheader ordinal/result, and refuse ambiguous joins. [ASSUMED]

**Warning signs:** two schedule games map to one player-log key, or scope membership has fewer games than the completed schedule slice. [ASSUMED]

### Pitfall 5: Aggregating Rates Instead of Counts

**What goes wrong:** Career, last-10, or conference AVG/ERA/OPS is computed by averaging season/game rates. [ASSUMED]

**Why it happens:** Source pages expose ready-made percentage columns. [ASSUMED]

**How to avoid:** Sum compatible counting inputs, then run the shared formula once; store included fact IDs and exclusions. [ASSUMED]

**Warning signs:** aggregate changes when identical totals are repartitioned across games/seasons. [ASSUMED]

### Pitfall 6: Treating Missing Season as Zero in Career Aggregates

**What goes wrong:** Transfers or missing source coverage depress a career rate or count. [VERIFIED: codebase grep]

**How to avoid:** Aggregate only available compatible rows; persist included rows, missing-season disclosure, and aggregate status. [VERIFIED: codebase grep]

**Warning signs:** aggregate season count differs from inclusion count without an exclusion reason. [ASSUMED]

### Pitfall 7: Losing Sample Context

**What goes wrong:** A valid .500 split from two AB looks equivalent to a full-season .500. [VERIFIED: codebase grep]

**How to avoid:** Every metric result stores the relevant PA/AB/BF/outs/opportunities/game count and no minimum-sample suppression. [VERIFIED: codebase grep]

**Warning signs:** a metric row cannot answer “what was its denominator?” without re-querying raw data. [ASSUMED]

### Pitfall 8: Request Explosion

**What goes wrong:** `players × seasons × types` creates hundreds of redundant requests and exhausts Browserless capacity. [ASSUMED]

**Why it happens:** The player page already carries career totals and game logs across seasons, but an implementation may still fan out per season. [CITED: https://github.com/billpetti/baseballr/blob/master/R/ncaa_game_logs.R]

**How to avoid:** Fetch once per stable player/type and filter the active-season game rows locally; apply a persisted request ledger and stop supplemental families at their budget. [ASSUMED]

**Warning signs:** the same player/type URL is fetched more than once in one build or live requests scale with career length. [ASSUMED]

### Pitfall 9: Weak Publication Validation for New Tables

**What goes wrong:** New facts reference another snapshot/organization or lack exact source evidence. [VERIFIED: codebase grep]

**How to avoid:** Repeat the existing composite snapshot/organization/source foreign-key pattern and extend `publish_snapshot()` parity counts/traces for every typed family. [VERIFIED: codebase grep]

**Warning signs:** a typed fact can be inserted without a same-snapshot source record or scope. [VERIFIED: codebase grep]

## Code Examples

Verified and recommended patterns for planning; proposed APIs are marked `[ASSUMED]`.

### Safe Ratio Contract

```python
# Source formula behavior: NCAA manual; API shape is proposed. [ASSUMED]
from dataclasses import dataclass
from decimal import Decimal

@dataclass(frozen=True)
class MetricResult:
    value: Decimal | None
    status: str
    reason: str | None
    inputs: dict[str, Decimal | int | None]
    sample: dict[str, int]

def safe_ratio(numerator, denominator, *, inputs, sample):
    if any(value is None for value in inputs.values()):
        return MetricResult(None, "missing_input", "required input missing", inputs, sample)
    if denominator == 0:
        return MetricResult(None, "undefined_denominator", "denominator is zero", inputs, sample)
    return MetricResult(Decimal(numerator) / Decimal(denominator), "available", None, inputs, sample)
```

The engine must not use truthiness because numeric zero is valid evidence. [VERIFIED: codebase grep]

### Current NCAA Player-Page Contract

```python
# Source: https://github.com/billpetti/baseballr/blob/master/R/ncaa_game_logs.R
url = f"https://stats.ncaa.org/players/{player_id}?year_stat_category_id={category_id}"
career_selector = f"#career_totals_{player_id}_player"
game_selector = f"#game_log_{player_id}_player"
```

The selectors and URL must still be captured in D1/D2/D3 fixtures and validated before becoming a locked parser contract. [CITED: https://github.com/billpetti/baseballr/blob/master/R/ncaa_game_logs.R]

### Exact Last-10 Membership

```python
# Proposed deterministic pattern. [ASSUMED]
completed = [game for game in games if game.completion_status == "completed"]
completed.sort(key=lambda game: (game.completed_at_or_date, game.doubleheader_ordinal, game.source_game_id))
included = tuple(completed[-10:])

scope = StatisticScope(
    code="last_10_completed_games",
    actual_game_count=len(included),
    start_date=included[0].game_date if included else None,
    end_date=included[-1].game_date if included else None,
)
scope.games = included  # normalized join rows persist exact IDs
```

Do not emit the scope as complete if any included game lacks the source facts required for the requested stat type. [VERIFIED: codebase grep]

### Season-Specific Conference Classification

```python
# Proposed rule using audited season membership IDs. [ASSUMED]
def is_conference_game(game, report_team_membership, opponent_membership):
    if report_team_membership is None or opponent_membership is None:
        return None
    if report_team_membership.season_id != game.season_id:
        return None
    if opponent_membership.season_id != game.season_id:
        return None
    return report_team_membership.conference_id == opponent_membership.conference_id
```

The NCAA schedule tooling exposes season-specific team and opponent conference IDs, but Roster Scout must preserve the source evidence rather than trust a current affiliation string. [CITED: https://github.com/BillPetti/baseballr/blob/master/R/ncaa_schedule_info.R]

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `/team/{team_id}/stats` | `/teams/{season_team_id}/season_to_date_stats?year_stat_category_id=...` | Current baseballr source/release documents the redesigned NCAA page | Use season-team identity and validate `#stat_grid`. [CITED: https://github.com/BillPetti/baseballr/releases] |
| `/player/index?...stats_player_seq=` | `/players/{player_id}?year_stat_category_id=...` | Current baseballr source documents the migration | One page provides ID-keyed career and game-log grids. [CITED: https://github.com/billpetti/baseballr/blob/master/R/ncaa_game_logs.R] |
| Static HTTP alone | Browser fallback/sticky browser behind Akamai | Current upstream release notes | Keep Browserless capacity, terms acceptance, and challenge detection. [CITED: https://github.com/BillPetti/baseballr/releases] |
| Batting/pitching only | Generic category grid supports fielding too | Current baseballr source | Add fielding via the same category/page parser family, not a new source adapter. [CITED: https://github.com/billpetti/baseballr/blob/master/R/ncaa_team_player_stats.R] |

**Deprecated/outdated:** Hard-coded old endpoints and static-request assumptions are outdated against the current NCAA page layout and Akamai behavior. [CITED: https://github.com/BillPetti/baseballr/releases]

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|-------------|-----------|---------|----------|
| Python | All Phase 3 code/tests | ✓ | 3.14.7 | — [VERIFIED: environment probe] |
| uv | Reproducible commands | ✓ | 0.10.10 | system Python, but release checks use uv. [VERIFIED: environment probe] |
| Existing Python dependencies | Runtime/tests | ✓ | imports succeeded | — [VERIFIED: environment probe] |
| Alembic | Migration | ✓ | head `0024` | — [VERIFIED: environment probe] |
| Docker daemon | Disposable PostgreSQL/Redis tests | ✓ | server 29.9.2 | Existing externally managed test DB if configured. [VERIFIED: environment probe] |
| Local `psql` / `pg_isready` | Manual DB inspection | ✗ | — | Use Docker/SQLAlchemy release scripts. [VERIFIED: environment probe] |
| Local `redis-cli` | Manual broker probe | ✗ | — | Use Docker/Celery test harness. [VERIFIED: environment probe] |
| Browserless credentials/service | Live NCAA verification | not inspected | — | Deterministic fixtures; optional `scripts/live_ncaa_smoke.py`. [VERIFIED: codebase grep] |

**Missing dependencies with no fallback:** None for fixture-driven planning and unit tests. [VERIFIED: environment probe]

**Missing dependencies with fallback:** Local PostgreSQL/Redis CLIs are absent, but Docker is available and the repository's release scripts use SQLAlchemy against a disposable PostgreSQL URL. [VERIFIED: environment probe]

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | Python stdlib `unittest`; PostgreSQL integration tests through existing session fixtures. [VERIFIED: codebase grep] |
| Config file | None; tests are discovered from `tests/`. [VERIFIED: codebase grep] |
| Quick run command | `uv run python -m unittest tests.test_detailed_stats tests.test_ncaa_detail tests.test_scoped_stats tests.test_player_history -v` [ASSUMED] |
| Full suite command | `uv run python -m unittest discover -s tests -v` [VERIFIED: codebase grep] |
| Phase gate command | `PHASE2_DESTRUCTIVE_DATABASE=local-disposable DATABASE_URL=postgresql+psycopg://..._test uv run python scripts/validate_phase3_release.py` [ASSUMED] |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| STAT-01 | Expanded hitter inputs and requested metrics | unit + DB integration | `uv run python -m unittest tests.test_detailed_stats.HitterMetricTests -v` | ❌ Wave 0 |
| STAT-02 | Expanded pitcher inputs and requested metrics | unit + DB integration | `uv run python -m unittest tests.test_detailed_stats.PitcherMetricTests -v` | ❌ Wave 0 |
| STAT-03 | Fielding/catcher parsing and facts | parser + DB integration | `uv run python -m unittest tests.test_ncaa_detail.FieldingTests -v` | ❌ Wave 0 |
| STAT-04 | Zero/missing/undefined distinction | unit/property table | `uv run python -m unittest tests.test_detailed_stats.DenominatorSemanticsTests -v` | ❌ Wave 0 |
| STAT-05 | wOBA constant provenance/unavailable | unit + DB integration | `uv run python -m unittest tests.test_detailed_stats.ConstantMetricTests.test_woba -v` | ❌ Wave 0 |
| STAT-06 | FIP constant provenance/unavailable | unit + DB integration | `uv run python -m unittest tests.test_detailed_stats.ConstantMetricTests.test_fip -v` | ❌ Wave 0 |
| STAT-07 | Methodology and exact inputs persist | DB integration | `uv run python -m unittest tests.test_detailed_stats.MethodologyPersistenceTests -v` | ❌ Wave 0 |
| STAT-08 | D1/D2/D3 same schema/classification | fixture contract | `uv run python -m unittest tests.test_phase3_release.DivisionParityTests -v` | ❌ Wave 0 |
| SPLT-01 | Seven batting split codes | parser/canonical fixture | `uv run python -m unittest tests.test_ncaa_detail.BattingSplitTests -v` | ❌ Wave 0 |
| SPLT-02 | Seven pitching split codes or unavailable | parser/canonical fixture | `uv run python -m unittest tests.test_ncaa_detail.PitchingSplitTests -v` | ❌ Wave 0 |
| SPLT-03 | Split code/label/type/source/status | DB integration | `uv run python -m unittest tests.test_ncaa_detail.SplitProvenanceTests -v` | ❌ Wave 0 |
| SPLT-04 | Per-season/type ID discovery and stale-ID rejection | parser contract | `uv run python -m unittest tests.test_ncaa_detail.SplitDiscoveryTests -v` | ❌ Wave 0 |
| SPLT-05 | Missing split unavailable, never zero | unit + DB integration | `uv run python -m unittest tests.test_ncaa_detail.SplitAvailabilityTests -v` | ❌ Wave 0 |
| HIST-01 | Transfer career rows keep school/team/division/season | parser + DB integration | `uv run python -m unittest tests.test_player_history.CareerSeasonTests -v` | ❌ Wave 0 |
| HIST-02 | Aggregate includes only compatible available rows | unit | `uv run python -m unittest tests.test_player_history.CareerAggregateTests -v` | ❌ Wave 0 |
| HIST-03 | Current-season batting/pitching/fielding game logs | parser + DB integration | `uv run python -m unittest tests.test_player_history.GameLogTests -v` | ❌ Wave 0 |
| HIST-04 | Missing history/games disclose completeness | unit + DB integration | `uv run python -m unittest tests.test_player_history.HistoryCompletenessTests -v` | ❌ Wave 0 |
| HIST-05 | Request ledger, cache, concurrency, retry/budget | job integration | `uv run python -m unittest tests.test_report_jobs tests.test_player_history.CollectionBudgetTests -v` | Existing base + ❌ Wave 0 detail |
| HIST-06 | One player drills to all same-snapshot views | DB integration | `uv run python -m unittest tests.test_player_history.SnapshotDrillPathTests -v` | ❌ Wave 0 |
| OPS-03 | Safe stuck/failure/capacity diagnostics | unit + integration | `uv run python -m unittest tests.test_phase3_release.OperatorDiagnosticsTests -v` | ❌ Wave 0 |

### Required Edge-Case Matrix

| Case | Expected assertion |
|------|--------------------|
| D1, D2, D3 representative pages | Same canonical columns, scope codes, metric definitions, and availability vocabulary. [VERIFIED: codebase grep] |
| Two-way transfer | One global stable Player; distinct team-season rows; typed role facts never collapse. [VERIFIED: codebase grep] |
| Fewer than ten completed games | Complete last-10 scope with actual count 0–9 only when the full schedule set is known. [VERIFIED: codebase grep] |
| Completed doubleheader | Two exact game IDs and two membership rows. [VERIFIED: codebase grep] |
| Cancellation/postponement/no-contest | Excluded from completed-game membership. [VERIFIED: codebase grep] |
| Conference realignment | Classification uses report-season IDs, not current affiliation. [VERIFIED: codebase grep] |
| Missing opponent/conference ID | Conference scope partial/unavailable, never guessed. [VERIFIED: codebase grep] |
| Valid zero numerator | Numeric zero when denominator is positive. [VERIFIED: codebase grep] |
| Zero denominator | `undefined_denominator`, no infinity/zero. [VERIFIED: codebase grep] |
| Missing input | `missing_input`, distinct from zero. [VERIFIED: codebase grep] |
| Missing wOBA/FIP constant | `unavailable_constant` with season/division context. [VERIFIED: codebase grep] |
| Split ID drift/wrong header | Source identifier rejected; split unavailable/failed with context. [VERIFIED: codebase grep] |
| Budget exhausted mid-player family | Core can publish; supplemental section is partial with exact completed/remaining counts. [VERIFIED: codebase grep] |
| Same-build retry | Reuses only version/source-matched completed evidence. [VERIFIED: codebase grep] |
| Explicit refresh | Performs live source collection and creates successor snapshot. [VERIFIED: codebase grep] |
| Cross-organization substitution | Every new fact/scope/game/source FK rejects foreign snapshot ownership. [VERIFIED: codebase grep] |

### Sampling Rate

- **Per task commit:** Run the focused test class for that task plus `tests.test_canonical_data`. [ASSUMED]
- **Per wave merge:** Run all four Phase 3 focused modules plus `tests.test_report_jobs`, `tests.test_snapshot_persistence`, and `tests.test_ncaa_workflow`. [ASSUMED]
- **Phase gate:** Disposable-PostgreSQL `validate_phase3_release.py`, full unittest discovery, catalog validation, compile checks from root `CONTEXT.md`, and `git diff --check`. [VERIFIED: codebase grep]

### Wave 0 Gaps

- [ ] `tests/fixtures/ncaa_detail/{d1,d2,d3}/` — sanitized current-layout team fielding, split discovery/results, player career/game logs, and schedule pages. [ASSUMED]
- [ ] `tests/test_detailed_stats.py` — formula table, input lineage, constants, zero/missing/undefined. [ASSUMED]
- [ ] `tests/test_ncaa_detail.py` — parser/source contracts and all split availability states. [ASSUMED]
- [ ] `tests/test_scoped_stats.py` — exact last-10 and conference membership plus aggregation. [ASSUMED]
- [ ] `tests/test_player_history.py` — stable-ID fan-out, transfers, game links, budgets. [ASSUMED]
- [ ] `tests/test_phase3_release.py` and `scripts/validate_phase3_release.py` — D1/D2/D3, tenancy, immutability, and operator gates. [ASSUMED]
- [ ] A migration fixture proving upgrade from Alembic head `0024` preserves all published Phase 1/2 snapshots unchanged. [VERIFIED: environment probe]

The focused baseline canonical/NCAA tests passed during research; one attempted test selector was invalid because `JobLifecycleTests.test_bounded_retry_delay` does not exist, so no claim is made that the job integration suite ran. [VERIFIED: environment probe]

## Security Domain

Security enforcement is enabled because `.planning/config.json` does not disable it. [VERIFIED: codebase grep]

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no new auth behavior | Existing durable jobs remain initiated through Phase 2 authenticated report acceptance. [VERIFIED: codebase grep] |
| V3 Session Management | no new browser session behavior | Phase 3 runs in workers and persistence. [VERIFIED: codebase grep] |
| V4 Access Control | yes | Repeat organization+snapshot composite keys and tenant filters for every new table/query. [VERIFIED: codebase grep] |
| V5 Input Validation | yes | Treat NCAA HTML, labels, IDs, URLs, and numbers as untrusted; allowlist source host/path and validate exact schema/contracts. [ASSUMED] |
| V6 Cryptography | no new cryptographic primitive | Reuse SHA-256 evidence checksums; do not add custom crypto. [VERIFIED: codebase grep] |
| V10 Malicious Code | yes | No dynamic code/eval from source values; all source strings remain data. [ASSUMED] |
| V12 Files and Resources | yes | Failure HTML stays restricted and follows existing 30-day purge; successful snapshots persist extracted evidence, not full HTML. [VERIFIED: codebase grep] |
| V13 API and Web Service | yes | Fixed NCAA origin, bounded redirects/timeouts, safe error categories, and capacity limiting. [VERIFIED: codebase grep] |

ASVS category names follow the OWASP ASVS project; exact requirement numbers should be mapped by the security-review phase to the repository's selected ASVS version. [CITED: https://github.com/OWASP/ASVS]

### Known Threat Patterns for This Stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Tenant-scoped fact references another organization's snapshot | Information disclosure / Tampering | Composite organization+snapshot+fact/source foreign keys and negative DB tests. [VERIFIED: codebase grep] |
| Source URL drives arbitrary outbound request | SSRF / Information disclosure | Construct URLs only from validated NCAA IDs and fixed `https://stats.ncaa.org` paths; never fetch a persisted arbitrary URL. [ASSUMED] |
| Malicious/changed HTML poisons canonical facts | Tampering | Header/selector/source-identity contract, Decimal/count validation, checksum, and raw evidence before facts. [VERIFIED: codebase grep] |
| Upstream response or exception leaks secrets/customer data | Information disclosure | Fixed safe failure codes/messages and sanitized metadata-only operator output. [VERIFIED: codebase grep] |
| Request fan-out exhausts Browserless/worker capacity | Denial of service | Shared slot, per-build ledger, hard request/time bounds, capped concurrency, retry ceiling. [VERIFIED: codebase grep] |
| Retry publishes mixed old/new evidence | Tampering | Same-job cache key includes section/source/schema/parser; explicit refresh bypasses older builds. [VERIFIED: codebase grep] |
| Published snapshot gains later detail | Tampering / Repudiation | Publication triggers/locks plus successor-only refresh. [VERIFIED: codebase grep] |

## Operations and Observability

Add a metadata-only operator projection/CLI that reports job ID, state, attempt/generation, age/heartbeat, result snapshot ID, each requested section/scope state and safe reason, request budget used/remaining, cache/live counts, Browserless capacity category, and next retry time. [ASSUMED] It must omit player names, raw rows, exception text, query strings, credentials, and organization/customer content. [VERIFIED: codebase grep]

Define stuck conditions as configuration, not ad hoc queries: queued without dispatch/heartbeat past the existing stale threshold, running past the existing running timeout, retrying past `next_attempt_at`, and a request ledger without progress past its navigation timeout. [ASSUMED] Reuse `reconcile_stale_report_jobs()` for repair; diagnostics should observe and explain, not mutate by default. [VERIFIED: codebase grep]

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | One `StatisticScope` registry should represent source splits and computed last-10/conference scopes. | Architecture | Medium; could require adjacent typed scope models, but membership/provenance contract remains. |
| A2 | Proposed new module/table/file names are suitable. | Project Structure | Low; planner may map them to existing conventions. |
| A3 | Initial limits of 2 in-session navigations, 150 pages, 45s/page, and 12m/build are safe starting values. | Request Ledger | High; validate in staging telemetry before production lock. |
| A4 | Conventional OPS/ISO/BB%/K%/BABIP/WHIP/per-nine/K-BB formulas listed are the desired product definitions. | Metric Registry | High; user/product confirmation and golden fixtures should lock exact definitions/rounding. |
| A5 | Player game-log rows can be uniquely linked to schedule games with date/opponent/result plus explicit doubleheader evidence. | Schedule-First Identity | High; capture live fixtures and mark ambiguous rows partial. |
| A6 | Career grid source links or season lookup can resolve each historical team ID exactly. | Player Fan-Out | High; if not, the affected career row must be partial/unavailable. |
| A7 | Proposed availability/reason code vocabulary is acceptable. | Availability | Medium; codes may be renamed but must stay machine-safe and semantically distinct. |
| A8 | A reusable metadata-only projection plus `inspect_report_jobs.py` as the minimum OPS-03 surface is selected. | Operations | Resolved in Plans 03-07/03-08; later adapters may consume the same safe projection. |
| A9 | Proposed expanded count columns, scope fields, request-ledger fields, and exact new module/table names fit the final schema. | Data Shape / Project Structure | Medium; preserve semantics if names or normalization change. |
| A10 | The proposed `MetricResult` API and metric status vocabulary are the right implementation form. | Metric Pattern | Medium; equivalent typed result objects are acceptable. |
| A11 | Summing compatible counts before recalculating rates is the required career/scoped aggregation behavior for every listed rate. | Pitfalls | Medium; verify each metric's compatibility rules with golden fixtures. |
| A12 | Proposed test files, focused commands, release-script name, and per-commit sampling cadence match the planner's final wave structure. | Validation Architecture | Low; update commands if tasks split differently. |
| A13 | Fixed-host URL construction, no dynamic evaluation, and the proposed operator redaction fields cover the new Phase 3 security surface. | Security / Operations | Medium; security review may add stricter controls. |
| A14 | Page selectors and exact URL contracts observed in current upstream source will remain valid until fixture capture. | NCAA Source Contract | High; validate live before locking parser versions. |

## Resolved Planning Questions

1. **NCAA split identifiers/controls — resolved by fixture discovery and fail-closed contracts.**
   - Selected outcome: Phase 3 first captures sanitized D1/D2/D3 control and result-grid fixtures through the existing Browserless boundary. Identifiers are discovered and certified per season/stat type/label/header/player-link contract; production never bakes historical IDs into constants. A missing result is stored as explicit source-absence evidence, and stale/mismatched identifiers fail closed. [VERIFIED: plans 03-03 and 03-04]

2. **wOBA weights and FIP constants — resolved as explicit unavailability by default.**
   - Selected outcome: Phase 3 defines an append-only, provenance-rich NCAA constant-set contract but seeds no rows. wOBA and FIP return `unavailable_constant` unless an exact season/division/definition set is independently verified; MLB or guessed/fitted third-party constants are never substituted. [VERIFIED: plans 03-01 and 03-08]

3. **Historical team identity — resolved as exact identity or partial/unavailable.**
   - Selected outcome: A career row is accepted with authoritative team identity only from a source link or exact season-specific lookup. Text-only ambiguous teams and ambiguous current-season game links remain partial/unavailable with exact reasons; they are never guessed or name-linked across seasons. [VERIFIED: plan 03-05]

4. **Operator diagnostics surface — resolved as reusable metadata projection plus CLI minimum.**
   - Selected outcome: Models/services expose a provider-neutral safe metadata projection for job, section, request-budget, capacity, and external refresh attempts; `scripts/inspect_report_jobs.py` is the minimum read-only operator surface. The projection excludes customer/raw/secret data and can support later structured metrics or internal adapters without changing Phase 3 canonical semantics. [VERIFIED: plans 03-07 and 03-08]

## Sources

### Primary (HIGH confidence)

- [Official NCAA Baseball Statistics Rules](https://s3.amazonaws.com/fs.ncaa.org/Docs/stats/Stats_Manuals/Baseball.pdf) — official scoring, innings-as-outs, AVG/OBP/SLG/ERA/fielding formulas. [CITED: https://s3.amazonaws.com/fs.ncaa.org/Docs/stats/Stats_Manuals/Baseball.pdf]
- [NCAA Baseball Statistics & Records](https://www.ncaa.org/championships/statistics-and-records/baseball/) — official statistics resource index. [CITED: https://www.ncaa.org/championships/statistics-and-records/baseball/]
- Repository models/canonical/jobs/snapshots/scraper/tests — existing architecture, constraints, cache, publication, and validation contracts. [VERIFIED: codebase grep]
- [baseballr current team-stat source](https://github.com/billpetti/baseballr/blob/master/R/ncaa_team_player_stats.R) — current NCAA season-team/category endpoint, fielding support, parser selector. [CITED: https://github.com/billpetti/baseballr/blob/master/R/ncaa_team_player_stats.R]
- [baseballr current player-log source](https://github.com/billpetti/baseballr/blob/master/R/ncaa_game_logs.R) — current player endpoint and career/game grid selectors. [CITED: https://github.com/billpetti/baseballr/blob/master/R/ncaa_game_logs.R]
- [baseballr current schedule source](https://github.com/BillPetti/baseballr/blob/master/R/ncaa_schedule_info.R) — contest/team/conference identity fields. [CITED: https://github.com/BillPetti/baseballr/blob/master/R/ncaa_schedule_info.R]
- [baseballr releases](https://github.com/BillPetti/baseballr/releases) — endpoint redesign and Akamai/browser behavior. [CITED: https://github.com/BillPetti/baseballr/releases]

### Secondary (MEDIUM confidence)

- [sportsdataverse/baseballr-data](https://github.com/sportsdataverse/baseballr-data) — operational evidence that NCAA capture is slow/rate-sensitive and resumable. [CITED: https://github.com/sportsdataverse/baseballr-data]
- [collegebaseball split docs](https://collegebaseball.readthedocs.io/en/latest/season_stats.html) — historical seven split-family vocabulary; current IDs remain unverified. [CITED: https://collegebaseball.readthedocs.io/en/latest/season_stats.html]
- [ncaa_bbStats advanced metrics docs](https://collegebaseballstatspackage.readthedocs.io/en/latest/advanced_stats.html) — evidence that NCAA-calibrated constants can be fitted, but not authority for NCAA-published weights. [CITED: https://collegebaseballstatspackage.readthedocs.io/en/latest/advanced_stats.html]
- [OWASP ASVS](https://github.com/OWASP/ASVS) — security category framework. [CITED: https://github.com/OWASP/ASVS]

### Tertiary (LOW confidence)

- None used as implementation authority. [VERIFIED: research log]

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — existing pins/imports/migration head verified locally; no new package is recommended. [VERIFIED: environment probe]
- Architecture: HIGH — it extends existing code seams and locked snapshot rules; proposed exact names/budgets remain assumptions. [VERIFIED: codebase grep]
- NCAA team/player page shapes: MEDIUM — verified in current upstream source/release notes but not captured through this project's live Browserless session. [CITED: https://github.com/BillPetti/baseballr/releases]
- Split discovery: LOW — family vocabulary is known, but current IDs/controls require a live fixture spike. [CITED: https://collegebaseball.readthedocs.io/en/latest/season_stats.html]
- Metric formulas: HIGH for NCAA official AVG/OBP/SLG/ERA/FPCT; MEDIUM for conventional derived formulas pending product lock. [CITED: https://s3.amazonaws.com/fs.ncaa.org/Docs/stats/Stats_Manuals/Baseball.pdf]
- Pitfalls: HIGH — mostly direct consequences of locked decisions and existing tested contracts. [VERIFIED: codebase grep]

**Research date:** 2026-11-15
**Valid until:** 2026-11-22 for NCAA page selectors/IDs; 2026-12-15 for internal architecture and official formula guidance. [ASSUMED]
