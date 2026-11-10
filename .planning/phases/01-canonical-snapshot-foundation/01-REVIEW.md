---
phase: 01-canonical-snapshot-foundation
reviewed: 2026-11-10T03:27:43Z
depth: standard
files_reviewed: 31
files_reviewed_list:
  - .env.example
  - DEPLOYMENT.md
  - Dockerfile
  - Procfile
  - app.py
  - compose.yaml
  - fly.toml
  - migrations/versions/0006_snapshot_mutation_serialization.py
  - migrations/versions/0007_report_attempt_generations.py
  - migrations/versions/0008_source_identity_binding.py
  - migrations/versions/0009_evidence_purge_health.py
  - roster_scout/canonical.py
  - roster_scout/evidence.py
  - roster_scout/jobs.py
  - roster_scout/models.py
  - roster_scout/repositories.py
  - roster_scout/snapshots.py
  - roster_scout/tasks.py
  - roster_scraper.py
  - scripts/run_purge.py
  - scripts/run_scheduler.py
  - scripts/validate_phase1_release.py
  - tests/test_app_routes.py
  - tests/test_canonical_data.py
  - tests/test_canonical_publish.py
  - tests/test_deployment_config.py
  - tests/test_evidence.py
  - tests/test_ncaa_workflow.py
  - tests/test_phase1_release.py
  - tests/test_report_jobs.py
  - tests/test_snapshot_persistence.py
findings:
  critical: 7
  warning: 1
  info: 0
  total: 8
status: issues_found
---

# Phase 1: Code Review Report

**Reviewed:** 2026-11-10T03:27:43Z
**Depth:** standard
**Files Reviewed:** 31
**Status:** issues_found

## Summary

The post-gap implementation closes the original publication-lock race and establishes one database-owned retry-generation authority, but Phase 1 is still not safe to ship. The new NCAA header contract was not connected to a new parser/cache version, ambiguous ID-less rows can still merge on name alone, and duplicate core headers still pass the supposedly fail-closed parser. The evidence-retention path also has three independent correctness holes: the 28-day maximum is not a database invariant, readiness ignores invalid retention configuration, and the purge makes no progress when its unbounded update exceeds a lock or statement timeout. Finally, the new source-to-identity foreign key proves only that the application chose the same `PlayerTeamSeason` for the source and fact; it does not verify that an authoritative source player ID matches the underlying canonical `Player`.

Plans 01-15 and 01-16 materially close the prior snapshot-serialization and dual-retry-authority gaps. Plans 01-14, 01-17, 01-18, 01-19, 01-20, and 01-21 remain partial because of the findings below. Static compilation, catalog validation (942 selectable teams), `git diff --check`, and all 116 locally runnable tests passed; 35 PostgreSQL-backed classes were skipped because this review environment had no `DATABASE_URL`. The passing suite does not cover the counterexamples identified here.

## Narrative Findings (AI reviewer)

### Prior gap revalidation

| Prior verification gap | Post-gap result | Review evidence |
|---|---|---|
| Concurrent snapshot mutation | **CLOSED** | Revision 0006 gives publisher and child/metric mutations a shared transaction advisory-lock protocol and deterministic definition-before-snapshot ordering. |
| Competing retry authorities / stale delivery | **CLOSED** | Revision 0007 persists generations; the outbox is the sole retry clock and Celery no longer calls `self.retry()`. |
| Physical evidence deletion by 720 hours | **OPEN** | The independent process and durable heartbeat help, but an unbounded all-or-nothing purge can repeatedly time out without deleting any backlog, and the retention cap is not enforced by PostgreSQL. |
| Same-player source identity | **PARTIAL / OPEN** | Source and fact share a `PlayerTeamSeason` ID, but authoritative `SourceRecord.source_player_id` is never compared with the linked `Player.source_player_id`. |
| Section-specific NCAA schema validation | **PARTIAL / OPEN** | Header sets are checked, but the unchanged parser version permits pre-fix evidence reuse and duplicate core headers still pass. |
| Unsafe ID-less identity becomes unresolved | **PARTIAL / OPEN** | Several ambiguity cases are unresolved, but a one-to-one batting/pitching pair with no jersey or position is still merged as fallback on name alone. |

## Critical Issues

### CR-01 [BLOCKER]: The new header contract reuses the old parser/cache version

**Files:** `.env.example:21`; `DEPLOYMENT.md:225`; `roster_scraper.py:81`, `roster_scraper.py:1003-1011`; `roster_scout/jobs.py:46-47`, `roster_scout/jobs.py:445-494`

**Issue:** Plan 01-14 changes which NCAA HTML is acceptable, but both the default and deployed `NCAA_PARSER_VERSION` remain `parser-1`, exactly as they were before the fix. The `ncaa-core-v1` value is stored only in an in-memory signature dictionary and is never included in `NcaaCollectionMetadata`, `ReportSection.parser_version`, or the cache key. Consequently, an interrupted pre-deploy job whose malformed `parser-1` sections were previously marked complete still satisfies `_load_cached_sections()` and `_matching_cached_sections()` after deployment. It can be reconstructed and published without ever running the new header validation. This defeats both fail-closed parsing and the promised version-aware same-job recovery.

**Fix:** Bump the parser version everywhere the release supplies or defaults it (for example, `parser-2`) and make the core-contract version part of the persisted parser identity rather than a disconnected constant. Add a recovery test that persists a pre-fix `parser-1` complete section, deploys/uses the new version, and proves that the stale section is rejected and recollected.

### CR-02 [BLOCKER]: One-to-one ID-less rows merge on name alone

**File:** `roster_scout/canonical.py:133-150`, `roster_scout/canonical.py:204-228`

**Issue:** `_compatible()` treats absent jersey and position values as wildcards. The reciprocal-unique branch then merges one batting row and one pitching row with the same normalized name before the later “nonempty support” check runs. For example, a batting `Same Name` row and a pitching `Same Name` row with neither jersey nor position produce one `fallback` player, even though they could be two namesake teammates and contain no corroborating evidence. This contradicts Plan 01-18's stated rule that missing support is unresolved and can publish one player's pitching facts under another player's identity.

**Fix:** Require reciprocal pairs to share at least one nonempty, compatible support attribute before merging. If neither jersey nor position provides corroboration, emit separate `unresolved` identities. Add unit and PostgreSQL publication tests for a one-batting/one-pitching same-name pair with both support fields missing and with support present on only one side.

### CR-03 [BLOCKER]: PostgreSQL does not enforce the 28-day retention maximum

**Files:** `roster_scout/models.py:524-543`; `migrations/versions/0009_evidence_purge_health.py:17-51`; `roster_scout/evidence.py:139-149`

**Issue:** The service calculates `expires_at = collected_at + retention`, but the `failure_evidence` table has no constraint relating those timestamps. Revision 0009 adds only the purge-health table and does not harden existing or future evidence rows. A direct insert, migration mistake, maintenance script, or future application regression can therefore store bytes with an expiry beyond 672 hours; the purge and readiness query trust that stored expiry and will retain the bytes beyond the 720-hour deadline. A compliance maximum that exists only in one Python constructor is not a durable data invariant.

**Fix:** Add a reversible migration and matching model constraint such as `expires_at > collected_at AND expires_at <= collected_at + INTERVAL '28 days'`, validate/backfill existing rows fail-closed, and test direct SQL inserts at 672 hours and one microsecond beyond it. If future-dated collection times are not legitimate, constrain those at insertion as well.

### CR-04 [BLOCKER]: Readiness can report healthy with an invalid retention configuration

**Files:** `app.py:139-167`; `roster_scout/evidence.py:42-58`, `roster_scout/evidence.py:155-189`; `roster_scout/jobs.py:968-1021`; `tests/test_app_routes.py:112-134`

**Issue:** `/readyz` checks only the persisted purge projection; it never calls `get_failure_evidence_retention()`. With `FAILURE_EVIDENCE_RETENTION_DAYS=29`, blank, or malformed, the app can return HTTP 200 as long as the last purge heartbeat is fresh. The first terminal collection failure then calls `store_failure_evidence()` inside `_record_failure()`'s transaction, the configuration exception rolls back the already-written terminal job state, and the job remains `running` until stale reconciliation. The test named `test_readyz_sanitizes_database_and_configuration_failures` mocks `get_engine()` failure, not the actual retention parser, so it misses this path.

**Fix:** Validate the retention setting during app and worker startup and include that validation in `/readyz`; return the same sanitized `unavailable` response on configuration failure. Do not let optional evidence insertion roll back the safe terminal job transition: either validate before accepting work or persist a sanitized configuration failure state in a separate transaction. Add an end-to-end test using a genuinely invalid environment value through `/readyz` and `_record_failure()`.

### CR-05 [BLOCKER]: A large or contended purge backlog can make zero deletion progress forever

**Files:** `roster_scout/evidence.py:192-242`; `scripts/run_purge.py:66-96`; `tests/test_evidence.py:469-568`

**Issue:** `purge_expired_evidence()` updates every eligible row in one transaction under a 5-second lock timeout and 30-second statement timeout. Any locked eligible row aborts the whole statement, and a backlog too large for 30 seconds rolls back every deletion. The runner retries the same all-or-nothing query every 60 seconds, so the same contention or backlog can yield zero progress until bytes exceed 720 hours. Existing tests prove rollback and heartbeat integrity for one row, but they do not prove bounded forward progress under contention or volume. This is a deadline-correctness and retention-risk issue, not merely a performance concern.

**Fix:** Purge in bounded batches selected with deterministic ordering and `FOR UPDATE SKIP LOCKED`, commit each batch, and update the heartbeat only after a complete eligibility scan (or persist separate progress and completion state). Ensure one locked row cannot block unrelated expired rows. Add multi-connection tests showing unlocked rows are physically deleted while another eligible row is locked, plus a multi-batch backlog test under the configured statement timeout.

### CR-06 [BLOCKER]: Authoritative source IDs can still be bound to a different canonical player

**Files:** `roster_scout/models.py:145-165`, `roster_scout/models.py:204-246`; `roster_scout/snapshots.py:75-108`; `migrations/versions/0008_source_identity_binding.py:80-115`

**Issue:** Revision 0008 makes a fact and its source row share the same `PlayerTeamSeason`, but it never links the source row's authoritative NCAA `source_player_id` to the `Player` behind that identity. A draft can therefore contain `SourceRecord.source_player_id='42'`, bind that row to a `PlayerTeamSeason` whose `Player.source_player_id='99'`, insert a fact with the same `PlayerTeamSeason`, and pass every foreign key and publication traceability count. The current Python canonicalizer usually assigns these correctly, but the database and publisher still accept the exact authoritative-evidence mismatch the invariant is intended to prevent; the direct-SQL tests swap only the fact's identity and never exercise this case.

**Fix:** Enforce authoritative equivalence when a source identity is assigned, either with a trigger that joins `SourceRecord -> PlayerTeamSeason -> Player` or by persisting an immutable source identity key that participates in the composite foreign key. Publication should independently join `Player` and reject non-null source IDs that differ. Add ORM and direct-SQL negative tests for a source ID/Player ID mismatch while source and fact share the same `PlayerTeamSeason`.

### CR-07 [BLOCKER]: Duplicate core headers pass the fail-closed NCAA schema check

**Files:** `roster_scraper.py:2063-2103`; `tests/test_ncaa_workflow.py:596-644`

**Issue:** The parser requires exactly one `Player` header but validates the remaining core signature with set inclusion. Batting headers such as `Player, AB, AB, H` and pitching headers such as `Player, IP, ERA, ERA` therefore pass. `_unique_headers()` silently renames the duplicate and canonicalization consumes whichever unsuffixed value appears first, so an ambiguous or malformed upstream schema can become complete or verified-empty and publish. The corruption matrix covers missing, renamed, unrelated, and cross-section headers but not duplicates.

**Fix:** Count normalized headers and require exactly one occurrence of every required core header (and reject any other known ambiguous duplicate that feeds canonical aliases). Add row-bearing and empty-table duplicate-header tests for both batting and pitching, and prove the durable job records sanitized `schema_validation` without publishing.

## Warnings

### WR-01 [WARNING]: Operational documentation still describes removed Celery retries

**Files:** `DEPLOYMENT.md:221`; `roster_scout/jobs.py:62-68`, `roster_scout/jobs.py:147-158`

**Issue:** Plan 01-16 correctly makes PostgreSQL/outbox scheduling the sole retry authority, but the deployment table still says “Celery retries are one fewer,” `RetryableJobError` says Celery may retry, and the outbox drain docstring says messages are UUID-only even though generation is now mandatory. These statements conflict with the safety model operators and maintainers must preserve and invite a future reintroduction of the dual-authority bug.

**Fix:** Update the documentation and docstrings to state that `REPORT_JOB_MAX_ATTEMPTS` is enforced by generation CAS, PostgreSQL owns retry due times, and every broker payload is `(job_id, generation)`.

---

_Reviewed: 2026-11-10T03:27:43Z_
_Reviewer: the agent (gsd-code-reviewer)_
_Depth: standard_
