---
spike: 001
name: baseballr-ncaa-feasibility
type: standard
validates: "Given a pinned baseballr runtime, when representative D1/D2/D3 rosters and all player-stat categories are requested, then non-empty structured results are returned with a measurable local runtime."
verdict: INVALIDATED
related: []
tags: [baseballr, ncaa, d1, d2, d3, docker, chromote]
---

# Spike 001: baseballr NCAA Feasibility

## What This Validates

Given `baseballr` 2.0.0 in a reproducible local container, when its NCAA lookup, roster, and player-stat functions are exercised for Division I, II, and III, then the package returns useful structured data for every level and exposes an acceptable runtime/resource profile for Roster Scout.

Success requires:

- the package installs at the pinned release;
- the lookup dataset contains teams from all three divisions in one common season;
- a representative team is selected from each division;
- roster plus batting, pitching, and fielding calls return non-empty data frames for each selected team;
- JSON serialization preserves endpoint metadata, rows, and column names;
- repeated access in one R session demonstrates whether the browser session is reused;
- invalid-input and unavailable-data behavior is observable rather than silently accepted.

## Research

The `baseballr` 2.0.0 reference documents `ncaa_teams()` for Divisions I, II, and III, `ncaa_roster()` for roster data, and `ncaa_team_player_stats()` for batting, pitching, and fielding. Its release notes and source explain that `stats.ncaa.org` now returns hard or soft Akamai challenges to static clients. The package's supported path is a cached stealth Chrome session through the optional `chromote` dependency.

| Approach | Tool/Library | Pros | Cons | Status |
|---|---|---|---|---|
| Static HTTP | `httr2` path inside `baseballr` | Fast and light when NCAA allows it | Currently challenged by Akamai; cannot be relied on alone | Retained as package fast path |
| Package browser fallback | `baseballr` + `chromote` + Chrome | Official package behavior; cached browser session; callers stay unchanged | Requires a browser binary and more memory | Chosen |
| Custom browser scraper | Python/Playwright or Selenium | Full control | Violates the accepted requirement that NCAA access be based on `baseballr` | Rejected |

**Chosen approach:** run the pinned package unchanged with `chromote` and Chrome in Docker. The harness calls only exported `baseballr` NCAA functions for roster and stats. It records the package's own fallback messages and timings instead of reimplementing the scrape.

## How to Run

From this spike directory:

```bash
./run.sh
```

Optional environment variables:

```bash
SPIKE_YEAR=2026 SPIKE_REPEAT=1 ./run.sh
```

Results are written to `results/` and are intentionally ignored by Git except for the checked-in summarized result files added after verification.

## What to Expect

- `results/events.jsonl`: timestamped event stream and errors.
- `results/summary.json`: environment, selected teams, row/column counts, timings, and overall pass/fail.
- `results/fixtures/`: small JSON samples proving the output bridge without committing complete NCAA datasets.
- Docker build and image-size measurements in the final results.

## Observability

The harness records an ISO timestamp, category, operation, division, team, duration, row count, column count, and error for each probe. Calls remain sequential to respect the package's built-in five-second NCAA courtesy delay and avoid turning a feasibility check into a load test.

## Investigation Trail

1. The workstation has Docker Desktop but no local R runtime or Chrome installation. That makes an isolated container the least invasive reproducible runtime.
2. Direct requests to the NCAA stats page were previously observed returning a small HTTP-200 `bm-verify` interstitial. The pinned package source detects both this response and hard 403 responses, then invokes a cached `chromote` session.
3. The live harness is designed to distinguish package installation, static lookup coverage, browser startup, roster parsing, category parsing, and JSON serialization failures.
4. The pinned package installed successfully and its static lookup returned 15,928 rows. The 2026 slice contained 305 D1, 242 D2, and 370 D3 teams with unique franchise IDs.
5. The exported live functions were tested with Florida State (D1), Tampa (D2), and Salisbury (D3). All three roster calls failed because the current legacy roster page has no roster table. All nine stat calls returned empty data because the package could not resolve a modern season-team ID through that page.
6. The lookup itself already contained the modern IDs, so a second probe fetched the modern routes directly through `baseballr`'s own request and Chrome helpers. All 16 modern pages tested remained Akamai interstitials after the browser fallback, including Scranton's exact D3 page at season-team ID `615349`.
7. A final controlled probe aligned the package's hard-coded Chrome 138 user-agent with the installed Chrome 156 and injected the known season-team ID. The browser still returned no usable HTML after 55.8 seconds, and the exported stats function still returned zero rows after 38.0 seconds.
8. The broad reproducible image measured 4,810,617,803 bytes (4.81 GB decimal). The public-function run used about 206 MiB before successful browser activation; the direct browser diagnostic peaked at about 1.02 GiB under a 4 GiB cap.

## Results

**Verdict: INVALIDATED.** `baseballr` 2.0.0 cannot currently serve as Roster Scout's complete live NCAA data layer in a clean local Docker runtime.

| Check | Result |
|---|---|
| Pinned package installation | PASS — version 2.0.0, release commit `7f7a0e1` |
| 2026 D1/D2/D3 lookup coverage | PASS — 305 / 242 / 370 teams |
| D1 roster + three stat types | FAIL — roster error; three empty stat frames |
| D2 roster + three stat types | FAIL — roster error; three empty stat frames |
| D3 roster + three stat types | FAIL — roster error; three empty stat frames |
| Exact D3 page `615349` | FAIL — correctly maps to Scranton, but modern routes remain challenged |
| Invalid stat type handling | PASS — clear validation error |
| JSON bridge | PARTIAL — metadata serializes; no live player rows existed to serialize |
| No-cold-start local operation | TECHNICALLY POSSIBLE, but not useful while live retrieval fails |

The failure has two independent layers:

1. **Parser/ID handoff:** `ncaa_roster()` still expects a table on the legacy roster route, and `ncaa_team_player_stats()` tries to derive `season_team_id` from that legacy page even though `load_ncaa_baseball_teams()` already provides it.
2. **Access:** bypassing that handoff and using the correct modern IDs still does not clear the NCAA Akamai challenge in the package's cached stealth Chrome session.

This means a thin ID adapter is insufficient. A production build should pause before integrating `baseballr` live NCAA calls. Credible next experiments are either an upstream/forked `baseballr` browser fix tested against current Chrome, or a source decision that relaxes the requirement that live NCAA data must flow exclusively through the package.
