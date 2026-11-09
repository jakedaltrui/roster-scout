---
created: 2026-11-09T00:12:51.621Z
title: Add last-10 and conference-only stats
area: planning
resolves_phase: 3
files:
  - .planning/REQUIREMENTS.md
  - .planning/ROADMAP.md
---

## Problem

Roster Scout's detailed NCAA intelligence scope includes full-season statistics and seven situational batting and pitching split families, but it does not yet require two views coaches commonly use for opponent preparation: performance over the team's most recent ten completed games and performance in conference games only. Without these scopes, the report can hide recent form and the difference between overall and conference-level performance.

These views must remain snapshot-specific and auditable. A "last 10" result needs the exact included games, dates, and count so postponements, cancellations, doubleheaders, and fewer-than-ten completed games cannot silently change its meaning. A conference-only result needs the season-specific conference identity and source evidence. Missing NCAA coverage must be represented as unavailable, never as zero production.

## Solution

Extend Phase 3 planning with two canonical statistic scopes: `last_10_completed_games` and `conference_only`. Cover batting, pitching, and fielding when the NCAA source supports them, preserve player IDs and source URLs, and retain the included game IDs/date range or conference/season identity with collection time and availability status.

Use the same canonical fact and metric definitions as full-season statistics so values can be compared without changing formulas. Expose both scopes in the detailed Excel workbook and Power BI semantic model/dashboard, and add representative D1, D2, and D3 fixtures plus edge cases for fewer than ten games, doubleheaders, transfers, and unavailable conference data.
