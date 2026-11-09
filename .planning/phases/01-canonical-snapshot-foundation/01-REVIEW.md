---
phase: 01-canonical-snapshot-foundation
reviewed: 2026-11-09T22:19:18Z
depth: standard
files_reviewed: 33
files_reviewed_list:
  - .env.example
  - DEPLOYMENT.md
  - Dockerfile
  - app.py
  - compose.yaml
  - fly.toml
  - migrations/versions/0001_canonical_snapshot_foundation.py
  - migrations/versions/0003_snapshot_integrity_hardening.py
  - migrations/versions/0004_publication_traceability.py
  - migrations/versions/0005_report_dispatch_outbox.py
  - roster_scout/canonical.py
  - roster_scout/celery_app.py
  - roster_scout/database.py
  - roster_scout/evidence.py
  - roster_scout/jobs.py
  - roster_scout/models.py
  - roster_scout/repositories.py
  - roster_scout/snapshots.py
  - roster_scout/tasks.py
  - roster_scraper.py
  - scripts/run_scheduler.py
  - scripts/run_worker.py
  - scripts/validate_phase1_release.py
  - tests/test_canonical_data.py
  - tests/test_canonical_publish.py
  - tests/test_deployment_config.py
  - tests/test_evidence.py
  - tests/test_ncaa_workflow.py
  - tests/test_phase1_release.py
  - tests/test_report_jobs.py
  - tests/test_snapshot_persistence.py
  - tests/test_usage_limits.py
  - usage_limits.py
findings:
  critical: 5
  warning: 2
  info: 0
  total: 7
status: issues_found
---

# Phase 1: Code Review Report

**Reviewed:** 2026-11-09T22:19:18Z
**Depth:** standard  
**Files Reviewed:** 33
**Status:** issues_found

## Summary

The gap-closure work adds real safeguards, but Phase 1 is not safe to ship. Publication immutability can still be defeated by concurrent transactions, the outbox and Celery independently schedule the same retry, publication does not prove that a fact belongs to the player named by its evidence row, malformed NCAA stat schemas can still publish as valid data, and the scheduler cannot enforce the promised 720-hour physical-deletion deadline.

The legacy two-sheet workbook path, global player upsert, stored authoritative-ID conflict handling, nonnegative constraints, URL credential stripping, loopback-only Compose bindings, and aggregate Browserless capacity coordination were rechecked and are materially improved. Static compilation and `git diff --check` passed. The 66 runnable non-PostgreSQL canonical/NCAA/deployment tests passed; PostgreSQL-backed integration tests could not be executed because no test `DATABASE_URL` was available, so the transaction-race findings below are based on direct PostgreSQL lock/visibility semantics and implementation tracing.

## Narrative Findings (AI reviewer)

### Original finding revalidation

| Original | Result | Evidence |
|---|---|---|
| CR-01 | **OPEN** as CR-01 below | Triggers check status without serializing with publication. |
| CR-02 | **OPEN** as CR-02 below | Whole-attempt locking rejects overlap but does not identify stale sequential deliveries. |
| CR-03 | **PARTIAL / OPEN** as CR-02 below | Durable outbox closes acceptance loss, but outbox and Celery now create competing retries. |
| CR-04 | **CLOSED** | Final terminal transition and evidence insertion share one transaction. |
| CR-05 | **CLOSED** | PostgreSQL conflict-safe player and metric-definition inserts replace check-then-insert. |
| CR-06 | **OPEN** as CR-05 below | Only the `Player` column and row shape are validated, not section stat headers. |
| CR-07 | **OPEN** as CR-03 below | Capture is wired, but physical deletion within 720 hours is not operationally guaranteed. |
| CR-08 | **CLOSED** | PostgreSQL advisory capacity slots coordinate app and worker processes. |
| CR-09 | **CLOSED** | Compose PostgreSQL and Redis ports are loopback-bound. |
| CR-10 | **OPEN** as CR-04 below | Counts and ownership are checked, but evidence-to-player identity is not. |
| WR-01 | **CLOSED** | A stored authoritative player ID with a materially different name blocks publication. |
| WR-02 | **OPEN** as CR-01 below | Metric-definition trigger has the same publication race. |
| WR-03 | **CLOSED** | Cached source reuse requires same-job provenance and parser/schema versions. |
| WR-04 | **CLOSED** | Hardening is in forward migrations rather than another edit to `0001`. |
| WR-05 | **CLOSED** | Stored evidence URLs remove userinfo, query, fragment, controls, and invalid hosts. |
| WR-06 | **CLOSED** | Database checks reject negative attempts, row counts, ordinals, and typed counting stats. |

## Critical Issues

### CR-01 [BLOCKER]: Concurrent draft mutation can commit after publication

**Files:** `migrations/versions/0003_snapshot_integrity_hardening.py:43-95`; `roster_scout/snapshots.py:21-27`, `roster_scout/snapshots.py:101-115`
**Issue:** The child-row trigger reads the parent snapshot status with an ordinary `SELECT`; the metric-definition trigger similarly uses an unlocked `EXISTS`. `publish_snapshot()` locks only the snapshot row. Under PostgreSQL `READ COMMITTED`, a child or metric mutation can observe `draft` while a publisher's `ready` update is uncommitted, pass its trigger, and commit after the publisher. The supposedly immutable published snapshot then contains data the publication validation never saw. The reverse interleaving is also unsafe because updating a child field does not serialize on the parent row. This leaves both original CR-01 and WR-02 open.

**Fix:** Use one transaction-scoped lock protocol for every operation that can affect a snapshot. For example, acquire `pg_advisory_xact_lock` derived from the snapshot UUID in `publish_snapshot()` and in every child mutation trigger before checking status. For metric-definition update/delete, lock every referencing snapshot in a deterministic order and recheck status after the lock is acquired. Alternatively, make child triggers take a conflicting parent-row lock and make publication lock affected metric definitions in a consistent order. Add real two-connection barrier tests that pause a draft mutation after its status check while another transaction publishes, and vice versa.

### CR-02 [BLOCKER]: Two retry authorities dispatch the same failed attempt generation

**Files:** `roster_scout/jobs.py:869-902`, `roster_scout/jobs.py:905-962`; `roster_scout/tasks.py:29-53`; `roster_scout/repositories.py:58-80`
**Issue:** A retryable failure immediately creates a pending outbox intent, then `build_report_job()` also calls Celery `self.retry()`. Both deliveries contain only the job UUID; neither carries the outbox `message_id` or an attempt generation. The advisory lock prevents simultaneous execution, but after the first delivery releases it, the second is indistinguishable from a legitimate next attempt and can increment `attempt_count`. This bypasses the intended Celery backoff, can hammer a failing source, and can exhaust the job's durable attempt budget earlier than intended. It also means a stale acknowledged broker message can execute after a newer retry generation. Original CR-02 is therefore not closed, and the CR-03 outbox fix introduced a competing delivery path.

**Fix:** Choose one retry authority. Prefer making the database outbox authoritative: after `_record_failure()` durably schedules the next attempt, the Celery task should acknowledge/return without `self.retry()`, and `next_attempt_at` should encode the bounded backoff. If Celery retries remain, do not create an outbox retry for the same failure. In either design, put a dispatch/attempt generation token in the task payload and compare-and-set it when claiming the job so stale deliveries exit without changing state. Add tests for sequential duplicates, an outbox delivery racing a scheduled Celery retry, and a stale generation arriving after a newer one.

### CR-03 [BLOCKER]: The 720-hour physical evidence-deletion deadline is not enforceable

**Files:** `roster_scout/evidence.py:17-18`, `roster_scout/evidence.py:148-160`; `scripts/run_scheduler.py:72-78`, `scripts/run_scheduler.py:128-217`; `roster_scout/jobs.py:137-185`; `fly.toml:10-26`
**Issue:** Evidence bytes expire at 696 hours and the allowed purge interval is 24 hours, leaving zero operational margin. The only purge owner is the same single-threaded scheduler loop that performs synchronous reconciliation and broker publication. A hung broker call, database call, stalled cycle, restart, or unavailable scheduler can delay the next purge past 720 hours. `EvidencePurgeHealth` is process-local and only logged; it is not persisted, exposed through a health/readiness endpoint, or checked by Fly for the scheduler process. The exact-time unit tests exercise the happy path but do not make the physical-deletion promise true in deployment.

**Fix:** Run purge in an independently supervised process/periodic task with strict database and broker timeouts, persist its last successful completion, and make deployment readiness/alerting fail before the compliance deadline. Give the schedule a real safety margin (for example, expire substantially before 696 hours and purge much more frequently than daily). Add an integration test that blocks dispatch/reconciliation while proving purge still commits, plus a deployment check that fails when persisted purge health is stale.

### CR-04 [BLOCKER]: Publication traceability can attach a source row to the wrong player

**Files:** `roster_scout/snapshots.py:71-99`; `roster_scout/models.py:203-255`, `roster_scout/models.py:267-292`; `roster_scout/jobs.py:737-813`
**Issue:** Publication proves that each fact links to a unique source row and a snapshot-owned `PlayerTeamSeason`, but it never proves those two rows describe the same player. Swapping two facts' `player_team_season_id` values within one draft preserves every current foreign key, section count, distinct-source count, and publication check, yet publishes player A's statistics as player B's. For authoritative records the missing comparison is directly available as `SourceRecord.source_player_id` versus the linked `Player.source_player_id`; fallback identities likewise lack a durable evidence-to-identity constraint. Original CR-10's traceability requirement is therefore only count-level, not row-level.

**Fix:** Persist an explicit source-record-to-snapshot-identity link (or an immutable identity key on `SourceRecord`) and make each fact reference a matching source/identity pair through a composite foreign key. At minimum, publication must join `PlayerTeamSeason -> Player` and reject authoritative rows whose source player ID differs; fallback rows need a deterministic, stored source identity key rather than a name-only inference. Add a negative test that swaps two facts' identity IDs and verifies publication fails.

### CR-05 [BLOCKER]: NCAA schema validation still accepts stat-less or renamed sections

**Files:** `roster_scraper.py:2051-2105`, `roster_scraper.py:2236-2294`; `tests/test_ncaa_workflow.py:510-569`
**Issue:** `_parse_ncaa_stat_table_result()` requires exactly one `Player` column and consistent row widths, but it does not validate section-specific statistic headers. A batting or pitching page containing `Player` plus unrelated/renamed columns is accepted. Canonicalization then emits typed facts and metrics with all core values unavailable, and publication accepts them because its row/source/fact counts still match. Even an empty `Player`-only table is treated as verified-empty. The corruption tests cover a renamed player header and malformed row shapes, but not the core stat-header contract requested by original CR-06.

**Fix:** Make parsing section-aware and require a versioned minimum header signature before accepting rows or `verified_empty` (for example, recognized batting count columns such as `AB`/`H`, and pitching columns such as `IP`/`ERA`, with explicitly supported aliases). Treat missing or unrecognized core headers as schema failure and retain bounded evidence. Add tests for `Player`-only, `Player` plus unrelated headers, and renamed core stat columns in both sections.

## Warnings

### WR-01 [WARNING]: Worker and scheduler inherit an HTTP health check they can never pass

**Files:** `Dockerfile:41-42`; `compose.yaml:24-63`
**Issue:** The image health check always requests `127.0.0.1:5000/healthz`. Compose's worker and scheduler reuse that image but run only Python background processes, so Docker marks both containers unhealthy even when they are functioning. This makes operational status unreliable and can break tooling that gates on container health.

**Fix:** Disable the inherited HTTP health check for non-web Compose services and add process-specific checks, or build separate images/targets whose health checks match each process. The scheduler check should include persisted purge freshness rather than merely process existence.

### WR-02 [WARNING]: Documented evidence-retention setting has no effect

**Files:** `.env.example:17`; `compose.yaml:36`; `DEPLOYMENT.md:195`; `roster_scout/evidence.py:17-18`
**Issue:** Operators are told that `FAILURE_EVIDENCE_RETENTION_DAYS` controls retention and Compose sets it to 30, but no production code reads the variable. Evidence byte retention is hardcoded to 696 hours. Changing the documented setting silently does nothing, which is especially dangerous for a compliance-sensitive deletion policy.

**Fix:** Because the 720-hour ceiling should not be operator-extendable, remove the unused variable from Compose and `.env.example` and document the internal fixed maximum plus purge cadence. If configurability is required, parse and validate it at startup with a hard upper bound and use the resulting value when calculating `expires_at`.

---

_Reviewed: 2026-11-09T22:19:18Z_
_Reviewer: the agent (gsd-code-reviewer)_
_Depth: standard_
