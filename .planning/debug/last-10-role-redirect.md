---
status: fixing
trigger: "Last 10 and conference dashboard scopes did not populate on the latest staging run."
created: 2026-11-18T00:50:00-06:00
updated: 2026-11-18T00:50:00-06:00
---

## Current Focus

hypothesis: confirmed; NCAA redirects a category URL to a player's available category when that player has no record for the requested stat type, and the strict three-category batch validator discarded the whole player batch
test: role-aware player-history collection requests only stat types with positive season activity and retains strict category-header validation for those requested pages
expecting: Last 10 and conference facts publish from hitter batting logs, pitcher pitching logs, and applicable fielding logs instead of all game-log sections becoming unavailable
next_action: finish regression gates, deploy to staging, run Abilene Christian, and verify scoped dashboard rows in the browser and database

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

## Eliminated

- The native dashboard projection is not the root cause; it populated scoped leaders when canonical game-log facts existed.
- Schedule scope construction is not the root cause; the latest snapshot retained 10 Last 10 games and 21 conference games for each stat type.
- Workbook publication, storage, authentication, and tenant isolation completed normally.

## Resolution

root_cause:
fix:
verification:
files_changed: []
