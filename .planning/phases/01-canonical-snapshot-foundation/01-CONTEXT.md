# Phase 1: Canonical Snapshot Foundation - Context

**Gathered:** 2026-11-01
**Status:** Ready for planning

<domain>
## Phase Boundary

Phase 1 adds managed PostgreSQL persistence, migrations, canonical snapshot/provenance records, durable report jobs, section state, idempotency, cache/version controls, and a compatibility adapter for the existing NCAA batting and pitching workflow. It must publish representative D1, D2, and D3 results without changing the public product yet or regressing the selector, scraper, two-way classification, cache, or basic workbook.

This phase does not add authentication, organizations, fielding, split statistics, career history, game logs, the detailed workbook, or Power BI. It establishes the organization-ready data and job boundaries those later phases require.

</domain>

<decisions>
## Implementation Decisions

### Publication and Section Completeness
- **D-01:** Batting and pitching are publication-critical core sections. If either section is unavailable, malformed, or fails collection or validation, the job fails and publishes no snapshot.
- **D-02:** A core section with zero player rows is valid only when the NCAA page loaded successfully and explicitly represents a verified empty result. A missing or blocked table is not an empty result.
- **D-03:** The section model must distinguish publication-critical core sections from supplemental sections. When later phases add fielding, splits, career history, and game logs, a supplemental-section failure may publish an explicitly `partial` snapshot; it must never masquerade as complete.
- **D-04:** Snapshot publication is atomic. Required canonical facts become visible together, and a failed or interrupted transaction cannot leave a snapshot marked `ready`.
- **D-05:** Canonical snapshot readiness is independent from artifact readiness. Once required facts commit atomically, the snapshot may be `ready`; workbook generation has its own retryable artifact state.

### Refresh, Cache, and Idempotency
- **D-06:** Reopening a saved snapshot or downloading its stored artifact must not contact NCAA. Immutable snapshots provide the fast path for repeated use during a series.
- **D-07:** Every explicit refresh bypasses source data from older builds, performs a live NCAA collection, and creates a new successor snapshot. A 36-48 hour cross-build source cache must not silently serve post-game statistics.
- **D-08:** Cache reuse is limited to the same accepted build: retries may reuse successfully collected sections, and duplicate requests with the same idempotency identity attach to the active job.
- **D-09:** Repeated refresh clicks while a refresh is running return the active job instead of consuming another browser session or creating another snapshot.
- **D-10:** Every value sourced from cached same-build evidence retains its original source collection time. Job start, publication, or download time must never be presented as source freshness.

### Player Identity
- **D-11:** An NCAA player ID is the authoritative source identity and must be preserved in canonical records and provenance.
- **D-12:** When a player ID is absent, batting and pitching rows may merge only within the same team-season when normalized name and supporting source details produce one unique, consistent match. Otherwise the record remains unresolved.
- **D-13:** ID-less records must not auto-link across teams or seasons as transfers. Cross-team career linkage requires a stable NCAA player ID or another authoritative source introduced by a later phase.
- **D-14:** If one NCAA player ID appears with materially conflicting names in the same team-season, preserve the evidence, classify an identity conflict, and block publication rather than silently merging or splitting it.
- **D-15:** ID-less same-name teammates remain separate when jersey number, position, or other source fields distinguish them. Each receives a deterministic snapshot-scoped fallback identity and an explicit missing-stable-ID status.

### Source Evidence and Failure Records
- **D-16:** Every published snapshot permanently retains exact extracted NCAA source rows alongside normalized facts, including source URL and identifiers, collection time, parser version, schema version, and payload checksum.
- **D-17:** Complete NCAA HTML is not a permanent part of successful snapshots. Relevant HTML may be retained only for failed or blocked builds, with restricted operator access and automatic deletion after 30 days.
- **D-18:** After troubleshooting HTML expires, a failed build permanently retains a sanitized operational record: job and section states, source identifiers and URLs, timestamps, parser versions, row counts, checksums, and safe failure categories. Secret-bearing or raw exception text is excluded.

### the agent's Discretion
- The user delegated the snapshot-versus-artifact readiness boundary. The selected rule is D-05: publish complete canonical facts independently and make artifact generation separately retryable.
- Exact database, migration, queue, ORM, and object-storage libraries remain implementation choices for research and planning, provided they satisfy the locked durability, atomicity, provenance, and compatibility decisions above.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Milestone and Phase Contract
- `.planning/V2-SPEC.md` — Defines the v2.0 product contract, Phase 1 scope, exclusions, constraints, and acceptance criteria.
- `.planning/REQUIREMENTS.md` — Defines SNAP-01 through SNAP-04, DATA-01 through DATA-10, OPS-01, OPS-02, OPS-04, and OPS-05 assigned to Phase 1.
- `.planning/ROADMAP.md` — Defines the Phase 1 goal, success criteria, dependencies, and three-plan delivery sequence.
- `.planning/PROJECT.md` — Defines milestone-wide product decisions, immutable snapshot rules, canonical-model constraints, NCAA coverage, and external failure boundaries.

### Existing Application Contract
- `CONTEXT.md` — Maps the current runtime flow, change impact, external boundaries, and required regression checks.

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `roster_scraper.py::_parse_ncaa_stat_table()` already extracts `_player_id` and the complete NCAA grid row. Phase 1 can preserve this evidence instead of dropping the identifier during normalization.
- `roster_scraper.py::_read_json_cache()`, `_write_json_cache()`, and `_atomic_write()` provide an existing successful-result cache with atomic filesystem writes and a cache version. The durable design can preserve the version-aware behavior while moving canonical state out of local files.
- `usage_limits.py::browserless_slot()` already enforces process-safe Browserless capacity and releases slots after errors.
- `roster_scraper.py::split_roster()` contains the established two-way-player classification behavior that compatibility tests must preserve.
- `app.py::_create_workbook()` and `_write_worksheet()` provide the existing two-sheet artifact path that must continue to pass during the migration.

### Established Patterns
- `/scrape` currently validates the request, collects NCAA data synchronously, splits players, builds an in-memory workbook, and returns the download in one request. Phase 1 adds a parallel durable job boundary without changing this public interaction yet.
- NCAA selection uses exact `season_team_id` values and catalog metadata that already include `team_id`, `season_id`, division, and year.
- The current result cache stores only successful whole-team results and defaults to a six-hour TTL; failures are not cached. The new same-build cache must become section-aware and include schema/parser versions and source timestamps.
- NCAA batting and pitching rows are merged by `_player_id` when present and normalized name otherwise. The resulting public row currently discards `_player_id`, raw source rows, source URLs, and collection metadata.
- Innings pitched remain text in Excel because NCAA `7.2` means seven and two-thirds innings. Canonical storage must convert innings to outs or thirds while preserving the raw source value.

### Integration Points
- `app.py::scrape()` is the compatibility boundary for existing selector, error, branding, and workbook behavior.
- `roster_scraper.py::_scrape_ncaa_team()` is the acquisition-to-cache seam where team/season metadata, source URLs, raw rows, normalized facts, and collection timestamps can be handed to the durable publisher.
- `roster_scraper.py::_fetch_ncaa_pages()` and `usage_limits.py::browserless_slot()` are the NCAA concurrency boundary durable workers must reuse rather than bypass.
- `tests/test_ncaa_workflow.py` and `tests/test_app_routes.py` already cover representative D1/D2/D3 IDs, two-way merging, successful caching, failure non-caching, Browserless capacity, API errors, and workbook output.

</code_context>

<specifics>
## Specific Ideas

- The freshness model is based on a three-game series: a coach may prepare Friday evening, Saturday afternoon, and Sunday afternoon. Reopening each dated snapshot should be instant, but a refresh after each game must retrieve changed live statistics.
- A saved snapshot and a source-response cache solve different jobs. Snapshots provide repeat access and historical evidence; cache is only an efficiency mechanism inside one accepted build and must never make stale data look current.
- Trustworthiness takes precedence over forced completeness: unresolved identities remain explicit, and conflicting authoritative IDs block publication.

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 1-Canonical Snapshot Foundation*
*Context gathered: 2026-11-01*
