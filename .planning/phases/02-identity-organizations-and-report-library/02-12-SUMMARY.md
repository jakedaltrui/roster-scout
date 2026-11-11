---
phase: 02-identity-organizations-and-report-library
plan: "12"
subsystem: authenticated-application-shell
tags: [flask, workos, csrf, authorization, session-interface, accessibility]
requires:
  - phase: 02-identity-organizations-and-report-library
    provides: Plan 02-02 managed identity, opaque sessions, and canonical local authorization
provides:
  - Default-deny Flask route gate backed by revalidated local membership context
  - SQL-backed Flask session projection and global CSRF enforcement
  - Local-first POST logout and membership-aware callback destinations
  - Accessible reusable authenticated product shell and responsive design tokens
  - Secret-name-only hosted identity configuration and explicit production readiness gates
affects: [02-03-organizations, 02-04-report-library, 02-05-report-detail, deployment]
tech-stack:
  added: []
  patterns: [short-public-allowlist, default-deny-route-gate, sql-backed-csrf, fail-closed-hosted-auth]
key-files:
  created:
    - templates/product_base.html
    - static/product.css
  modified:
    - roster_scout/auth_routes.py
    - app.py
    - .env.example
    - DEPLOYMENT.md
    - tests/test_identity.py
    - tests/test_deployment_config.py
key-decisions:
  - "Every non-public route reloads canonical local session and membership state; the public allowlist stays deliberately short."
  - "Flask-WTF reads its CSRF token from the SQL-backed AccountSession projection rather than creating a second browser session cookie."
  - "Hosted identity configuration fails closed, and deterministic code_verified evidence remains separate from production_auth_verified evidence."
requirements-completed: [AUTH-01, AUTH-02, AUTH-03, AUTH-04, AUTH-05, ORG-04, ORG-07, SECU-03, SECU-05]
duration: 17 min
completed: 2026-11-11
---

# Phase 2 Plan 12: Authenticated Flask Shell Summary

**Roster Scout now composes its isolated identity backend into a default-deny, CSRF-protected Flask application with a reusable accessible product shell and fail-closed hosted configuration.**

## Performance

- **Duration:** 17 min
- **Started:** 2026-11-11T23:02:48Z
- **Completed:** 2026-11-11T23:20:08Z
- **Tasks:** 2
- **Files modified:** 8

## Accomplishments

- Installed a SQL-backed Flask `SessionInterface` that projects the persisted account-session CSRF secret without exposing account or provider state in a second browser cookie.
- Added a short explicit public endpoint allowlist and a default-deny `before_request` gate that revalidates the opaque account session and active canonical local membership for every product and legacy route.
- Composed callback and logout routes over the Plan 02-02 services: callbacks select only current local memberships, and POST logout commits local revocation before a best-effort provider redirect.
- Added a responsive, keyboard-accessible product shell with skip navigation, one main landmark and heading, organization/account controls, CSRF-safe logout, dark mode, and reduced-motion support.
- Added secret-name-only identity configuration plus a runbook that keeps deterministic `code_verified` evidence distinct from later live `production_auth_verified` evidence.
- Made hosted configuration reject missing settings, non-HTTPS redirects, and callback URLs containing paths or query state beyond the exact `/auth/callback` endpoint.

## Task Commits

Each task was committed atomically:

1. **Task 1: Specify the complete Flask route gate, shared shell, and safe configuration** - `adccb17` (test)
2. **Task 2: Wire the protected Flask application and shared authenticated shell** - `a475a17` (feat)

## Files Created/Modified

- `roster_scout/auth_routes.py` - Adds the SQL-backed browser-session projection, membership-aware callback routing, local-first POST logout, and Flask registration boundary.
- `app.py` - Selects the fake or WorkOS adapter safely, installs global CSRF, and enforces the centralized default-deny authorization gate.
- `templates/product_base.html` - Supplies the reusable accessible authenticated page shell.
- `static/product.css` - Implements responsive Phase 2 product tokens, focus states, dark mode, and reduced-motion behavior.
- `.env.example` - Names required identity settings without credential values.
- `DEPLOYMENT.md` - Documents deterministic verification, staging configuration, and later production identity gates.
- `tests/test_identity.py` - Enumerates the route matrix, callback membership outcomes, logout ordering, CSRF behavior, and shell contract.
- `tests/test_deployment_config.py` - Verifies secret-free tracked configuration and fail-closed hosted identity readiness.

## Decisions Made

- Public access is endpoint-based and explicit. Every current or future endpoint not in the small allowlist is protected automatically.
- Account-only transitions such as logout and no-membership state reload the opaque account session but do not fabricate an organization context.
- Product access always comes from `require_context`, which revalidates canonical local membership on every request and never trusts provider organization or domain claims.
- Flask-WTF uses the persisted `AccountSession.csrf_secret`; the server session interface intentionally performs no browser-side save.
- Missing or malformed hosted identity configuration selects an unavailable provider and reports `configuration_required`; it never falls back to the deterministic fake adapter.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Prevented the tracked-secret assertion from crossing line boundaries**
- **Found during:** Task 2 focused verification
- **Issue:** The original whitespace expression could consume a newline and treat the next environment setting as a WorkOS secret value.
- **Fix:** Restricted assignment whitespace to horizontal spaces and tabs.
- **Files modified:** `tests/test_deployment_config.py`
- **Commit:** `a475a17`

**2. [Rule 2 - Missing critical functionality] Validated the hosted callback URL before enabling identity**
- **Found during:** Task 2 configuration review
- **Issue:** Non-empty WorkOS settings alone could mark an HTTP or query-bearing callback URL ready in a hosted runtime.
- **Fix:** Added exact callback parsing and regressions so hosted identity requires HTTPS and the exact `/auth/callback` path with no credentials, query, or fragment.
- **Files modified:** `app.py`, `tests/test_deployment_config.py`
- **Commit:** `a475a17`

## Issues Encountered

- One whole-repository discovery run exposed existing order-dependent shared-test-database failures in canonical publication, purge readiness, and an enterprise authorization expectation. The identity and deployment modules pass independently, and the complete plan-specified 82-test authentication/configuration run passes in one process. No out-of-scope database-test lifecycle code was changed.
- No live WorkOS workspace, email, external IdP, production database, or deployment resource was contacted.

## User Setup Required

None for deterministic development and verification. Production WorkOS provisioning, billing confirmation, secrets, exact redirect registration, passwordless email, Google/Microsoft, and real customer OIDC or SAML smoke evidence remain explicit later deployment gates.

## Next Phase Readiness

- Organization chooser, invitation, and report-library routes can extend `product_base.html` and inherit the default-deny route boundary automatically.
- Membership changes take effect on the next request because the application does not cache provider or local authorization claims in the browser.
- Hosted releases fail product authentication closed until the full WorkOS configuration is installed, without overstating production readiness.

## Verification

- Plan-specified identity, server-session, secret-redaction, and full deployment configuration run - **82 tests passed**
- `tests.test_identity` - **29 tests passed**
- `tests.test_app_routes.AppReadinessTests` - **9 tests passed**
- Python compilation for the application, scraper, required operations scripts, and auth routes - **passed**
- Required `templates/index.html` and `templates/demo.html` checks - **passed**
- `scripts/validate_ncaa_catalog.py` - **942 selectable teams; valid**
- `node --test tests/js/*.test.mjs` - **16 browser tests passed**
- `git diff --check` - **passed**
- Stub scan across all plan-created and plan-modified files - **no blocking stubs found**

## Security and Threat Surface

- The new request gate, callback/logout paths, CSRF handling, and hosted configuration boundary are all covered by the plan threat register.
- Browser errors and operational logs use fixed categories; provider exceptions, callback codes, session cookies, CSRF secrets, and credential values are not logged or rendered.
- Tracked configuration contains setting names only, and hosted runtimes never silently select the fake identity adapter.

## Self-Check: PASSED

- Created files exist: `templates/product_base.html` and `static/product.css`.
- Task commits exist: `adccb17` and `a475a17`.
- Planned focused, readiness, catalog, browser, compilation, and diff verification completed successfully.
- User-owned untracked planning copies and `brag-output/` remain untouched.

---
*Phase: 02-identity-organizations-and-report-library*
*Completed: 2026-11-11*
