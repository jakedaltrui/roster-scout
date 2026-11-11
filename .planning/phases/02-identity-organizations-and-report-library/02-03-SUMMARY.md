---
phase: 02-identity-organizations-and-report-library
plan: "03"
subsystem: organization-access
tags: [postgresql, sqlalchemy, alembic, workos, invitations, audit]
requires:
  - phase: 02-identity-organizations-and-report-library
    provides: Plans 02-02 and 02-12 canonical accounts, memberships, managed identity boundary, and default-deny application shell
provides:
  - Locally authoritative exact-address organization invitations with seven-day expiry
  - Idempotent and concurrent-safe invitation acceptance into one local organization
  - Retryable WorkOS organization and invitation delivery metadata that never authorizes access
  - Database-enforced append-only allowlisted security audit events
  - Restricted-operator organization bootstrap and invitation revocation domain primitives
affects: [02-04-membership-administration, 02-14-organization-routes, identity, authorization, audit]
tech-stack:
  added: []
  patterns: [local-row-first-provider-delivery, exact-address-authorization, caller-owned-domain-transactions, append-only-audit-ledger]
key-files:
  created:
    - migrations/versions/0017_phase2_invitations_audit.py
    - roster_scout/organizations.py
    - roster_scout/audit.py
    - tests/test_organizations.py
  modified:
    - roster_scout/models.py
    - roster_scout/identity.py
key-decisions:
  - "Only a pending, unexpired local invitation matching the account's exact normalized verified email can create a local membership."
  - "WorkOS organization and invitation identifiers are delivery/SSO routing metadata only; provider domain or organization claims never grant access."
  - "Provider calls run outside database transactions, while local invitation creation, acceptance, and audit writes remain atomic in caller-owned transactions."
  - "Security audit rows accept only canonical UUIDs and fixed tokens and are protected from update or delete by a PostgreSQL trigger."
patterns-established:
  - "Local row first: invitation durability precedes provider delivery, so provider failure leaves a safe retryable local record."
  - "Exact-address acceptance: lock the invitation, revalidate local account email and state, then create/reselect membership and consume the invitation atomically."
  - "Provider mirror isolation: local organization UUID is the provider external ID, but provider membership data is never imported as authorization."
requirements-completed: [ORG-01, ORG-02, ORG-03, ORG-04, ORG-05, ORG-07, AUTH-04, SECU-03, SECU-04]
duration: 16 min
completed: 2026-11-11
---

# Phase 2 Plan 03: Canonical Organization Invitation Domain Summary

**Roster Scout now has locally authoritative, exact-address organization invitations with concurrent-safe acceptance, retryable WorkOS delivery mirrors, and database-enforced append-only security audits.**

## Performance

- **Duration:** 16 min
- **Started:** 2026-11-11T23:33:29Z
- **Completed:** 2026-11-11T23:48:58Z
- **Tasks:** 2
- **Files modified:** 6

## Accomplishments

- Added revision 0017 with provider-organization mirrors, invitation lifecycle and composite ownership constraints, one-active-invite partial uniqueness, audit-event allowlists, and a database trigger that rejects audit updates and deletes.
- Implemented restricted-operator bootstrap, active-admin invitation creation and revocation, exact-email acceptance, and idempotent replay/concurrency behavior without adding route, CLI, or template scope.
- Extended the WorkOS boundary to mirror organizations by local UUID and deliver invitations while keeping all provider identifiers non-authoritative and all provider failures fixed-category and redacted.
- Preserved local invitation durability across provider outages and bounded provider expiry metadata to the canonical seven-day local authorization deadline.
- Proved provider domain and organization claims cannot create local membership.

## Task Commits

Each task was committed atomically:

1. **Task 1: Specify invitation creation, exact-email acceptance, and no-domain-join** - `25cccb9` (test)
2. **Task 2: Implement canonical organization and exact-email invitation services** - `0c86ac2` (feat)
3. **Task 2 hygiene: Normalize new module endings** - `c1cb75d` (style)

## Files Created/Modified

- `migrations/versions/0017_phase2_invitations_audit.py` - Creates provider mirror, invitation, and append-only audit ownership schema and reverses cleanly to 0016.
- `roster_scout/models.py` - Maps organization identity connections, invitation lifecycle fields/constraints, audit events, and the auth-transaction invitation foreign key.
- `roster_scout/organizations.py` - Supplies connection-free bootstrap, invite, mirror, delivery, acceptance, and revocation services.
- `roster_scout/audit.py` - Appends sanitized allowlisted security events without accepting free text or owning commits.
- `roster_scout/identity.py` - Adds protocol-neutral provider organization/invitation values and matching fake and WorkOS adapter methods.
- `tests/test_organizations.py` - Covers schema lifecycle, exact-address counterexamples, retry/redaction, idempotent bootstrap/mirror, expiry bounds, audit immutability, and concurrent acceptance.

## Decisions Made

- Invitation creation locks the local organization and verifies an active local admin before inserting the canonical row; no provider call can create authorization state.
- Acceptance locks the invitation and treats only the local account's normalized verified email as proof of eligibility. Same-domain substitutions fail without consuming the invite.
- Successful replay returns the already-created membership, and concurrent acceptance serializes on the invitation row so both callers converge on one membership.
- Provider organization creation uses the local organization UUID as WorkOS `external_id`; the mapping exists only for delivery and future SSO routing.
- Provider calls are coordinated with an injected session factory and execute outside database transactions. Local mutations and audit evidence remain in the same database transaction.
- Audit actions, outcomes, reasons, and target types are code and database allowlisted; provider payloads, invitation secrets, raw callback material, and exception text cannot enter audit rows.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Removed trailing blank lines detected by the final diff hygiene check**
- **Found during:** Task 2 final commit verification
- **Issue:** Two new modules ended with an extra blank line and failed `git diff --check`.
- **Fix:** Normalized both file endings in a dedicated follow-up commit.
- **Files modified:** `migrations/versions/0017_phase2_invitations_audit.py`, `roster_scout/audit.py`
- **Verification:** `git diff --check` passes.
- **Committed in:** `c1cb75d`

---

**Total deviations:** 1 auto-fixed (1 bug)
**Impact on plan:** Hygiene-only correction; no scope or behavior changed.

## Issues Encountered

- The system Python lacked the repository's installed `requests` dependency for the catalog check. Re-running the documented command with `.venv/bin/python` used the pinned project environment and passed.
- The initial sandboxed PostgreSQL run could not access localhost. The same planned checks ran against the repository's local test database with approved localhost access and passed.
- No production database, WorkOS workspace, email delivery, deployment resource, or external NCAA smoke boundary was contacted.

## User Setup Required

None for this domain slice. Plan 02-14 owns the restricted operator command and customer-facing routes; live WorkOS configuration and delivery remain deployment gates.

## Next Phase Readiness

- Plan 02-04 can build membership administration and last-admin protections on the canonical memberships, invitation lifecycle, admin authorization helper, and audit ledger.
- Plan 02-14 can compose the restricted bootstrap command and invitation routes without duplicating authorization or provider-delivery logic.
- The authenticated shell can continue to authorize exclusively from active local memberships; provider claims remain deliberately non-authoritative.

## Verification

- Full Python suite - **477 tests passed**
- Relevant identity and organization regression suite - **43 tests passed**
- Organization domain module, including concurrent acceptance - **14 tests passed**
- Revision 0017 downgrade/re-upgrade and Alembic drift check - **passed**
- Node download-tracking suite - **16 tests passed**
- NCAA catalog validation - **942 selectable teams; valid**
- Application, scraper, migration, service, and operations-script compilation - **passed**
- `git diff --check` - **passed**
- Stub scan across all plan-created and plan-modified files - **no blocking stubs found**

## Security and Threat Surface

- All new provider calls and schema trust boundaries are covered by the plan's invitation/provider STRIDE register; no unplanned threat surface was introduced.
- Logs use fixed delivery categories and omit provider exceptions, customer addresses, tokens, and payloads.
- Database constraints bind inviter and accepting accounts to memberships in the same organization and reject invalid invitation lifecycle transitions.
- Audit events are append-only in PostgreSQL and accept only canonical IDs and fixed allowlisted tokens.

## Known Stubs

None.

## Self-Check: PASSED

- Created files exist: revision 0017, `roster_scout/organizations.py`, `roster_scout/audit.py`, and `tests/test_organizations.py`.
- Task commits exist: `25cccb9`, `0c86ac2`, and `c1cb75d`.
- Planned focused, migration, full Python, Node, catalog, compilation, and diff verification completed successfully.
- User-owned untracked Phase 1 duplicate files and `brag-output/` remain untouched.

---
*Phase: 02-identity-organizations-and-report-library*
*Completed: 2026-11-11*
