---
phase: 02-identity-organizations-and-report-library
plan: "04"
subsystem: organizations
tags: [flask, postgresql, sessions, csrf, accessibility, authorization]

requires:
  - phase: 02-03
    provides: Locally authoritative organization memberships, invitations, and append-only audit services
  - phase: 02-14
    provides: Organization route composition, authenticated member shell, and invitation administration
provides:
  - Current-local-membership-only organization chooser with session rotation
  - Admin role-change and removal workflows with serialized last-admin protection
  - Immediate stale-session clearing and request-time membership revalidation
affects: [02-05, report-library, organization-authorization, customer-portal]

tech-stack:
  added: []
  patterns:
    - Organization-row serialization before administrator and target membership mutation
    - Signed browser candidate tokens followed by canonical account-scoped membership lookup
    - Tenant switch rotation with fixed report-library destination

key-files:
  created:
    - templates/organization_select.html
  modified:
    - roster_scout/organization_routes.py
    - roster_scout/organizations.py
    - roster_scout/sessions.py
    - templates/organization_members.html
    - app.py
    - tests/test_organizations.py
    - tests/test_identity.py

key-decisions:
  - "Organization switches rotate the verifier and CSRF secret while preserving the session family and original absolute expiration."
  - "Rendered member controls use signed candidate tokens; every mutation still reloads and scopes the canonical membership by the server-derived account or organization."
  - "Last-admin checks serialize on the organization row before validating the actor and target, so concurrent demote/remove attempts cannot both succeed."

patterns-established:
  - "Organization switching ignores submitted organization, role, resource, and return-path fields and always redirects to /reports."
  - "Membership removal deactivates the membership and clears every matching live active-organization session in the same transaction."

requirements-completed: [ORG-03, ORG-04, ORG-05, ORG-06, ORG-07, AUTH-05, SECU-02, SECU-04]

duration: 18 min
completed: 2026-11-11
---

# Phase 2 Plan 04: Organization Switching and Member Administration Summary

**Locally authoritative organization switching with verifier rotation, signed browser candidates, accessible administrator controls, and PostgreSQL-serialized last-admin protection.**

## Performance

- **Duration:** 18 min
- **Started:** 2026-11-12T00:14:02Z
- **Completed:** 2026-11-12T00:31:58Z
- **Tasks:** 2
- **Files modified:** 8

## Accomplishments

- Added an account-only organization chooser that lists current local memberships, accepts only a membership candidate, proves account ownership, rotates the local session, and redirects to the report library without carrying resource destinations across tenants.
- Added administrator role-change and removal services that lock the organization row, revalidate the active administrator, protect the final admin under real concurrency, append sanitized audit events, and commit atomically.
- Made membership removal effective immediately by deactivating the membership and clearing all matching active organization session contexts in the same transaction; protected requests continue to reload local membership and role state.
- Added semantic desktop and mobile administration controls with CSRF tokens, fixed permission/error copy, deliberate destructive confirmation, 44px controls, and no visible canonical UUIDs.

## Task Commits

Each task was committed atomically:

1. **Task 1: Wave 0 — specify multi-organization selection and last-admin-safe administration** - `73b2500` (test)
2. **Task 2: Implement safe organization switching and member administration** - `a02499e` (feat)

## Files Created/Modified

- `templates/organization_select.html` - Renders only active local organizations with signed membership candidates and CSRF-protected switch forms.
- `roster_scout/organization_routes.py` - Composes account-only selection/session rotation and protected member role/removal routes over server-derived context.
- `roster_scout/organizations.py` - Implements organization-row locking, last-admin counting, scoped role change, removal, and audit writes.
- `roster_scout/sessions.py` - Rotates a tenant session without extending its absolute lifetime and clears removed membership contexts.
- `templates/organization_members.html` - Adds equivalent desktop/mobile role controls and destructive removal confirmations.
- `app.py` - Classifies selection and switching as authenticated account-only routes while keeping member administration organization-protected.
- `tests/test_organizations.py` - Proves deny-by-default capabilities, safe switching, CSRF, immediate invalidation, and the two-connection last-admin race.
- `tests/test_identity.py` - Keeps the exact route and authorization inventory current for all new endpoints.

## Decisions Made

- Session rotation keeps the opaque provider state, rotation family, and original absolute deadline, but generates a new lookup verifier and CSRF secret. This prevents tenant switching from extending the maximum login lifetime or retaining a bearer credential across contexts.
- Browser membership identifiers are signed before rendering. They remain lookup candidates only: routes re-query active canonical memberships using the signed-in account for switches and the server-derived organization for administrator mutations.
- All last-admin-sensitive paths lock the organization first, then reload the actor and target. This gives parallel demote/remove transactions one stable serialization order and ensures the loser observes the winner's committed membership state.
- The existing `require_context` request gate already reloaded membership and role on every protected request and cleared stale active organization state, so no duplicate authorization path was added.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Corrected the removal-invalidation test fixture**
- **Found during:** Task 2 service implementation
- **Issue:** The red test attempted to remove the only active administrator while also expecting removal to succeed, contradicting the plan's last-admin invariant.
- **Fix:** Added a second active administrator to the organization and used that administrator as the removal actor.
- **Files modified:** `tests/test_organizations.py`
- **Verification:** The immediate invalidation test and real concurrent last-admin race both pass.
- **Committed in:** `a02499e`

**2. [Rule 1 - Bug] Updated the exact application route inventory**
- **Found during:** Task 2 identity regression verification
- **Issue:** The security regression enumerates every Flask route and account-only endpoint; registering the four planned organization routes made that inventory stale.
- **Fix:** Added selection, switch, role, and removal routes plus the two account-only endpoint classifications.
- **Files modified:** `tests/test_identity.py`
- **Verification:** `AuthGateRouteMatrixTests`, the combined focused suite, and clean full discovery pass.
- **Committed in:** `a02499e`

---

**Total deviations:** 2 auto-fixed bugs
**Impact on plan:** Both fixes were required to keep the executable contract internally consistent and the default-deny route inventory complete; no product scope was added.

## Issues Encountered

- An initial broad discovery run reproduced the repository's documented shared-database order contamination: migration downgrade/upgrade tests and leftover rows cascaded into unrelated organization, job, and snapshot failures. Only the disposable `roster_scout_test` database was recreated; one clean authoritative run then passed all 491 tests.
- Local PostgreSQL verification required approved localhost access. No production database, identity workspace, external provider, deployment target, or live NCAA boundary was contacted.

## User Setup Required

None - no dependencies, schema changes, provider settings, or hosted configuration were added.

## Next Phase Readiness

- Every report-library route can rely on a freshly revalidated local organization and role after switches, role changes, and removals.
- The chooser and authenticated header now support multi-organization customer accounts without carrying report/job/snapshot destinations across tenants.
- No blockers remain for the next report-library plan.

## Verification

- Clean `unittest discover` against recreated disposable `roster_scout_test` - **491 tests passed**
- `tests.test_organizations` plus route-matrix/session lifecycle regressions - **40 tests passed**
- `node --test tests/js/*.test.mjs` - **16 tests passed**
- Application, scraper, operations scripts, and changed organization/session module compilation - **passed**
- NCAA selector catalog - **942 selectable teams; valid**
- Required template existence and `git diff --check` - **passed**
- Stub scan across all created/modified plan files - **no blocking stubs found**

## Security and Threat Surface

- All new endpoints, session rotation, membership candidate handling, removal invalidation, and last-admin serialization are covered by Plan 02-04's threat register; no unplanned trust boundary was introduced.
- Unsafe forms use the global SQL-backed CSRF boundary, browser fields never establish actor/organization/role authority, and signed candidate tokens avoid rendering raw membership UUIDs in the new controls.
- Security events contain only canonical UUIDs and fixed allowlisted action/outcome/reason tokens.

## Known Stubs

None.

## Self-Check: PASSED

- The created organization chooser, all seven modified implementation/test files, and both task commits exist.
- Focused, full-suite, JavaScript, compilation, catalog, template, stub, and diff claims were rerun after the implementation commit.

---
*Phase: 02-identity-organizations-and-report-library*
*Completed: 2026-11-11*
