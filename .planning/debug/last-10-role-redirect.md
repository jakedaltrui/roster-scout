---
status: resolved
trigger: "Last 10 and conference dashboard scopes did not populate on the latest staging run."
created: 2026-11-18T00:50:00-06:00
updated: 2026-11-18T11:22:00-06:00
---

## Current Focus

hypothesis: confirmed; NCAA redirects a category URL to a player's available category when that player has no record for the requested stat type, and the strict three-category batch validator discarded the whole player batch
test: passed; role-aware player-history collection requests only stat types with positive season activity and retains strict category-header validation for those requested pages
expecting: met; Last 10 and conference facts publish from hitter batting logs and pitcher pitching logs instead of all game-log sections becoming unavailable
next_action: none; production remains unchanged pending explicit deployment approval

## Symptoms

expected: Last 10 and conference dashboard views populate from current-season NCAA game logs.
actual: The latest Abilene Christian detailed snapshot had zero game-log rows and the dashboard showed the scopes as unavailable.
errors: The customer page reported missing Game Logs:Batting, Game Logs:Fielding, and Game Logs:Pitching; the job itself completed partial.
reproduction: Refresh staging report f77cf882-ec0f-48b9-8ab8-9c237aa47cae and inspect its detailed successor.
started: After category-header validation was added to the player-history Browserless collector on 2026-11-18.

## Evidence

- Latest detailed job 6d621861-6fb2-4735-a72f-e34f4787fae5 published snapshot 789e403f-d34e-4ec6-abe3-6f293d478706 with 56 live pages but zero game-log rows for every stat type.
- The prior job b8bd96cf-4ad1-49b5-8522-3ae6028e0754 published 1,254 partial game-log rows and populated Last 10 and conference hitting.
- Live NCAA inspection showed a hitter's pitching-category URL rendering batting headers, while actual pitchers are present in the season pitching table.
- Abilene Christian's season batting table includes pitchers with blank AB values, so table membership alone cannot determine batting applicability.
- Fixed staging job 2fe22deb-37c7-468a-8b9e-aa0a935cb2f8 completed on attempt 1 with 86 live pages and published snapshot a6c277f7-0003-46ac-a64d-e4831aabc219.
- The fixed snapshot retained 546 batting and 118 pitching game-log rows; Last 10 published 13 batting and 8 pitching facts, while conference published 12 batting and 7 pitching facts.
- Browser verification showed populated Last 10 and conference leader tables with nonzero PA, IP, OPS, ERA, WHIP, and K/9 values.
- NCAA did not serve the team fielding source during the verification run, so fielding remained explicitly unavailable without suppressing the valid batting and pitching scopes.

## Eliminated

- The native dashboard projection is not the root cause; it populated scoped leaders when canonical game-log facts existed.
- Schedule scope construction is not the root cause; the latest snapshot retained 10 Last 10 games and 21 conference games for each stat type.
- Workbook publication, storage, authentication, and tenant isolation completed normally.

## Resolution

root_cause: The player-history transport requested batting, pitching, and fielding for every rostered player. NCAA falls back to an available category when the requested player/category pair does not exist, so strict header validation rejected whole batches containing ordinary hitter-only or pitcher-only players.
fix: Derive each player's applicable history categories from positive season activity, request and sanitize only those role-matched pages, allow the secret-free player-detail contract to contain a validated nonempty subset of stat sections, and require request-ledger families only for core stat types that exist in the snapshot.
verification: 89 player-detail/parser tests and 73 PostgreSQL report-job tests passed; compilation and diff hygiene passed; the live Abilene Christian staging run populated Last 10 and conference Hitting and Pitching in canonical storage and in the browser.
files_changed: [roster_scout/jobs.py, roster_scout/ncaa_detail.py, roster_scout/snapshots.py, roster_scraper.py, scripts/capture_phase3_ncaa_fixtures.py, tests/test_player_history.py, tests/test_report_jobs.py]
