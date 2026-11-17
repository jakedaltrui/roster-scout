---
status: resolved
trigger: "The latest hosted report run did not work; inspect the logs, explain why, and prevent recurrence. Also simplify the workbook metadata banner to the date and remove the Last 10 status column."
created: 2026-11-17T00:00:00-06:00
updated: 2026-11-17T03:13:00-06:00
---

## Current Focus

hypothesis: confirmed; legacy saved reports lacked the NCAA season/category identities required by computed scopes
test: passed; the exact Central Arkansas staging replay recovered the authoritative IDs and crossed collection, persistence, and artifact creation
expecting: met; a truthful partial snapshot and checksum-valid two-sheet workbook were published
next_action: none; production remains unchanged pending explicit deployment approval

## Symptoms

expected: A valid signed-in report request completes, publishes its saved report, and produces the established two-sheet workbook.
actual: The user's latest hosted run did not work as expected.
errors: No customer-visible error text was supplied; inspect sanitized staging diagnostics and logs.
reproduction: Review the most recent staging report/job created around Nov 17, 2026 06:12-06:13 UTC and trace its full lifecycle.
started: Latest staging run reported by the user on Nov 17, 2026.

## Eliminated

- The job did not crash, stall, exhaust retries, or fail artifact storage.
- Authentication, tenant isolation, Redis dispatch, PostgreSQL publication, and private workbook creation were not involved.
- The core batting and pitching collectors were healthy (34 batting rows and 17 pitching rows).


## Evidence

- Latest staging job `855c412a-bc74-48c5-9ebb-5f0b026fe4a0` ran from 06:11:33Z to 06:13:50Z, attempt 1, and returned snapshot `099daf01-6932-4575-b84b-fbbe90931279` with a ready XLSX artifact.
- The job and snapshot were `partial`, not failed. Fielding was `source_blocked`; schedule/game-log/history sections were `schedule_unavailable`; all 14 split scopes were `source_identifier_not_found`.
- Worker task `c047adc5-079f-4589-9ca2-fe838c231b66` completed successfully in 139.09 seconds.
- The affected source was Central Arkansas, D1, 2026, NCAA season-team `614825`.
- Replaying the schedule collector exposed `CaptureUnavailable(stage="schedule_row_shape")`.
- Live HTML inspection showed a valid four-cell game row followed by a one-cell empty spacer row after every game. The spacer row was the exact deterministic trigger.
- Live split controls expose numeric identities through `data-sport-stat-id` (for example, `4` for vs. LHP); that live attribute was absent from the approved identifier set.
- Live fielding HTML currently uses `GP`, `FldPct`, and `IDP` and includes `#`, `Yr`, `Pos`, `Ht`, `B/T`, `CI`, `TP`, and `SBAPct`; the prior parser only accepted the older reduced header vocabulary.
- A later fielding diagnostic returned a valid `#stat_grid`, so the earlier `source_blocked` state was an upstream/transport presentation rather than missing Central Arkansas fielding data.
- Staging v18 crossed the repaired schedule boundary and began player history, but consumed only 51 pages in 9m16s because `PHASE3_NAVIGATION_CONCURRENCY=2` was validated but never used by the sequential history loop. At that observed rate, a normal full roster would hit the 12-minute wall before all player pages completed.
- The sequential diagnostic ended at 07:08:37Z, 12m02s after it started, after 67 live pages. Its final database operation lost the connection and the job was safely marked `failed/unexpected`, confirming that the full-roster sequential path cannot fit inside the enforced wall.
- Staging v19 proved bounded concurrency was active and reached 136 pages in 12m03s, but Central Arkansas still had a few split requests left. It hit the same exact wall and was marked `failed/unexpected`; the default wall therefore lacked enough margin even after concurrency was corrected.
- Staging v20 reached the old 150-page budget in 13m50s but failed on the next database operation. The repeated `OperationalError` came from the detached PostgreSQL connection holding the job advisory lock: it remained idle while separate collector sessions recorded page reservations, then was no longer usable for publication. The full Central Arkansas request set also demonstrably exceeds 150 pages.
- Staging v21 retained the heartbeated claim and completed 176 live pages, reaching canonical persistence. It then failed closed at `_persist_schedule_scopes`: the schedule was correctly classified `partial` with 57 valid rows, but `_persist_sections` skipped all non-complete schedule rows while the scope layer explicitly accepts `complete` or `partial`. This deterministic contradiction is covered by a PostgreSQL regression test.
- Staging v22 exercised automatic source retries: two `source_blocked` core attempts recovered into a third live run, which reached 158 pages. It then hit an `IntegrityError` during the first evidence transaction. The transaction rolled back cleanly and exposed no data, but the prior logger omitted the database constraint identifier; safe constraint/table telemetry is now required for the exact final fix.
- Safe SQL diagnostics identified PostgreSQL error `23502` on `statistic_scopes.source_stat_category_id`. The affected legacy `TeamSeason` stored `NULL` batting/pitching category IDs and its linked `Season` stored the display year `2026` instead of the current NCAA source season ID.
- The authoritative 2026 NCAA season contract resolves season `17040`, batting category `15867`, pitching category `15868`, and fielding category `15869`; these values are now recovered before supplemental collection and reused by computed Last 10/conference scopes.
- The regression fixture now mutates a report to the same legacy shape (`source_season_id="2026"`, null batting/pitching category IDs) and proves schedule/history/game-log publication succeeds without a null scope identity.
- The focused persistence, recovery, evidence, workbook, and history suite passed 138 tests. The first 817-test sweep exposed two test-order issues; both failing tests then passed alone and in the 138-test focused suite after the guard was kept source-agnostic at persistence and authoritative at the NCAA boundary.
- Final staging job `78b5f7ab-d7be-4c09-9a72-29b0a445b3b3` completed on attempt 1 in 883.02 seconds after 163 live pages and published snapshot `82235900-ee90-4f3b-9f3e-b83e0450f4ef`.
- The final snapshot recovered source season `17040`, batting category `15867`, and pitching category `15868` in every applicable split and computed scope. Its XLSX artifact is `ready`, uses schema `coach-facing-two-sheet-xlsx-v4`, and passed its stored SHA-256 check.
- Direct inspection of the stored 25,359-byte XLSX verified exactly two worksheets, the date `NOVEMBER 17, 2026`, a `Games` header, no `Status` header, and none of `PUBLISHED` or `PARTIAL SNAPSHOT`.
- The final snapshot remains truthfully `partial`: core batting (34 rows) and pitching (17 rows) are complete, while NCAA returned blocked/incomplete supplemental fielding, schedule, split, history, and game-log evidence. Those source gaps no longer prevent the usable report or workbook from publishing.


## Resolution

root_cause: NCAA markup drift first hid the schedule, split, and fielding data; after those adapters were repaired, the full supplemental request set exposed undersized execution limits, an idle advisory-lock connection, dropped partial schedule rows, and finally legacy report records with null NCAA stat-category identities. The last defect violated `statistic_scopes.source_stat_category_id` during computed scope persistence.
fix: The collectors accept current NCAA markup, history fetches run with bounded concurrency under a 200-page/18-minute budget, the advisory claim is heartbeated, partial schedule evidence is retained, and legacy reports recover authoritative season/category IDs from the current NCAA season contract before split or computed-scope persistence. Workbook schema v4 renders only the date in its metadata banner and omits the Last 10 Status column while retaining Games.
verification: Targeted legacy-shape PostgreSQL regression passed; 138 focused report/evidence/history/artifact/workbook tests passed; workbook release acceptance, Phase 3 focused/security gates, browser state machines, compilation, and diff hygiene passed. The exact staging replay completed in 883.02 seconds on attempt 1, published a checksum-valid v4 workbook, and direct XLSX inspection confirmed the requested date-only banner and Last 10 column cleanup.
files_changed: [scripts/capture_phase3_ncaa_fixtures.py, roster_scout/ncaa_detail.py, roster_scout/jobs.py, roster_scout/workbooks.py, roster_scout/artifacts.py, tests/test_report_jobs.py, tests/test_player_history.py]
