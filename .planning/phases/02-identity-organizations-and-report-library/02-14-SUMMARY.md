---
phase: 02-identity-organizations-and-report-library
plan: "14"
subsystem: organizations
tags: [flask, postgresql, workos, csrf, accessibility, invitations]

requires:
  - phase: 02-03
    provides: Locally authoritative organization, invitation, provider-mirror, and append-only audit services
  - phase: 02-12
    provides: Default-deny authentication gate, SQL-backed sessions, global CSRF, and authenticated product shell
provides:
  - Restricted idempotent organization and first-admin bootstrap command
  - Admin-only invitation listing, creation, and revocation browser workflow
  - Exact-email invitation acceptance with fixed non-enumerating outcomes
affects: [02-04, organization-selection, membership-administration, deployment]

tech-stack:
  added: []
  patterns:
    - Thin Flask composition over caller-owned organization service transactions
    - Local invitation durability before provider delivery
    - Server-derived account, organization, and administrator authority

key-files:
  created:
    - roster_scout/organization_routes.py
    - scripts/provision_organization.py
    - templates/organization_members.html
    - templates/invitation_outcome.html
  modified:
    - app.py
    - static/product.css
    - tests/test_identity.py
    - tests/test_organizations.py

key-decisions:
  - "Organization creation remains operator-only; the browser exposes no organization-create route."
  - "Invitation mutations reuse the Plan 02-03 service boundary, with provider delivery after the canonical local transaction commits."
  - "Invitation acceptance is account-only and compares the signed-in canonical account through the domain service; browser organization and role claims are never accepted."

patterns-established:
  - "Invitation outcome pages use fixed copy and omit recipient, organization, provider, token, and exception details on failures."
  - "Administration renders one scoped view model as semantic desktop tables and equivalent mobile cards."

requirements-completed: [ORG-02, ORG-03, ORG-04, ORG-05, ORG-07, AUTH-04, SECU-03, SECU-04]

duration: 19 min
completed: 2026-11-11
---

# Phase 2 Plan 14: Organization Invitation Composition Summary

**Restricted organization bootstrap plus CSRF-safe, server-authorized invitation administration and exact-email acceptance in the authenticated Roster Scout shell.**

## Performance

- **Duration:** 19 min
- **Started:** 2026-11-11T23:49:00Z
- **Completed:** 2026-11-12T00:08:02Z
- **Tasks:** 2
- **Files modified:** 8

## Accomplishments

- Added an idempotent operator command that creates or reselects a local organization, mirrors it through the provider boundary, delivers the first-admin invitation, and prints only fixed safe output.
- Added default-deny Flask routes for administrator invitation listing, creation, revocation, and account-only acceptance, all using the revalidated server session and global CSRF boundary.
- Added accessible member and invitation screens with semantic tables, equivalent mobile cards, 44px controls, error-summary focus, deliberate destructive confirmation, fixed permission copy, and non-enumerating acceptance outcomes.
- Proved local invitation state survives provider delivery failure and that exact-address mismatch cannot consume or disclose an invitation.

## Task Commits

Each task was committed atomically:

1. **Task 1: Wave 0 — specify bootstrap CLI and invitation browser composition** - `4633df0` (test)
2. **Task 2: Implement bootstrap and invitation route/UI composition** - `3f7f80a` (feat)

## Files Created/Modified

- `roster_scout/organization_routes.py` - Composes scoped organization reads and invitation service transactions behind administrator and account-only routes.
- `scripts/provision_organization.py` - Provides the restricted, idempotent first-organization/first-admin provisioning command with fixed output.
- `templates/organization_members.html` - Renders invitation administration, active members, pending invitations, validation feedback, and permission denial.
- `templates/invitation_outcome.html` - Renders accepted, mismatch, unavailable, and not-found outcomes without enumeration.
- `app.py` - Registers organization routes and classifies invitation acceptance as account-only while leaving administration protected by active membership.
- `static/product.css` - Adds responsive administration tables/cards, forms, banners, and confirmation styling using the existing product tokens.
- `tests/test_organizations.py` - Covers the operator CLI, administrator/member access, CSRF, fixed outcomes, provider outage durability, and absence of public organization creation.
- `tests/test_identity.py` - Extends the exact route matrix and account-only allowlist assertions for the new endpoints.

## Decisions Made

- The command commits local bootstrap state before provider mirroring or invitation delivery, allowing a fixed-error retry to converge without creating another organization or invitation.
- Administration derives the actor, organization, and current role exclusively from `g.authorization_context`; acceptance derives the account exclusively from `g.account_session`.
- Pending invitation rows exclude expired records, show invited-by/sent/expiry context, and revoke through a CSRF-protected form only after deliberate confirmation.
- Provider failure remains operationally distinct but sanitized: the local pending invitation is preserved and no provider identifier, exception, token, or recipient secret is rendered.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Updated the exact application route inventory**
- **Found during:** Task 2 route registration
- **Issue:** The existing security regression asserted the complete Flask URL map and account-only allowlist, so registering planned routes without updating it made the gate stale.
- **Fix:** Added all four organization/invitation routes and classified acceptance as account-only in the route matrix.
- **Files modified:** `tests/test_identity.py`
- **Verification:** `tests.test_identity.AuthGateRouteMatrixTests` and the combined 52-test identity/organization suite pass.
- **Committed in:** `3f7f80a`

**2. [Rule 2 - Missing critical functionality] Added shared responsive and accessible administration styles**
- **Found during:** Task 2 template implementation
- **Issue:** The plan required 44px controls, semantic desktop/mobile equivalence, visible errors, destructive styling, and responsive layout, but the plan file list omitted the shared stylesheet.
- **Fix:** Extended the existing authenticated product stylesheet rather than embedding page-local styles.
- **Files modified:** `static/product.css`
- **Verification:** Route tests assert table/card equivalence and form/error contracts; compilation and browser regression suites pass.
- **Committed in:** `3f7f80a`

---

**Total deviations:** 2 auto-fixed (1 bug, 1 missing critical functionality)
**Impact on plan:** Both changes preserve the centralized authorization inventory and the required accessible UI contract without expanding product behavior.

## Issues Encountered

- Whole-repository `unittest discover` ran 450 tests but reproduced the pre-existing shared-test-database order-dependence documented by Plan 02-12: migration lifecycle and concurrency classes leave locks/schema state that cascade into unrelated failures. The clean plan-specific organization module passes 23 tests, and the combined identity/organization regression run passes 52 tests.
- The initial sandboxed PostgreSQL checks required approved localhost access. All database-backed verification used the local test database only.
- No production database, WorkOS workspace, live invitation delivery, deployment resource, or external NCAA smoke boundary was contacted.

## User Setup Required

None for deterministic development and verification. Hosted WorkOS settings and live invitation delivery remain deployment gates; the new operator command expects the already-documented database and identity environment configuration.

## Next Phase Readiness

- Plan 02-04 can extend the new organization blueprint and members template with multi-organization selection, role changes, removal, last-admin protection, and session invalidation.
- The server-derived context, CSRF forms, fixed error outcomes, responsive view model, and restricted bootstrap path are ready for that work.

## Verification

- `tests.test_organizations` - **23 tests passed**
- `tests.test_identity tests.test_organizations` - **52 tests passed**
- `tests.test_identity.AuthGateRouteMatrixTests` plus Plan 02-14 route/CLI classes - **14 tests passed**
- `node --test tests/js/*.test.mjs` - **16 tests passed**
- Alembic upgrade and drift check - **passed; no new upgrade operations**
- Application, scraper, organization route, CLI, and operations-script compilation - **passed**
- NCAA selector catalog - **942 selectable teams; valid**
- Template existence and `git diff --check` - **passed**
- Stub scan across created/modified plan files - **no blocking stubs found**

## Security and Threat Surface

- All new operator, invitation form, provider delivery, and acceptance surfaces are covered by the plan's STRIDE register; no unplanned trust boundary was introduced.
- No browser field supplies organization, actor, or current-role authority, and all unsafe routes pass through centralized CSRF enforcement.
- Failure pages and command output omit provider IDs, raw mismatch addresses, tokens, callback material, exception text, and canonical UUIDs.

## Known Stubs

None.

## Self-Check: PASSED

- All four created implementation artifacts and both task commits exist.
- The summary's focused verification claims were rerun after the implementation commit.

---
*Phase: 02-identity-organizations-and-report-library*
*Completed: 2026-11-11*
