# Phase 4: Detailed Excel Report - Context

**Gathered:** 2026-11-16
**Status:** Ready for planning
**Mode:** Autonomous defaults approved by the user

<domain>
## Phase Boundary

Phase 4 extends the existing school-branded Excel download without replacing its familiar two-sheet experience. Every workbook contains exactly `Hitters` and `Pitchers`. Each sheet retains the established logo lockup, school-derived palette, compact alternating rows, filters, frozen headers, numeric typing, safe text handling, print behavior, and two-way-player logic. The only new presentation layer is a grouped header row containing Player, Season, Derived Metrics, Last 10 Games, and Conference Games.

The workbook does not contain separate fielding, split, career, game-log, summary, or methodology worksheets. Those deeper analytical views belong to the Phase 5 embedded dashboard.

</domain>

<decisions>
## Implementation Decisions

### Workbook Structure
- Exactly two worksheets remain: `Hitters` and `Pitchers`.
- Both sheets use the existing workbook renderer and visual conventions as the baseline. This is an extension, not a redesign.
- A school-colored grouped header row sits above the existing detailed column header row with only five groups: Player, Season, Derived Metrics, Last 10 Games, and Conference Games.
- No summary, fielding, split, career, game-log, source-methodology, or audit worksheet is added.

### Columns and Data
- Player identity retains the stable NCAA player ID and exact source URL alongside the existing name, number, position/class, and available roster attributes.
- Season columns retain the established basic batting and pitching statistics and add the canonical season counts required to interpret the derived metrics.
- Hitter derived metrics include OPS, ISO, BB%, K%, BABIP, and wOBA when canonical metric status is available.
- Pitcher derived metrics include K/9, BB/9, H/9, HR/9, K/BB, K%, BB%, BABIP allowed, and FIP when canonical metric status is available.
- Last-10 and conference columns expose compact core rates plus sample size/game count and an explicit availability status. Missing or partial scopes never appear as zero.

### Styling and Behavior
- Match the existing school logo lockup, extracted palette, typography, dark detailed headers, accent underline, alternating body bands, row density, filters, frozen headers, tab color, 90% zoom, landscape printing, repeated header rows, margins, print area, and footer logic.
- Preserve safe plain-text handling for formula-like and URL-like source values.
- Use real numeric cells and the canonical display precision: counts as integers, rate statistics to three decimals, percentages to one decimal, and pitching rate metrics to two decimals.
- Keep the existing readable dated download naming convention.

### Artifact and Historical Behavior
- Snapshot workbooks are generated only from the exact immutable canonical snapshot and stored once with schema version and SHA-256 checksum.
- Historical downloads always return the stored artifact for that snapshot; they never rebuild from a newer snapshot or current live NCAA data.
- The database-optional homepage renderer remains usable, and the canonical snapshot renderer extends the same shared workbook presentation rather than creating a second visual implementation.

### the agent's Discretion
- Choose the exact compact ordering of columns within each locked group while keeping identity columns visible and common baseball-stat ordering intuitive.
- Choose whether unavailable scope status is repeated per group or represented by one group-level status column, provided the state is explicit and cannot be confused with zero.
- Choose bounded internal projection objects and query layout used to transform canonical snapshot facts into workbook rows.

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `roster_scout/workbooks.py` is the canonical two-sheet renderer shared by homepage and saved-report flows. It already owns school branding, styling, numeric conversion, filtering, printing, safe strings, and download naming.
- `roster_scout/artifacts.py` already owns immutable object keys, storage verification, checksums, artifact state, exact historical reads, and the snapshot-to-workbook boundary.
- `BattingFact`, `PitchingFact`, `MetricValue`, `StatisticScope`, `ScopedBattingFact`, and `ScopedPitchingFact` already contain the canonical season, derived, last-10, and conference data required by the new columns.
- `PlayerTeamSeason` and `SourceRecord` retain stable player identity, exact source player ID, source URL, collection time, and same-snapshot ownership.

### Established Patterns
- The homepage and saved-report paths must share one workbook renderer so visual and numeric behavior cannot drift.
- Formula-like and URL-like values are written as plain text by xlsxwriter with automatic formula and URL conversion disabled.
- Published snapshots and their facts are immutable; artifact generation may retry independently, but a ready artifact is content-addressed and returned exactly.
- Supplemental scope data can be complete, partial, unavailable, or failed. Availability is data, not an exception to hide.

### Integration Points
- Extend the `SnapshotWorkbookSource` projection in `roster_scout/artifacts.py` to read canonical facts and scopes for one authorized immutable snapshot.
- Extend `roster_scout/workbooks.py` with grouped column specifications while preserving the legacy row input and styling path.
- Update artifact schema identity only when the workbook contract changes, then verify historical exact-download behavior remains unchanged.
- Add focused workbook tests to `tests/test_artifacts.py`, `tests/test_app_routes.py`, and a Phase 4 release gate covering D1/D2/D3 fixtures, value parity, unsafe strings, partial scopes, and visual structure.

</code_context>

<specifics>
## Specific Ideas

- The user explicitly rejected a ten-sheet workbook and wants the same two-sheet workbook they already know, only with more columns.
- The visible group labels should be Player, Season, Derived Metrics, Last 10 Games, and Conference Games.
- Situational splits, fielding depth, career history, game logs, methodology, and broader advanced analysis belong in the dashboard/Power BI experience.

</specifics>

<deferred>
## Deferred Ideas

- Embedded Power BI report pages, situational splits, fielding depth, career and game-log drill-through, methodology, and data-quality views remain Phase 5.
- Pricing, billing, entitlements, and subscription ownership remain an open product decision.

</deferred>
