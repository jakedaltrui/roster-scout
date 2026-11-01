# Feature Research: Opponent Intelligence

**Date:** 2026-11-01

## Table Stakes

### Identity and organizations

- Passwordless email sign-in with secure expiry, single use, rate limits, and non-enumerating responses
- Microsoft and Google federation for school/work accounts
- Organization-owned reports, explicit invitations, admin/member roles, membership removal, and logout/session management
- Enterprise OIDC/SAML connection capability without making custom SSO configuration a prerequisite for every customer
- Default-deny organization authorization on report pages, exports, refresh actions, APIs, and embed tokens

### Report lifecycle

- Asynchronous report creation with queued/running/partial/ready/failed states
- Immutable dated snapshots with source collection time and schema version
- Explicit refresh that creates a linked successor snapshot
- Organization report library with opponent, season, generated time, creator, completeness, and version history
- Section-level status so one unavailable source does not masquerade as zero statistics
- Exact snapshot download rather than silently rebuilding from newer upstream data

### Canonical baseball data

- Stable NCAA player ID, team ID, season-team ID, season, division, source URL, collection timestamp, and source hash/provenance
- Batting, pitching, and fielding fact tables at season, split, career-season, and game granularity
- Two-way players represented once in player identity and related to multiple statistical fact types
- Raw source values retained alongside derived values and metric-definition versions

### Detailed metrics

Minimum batting metrics:
- PA, AVG, OBP, SLG, OPS, ISO, BB%, K%, BABIP, 1B, TB

Minimum pitching metrics:
- ERA, WHIP, K/9, BB/9, H/9, HR/9, K/BB, K%, BB%, BABIP allowed, FIP

Minimum fielding metrics:
- G/GS when available, PO, A, E, TC, DP, fielding percentage; catcher-specific PB, SBA, and CSB when available

Weighted metrics:
- wOBA and FIP must carry season/division-specific weight or constant provenance. If a verified constant cannot be produced for a snapshot, the metric is `unavailable` with a reason, not guessed from MLB constants.

### Situational splits

The v2.0 split dimension includes batting and pitching results for:

- vs left-handed opponents
- vs right-handed opponents
- runners on
- bases empty
- bases loaded
- runners in scoring position
- two outs

Every split row identifies the canonical split code, human label, source `available_stat_id` or upstream artifact key, season, stat type, and availability status. Split identifiers are discovery data, not timeless constants.

### Career and game logs

- Career view is a season-by-season history keyed by NCAA player ID, school, team ID, division, and season, plus an explicitly labeled career aggregate
- Game logs include date, opponent, opponent ID when known, result/context, and available batting/pitching/fielding lines
- Transfers remain separate player-season/team records under one stable NCAA player identity
- Missing historical seasons or games are disclosed rather than converted to zeros

### Customer report surfaces

Power BI pages:
1. Opponent Overview
2. Roster and Player Directory
3. Hitting
4. Pitching
5. Fielding
6. Situational Splits
7. Player Detail with career and game logs
8. Snapshot Sources and Data Quality

Excel worksheets:
1. Report Summary
2. Roster
3. Hitters
4. Pitchers
5. Fielding
6. Hitting Splits
7. Pitching Splits
8. Career Stats
9. Game Logs
10. Sources & Methodology

The report and workbook may format data differently, but metric values for the same snapshot/player/context must agree.

## Differentiators

- Snapshot history designed for multi-game series preparation
- Opponent branding across report and workbook
- Side-by-side comparison between two snapshots of the same opponent
- Data-quality indicators and source traceability visible to nontechnical staff
- One-click full workbook download from the report page
- Player drill-through from team overview to splits, career, and recent game form

Snapshot comparison is valuable but can be deferred until the report library, refresh lineage, and canonical facts are stable.

## Anti-Features

- Public anonymous report links
- Automatic membership based only on `.edu` or another email suffix
- User-authored Power BI reports or arbitrary DAX editing
- Live in-game tracking
- Predictive rankings presented without a separately validated model
- Silent fallback from current data to stale data
- MLB league constants reused for NCAA weighted metrics without explicit validation

## Primary Sources

- `collegebaseball` season stats and historical split vocabulary: https://collegebaseball.readthedocs.io/en/latest/season_stats.html
- MLB ISO definition: https://www.mlb.com/glossary/advanced-stats/isolated-power
- MLB BABIP definition: https://www.mlb.com/glossary/advanced-stats/babip
- MLB FIP definition: https://www.mlb.com/glossary/advanced-stats/fielding-independent-pitching
- FanGraphs wOBA methodology: https://library.fangraphs.com/offense/woba/
- SportsDataverse NCAA data pipeline: https://github.com/sportsdataverse/baseballr-data
- Microsoft Power BI data export limits: https://learn.microsoft.com/en-us/power-bi/visuals/power-bi-visualization-export-data

