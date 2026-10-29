---
status: resolved
trigger: "Johns Hopkins D3 returns NCAA browser verification did not produce the expected stats table"
created: 2026-10-27
updated: 2026-10-27
---

## Symptoms

- expected: Johns Hopkins D3 batting and pitching statistics produce the normal shared Google Sheet.
- actual: The request ends on the error screen before Sheet creation.
- error: NCAA browser verification did not produce the expected stats table; the service suggests a headed browser.
- timeline: First run after the NCAA D1/D2/D3 workflow was deployed locally.
- reproduction: Submit Johns Hopkins with D3 selected to the Docker service on localhost:5050.

## Current Focus

- hypothesis: Team resolution succeeds, but stats.ncaa.org rejects both direct HTTP and the headless Playwright browser; Docker cannot present a human verification window.
- test: Resolve Johns Hopkins IDs and test the current and alternate baseballr-style NCAA URLs independently.
- expecting: IDs resolve correctly and the failure occurs only at NCAA page retrieval.
- next_action: Gather endpoint and container evidence, then implement the smallest reliable retrieval path.

## Evidence

- Johns Hopkins resolves correctly to org `322`, season-team `615236`, D3, 2026.
- Direct HTTP is rejected by NCAA Akamai.
- Vanilla Playwright and Linux Chrome for Testing are rejected even on a virtual display.
- Native macOS Google Chrome through Patchright reaches NCAA's `/stats_terms` gate.
- NCAA rejects immediate terms submission but accepts it after an eight-second dwell and real browser input.
- Native live smoke passed with 42 players, 42 hitter rows, and 21 pitcher rows in 26.885 seconds.
- Native Gunicorn service returned HTTP 200 on localhost:5050.

## Eliminated

## Resolution

- root_cause: The Docker workflow used a browser fingerprint NCAA rejects and did not handle NCAA's new statistics-terms gate. The error incorrectly implied that simply disabling headless mode in Docker would work.
- fix: Replaced Playwright with Patchright, added delayed NCAA terms handling, added a native macOS setup/runner that uses installed Google Chrome, and retained Docker as a non-primary packaging option.
- verification: Ten deterministic tests pass; native Johns Hopkins live smoke passes; localhost:5050 returns HTTP 200.
- files_changed: roster_scraper.py, app.py, requirements.txt, Dockerfile, compose.yaml, DEPLOYMENT.md, CONTEXT.md, .gitignore, scripts/setup_local.sh, scripts/run_local.sh, tests/test_ncaa_workflow.py
