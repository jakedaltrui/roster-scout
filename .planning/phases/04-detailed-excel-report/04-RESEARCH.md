# Phase 4: Detailed Excel Report - Research

**Researched:** 2026-11-16
**Domain:** Canonical snapshot projection, safe XLSX generation, immutable artifact delivery
**Confidence:** HIGH

## Summary

The repository already has the right architectural seams. `roster_scout/workbooks.py` is the single two-sheet renderer used by the compatibility path and saved artifacts. `roster_scout/artifacts.py` already stores one immutable artifact per snapshot with schema version, SHA-256 verification, exact historical reads, and bounded retries. Phase 4 should extend those seams rather than create a separate report builder.

The canonical database already contains every source needed for the requested columns: `PlayerTeamSeason` and `SourceRecord` own identity and exact URLs; `BattingFact` and `PitchingFact` own full-season counts; `MetricValue` owns versioned full-season derived metrics; `StatisticScope` plus `ScopedBattingFact` and `ScopedPitchingFact` own exact last-10 and conference aggregates and availability. When a scoped rate was not persisted, the workbook projection can call the existing `calculate_metric()` registry against the stored `counts_json`; it must never introduce a second formula implementation.

The existing workbook renderer establishes the visual contract: Arial, school-derived palette, logo lockup, title/subtitle rows, compact dark headers with a school accent underline, alternating school-tinted body bands, left alignment, filters, frozen headers, 90% zoom, landscape printing, repeated headers, tight margins, and school/sheet/page footer. The detailed version should add one school-tinted group-header row and preserve everything else.

## Recommended Architecture

1. Add explicit workbook column specifications to `roster_scout/workbooks.py` for the two detailed sheet contracts.
2. Keep `create_workbook()` as the only renderer. It accepts the existing row dictionaries and renders the extended columns; missing scope values remain blank with an explicit group status.
3. Extend snapshot projection in `roster_scout/artifacts.py` to create enriched row dictionaries from same-snapshot typed facts, metrics, scopes, and source evidence.
4. Use the existing metric registry for any scoped rate calculated from persisted count inputs.
5. Bump the artifact schema version because the saved workbook byte contract changes. Do not replace old artifact rows or object keys.
6. Verify layout with deterministic XLSX XML checks and rendered LibreOffice/PDF or spreadsheet previews for representative D1/D2/D3 fixtures.

## Column Contract

### Hitters

- **Player:** NCAA Player ID, Name, #, Position, Class, Source URL
- **Season:** G, GS, PA, AB, H, 2B, 3B, HR, RBI, R, BB, SO, SB, AVG, OBP, SLG
- **Derived Metrics:** OPS, ISO, BB%, K%, BABIP, wOBA
- **Last 10 Games:** G, PA, AB, AVG, OPS, K%, Status
- **Conference Games:** G, PA, AB, AVG, OPS, K%, Status

### Pitchers

- **Player:** NCAA Player ID, Name, #, Position, Class, Source URL
- **Season:** G, GS, IP, W, L, SV, H, ER, BB, SO, HR, BF, ERA, WHIP
- **Derived Metrics:** K/9, BB/9, H/9, HR/9, K/BB, K%, BB%, BABIP, FIP
- **Last 10 Games:** G, IP, ERA, WHIP, K/9, Status
- **Conference Games:** G, IP, ERA, WHIP, K/9, Status

The exact status label is `Complete`, `Partial`, `Unavailable`, or `Failed`. Numeric blanks and status text remain distinct from real zero values.

## Safety and Compatibility

- Keep xlsxwriter `strings_to_formulas=False` and `strings_to_urls=False`.
- Store numeric cells as numeric values, not formatted strings.
- Convert canonical innings outs to baseball innings display without base-10 arithmetic.
- Preserve two-way players on both sheets while keeping one stable NCAA identity.
- The compatibility path may lack scoped facts; it still renders the same columns and explicit `Unavailable` statuses.
- Historical artifacts remain immutable. The schema bump applies only to newly created artifacts.
- No new package is required.

## Verification Strategy

- Unit tests for column groups, ordering, number formats, source safety, row banding, logo lockup, filters, freeze panes, printing, and two sheets only.
- Snapshot projection tests for full-season metrics, scoped metrics, unavailable/partial scopes, player/source identity, two-way players, and predecessor immutability.
- Artifact tests for schema version, checksum/object-key identity, exact historical bytes, and no rebuild during download.
- Representative D1/D2/D3 fixtures with parity checks against canonical values.
- A deterministic generated preview inspected visually before release.

## Risks

- **Very wide worksheets:** preserve 90% zoom, freeze the player columns as well as headers, use compact widths, and rely on filters/scrolling rather than shrinking text.
- **Scope rate drift:** calculate only through the canonical metric registry.
- **Missing source URL on legacy rows:** preserve an explicit blank instead of inventing one.
- **Old artifact confusion:** schema-versioned object keys keep old and new contracts separate.

## Recommendation

Implement the detailed workbook in three ordered slices: renderer contract, canonical snapshot projection/artifact schema, then parity and visual release verification.
