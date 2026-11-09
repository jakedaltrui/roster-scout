---
phase: 1
slug: canonical-snapshot-foundation
status: draft
nyquist_compliant: true
wave_0_complete: false
created: 2026-11-01
---

# Phase 1 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | Python 3.12 `unittest` + PostgreSQL integration database |
| **Config file** | Existing `tests/`; Wave 0 adds Compose PostgreSQL/Redis and database test helpers |
| **Quick run command** | `.venv/bin/python -m unittest tests.test_canonical_data -v` |
| **Full suite command** | `.venv/bin/python -m unittest discover -s tests -v` |
| **Database command** | `DATABASE_URL=postgresql+psycopg://roster_scout:roster_scout@localhost:5432/roster_scout_test .venv/bin/python -m unittest tests.test_snapshot_persistence tests.test_report_jobs -v` |
| **Estimated runtime** | Every task target: <30 seconds; quick unit target: <5 seconds; plan/final PostgreSQL, migration, D1/D2/D3, workbook, and release gates may exceed 30 seconds |

---

## Sampling Rate

- **After every task commit:** Run that task's narrow command/assertion from the map and `git diff --check`; the task command must finish in `<30 seconds`.
- **After every plan:** Run the plan's `<verification>` block, including its broader PostgreSQL or migration checks where applicable.
- **After every plan wave:** Run full unittest discovery, PostgreSQL integration tests, and `alembic check` as a plan/wave boundary, not as task feedback.
- **Before `$gsd-verify-work`:** Compilation, full unittest discovery, PostgreSQL migration/integration tests, catalog validation, and `git diff --check` must be green.
- **Max task feedback latency:** `<30 seconds`; split a PostgreSQL target by class or test method when it exceeds the budget.
- **Complete release validator boundary:** The complete PostgreSQL + D1/D2/D3 + workbook gate (`DATABASE_URL=... .venv/bin/python scripts/validate_phase1_release.py`) runs only at Plan 01-11/final verification boundaries, never after an individual task.

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 01-01-01 | 01 | 1 | OPS-04 | T-01-07 | Migration failure prevents rollout; connection secrets stay in environment | integration | `DATABASE_URL=... .venv/bin/python -m unittest tests.test_snapshot_persistence.MigrationTests.test_upgrade_head -v` | ❌ W0 | ⬜ pending |
| 01-01-02 | 01 | 1 | SNAP-01, SNAP-03, DATA-02, DATA-04, DATA-05, DATA-06, DATA-08, DATA-09 | T-01-01, T-01-03 | Tenant keys and immutable snapshot invariants are database-enforced | integration | `DATABASE_URL=... .venv/bin/python -m unittest tests.test_snapshot_persistence -v` | ❌ W0 | ⬜ pending |
| 01-01-03 | 01 | 1 | DATA-03, DATA-07, DATA-10 | T-01-01 | Canonical facts retain exact source evidence and published-only reads | integration | `DATABASE_URL=... .venv/bin/python -m unittest tests.test_snapshot_persistence.CanonicalSchemaTests -v` | ❌ W0 | ⬜ pending |
| 01-02-01 | 02 | 2 | SNAP-02, OPS-01 | T-01-02, T-01-06 | Broker messages contain only job IDs and retries are bounded | unit/integration | `DATABASE_URL=... .venv/bin/python -m unittest tests.test_report_jobs.JobLifecycleTests -v` | ❌ W0 | ⬜ pending |
| 01-02-02 | 02 | 2 | SNAP-03, SNAP-04 | T-01-02, T-01-03 | Duplicate delivery cannot duplicate snapshots; failed core sections cannot publish | integration | `DATABASE_URL=... .venv/bin/python -m unittest tests.test_report_jobs.PublicationTests -v` | ❌ W0 | ⬜ pending |
| 01-02-03 | 02 | 2 | OPS-02, OPS-04 | T-01-04, T-01-05 | Cache state is versioned and failed HTML retention is bounded | integration/source | `DATABASE_URL=... .venv/bin/python -m unittest tests.test_report_jobs.RetentionAndCacheTests -v` | ❌ W0 | ⬜ pending |
| 01-03-01 | 03 | 3 | DATA-01, DATA-02, DATA-03, DATA-05, DATA-06, DATA-07 | T-01-03 | NCAA IDs, raw rows, missing values, and innings outs survive normalization | unit | `.venv/bin/python -m unittest tests.test_canonical_data -v` | ❌ W0 | ⬜ pending |
| 01-03-02 | 03 | 3 | SNAP-01, SNAP-03, SNAP-04, DATA-08, DATA-10 | T-01-02, T-01-03 | D1/D2/D3 fixture builds publish the same canonical contract | integration | `DATABASE_URL=... .venv/bin/python -m unittest tests.test_canonical_publish -v` | ❌ W0 | ⬜ pending |
| 01-03-03 | 03 | 3 | OPS-01, OPS-02, OPS-05 | T-01-02 | Existing routes, cache, capacity handling, and workbook output do not regress | regression | `.venv/bin/python -m unittest tests.test_app_routes -v` | ✅ existing | ⬜ pending |
| 01-04-01 | 04 | 4 | OPS-04 | T-01-04-04 | Frozen 0001 DDL is independent of mutable ORM metadata | PostgreSQL integration | `DATABASE_URL=... .venv/bin/python -m unittest tests.test_snapshot_persistence.MigrationTests.test_0001_boundary_is_explicit -v` | ❌ task | ⬜ pending |
| 01-04-02 | 04 | 4 | SNAP-03, DATA-05, DATA-08 | T-01-04-01, T-01-04-02, T-01-04-03 | Published-parent moves, metric mutation, and negative counts are rejected | unit/PostgreSQL integration | `DATABASE_URL=... .venv/bin/python -m unittest tests.test_snapshot_persistence.PublishedMutationTests tests.test_canonical_data.NegativeCountTests -v` | ❌ task | ⬜ pending |
| 01-05-01 | 05 | 5 | DATA-01, DATA-03, DATA-07 | T-01-05-01, T-01-05-04 | Concurrent shared-record creation converges and conflicting NCAA identity fails closed | PostgreSQL concurrency | `DATABASE_URL=... .venv/bin/python -m unittest tests.test_canonical_publish.ConcurrentSharedRecordTests tests.test_canonical_publish.AuthoritativeIdentityTests -v` | ❌ task | ⬜ pending |
| 01-05-02 | 05 | 5 | SNAP-03, SNAP-04, DATA-09, DATA-10 | T-01-05-02, T-01-05-03 | Publication requires snapshot-owned source, identity, and typed-fact traceability | PostgreSQL integration | `DATABASE_URL=... .venv/bin/python -m unittest tests.test_canonical_publish.PublicationIntegrityTests -v` | ❌ task | ⬜ pending |
| 01-06-01 | 06 | 6 | SNAP-02, SNAP-03, OPS-01 | T-01-06-01, T-01-06-02 | One PostgreSQL claim owns the whole collection/publication attempt | PostgreSQL concurrency | `DATABASE_URL=... .venv/bin/python -m unittest tests.test_report_jobs.ConcurrentExecutionClaimTests -v` | ❌ task | ⬜ pending |
| 01-06-02 | 06 | 6 | SNAP-02, OPS-01 | T-01-06-03, T-01-06-04 | Overlapping Celery entrypoints collect once and preserve sanitized state | worker integration | `DATABASE_URL=... .venv/bin/python -m unittest tests.test_report_jobs.CeleryConcurrentDeliveryTests -v` | ❌ task | ⬜ pending |
| 01-07-01 | 07 | 7 | SNAP-02, SNAP-03, OPS-04 | T-01-07-02 | Job acceptance and one reclaimable dispatch intent commit atomically | PostgreSQL concurrency | `DATABASE_URL=... .venv/bin/python -m unittest tests.test_report_jobs.TransactionalOutboxTests -v` | ❌ task | ⬜ pending |
| 01-07-02 | 07 | 7 | SNAP-02, OPS-01 | T-01-07-01, T-01-07-03 | A broker-acknowledged lost message is reconciled without duplicate publication | worker integration | `DATABASE_URL=... .venv/bin/python -m unittest tests.test_report_jobs.BrokerLossRecoveryTests -v` | ❌ task | ⬜ pending |
| 01-08-01 | 08 | 8 | SNAP-04, DATA-02, OPS-05 | T-01-08-01 | Malformed NCAA tables cannot become verified-empty sections | unit | `.venv/bin/python -m unittest tests.test_ncaa_workflow.NcaaParserCorruptionTests tests.test_ncaa_workflow.NcaaStructuredCollectionTests -v` | ❌ task | ⬜ pending |
| 01-08-02 | 08 | 8 | SNAP-03, SNAP-04, DATA-07 | T-01-08-03 | Complete final-attempt evidence publishes without another collection | PostgreSQL integration | `DATABASE_URL=... .venv/bin/python -m unittest tests.test_report_jobs.FinalAttemptEvidenceRecoveryTests -v` | ❌ task | ⬜ pending |
| 01-08-03 | 08 | 8 | SNAP-03, DATA-07, OPS-02 | T-01-08-02, T-01-08-04 | Per-section recovery fetches only missing evidence and preserves original freshness | PostgreSQL integration | `DATABASE_URL=... .venv/bin/python -m unittest tests.test_report_jobs.PerSectionRecoveryTests -v` | ❌ task | ⬜ pending |
| 01-09-01 | 09 | 9 | SNAP-02, SNAP-04, DATA-07 | T-01-09-01 | Only failed/blocked jobs retain bounded HTML with byte expiry at 696 hours; successful jobs retain none | PostgreSQL integration | `DATABASE_URL=... .venv/bin/python -m unittest tests.test_evidence.FailedCollectionEvidenceTests.test_failed_collector_stores_bounded_sanitized_evidence tests.test_report_jobs.FailureEvidenceIntegrationTests.test_successful_job_stores_no_html -v` | ❌ task | ⬜ pending |
| 01-09-02 | 09 | 9 | DATA-07 | T-01-09-02, T-01-09-03 | Persisted URLs exclude credentials/query/fragment and stored metadata matches the D-18 safe allowlist | unit/PostgreSQL integration | `DATABASE_URL=... .venv/bin/python -m unittest tests.test_evidence.SafeSourceUrlTests.test_credentials_queries_fragments_and_invalid_authorities tests.test_evidence.SafeEvidenceMetadataTests.test_persisted_failure_metadata_is_allowlisted -v` | ❌ task | ⬜ pending |
| 01-10-01 | 10 | 4 | SNAP-02, OPS-01, OPS-05 | T-01-10-01, T-01-10-04 | App and worker share one crash-safe Browserless account lease | PostgreSQL concurrency | `DATABASE_URL=... .venv/bin/python -m unittest tests.test_usage_limits.DistributedBrowserlessCapacityTests -v` | ❌ task | ⬜ pending |
| 01-10-02 | 10 | 4 | OPS-04 | T-01-10-02, T-01-10-03 | PostgreSQL and Redis development ports bind only to loopback | config | `.venv/bin/python -m unittest tests.test_deployment_config.LocalServiceBindingTests -v` | ❌ task | ⬜ pending |
| 01-10-03 | 10 | 4 | OPS-01, OPS-04 | T-01-10-01, T-01-10-04 | Process configuration shares capacity authority and preserves least privilege | config | `.venv/bin/python -m unittest tests.test_deployment_config.BrowserlessCapacityConfigTests -v` | ❌ task | ⬜ pending |
| 01-11-01 | 11 | 11 | SNAP-01..04, DATA-01..10, OPS-01, OPS-02, OPS-05 | T-01-11-01, T-01-11-02, T-01-11-03 | One representative operator flow exercises the durable snapshot contract; full D1/D2/D3/workbook matrix remains plan-level | PostgreSQL integration | `DATABASE_URL=... .venv/bin/python -m unittest tests.test_phase1_release.Phase1ReleaseTests.test_representative_d1_happy_path -v` | ❌ task | ⬜ pending |
| 01-11-02 | 11 | 11 | OPS-04 | T-01-11-04 | Exact goal text validates and the release script compiles; complete release execution remains plan/final-only | source/contract | `python3 -m py_compile scripts/validate_phase1_release.py && gsd-sdk query user-story.validate --story "As a Roster Scout operator, I want to publish existing NCAA D1/D2/D3 results through a durable job into an immutable, provenance-rich canonical snapshot, so that every downstream report can rely on one auditable source of truth without breaking today's workbook workflow." --raw` | ❌ task | ⬜ pending |
| 01-12-01 | 12 | 8 | SNAP-02, OPS-01, OPS-04 | T-01-12-01, T-01-12-02 | Scheduler validates bounded timing and invokes reconciliation without leaking secrets | unit/config | `.venv/bin/python -m unittest tests.test_deployment_config.DispatchSchedulerEntrypointTests -v` | ❌ task | ⬜ pending |
| 01-12-02 | 12 | 8 | SNAP-02, OPS-01, OPS-04 | T-01-12-03, T-01-12-04 | Exactly one least-privilege scheduler is wired in Compose and Fly | config | `.venv/bin/python -m unittest tests.test_deployment_config.DispatchSchedulerConfigTests -v` | ❌ task | ⬜ pending |
| 01-13-01 | 13 | 10 | DATA-07, OPS-04 | T-01-13-01, T-01-13-02, T-01-13-03 | Expired bytes are physically absent by 720 hours and exact stale-purge readiness boundaries fail closed | time-controlled/PostgreSQL integration | `DATABASE_URL=... .venv/bin/python -m unittest tests.test_evidence.EvidencePurgeDeadlineTests.test_immediately_after_prior_purge_is_deleted_by_720_hours tests.test_deployment_config.EvidencePurgeHealthTests.test_exact_24_hour_boundary_is_fresh_and_next_increment_is_stale -v` | ❌ task | ⬜ pending |
| 01-13-02 | 13 | 10 | OPS-04 | T-01-13-03, T-01-13-04 | One least-privilege scheduler enforces a purge cadence no longer than 24 hours and documents the bounded policy | config/source | `.venv/bin/python -m unittest tests.test_deployment_config.EvidencePurgeConfigTests.test_scheduler_wiring_enforces_24_hour_maximum -v && rg -n "retained for up to 30 days|696 hours|24-hour" DEPLOYMENT.md` | ❌ task | ⬜ pending |

*File/target: ✅ existing · ❌ W0 created in Wave 0 · ❌ task created/extended by that gap task. Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky.*

---

## Plan and Final Verification Boundaries

These checks preserve the original 01-01 through 01-03 coverage while keeping them out of the `<30 seconds` task feedback loop:

| Boundary | Complete check |
|----------|----------------|
| Plan 01-01 migration verification | `DATABASE_URL=... .venv/bin/alembic upgrade head && DATABASE_URL=... .venv/bin/alembic check` |
| Plan 01-02 durable-job verification | `DATABASE_URL=... .venv/bin/python -m unittest tests.test_report_jobs -v` |
| Plan 01-03 canonical + compatibility verification | `DATABASE_URL=... .venv/bin/python -m unittest tests.test_canonical_publish -v && .venv/bin/python -m unittest tests.test_app_routes tests.test_ncaa_workflow -v && .venv/bin/python scripts/validate_ncaa_catalog.py` |
| Each gap plan 01-04 through 01-13 | Run that plan's complete `<verification>` block after its individual task targets pass. |
| Plan 01-11 / final Phase 1 gate only | `DATABASE_URL=... .venv/bin/python scripts/validate_phase1_release.py` (complete PostgreSQL, migration, D1/D2/D3, workbook, catalog, and deployment validation) |

---

## Threat References

| Ref | Threat | Minimum automated proof |
|-----|--------|-------------------------|
| T-01-01 | Cross-organization relationship | Database rejects an organization A fact/snapshot link to organization B. |
| T-01-02 | Duplicate delivery or click publishes twice | Same idempotency key/job yields one active job and at most one snapshot. |
| T-01-03 | Draft, failed, or half-written data appears ready | Failure injection leaves no published snapshot; published reads exclude drafts. |
| T-01-04 | Secret/raw exception stored in failure state | Persisted failures contain allowlisted category and sanitized text only. |
| T-01-05 | HTML retained outside the failed-build maximum | Successful builds store no HTML; failed evidence expires at 696 hours, an at-least-daily purge physically clears bytes by 720 hours, stale purge health fails readiness, and sanitized metadata remains. |
| T-01-06 | Queue payload leaks source/customer data | Celery invocation argument contains only opaque job UUID. |
| T-01-07 | Schema change reaches app without migration | `alembic check` is clean after `upgrade head`; release command fails on migration error. |

---

## Wave 0 Requirements

- [ ] `compose.yaml` — PostgreSQL and Redis services with health checks and isolated test credentials.
- [ ] `tests/db_support.py` — PostgreSQL availability guard, migration setup, and transaction/schema cleanup helpers.
- [ ] `tests/test_canonical_data.py` — pure unit stubs for innings, missing values, identity, and provenance conversion.
- [ ] `tests/test_snapshot_persistence.py` — schema/constraint/publication integration stubs.
- [ ] `tests/test_report_jobs.py` — idempotency, retry, cache, retention, and task-entry stubs.
- [ ] `tests/test_canonical_publish.py` — D1/D2/D3 end-to-end publication stubs.
- [ ] `requirements.txt` — SQLAlchemy, Alembic, Psycopg, and Celery/Redis dependencies.

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Managed PostgreSQL and Redis provisioning | OPS-04 | Requires billing approval and external Fly account resources | Confirm services are provisioned, secrets are attached without appearing in logs, and `fly deploy` release migration succeeds. |
| Worker process remains within Browserless account capacity | OPS-01 | Depends on production account limits and Fly machine count | Confirm exactly one worker machine, Celery concurrency does not exceed `BROWSERLESS_MAX_SESSIONS`, then run one live smoke build. |

---

## Validation Sign-Off

- [x] All tasks have automated verification or explicit Wave 0 dependencies.
- [x] Sampling continuity: no three consecutive tasks lack automated verification.
- [x] Wave 0 covers all missing test references.
- [x] No watch-mode flags.
- [x] Every task-level command is a narrow module, class, or method target with a `<30 seconds` feedback budget.
- [x] Full PostgreSQL/migration and D1/D2/D3/workbook release validation is reserved for plan/wave/final boundaries.
- [x] `nyquist_compliant: true` set in frontmatter.

**Approval:** approved 2026-11-01 for planning; Wave 0 remains pending execution.
