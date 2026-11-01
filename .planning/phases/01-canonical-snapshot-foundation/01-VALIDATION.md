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
| **Estimated runtime** | Quick: <5 seconds; full deterministic suite: <30 seconds; migration/integration: <60 seconds |

---

## Sampling Rate

- **After every task commit:** Run that task's narrow `unittest` target and `git diff --check`.
- **After every plan wave:** Run full unittest discovery, PostgreSQL integration tests, and `alembic check`.
- **Before `$gsd-verify-work`:** Compilation, full unittest discovery, PostgreSQL migration/integration tests, catalog validation, and `git diff --check` must be green.
- **Max feedback latency:** 60 seconds for deterministic checks.

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 01-01-01 | 01 | 1 | OPS-04 | T-01-07 | Migration failure prevents rollout; connection secrets stay in environment | integration | `DATABASE_URL=... .venv/bin/alembic upgrade head && DATABASE_URL=... .venv/bin/alembic check` | ❌ W0 | ⬜ pending |
| 01-01-02 | 01 | 1 | SNAP-01, SNAP-03, DATA-02, DATA-04, DATA-05, DATA-06, DATA-08, DATA-09 | T-01-01, T-01-03 | Tenant keys and immutable snapshot invariants are database-enforced | integration | `DATABASE_URL=... .venv/bin/python -m unittest tests.test_snapshot_persistence -v` | ❌ W0 | ⬜ pending |
| 01-01-03 | 01 | 1 | DATA-03, DATA-07, DATA-10 | T-01-01 | Canonical facts retain exact source evidence and published-only reads | integration | `DATABASE_URL=... .venv/bin/python -m unittest tests.test_snapshot_persistence.CanonicalSchemaTests -v` | ❌ W0 | ⬜ pending |
| 01-02-01 | 02 | 2 | SNAP-02, OPS-01 | T-01-02, T-01-06 | Broker messages contain only job IDs and retries are bounded | unit/integration | `DATABASE_URL=... .venv/bin/python -m unittest tests.test_report_jobs.JobLifecycleTests -v` | ❌ W0 | ⬜ pending |
| 01-02-02 | 02 | 2 | SNAP-03, SNAP-04 | T-01-02, T-01-03 | Duplicate delivery cannot duplicate snapshots; failed core sections cannot publish | integration | `DATABASE_URL=... .venv/bin/python -m unittest tests.test_report_jobs.PublicationTests -v` | ❌ W0 | ⬜ pending |
| 01-02-03 | 02 | 2 | OPS-02, OPS-04 | T-01-04, T-01-05 | Cache state is versioned and failed HTML retention is bounded | integration/source | `DATABASE_URL=... .venv/bin/python -m unittest tests.test_report_jobs.RetentionAndCacheTests -v` | ❌ W0 | ⬜ pending |
| 01-03-01 | 03 | 3 | DATA-01, DATA-02, DATA-03, DATA-05, DATA-06, DATA-07 | T-01-03 | NCAA IDs, raw rows, missing values, and innings outs survive normalization | unit | `.venv/bin/python -m unittest tests.test_canonical_data -v` | ❌ W0 | ⬜ pending |
| 01-03-02 | 03 | 3 | SNAP-01, SNAP-03, SNAP-04, DATA-08, DATA-10 | T-01-02, T-01-03 | D1/D2/D3 fixture builds publish the same canonical contract | integration | `DATABASE_URL=... .venv/bin/python -m unittest tests.test_canonical_publish -v` | ❌ W0 | ⬜ pending |
| 01-03-03 | 03 | 3 | OPS-01, OPS-02, OPS-05 | T-01-02 | Existing routes, cache, capacity handling, and workbook output do not regress | regression | `.venv/bin/python -m unittest discover -s tests -v && .venv/bin/python scripts/validate_ncaa_catalog.py` | ✅ existing | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Threat References

| Ref | Threat | Minimum automated proof |
|-----|--------|-------------------------|
| T-01-01 | Cross-organization relationship | Database rejects an organization A fact/snapshot link to organization B. |
| T-01-02 | Duplicate delivery or click publishes twice | Same idempotency key/job yields one active job and at most one snapshot. |
| T-01-03 | Draft, failed, or half-written data appears ready | Failure injection leaves no published snapshot; published reads exclude drafts. |
| T-01-04 | Secret/raw exception stored in failure state | Persisted failures contain allowlisted category and sanitized text only. |
| T-01-05 | HTML retained outside 30-day failed-build policy | Successful builds store no HTML; expired failed evidence is purged while metadata remains. |
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
- [x] Feedback latency target is below 60 seconds for deterministic checks.
- [x] `nyquist_compliant: true` set in frontmatter.

**Approval:** approved 2026-11-01 for planning; Wave 0 remains pending execution.
