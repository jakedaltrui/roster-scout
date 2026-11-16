# Phase 03: Detailed NCAA Intelligence - Pattern Map

**Mapped:** 2026-11-15
**Files analyzed:** 18 new or modified files plus fixture directories
**Analogs found:** 18 / 18

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `roster_scout/models.py` | model | CRUD / immutable snapshot persistence | Existing `ReportSection`, `SourceRecord`, `BattingFact`, `PitchingFact`, `MetricValue` in the same file | exact |
| `roster_scout/enums.py` | config | lifecycle validation | Existing `SNAPSHOT_STATUSES` and `SECTION_STATUSES` in the same file | exact |
| `roster_scout/canonical.py` | service / transform | source rows → typed facts | Existing `CanonicalSourceRecord`, `CanonicalFact`, `MetricLineage`, `canonicalize_ncaa_collection()` | exact |
| `roster_scout/metrics.py` | utility / service | deterministic transform | `roster_scout/canonical.py` numeric parsing and metric lineage | role-match |
| `roster_scout/ncaa_detail.py` | parser / service | request-response → validated source evidence | `roster_scraper.py` NCAA dataclasses, `_parse_ncaa_stat_table_result()`, `_collect_ncaa_team()` | exact boundary |
| `roster_scout/scopes.py` | service / transform | audited games → scoped facts | `roster_scout/canonical.py` canonical dataclasses and deterministic grouping | role-match |
| `roster_scout/player_detail.py` | service / read projection | authorized published snapshot → fixed player JSON views | `roster_scout/reports.py` organization-scoped report/detail loaders | exact boundary |
| `roster_scout/jobs.py` | service / worker | event-driven durable collection and persistence | Existing `CollectedSection`, cache reconstruction, `_persist_canonical()`, `run_report_job()` | exact |
| `roster_scout/snapshots.py` | service | transactional validation / publication | Existing `publish_snapshot()` parity and trace checks | exact |
| `roster_scraper.py` | external-source adapter | request-response / browser I/O | Existing `_fetch_ncaa_pages()`, `_parse_ncaa_stat_table_result()`, `_collect_ncaa_team()` | exact |
| `migrations/versions/0025_detailed_ncaa_intelligence.py` | migration | additive DDL / immutability | `0001_canonical_snapshot_foundation.py` plus `0024_durable_workbook_artifacts.py` | exact |
| `scripts/inspect_report_jobs.py` | operator CLI | read-only DB query → sanitized JSON | `scripts/report_usage.py` | exact |
| `scripts/validate_phase3_release.py` | release gate | batch subprocess orchestration | `scripts/validate_phase1_release.py` | exact |
| `tests/test_detailed_stats.py` | unit + DB integration test | table-driven transform / CRUD | `tests/test_canonical_data.py`, `tests/test_snapshot_persistence.py` | exact |
| `tests/test_ncaa_detail.py` | parser contract test | fixture HTML → typed evidence | `tests/test_ncaa_workflow.py::NcaaParserCorruptionTests` | exact |
| `tests/test_scoped_stats.py` | unit + DB integration test | games → scope membership / aggregates | `tests/test_canonical_data.py`, `tests/test_snapshot_persistence.py` | role-match |
| `tests/test_player_history.py` | parser + job integration test | stable player page fan-out → persisted history/logs | `tests/test_ncaa_workflow.py`, `tests/test_report_jobs.py` | role-match |
| `tests/test_phase3_release.py` | release-contract test | manifest / schema / isolation assertions | Phase 1 release-contract tests and `tests/test_snapshot_persistence.py` | exact |
| `tests/fixtures/ncaa_detail/{d1,d2,d3}/` | sanitized fixture data | file I/O | `tests/fixtures/d1_batting.html`, `d2_*`, `d3_*` | exact |

## Pattern Assignments

### `roster_scout/models.py` (model, immutable snapshot CRUD)

**Analog:** existing `ReportSection`, `SourceRecord`, and typed fact models in `roster_scout/models.py`.

**Snapshot/tenant ownership pattern** (`roster_scout/models.py:647-710`):

```python
class ReportSection(UUIDPrimaryKey, Base):
    organization_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    section: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending"
    )
    __table_args__ = (
        UniqueConstraint(
            "id", "snapshot_id", "organization_id",
            name="uq_report_sections_snapshot_owner",
        ),
        ForeignKeyConstraint(
            ["snapshot_id", "organization_id"],
            ["report_snapshots.id", "report_snapshots.organization_id"],
            name="fk_report_sections_snapshot_organization",
        ),
        CheckConstraint(_allowed("status", SECTION_STATUSES),
                        name="ck_report_sections_status"),
    )
```

**Evidence-linked typed-fact pattern** (`roster_scout/models.py:766-861`):

```python
class BattingFact(UUIDPrimaryKey, Base):
    organization_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    player_team_season_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    source_record_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    __table_args__ = (
        ForeignKeyConstraint(
            ["player_team_season_id", "snapshot_id", "organization_id"],
            ["player_team_seasons.id", "player_team_seasons.snapshot_id",
             "player_team_seasons.organization_id"],
            name="fk_batting_facts_player_snapshot_organization",
        ),
        ForeignKeyConstraint(
            ["source_record_id", "player_team_season_id", "snapshot_id",
             "organization_id"],
            ["source_records.id", "source_records.player_team_season_id",
             "source_records.snapshot_id", "source_records.organization_id"],
            name="fk_batting_facts_source_identity",
        ),
    )
```

**Apply to:** `FieldingFact`, `StatisticScope`, scope-game membership, scoped typed facts, `Game`, career-season facts, game-log facts, request-ledger rows, and extended metric values. Every snapshot child repeats `organization_id` + `snapshot_id`; player facts also bind the same `player_team_season_id` and exact `source_record_id`. Add named nonnegative checks for all counts, a unique snapshot/scope identity, and explicit check constraints for status vocabularies. Never rely on a bare UUID foreign key where a composite owner key exists.

**Metric definition/value analog** (`roster_scout/models.py:864-892`): retain append-only `(code, version)` definitions, but extend value rows with tagged status, exact JSONB inputs, sample JSONB, scope reference, and optional constant-set version. The value/reason constraint must become status-aware rather than truthiness-based.

---

### `roster_scout/enums.py` (lifecycle config)

**Analog:** `roster_scout/enums.py:3-6`.

```python
SNAPSHOT_STATUSES = ("draft", "ready", "partial", "failed")
SECTION_STATUSES = ("pending", "complete", "unavailable", "failed")
```

Add `partial` to `SECTION_STATUSES` before any collector emits it. Define scope/metric status tuples here only if multiple model/service layers consume them; otherwise keep a single module-level registry in the owning service. Migration `0025` must replace the existing `ck_report_sections_status` constraint in the same transaction.

---

### `roster_scout/canonical.py` (canonical transform)

**Analog:** existing frozen canonical dataclasses and strict numeric parsing.

**Typed evidence objects** (`roster_scout/canonical.py:21-73`):

```python
@dataclass(frozen=True)
class CanonicalSourceRecord:
    section: str
    row_ordinal: int
    source_url: str
    source_player_id: str | None
    collected_at: object
    parser_version: str
    schema_version: str
    payload_sha256: str
    raw_values: dict

@dataclass(frozen=True)
class CanonicalFact:
    section: str
    identity_key: str
    source_record: tuple[str, int]
    values: dict
```

**Missing/invalid count behavior** (`roster_scout/canonical.py:83-125`):

```python
def _integer(row: NcaaSourceRow, *keys: str) -> int | None:
    value = _raw(row, *keys)
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"Invalid integer {value!r} for {keys[0]}.") from exc
    if parsed < 0:
        raise ValueError(f"Integer for {keys[0]} cannot be negative: {value!r}.")
    return parsed
```

**Stable identity pattern** (`roster_scout/canonical.py:181-203`): authoritative NCAA IDs group rows across typed roles; ID-less rows never create cross-season history identity.

**Apply to:** expand typed batting/pitching counts, fielding, career seasons, and current-season logs as frozen canonical records. Preserve `None` exactly, reject negative/invalid values, store innings as outs, and resolve two-way players through one authoritative identity while facts remain separately typed.

---

### `roster_scout/metrics.py` (deterministic metric engine)

**Analog:** `Decimal` parsing and `MetricLineage` in `roster_scout/canonical.py:5-15,59-66,281-287`.

```python
@dataclass(frozen=True)
class MetricLineage:
    identity_key: str
    code: str
    value: Decimal | None
    provenance: str
    definition_version: str = METRIC_DEFINITION_VERSION
```

The new engine should replace the current value-only helper with a frozen tagged result carrying `value`, `status`, `reason_code`, `inputs`, and `sample`. Use `Decimal` throughout. Test `value is None`, never truthiness: numeric zero is available when required inputs exist and the denominator is positive. Register formulas once and call the same registry for full-season, split, last-10, conference, and career aggregates. wOBA/FIP return `unavailable_constant` until an approved NCAA constant set is persisted.

---

### `roster_scout/ncaa_detail.py` and `roster_scraper.py` (untrusted NCAA parser/acquisition)

**Analog:** NCAA source dataclasses (`roster_scraper.py:93-150`), Browserless boundary (`1903-2034`), parser contract (`2110-2182`), and collection seam (`2325-2411`).

**Validated header/row-shape pattern** (`roster_scraper.py:2110-2182`):

```python
if not _has_ncaa_stat_grid(html):
    raise NcaaAccessError("NCAA stats table is missing or blocked.")
signature = NCAA_CORE_HEADER_SIGNATURES.get(section)
if signature is None:
    raise NcaaSchemaError("NCAA stats schema validation failed: unsupported section.")
...
if any(count != 1 for count in required_header_counts.values()):
    raise NcaaSchemaError(
        f"NCAA stats schema validation failed: unsupported {section} headers."
    )
...
if len(cells) != len(headers):
    raise NcaaSchemaError(
        "NCAA stats schema validation failed: row shape changed."
    )
```

**Browserless capacity and sanitized-error pattern** (`roster_scraper.py:1944-2014`): map 429 to `BrowserCapacityExceeded`, never echo authenticated WebSocket/exception text, and release the remote browser in `finally`.

**Evidence construction pattern** (`roster_scraper.py:2300-2322,2356-2383`): hash the HTML, retain source URL and discovered player ID/URL, remove private helper keys from raw values, and wrap schema/source failures in restricted failure evidence.

**Apply to:** fielding category discovery, seven split families per stat type, stable player career/game grids, schedule/results, and season-specific conference evidence. Construct every outbound URL from validated numeric/source IDs and fixed `https://stats.ncaa.org` paths; never fetch a persisted arbitrary URL. Discover category/split IDs per season and validate selector, normalized header signature, requested label/type, season identity, and player-link shape before accepting rows.

---

### `roster_scout/scopes.py` (audited scope selection and aggregation)

**Analog:** deterministic grouping in `roster_scout/canonical.py:181-278` and strict canonical fact objects at `51-73`.

Use pure functions over immutable canonical `Game` and fact inputs. Sort completed games by `(completed_at_or_date, doubleheader_ordinal, source_game_id)` and persist the exact selected game IDs. `last_10_completed_games` is complete with 0–9 games only when the full schedule evidence is complete. Conference classification returns unknown when either season-specific membership is absent or from a different season. Aggregate compatible counts first and then invoke `roster_scout.metrics`; never average source rates.

---

### `roster_scout/player_detail.py` (authorized published-snapshot projection)

**Analog:** `load_report_detail()` / `load_report_status()` in `roster_scout/reports.py` and the audited JSON-not-found pattern in `roster_scout/report_routes.py`.

Keep the service connection-free and accept the caller-owned session plus explicit `organization_id`, `report_id`, `snapshot_id`, `player_id`, and bounded view code. Join through the published `ready`/`partial` snapshot and same-snapshot `PlayerTeamSeason`; never authorize from a request-provided organization. Return fixed JSON-safe season, split, career, aggregate, game-log, and completeness projections. The route derives organization from `g.authorization_context`, returns the existing indistinguishable audited 404 for absent/foreign identifiers, and never contacts NCAA or mutates the snapshot.

---

### `roster_scout/jobs.py` (durable supplemental collection)

**Analog:** `CollectedSection`, same-build cache identity, evidence-first commit, and durable job replay.

**Section contract** (`roster_scout/jobs.py:81-118`):

```python
@dataclass(frozen=True)
class CollectedSection:
    name: str
    critical: bool
    rows: tuple[dict, ...] = field(default_factory=tuple)
    verified_empty: bool = False
    source_url: str | None = None
    source_stat_category_id: str | None = None
    collected_at: datetime | None = None
    parser_version: str = PARSER_VERSION
    schema_version: str = SCHEMA_VERSION
    status: str = "complete"
    failure_category: str | None = None
```

**Exact cache match** (`roster_scout/jobs.py:455-504`): cache reuse requires the same job, section, source team/season/team-season/category, schema version, and parser version. Extend the key with source scope/player/page identity where required. Explicit refresh still creates a new job/snapshot and performs live collection.

**Evidence-before-facts transaction boundary** (`roster_scout/jobs.py:1218-1244`): persist sections/source records and commit; in the following transaction persist canonical facts and publish. New supplemental facts must be collected before publication and included in the second transaction, never patched into a published snapshot later.

**Apply to:** bounded section families such as `fielding`, `splits:batting`, `splits:pitching`, `player_history`, `game_logs`, `schedule`, `last_10`, and `conference`. Core batting/pitching remain critical. Supplemental failures materialize `partial`, `unavailable`, or `failed` rows with safe reason codes and exact completed/remaining counts. A per-build request ledger must record planned source identity, cache/live outcome, timing, safe category, and budget remaining; cap concurrency/time/pages through validated configuration.

---

### `roster_scout/snapshots.py` (transactional publication)

**Analog:** `publish_snapshot()` at `roster_scout/snapshots.py:22-220`.

**Parity and trace pattern** (`roster_scout/snapshots.py:106-177`):

```python
for required in ("batting", "pitching"):
    section = by_name.get(required)
    if section is None or not section.critical or section.status != "complete":
        raise SnapshotPublicationError(f"{required} is not complete")
    ...
    if section.row_count != source_count:
        raise SnapshotPublicationError(
            f"{required} row count does not match source evidence"
        )
    if section.row_count != fact_count:
        raise SnapshotPublicationError(
            f"{required} row count does not match typed facts"
        )
```

Extend publication checks to every requested supplemental family: a materialized section/scope status must exist; complete row counts must match typed facts and uniquely linked same-snapshot source evidence; scope membership must reference same-snapshot games. Keep batting/pitching as the only critical sections. Existing final state selection (`snapshots.py:214-218`) already publishes `partial` when any supplemental section is not complete.

---

### `migrations/versions/0025_detailed_ncaa_intelligence.py` (additive schema migration)

**Analogs:** table/constraint design in `0001_canonical_snapshot_foundation.py:192-355`, immutability triggers at `497-542`, and focused additive upgrade/downgrade style in `0024_durable_workbook_artifacts.py:17-50`.

**Composite ownership DDL** (`0001_canonical_snapshot_foundation.py:272-314`):

```python
sa.ForeignKeyConstraint(
    ["player_team_season_id", "snapshot_id", "organization_id"],
    ["player_team_seasons.id", "player_team_seasons.snapshot_id",
     "player_team_seasons.organization_id"],
    name="fk_batting_facts_player_snapshot_organization",
),
sa.ForeignKeyConstraint(
    ["source_record_id", "snapshot_id", "organization_id"],
    ["source_records.id", "source_records.snapshot_id",
     "source_records.organization_id"],
    name="fk_batting_facts_source_snapshot_organization",
),
```

Create all new tables and named checks additively, update the section-status constraint, and install immutable-child triggers for every new snapshot-owned table. `downgrade()` must reverse triggers/constraints/tables in dependency order. Migration tests must prove `0024 → 0025 → 0024 → head` and a fresh install while existing published Phase 1/2 snapshots remain byte/row equivalent.

---

### `scripts/inspect_report_jobs.py` (sanitized operator CLI)

**Analog:** `scripts/report_usage.py:21-136`.

Use `SafeArgumentParser` so invalid input is replaced by fixed text, validate UUID/time arguments before constructing database dependencies, create/dispose the engine inside `main()`, print sorted JSON on stdout, and return a fixed nonzero error with no exception text. The projection may include job/snapshot IDs, lifecycle state, generation/attempt, age/heartbeat, section/scope state and safe reason, request budget, cache/live counts, capacity category, and next retry. It must omit player names, raw rows, exception text, arbitrary source query strings, email, credentials, and organization/customer content.

---

### `scripts/validate_phase3_release.py` (release gate)

**Analog:** `scripts/validate_phase1_release.py:13-128,211-230,245+`.

Keep an explicit fail-fast manifest of test classes and commands, validate that `DATABASE_URL` is PostgreSQL and ends in `_test`, run Alembic upgrade/current first, then focused Phase 3 counterexamples, migration lifecycle, full unittest discovery, catalog validation, compilation, and diff hygiene. Use `subprocess.run(..., cwd=PROJECT_ROOT, env=env, check=True)` and never shell-interpolate secrets.

---

### Phase 3 tests and fixtures

**Parser contract analog:** `tests/test_ncaa_workflow.py:622-834` uses per-division fixtures, patches `_fetch_ncaa_pages`, validates tuple/structured compatibility, generates minimal HTML tables, and asserts duplicate/missing/renamed headers and malformed row shapes fail before empty-state classification.

**Metric/canonical analog:** `tests/test_canonical_data.py:221-345` uses `subTest` tables for negative fields, compares exact `0` versus `None`, asserts innings-to-outs, authoritative two-way identity, and explicit metric provenance/version.

**Tenant/immutability analog:** `tests/test_snapshot_persistence.py:59-150,344-558` uses a disposable PostgreSQL base class, creates traceable source→identity→fact fixtures, asserts cross-organization inserts raise `IntegrityError`, publishes, then asserts updates/deletes raise `DBAPIError`.

**Partial/cache analog:** `tests/test_report_jobs.py:904-937` asserts a supplemental failure produces a partial snapshot and same-job evidence retains exact source collection time.

**Apply to new suites:**

- `test_detailed_stats.py`: formula tables for every status (`available`, `missing_input`, `undefined_denominator`, `unavailable_constant`, `incompatible_input`), exact inputs/sample metadata, and count-first aggregates.
- `test_ncaa_detail.py`: D1/D2/D3 fielding/split/player/schedule fixtures, discovered-ID/header/season mismatch rejection, missing table ≠ zero/verified-empty.
- `test_scoped_stats.py`: fewer-than-ten games, doubleheaders, canceled/postponed games, ambiguous linkage, historical conference identity, and exact scope membership.
- `test_player_history.py`: one stable player across transfers, typed two-way rows, ID-less history unavailable, per-build request budget/cache/retry behavior.
- `test_phase3_release.py`: schema parity, migration preservation, immutable publication, cross-tenant substitution for every new foreign key, and operator redaction.
- `tests/fixtures/ncaa_detail/{d1,d2,d3}/`: sanitized HTML captured only through the approved Browserless/source path; no tokens, cookies, customer data, or arbitrary URLs.

## Shared Patterns

### Tenant Isolation

**Sources:** `roster_scout/models.py:621-763`, `tests/test_snapshot_persistence.py:382-401`.

Apply composite `organization_id` + `snapshot_id` ownership to every new persisted entity. Bind player facts to `(player_team_season_id, snapshot_id, organization_id)` and evidence-linked facts to the source identity tuple. Add a negative cross-organization substitution test per new table family.

### Immutable Publication

**Sources:** `migrations/versions/0001_canonical_snapshot_foundation.py:497-542`, `roster_scout/snapshots.py:22-220`.

All new snapshot children participate in the same mutation-lock/immutability trigger protocol. Facts are finalized before `publish_snapshot()`; later enrichment requires a successor snapshot.

### Evidence Before Facts

**Sources:** `roster_scout/jobs.py:1218-1244`, `roster_scraper.py:2300-2383`.

Persist URL/source identifiers, parser/schema version, collection time, checksum, and exact raw row before typed facts. Publication verifies one-to-one traceability and row parity.

### External Input Validation and SSRF Boundary

**Sources:** `roster_scraper.py:1903-2034,2110-2182`.

Treat NCAA HTML, labels, IDs, URLs, and numbers as untrusted. Generate URLs only from validated IDs and fixed NCAA host/path templates. Require exact parser contracts, hard timeouts/budgets, sanitized errors, and shared Browserless capacity.

### Retry and Cache Safety

**Sources:** `roster_scout/jobs.py:81-118,455-566,1080-1253`.

Reuse only complete evidence from the same job/build with exact section/source/parser/schema identity. Retries are bounded and durable. A customer refresh is live and produces a linked successor; it never reuses older-build facts.

### Testing and Database Transactions

**Sources:** `tests/test_snapshot_persistence.py:59-150`, `tests/test_report_jobs.py` PostgreSQL base fixture.

Use caller-owned SQLAlchemy transactions, real PostgreSQL for constraint/migration/publication tests, and `unittest` for unit/fixture tests. No connection creation belongs in model or canonical modules.

## No Analog Found

No planned file lacks a usable codebase analog. The exact NCAA split-control discovery markup has no verified local fixture yet; use the existing NCAA parser/source-contract pattern, but make live D1/D2/D3 fixture capture the first source-discovery task and fail closed until it passes.

## Planner Warnings

- Do not place Phase 4 workbook rendering or Phase 5 dashboard/Power BI presentation in these plans.
- Do not add a second scraper runtime or new dependency; extend the current Browserless/NCAA seam.
- Do not hard-code split IDs or current conference-name inference.
- Do not model unavailable data as zeros or omit requested scope rows.
- Do not store successful full HTML; preserve extracted source rows/checksums. Restricted failure HTML follows the existing retention path.
- The research request-budget numbers are staging defaults, not proven production values; make them bounded configuration and verify them with fixture overrides.

## Metadata

**Analog search scope:** `roster_scout/`, `roster_scraper.py`, `migrations/versions/`, `scripts/`, and the four canonical/job/NCAA/PostgreSQL test modules named by Phase 3 context.

**Strong analogs used:** 11 source/migration/script patterns and 4 test families.

**Pattern extraction date:** 2026-11-15
