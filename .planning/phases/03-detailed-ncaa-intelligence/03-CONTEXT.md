# Phase 3: Detailed NCAA Intelligence - Context

**Gathered:** 2026-11-15
**Status:** Ready for planning

<domain>
## Phase Boundary

Phase 3 expands each immutable NCAA snapshot from the existing batting/pitching foundation into one auditable intelligence model containing expanded season facts, fielding, versioned derived metrics, seven situational batting and pitching split families, last-10 and conference-only scopes, transfer-aware career season history, career aggregates, and current-season player game logs. Every section must retain source identity and completeness or an explicit unavailable state.

This phase builds and validates canonical data only. Workbook presentation remains Phase 4, and the embedded opponent dashboard remains Phase 5.

</domain>

<decisions>
## Implementation Decisions

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

### Folded Todos
- **Add last-10 and conference-only stats:** Folded from `.planning/todos/pending/2026-11-09-add-last-10-and-conference-only-stats.md`. Phase 3 will add auditable `last_10_completed_games` and `conference_only` scopes, retain exact game/conference evidence, cover batting/pitching/fielding where supported, and include D1/D2/D3 plus edge-case fixtures.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Product and Phase Contract
- `.planning/V2-SPEC.md` § Phase 3 — Locks the detailed NCAA intelligence outcome, inclusions, exclusions, and acceptance criteria.
- `.planning/REQUIREMENTS.md` — Defines STAT-01–08, SPLT-01–05, HIST-01–06, and OPS-03, plus the existing DATA provenance guarantees Phase 3 must preserve.
- `.planning/ROADMAP.md` § Phase 3 — Defines the phase goal, success criteria, dependencies, and four intended capability slices.
- `.planning/PROJECT.md` — Defines the milestone-wide canonical-model, NCAA coverage, provenance, external-failure, snapshot, and consumer-parity constraints.
- `.planning/todos/pending/2026-11-09-add-last-10-and-conference-only-stats.md` — Defines the folded recent-form and conference-only scope requirements and their edge cases.

### Prior Snapshot Contract
- `.planning/phases/01-canonical-snapshot-foundation/01-CONTEXT.md` — Locks critical versus supplemental sections, atomic immutable publication, live refresh, same-build cache reuse, player identity, and source evidence rules.
- `CONTEXT.md` — Maps the live application, NCAA/Browserless boundary, workbook compatibility path, durable jobs, artifacts, and required repository checks.

### Existing Canonical and Collection Boundary
- `roster_scout/models.py` — Existing players, player-team-season identity, source records, report sections, typed batting/pitching facts, and metric definitions.
- `roster_scout/canonical.py` — Existing deterministic NCAA normalization, missing-value handling, innings-to-outs conversion, stable identity grouping, canonical facts, and metric lineage.
- `roster_scout/jobs.py` — Existing durable section contract, same-build cache validation, exact source-record persistence, canonical publication path, and bounded retry behavior.
- `roster_scraper.py` — Proven NCAA Browserless acquisition, stat-grid parsing, player-ID extraction, source URLs, cache boundary, and D1/D2/D3 normalization behavior.

### Verification Patterns
- `tests/test_canonical_data.py` — Canonical identity, fact, missing-value, innings, and metric-lineage fixtures.
- `tests/test_report_jobs.py` — Durable collection, section-cache, retry, publication, and failure-state contracts.
- `tests/test_ncaa_workflow.py` — NCAA catalog, Browserless, parser, caching, two-way classification, and division-parity contracts.
- `tests/test_snapshot_persistence.py` — PostgreSQL immutability, provenance, ownership, and migration lifecycle contracts.

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `roster_scout.canonical.CanonicalSourceRecord`, `CanonicalFact`, `MetricLineage`, and `canonicalize_ncaa_collection()` already provide a deterministic source-to-fact seam with explicit raw evidence and metric versions.
- `Player`, `PlayerTeamSeason`, `SourceRecord`, `ReportSection`, `BattingFact`, `PitchingFact`, and `MetricDefinition` provide the ownership and provenance pattern that fielding, splits, career, game logs, and scoped facts should follow.
- `roster_scout.jobs.CollectedSection` already carries criticality, section status, source identifiers, parser/schema versions, collection time, and safe failure category.
- `_fetch_ncaa_pages()`, `_parse_ncaa_stat_table_result()`, and `_scrape_ncaa_team_locked()` provide the proven NCAA/Browserless acquisition and successful-result cache boundary.

### Established Patterns
- Core batting/pitching collection is atomic; supplemental failures may produce an explicit partial snapshot without weakening immutable publication.
- Stable NCAA player IDs are authoritative. ID-less rows may merge only inside one team-season when support evidence uniquely corroborates them and never create cross-season transfer identity.
- Raw source rows and exact source identifiers persist before typed facts, so recomputation and audit do not depend on a future live NCAA response.
- Database code remains connection-free, callers own transactions, organization ownership is enforced through composite keys, and production PostgreSQL is the persistence authority.
- Same-build cache reuse is section/version/source-bound. Explicit customer refresh creates a new live collection and linked successor snapshot.

### Integration Points
- Extend NCAA acquisition at the existing section collection seam rather than creating a second scraper runtime or bypassing Browserless capacity controls.
- Extend `canonicalize_ncaa_collection()` and `_persist_canonical()` so each new typed fact references same-snapshot source evidence and the canonical player/team-season identity.
- Add migrations and constraints following the existing composite snapshot/organization/source ownership pattern.
- Extend section status and operator diagnostics so every requested supplemental family is complete, partial, unavailable, or failed with sanitized evidence.
- Build D1/D2/D3 deterministic fixtures before changing the Phase 4 workbook or Phase 5 Power BI consumers.

</code_context>

<specifics>
## Specific Ideas

- Coaches need recent form after each game of a weekend series, so last-10 must be tied to the exact completed-game set captured by that snapshot rather than a vague rolling label.
- Conference-only performance is useful only when conference membership is historically correct for the report season; current affiliation is not sufficient evidence.
- The product should expose all valid data while making sample size and completeness obvious, allowing coaches—not arbitrary software thresholds—to judge usefulness.
- Every downstream workbook and dashboard metric must come from these canonical definitions so Excel and Power BI cannot drift.

</specifics>

<deferred>
## Deferred Ideas

- Detailed workbook tabs, formatting, precision, and presentation remain Phase 4.
- Embedded opponent dashboard pages, Power BI semantic delivery, filters, and embed-token behavior remain Phase 5.

</deferred>

---

*Phase: 3-Detailed NCAA Intelligence*
*Context gathered: 2026-11-15*
