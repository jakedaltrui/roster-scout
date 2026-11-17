---
status: fixing
trigger: "The latest hosted report run did not work; inspect the logs, explain why, and prevent recurrence. Also simplify the workbook metadata banner to the date and remove the Last 10 status column."
created: 2026-11-17T00:00:00-06:00
updated: 2026-11-17T02:21:00-06:00
---

## Current Focus

hypothesis: the final remaining failure is a PostgreSQL integrity constraint during evidence persistence; existing sanitized logs omit the safe constraint name needed to identify it
test: log only sanitized constraint/table identifiers, retain all collector corrections, and allow an 18-minute retry-inclusive wall for publication margin
expecting: the next exact replay names the constraint if it recurs, without exposing row values, so the final persistence defect can be corrected deterministically
next_action: deploy safe integrity telemetry and reproduce the final persistence boundary once

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


## Resolution

root_cause:
fix:
verification:
files_changed: [scripts/capture_phase3_ncaa_fixtures.py, roster_scout/ncaa_detail.py, roster_scout/jobs.py, roster_scout/workbooks.py, roster_scout/artifacts.py]
