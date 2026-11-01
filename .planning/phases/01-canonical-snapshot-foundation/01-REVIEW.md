---
phase: 01-canonical-snapshot-foundation
reviewed: 2026-11-01T23:35:11Z
depth: standard
files_reviewed: 23
files_reviewed_list:
  - .env.example
  - DEPLOYMENT.md
  - compose.yaml
  - fly.toml
  - migrations/versions/0001_canonical_snapshot_foundation.py
  - migrations/versions/0002_durable_report_jobs.py
  - requirements.txt
  - roster_scout/canonical.py
  - roster_scout/celery_app.py
  - roster_scout/evidence.py
  - roster_scout/jobs.py
  - roster_scout/models.py
  - roster_scout/repositories.py
  - roster_scout/snapshots.py
  - roster_scout/tasks.py
  - roster_scraper.py
  - scripts/run_worker.py
  - tests/test_app_routes.py
  - tests/test_canonical_data.py
  - tests/test_canonical_publish.py
  - tests/test_ncaa_workflow.py
  - tests/test_report_jobs.py
  - tests/test_snapshot_persistence.py
findings:
  critical: 10
  warning: 6
  info: 0
  total: 16
status: issues_found
---

# Phase 1: Code Review Report

**Reviewed:** 2026-11-01T23:35:11Z  
**Depth:** standard  
**Files Reviewed:** 23  
**Status:** issues_found

## Summary

Phase 1 has serious correctness, integrity, security, and deployment defects despite its green deterministic suites. The most important gaps are unsafe concurrent Celery delivery, permanently stranded jobs after broker loss, a published-row immutability bypass, false verified-empty publication on malformed NCAA grids, and an evidence-retention path that is never invoked or purged. The existing duplicate-delivery tests are sequential and therefore do not exercise the races the implementation claims to prevent.

Verification performed during review:

- Python compilation passed.
- 57 non-database tests passed.
- 35 PostgreSQL-backed job, migration, persistence, and canonical-publication tests passed.
- `alembic check`, catalog validation, `docker compose config --quiet`, and `git diff --check` passed.

Passing checks do not cover the findings below.

## Narrative Findings (AI reviewer)

The findings below come from direct source review and call-path tracing across the submitted Phase 1 files.

## Critical Issues

### CR-01 [BLOCKER]: Published child rows can be moved out of an immutable snapshot

**File:** `migrations/versions/0001_canonical_snapshot_foundation.py:41-48`  
**Issue:** The child immutability trigger checks `NEW.snapshot_id` for every update. An update can therefore move a row from a published snapshot to a draft snapshot; the trigger sees only the draft target and permits the change. This can remove source rows, facts, sections, players, or metrics from an already published snapshot and invalidate its canonical contents. Existing tests update values in place and never change `snapshot_id`, so they miss the bypass.  
**Fix:** On `UPDATE`, reject when either the old or new parent snapshot is published. On `DELETE`, check `OLD`; on `INSERT`, check `NEW`. Add a regression that moves a fact and a required section from a published snapshot to a draft and expects PostgreSQL to reject both.

### CR-02 [BLOCKER]: Duplicate Celery deliveries can execute the same job concurrently

**File:** `roster_scout/jobs.py:172-193, 534-560`  
**Issue:** Dispatch eligibility is read without reserving the handoff, and `run_report_job()` releases its row lock immediately after setting `running`, before collection starts. A second request can enqueue the same job, or a duplicate broker message can acquire the row after the first transaction commits, increment `attempt_count` again, and perform another live NCAA/Browserless collection. This violates the stated duplicate-safe contract, consumes multiple browser slots, and can exhaust retries for one logical attempt. The tests at `tests/test_report_jobs.py:67-79` and `185-206` are sequential and do not cover this race.  
**Fix:** Introduce an atomic claim/lease or PostgreSQL advisory lock that remains authoritative for the whole attempt. Reserve dispatch with a compare-and-set/outbox row before publishing, reject or defer a concurrently claimed `running` job, and identify attempts with a lease token so stale workers cannot persist results. Add thread/process concurrency tests with barriers around dispatch and collection.

### CR-03 [BLOCKER]: A lost broker message strands an accepted job forever

**File:** `roster_scout/jobs.py:172-176, 185-193`  
**Issue:** `enqueued_at` is a permanent sent bit. If Redis acknowledges publication and then loses the message before a worker consumes it, the durable job remains `queued` with `enqueued_at` set. Repeating the same idempotent request will never dispatch it again, and no reconciler or outbox consumer exists. This contradicts the PostgreSQL-authoritative durability claim. Redis persistence is not an exactly-once handoff guarantee.  
**Fix:** Use a transactional outbox or a renewable dispatch lease plus a periodic reconciler for stale `queued`/`running` jobs. Re-dispatch must be safe in conjunction with the exclusive execution claim from CR-02.

### CR-04 [BLOCKER]: A crash after source persistence on the final attempt discards recoverable evidence

**File:** `roster_scout/jobs.py:548-564, 582-595`  
**Issue:** Exact source rows are deliberately committed before canonical publication, but the next delivery checks `attempt_count >= max_attempts` and fails the job before loading cached sections. With `REPORT_JOB_MAX_ATTEMPTS=1`, or a crash on the last allowed attempt, a worker loss after the source commit can never finalize the already collected evidence. The current recovery test only crashes on attempt one with the default limit of three.  
**Fix:** Load and validate same-job cached sections before declaring exhaustion. If all required evidence is complete, allow canonicalization/publication without consuming a new collection attempt. Count failed collections rather than redeliveries, and add tests for `max_attempts=1` and a crash on the final attempt.

### CR-05 [BLOCKER]: Global player and metric rows use race-prone check-then-insert logic

**File:** `roster_scout/jobs.py:419-431, 469-482`  
**Issue:** Two valid jobs running at the supported concurrency of two can both observe a missing authoritative `Player` or `MetricDefinition`, then both insert the same unique key. One transaction loses with an `IntegrityError`, which `_classified_error()` turns into a terminal `unexpected` job failure. This is especially likely when two organizations refresh the same team or when the first two jobs create the shared AVG/ERA definitions.  
**Fix:** Use PostgreSQL `INSERT ... ON CONFLICT DO NOTHING/UPDATE ... RETURNING` followed by a scoped reselect for both entities. Validate an existing player's identity before reuse, and add a two-session concurrency test.

### CR-06 [BLOCKER]: Malformed NCAA tables are published as verified empty

**File:** `roster_scraper.py:1981-2017, 2162-2174`  
**Issue:** `_parse_ncaa_stat_table()` silently skips rows with a changed cell count or without a recognized `Player` header. `_collect_ncaa_team()` then sets `verified_empty=not rows`. A schema change that makes every real row unparseable is therefore indistinguishable from a genuinely empty section and can be published as `ready`, causing silent data loss. The plan explicitly requires malformed core sections not to publish, but the test covers only an actually empty `<tbody>`.  
**Fix:** Validate the header contract and every candidate data-row shape. Return a distinct structural-empty result only when the table is valid and contains no player rows (or an explicit NCAA empty marker); otherwise raise a schema-validation error. Add malformed-header, mismatched-cell, and partially malformed table tests.

### CR-07 [BLOCKER]: Failure evidence storage and 30-day purge are dead runtime paths

**File:** `roster_scout/evidence.py:29-84`; `roster_scout/jobs.py:596-598`  
**Issue:** No production code calls `store_failure_evidence()` or `purge_expired_evidence()`. Job failures record only a category/message, so failed HTML is never retained. Conversely, if evidence is inserted manually, `expires_at` does not purge bytes by itself and there is no scheduled purge, so the claimed 30-day maximum is unenforced. The store also uses `bytes(html)`, which fails for the string HTML returned by `requests`. Unit tests call the helpers directly with bytes and do not test a real failed job or scheduled expiry.  
**Fix:** Carry bounded failure evidence through a private sanitized exception/result, transition the job and insert evidence in a coordinated transaction, encode string HTML explicitly, and schedule purge execution (Celery beat, platform cron, or database job) with monitoring. Add end-to-end failed-collection and post-expiry tests.

### CR-08 [BLOCKER]: Fly capacity accounting ignores that both app and worker use Browserless

**File:** `fly.toml:10-26`; `DEPLOYMENT.md:152-161`; `scripts/run_worker.py:21-34`  
**Issue:** Fly runs the legacy synchronous app and the durable worker on separate machines, and both can enter the Browserless NCAA path. Capacity locks are machine-local. The deployment guide prohibits extra workers but does not account for the app machine's concurrent sessions, so one app plus one worker can already exceed the account-wide limit the boundary is meant to protect. Setting `BROWSERLESS_MAX_SESSIONS=2` also applies the same allowance to both process groups, multiplying it.  
**Fix:** Use one distributed lease shared by app and worker, or allocate process-specific quotas whose sum is no greater than the Browserless account limit. Enforce and test the aggregate deployment boundary; documenting only the worker count is insufficient.

### CR-09 [BLOCKER]: Compose exposes unauthenticated Redis and known PostgreSQL credentials to the host network

**File:** `compose.yaml:40-61`  
**Issue:** PostgreSQL is published on `0.0.0.0:5432` with the checked-in `roster_scout/roster_scout` credentials, and Redis is published on `0.0.0.0:6379` with no authentication. On a developer machine or any host with reachable interfaces, another network client can read/alter the database or manipulate/flush the Celery broker. Calling the credentials “safe local values” does not make an all-interface binding local.  
**Fix:** Bind host ports explicitly to loopback (`127.0.0.1:5432:5432` and `127.0.0.1:6379:6379`) or omit them when only containers need access. If remote access is required, use strong secrets, TLS, authentication, and firewall rules.

### CR-10 [BLOCKER]: Publication validates section flags, not the canonical data they claim to represent

**File:** `roster_scout/snapshots.py:26-49`; `roster_scout/models.py:232-319`  
**Issue:** A draft can declare batting and pitching `complete` with positive `row_count` and then publish without any matching `SourceRecord`, player identity, or typed fact. Fact-to-player and fact-to-source foreign keys are nullable, and publication never compares section counts with source rows or verifies source-to-fact traceability. PostgreSQL can therefore expose a `ready` snapshot whose canonical contents are missing or orphaned even though Phase 1 presents publication as the integrity boundary.  
**Fix:** Before publication, validate section row counts against exact source rows and require every persisted fact/metric to reference a snapshot-owned identity and source record where applicable. Make required foreign keys non-null once draft ingestion is complete, or enforce equivalent deferred constraints. Add negative publication tests for missing rows, mismatched counts, and orphan facts.

## Warnings

### WR-01 [WARNING]: Authoritative IDs are not checked against previously stored identity

**File:** `roster_scout/jobs.py:419-430`; `roster_scout/canonical.py:152-159`  
**Issue:** Conflicting names are checked only within the current collection. If an NCAA player ID already belongs to a materially different stored player name, the new snapshot silently reuses that global `Player` row while storing the conflicting name on `PlayerTeamSeason`. A reused/corrupted source ID can therefore merge two people across snapshots.  
**Fix:** Compare normalized material identity evidence with the existing player before reuse. Allow reviewed aliases/suffix changes, but mark or fail material conflicts and preserve the evidence for resolution.

### WR-02 [WARNING]: Metric definitions can mutate the meaning of published values

**File:** `roster_scout/models.py:292-320`; `migrations/versions/0001_canonical_snapshot_foundation.py:54-66`  
**Issue:** Published `MetricValue` rows are trigger-protected, but their referenced `MetricDefinition` rows are not. Updating a definition's formula, code, or version retroactively changes the meaning of immutable published snapshots.  
**Fix:** Treat metric definitions as append-only once referenced by a published snapshot. Reject update/delete through a trigger or immutable repository contract, and create a new version for semantic changes.

### WR-03 [WARNING]: Partial section recovery can mix evidence from different collections

**File:** `roster_scout/jobs.py:250-330, 630-662`  
**Issue:** Cache reuse occurs only when both core sections are cached. If one complete section exists and another must be recollected, `_persist_sections()` refuses to replace the complete section's metadata and existing ordinals but appends any new ordinals from the new collection. This can mix collection times/payloads and leave `row_count` inconsistent with stored rows.  
**Fix:** Reuse matching complete sections individually and reconstruct them solely from persisted evidence, or reject all changes to a complete section and collect only missing sections. Never merge rows by ordinal across separate fetches.

### WR-04 [WARNING]: The initial migration is coupled to mutable application metadata

**File:** `migrations/versions/0001_canonical_snapshot_foundation.py:9, 18-20, 86-90`  
**Issue:** Revision 0001 calls the current `Base.metadata.create_all()` and `drop_all()`. Its schema therefore changes whenever models change; a fresh database at revision 0001 does not necessarily receive the historical 0001 schema. Revision 0002 already contains defensive inspection logic to compensate. This makes future fresh installs and migration debugging fragile and non-reproducible.  
**Fix:** Freeze 0001 with explicit `op.create_table`, indexes, constraints, and matching explicit downgrade operations. Migrations must not import evolving application metadata.

### WR-05 [WARNING]: URL sanitization retains embedded credentials

**File:** `roster_scout/evidence.py:20-26`  
**Issue:** `_safe_source_url()` removes query and fragment data but preserves `parts.netloc`, including `username:password@`. A credentialed source or proxy URL can therefore persist secrets in failure metadata.  
**Fix:** Rebuild the authority from validated hostname and optional port only; discard userinfo. Add tests for credentialed URLs, IPv6 hosts, invalid ports, queries, and fragments.

### WR-06 [WARNING]: Most count facts accept impossible negative values

**File:** `roster_scout/models.py:232-289`; `roster_scout/canonical.py:88-105`  
**Issue:** Only batting at-bats/hits and pitching innings have nonnegative checks. Walks, strikeouts, earned runs, hits allowed, and other count fields accept negative integers, and canonical parsing does not reject them. Malformed upstream values can be published as valid typed facts.  
**Fix:** Add nonnegative checks for every count field and validate parsed integers before persistence. Include migration and canonicalization tests for negative input.

---

_Reviewed: 2026-11-01T23:35:11Z_  
_Reviewer: the agent (gsd-code-reviewer)_  
_Depth: standard_
