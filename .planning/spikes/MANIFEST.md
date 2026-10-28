# Spike Manifest

## Idea

Determine whether Roster Scout can use the real `baseballr` package as its NCAA data layer for Division I, II, and III rosters and season statistics while remaining free to run locally and avoiding a cold-starting hosted service.

## Requirements

- NCAA D1, D2, and D3 data must come through the `baseballr` framework rather than a custom Python or browser scraper.
- The production design must support all three NCAA divisions, not only D1.
- The operating model must be free and must not depend on a cold-starting public server.
- The experiment must not read Google credentials or modify the existing Flask application.
- JUCO remains outside this spike because `baseballr` does not provide NJCAA coverage.

## Spikes

| # | Name | Type | Validates | Verdict | Tags |
|---|------|------|-----------|---------|------|
| 001 | baseballr-ncaa-feasibility | standard | Given a pinned `baseballr` runtime, when representative D1/D2/D3 rosters and all player-stat categories are requested, then non-empty structured results are returned with a measurable local runtime. | INVALIDATED | baseballr, ncaa, d1, d2, d3, docker, chromote |
