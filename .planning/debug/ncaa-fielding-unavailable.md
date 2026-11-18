---
status: resolved
trigger: "Fielding and fielding game logs are unavailable in the live staging dashboard; fix the collection path."
created: 2026-11-18T12:45:00-06:00
updated: 2026-11-18T15:12:00-06:00
---

## Symptoms

- expected: A detailed NCAA report publishes available team fielding facts and player fielding game logs so the dashboard Fielding tab and player cards populate.
- actual: The latest Abilene Christian staging snapshot reports `Fielding` and `Game Logs:Fielding` as unavailable with zero facts.
- errors: Customer-safe status is `source_unavailable`; no raw provider error is exposed.
- timeline: Observed in the current detailed staging snapshot after the Last 10 collection repair.
- reproduction: Open report `f77cf882-ec0f-48b9-8ab8-9c237aa47cae`; the latest detailed snapshot has no fielding facts.

## Current Focus

- hypothesis: A worker can retain a still-fresh four-column season lookup that satisfies the old batting/pitching schema but omits `fielding_id`, causing the detailed job to skip the fielding request entirely.
- test: Start a fresh detailed staging build after confirming the five-column catalog is loaded, and add a regression test with a fresh-but-incomplete cached lookup.
- expecting: The fresh build requests category `15869` and succeeds; the regression test fails until incomplete cache schemas force a refetch.
- next_action: Deploy the cache-contract guard to staging after the complete release gate passes.
- reasoning_checkpoint: Resolved. A fresh live snapshot published the exact NCAA fielding table and fielding game logs, and the dashboard renders them.

## Evidence

- The authoritative `ncaa_season_id_lu.csv` header is `season,id,batting_id,pitching_id,fielding_id`; its 2026 row is `2026,17040,15867,15868,15869`.
- `_load_ncaa_season_rows` previously required only the first four columns, so a fresh cached pre-fielding schema remained acceptable for 24 hours.
- `_collect_ncaa_team` and the detailed job only add fielding when `season.get("fielding_id")` is present, making that stale metadata omission a silent fielding skip.
- A new Abilene Christian staging detailed job loaded the five-column contract and recorded both `fielding: live_complete` and planned `history:fielding` requests.
- The published snapshot contains 34 `FieldingFact` rows, 598 `FieldingGameLogFact` rows, and 37 scoped fielding rows.
- Browser verification showed team fielding `0.971` and populated fielding leaders, including chances, errors, and FLD%.

## Eliminated

- NCAA does publish a numeric 2026 fielding category; this is not an upstream absence.
- The team fielding URL builder and parser are functional; the fresh live collection completed category `15869`.
- Dashboard rendering is not the primary fault; it already projects canonical `FieldingFact` rows when present.

## Resolution

- root_cause: The cached NCAA season lookup contract required only season, season ID, batting ID, and pitching ID. A fresh cache created before baseballr exposed `fielding_id` remained valid for 24 hours, so detailed jobs silently treated fielding as undiscovered and never requested it.
- fix: Require `fielding_id` in the season lookup schema, validate even fresh cached CSV headers, and refetch any cached lookup that no longer satisfies the current schema. Stale fallback is now allowed only when the stale file satisfies that same schema.
- verification: Two new regression tests fail on the old behavior and pass with the guard; the NCAA workflow/detail suites pass; the 2026 catalog validates all 942 selectable teams; the complete Phase 5 release gate passes (including 857 clean-process tests); live staging published and rendered 34 fielding rows plus 598 fielding game-log rows.
- files_changed: `roster_scraper.py`, `tests/test_ncaa_workflow.py`
