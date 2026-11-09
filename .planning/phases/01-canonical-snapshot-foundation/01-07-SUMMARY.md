---
phase: 01-canonical-snapshot-foundation
plan: "07"
subsystem: jobs
tags: [postgresql, transactional-outbox, celery, leases, reconciliation]

requires:
  - phase: 01-canonical-snapshot-foundation
    provides: Durable report jobs, whole-attempt advisory claims, and distributed Browserless capacity
provides:
  - Transactional report-job dispatch intent committed with acceptance
  - Token-matched SKIP LOCKED publisher leases with crash reclamation
  - Bounded UUID-only outbox draining and stale queued/running reconciliation
  - Broker-acknowledged message-loss recovery without duplicate snapshots
affects: [durable-jobs, celery-workers, dispatch-scheduler, canonical-publication, operations]

tech-stack:
  added: []
  patterns: [transactional outbox, renewable publisher lease, broker-as-hint reconciliation]

key-files:
  created:
    - migrations/versions/0005_report_dispatch_outbox.py
  modified:
    - roster_scout/models.py
    - roster_scout/repositories.py
    - roster_scout/jobs.py
    - roster_scout/tasks.py
    - roster_scout/celery_app.py
    - tests/test_report_jobs.py

key-decisions:
  - "Job acceptance and one active dispatch intent commit in the same PostgreSQL transaction; enqueued_at remains audit metadata only."
  - "Publisher ownership is a renewable token-matched lease claimed with FOR UPDATE SKIP LOCKED, while Redis carries only the opaque job UUID."
  - "Worker progress closes the acknowledged intent; stale running work reopens only after the timeout and only when the whole-attempt advisory claim is absent."

patterns-established:
  - "Outbox authority: pending, leased, and acknowledged rows are recoverable PostgreSQL state; broker acknowledgement is never a permanent sent bit."
  - "Reconciliation safety: queued deadlines and stale-running thresholds defer to the exclusive report-job advisory claim before redispatch."

requirements-completed:
  - SNAP-02
  - SNAP-03
  - OPS-01
  - OPS-04

duration: 35 min
completed: 2026-11-09
---

# Phase 1 Plan 7: Transactional Dispatch Outbox and Reconciliation Summary

**A PostgreSQL transactional outbox now recovers broker-acknowledged message loss through renewable leases and claim-aware reconciliation without duplicating jobs or canonical snapshots**

## Performance

- **Duration:** 35 min
- **Started:** 2026-11-09T20:19:06Z
- **Completed:** 2026-11-09T20:53:38Z
- **Tasks:** 2
- **Files modified:** 7

## Accomplishments

- Added reversible revision 0005 and an organization-owned dispatch outbox whose active intent is unique per report job.
- Made job acceptance and dispatch-intent creation one transaction, including rollback and two-session idempotency-race proof.
- Added token-matched publisher leases claimed with `FOR UPDATE SKIP LOCKED`, expiry reclamation, bounded draining, safe release, and opaque acknowledgement metadata.
- Reconciled acknowledged-but-dropped queued messages and unclaimed stale running work while active execution claims and terminal jobs remain ineligible.
- Proved the CR-03 loss case reaches exactly one ready/partial snapshot without another acceptance call.

## Task Commits

Each task was committed atomically using RED/GREEN TDD gates:

1. **Task 1 RED: Expose transactional outbox gaps** - `7477288` (test)
2. **Task 1 GREEN: Add transactional dispatch outbox** - `fd640a8` (feat)
3. **Task 2 RED: Reproduce broker message loss recovery** - `3b7df2c` (test)
4. **Task 2 GREEN: Reconcile durable dispatch leases** - `2dd443a` (feat)
5. **Task 2 hardening: Persist only opaque dispatch identifiers** - `028a359` (fix)

## Files Created/Modified

- `migrations/versions/0005_report_dispatch_outbox.py` - Reversible outbox table, ownership FK, active-intent uniqueness, due indexes, lease constraints, and nonterminal-job backfill.
- `roster_scout/models.py` - `ReportJobDispatch` ORM model aligned with revision 0005.
- `roster_scout/repositories.py` - Intent creation, SKIP LOCKED claim, token acknowledgement, safe release/reopen, and close operations.
- `roster_scout/jobs.py` - Atomic acceptance, bounded outbox drain, stale-job reconciliation, and execution-state integration.
- `roster_scout/tasks.py` - Strict UUID normalization at the registered Celery publication boundary.
- `roster_scout/celery_app.py` - Explicit resultless/non-tracked task configuration for PostgreSQL-authoritative state.
- `tests/test_report_jobs.py` - Transaction rollback, two-session barriers, migration reversibility, broker-loss, crash-reclaim, active-claim, stale-running, and terminal regressions.

## Decisions Made

- Retained `ReportJob.enqueued_at` and `broker_message_id` for audit compatibility, but removed them from dispatch authority; only the outbox state determines recoverability.
- Used one partial unique index across `pending`, `leased`, and `acknowledged` so a closed historical intent can coexist with exactly one current intent.
- Persisted the outbox-owned UUID after broker acknowledgement instead of broker-returned text, preventing topology, credential, or implementation details from entering durable job state.
- Closed active dispatch intents when a worker records `running`; a genuinely stale running job receives a new intent only after the configured timeout and an unsuccessful active-claim check.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Corrected the claim-race test clock**
- **Found during:** Task 1 GREEN verification
- **Issue:** The fixed test timestamp preceded the outbox row's database creation time, so the pending row was correctly not due and the race test claimed nothing.
- **Fix:** Anchored the shared barrier test time just after the current UTC instant while preserving deterministic lease-expiry arithmetic.
- **Files modified:** `tests/test_report_jobs.py`
- **Verification:** `TransactionalOutboxTests` passed all four rollback, acceptance-race, claim-race, and migration cases.
- **Committed in:** `fd640a8`

**2. [Rule 2 - Security] Prevented broker acknowledgement details from durable storage**
- **Found during:** Task 2 acceptance review
- **Issue:** Persisting a broker-returned message identifier could admit implementation-specific or secret-bearing text from a nonconforming broker adapter.
- **Fix:** Durable audit state now records only the outbox-owned opaque UUID after a token-matched acknowledgement.
- **Files modified:** `roster_scout/repositories.py`
- **Verification:** Broker-loss and lifecycle targets passed with one opaque UUID payload and no persisted broker detail.
- **Committed in:** `028a359`

---

**Total deviations:** 2 auto-fixed (1 Rule 1 bug, 1 Rule 2 security hardening).
**Impact on plan:** Both fixes tighten correctness and disclosure safety within the planned outbox boundary; no scope expansion.

## Issues Encountered

- The managed sandbox blocked localhost PostgreSQL access; scoped approved execution was used only for the disposable `roster_scout_test` database.
- Context7 and its CLI fallback were unavailable, so implementation followed the installed SQLAlchemy 2.1/Alembic 1.20 APIs and was validated through integration tests plus `alembic check`.

## User Setup Required

None - Plan 01-12 owns deployment wiring for the callable drain and reconciliation services.

## Verification Results

- `TransactionalOutboxTests`: 4 passed, including forced rollback, barrier-released acceptance, genuine two-session SKIP LOCKED claims, expired-lease reclaim, and revision 0005 reversal.
- `BrokerLossRecoveryTests`: 3 passed, including broker acknowledgement followed by message loss, active-claim suppression, terminal suppression, stale-running recovery, and publisher-crash reclaim.
- Full `tests.test_report_jobs`: 25 passed.
- Full PostgreSQL-backed discovery: 143 passed.
- Revision 0005 downgrade/re-upgrade and `alembic check`: passed with no schema drift.
- NCAA catalog validation: 942 selectable 2026 D1/D2/D3 teams, zero errors.
- Required Python compilation, template existence, and `git diff --check`: passed.

## Known Stubs

None.

## Threat Flags

None - the new PostgreSQL-to-Redis outbox and scheduler-facing reconciliation boundary are the exact trust surfaces registered in the plan threat model, with token matching, lease expiry, UUID-only payloads, and claim-aware redispatch implemented.

## Next Phase Readiness

- CR-03 is closed with a broker-acknowledged/message-lost reproduction and automatic recovery proof.
- Plan 01-12 can call `drain_report_dispatch_outbox()` and `reconcile_stale_report_jobs()` from its bounded scheduler process without duplicating SQL or state rules.
- Ready for Plan 01-08 final-attempt and per-section recovery; existing managed-service provisioning blockers remain unchanged.

## TDD Gate Compliance

- Task 1 RED commit `7477288` failed on the missing `ReportJobDispatch`, claim repository, and revision 0005; GREEN commit `fd640a8` passed the complete transactional-outbox target.
- Task 2 RED commit `3b7df2c` failed on the missing drain/reconciliation services; GREEN commit `2dd443a` passed all broker-loss and stale-lease cases.

## Self-Check: PASSED

- All seven created or modified implementation/test files exist.
- Task commits `7477288`, `fd640a8`, `3b7df2c`, `2dd443a`, and `028a359` exist in git history.
- Every task acceptance criterion, plan-level verification command, and repository handoff check passed.

---
*Phase: 01-canonical-snapshot-foundation*
*Completed: 2026-11-09*
