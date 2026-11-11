# Phase 2: Identity, Organizations, and Report Library - Research

**Researched:** 2026-11-11  
**Domain:** Managed identity, B2B organization authorization, secure Flask sessions, tenant-isolated report workflows  
**Confidence:** HIGH

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

### Sign-in, Account Linking, and Sessions
- **D-01:** Use a managed identity service rather than implementing passwordless or enterprise federation cryptography in Roster Scout. Research may select the provider, but it must support passwordless email, Google and Microsoft federation, and an enterprise OIDC/SAML connection path without changing Roster Scout's authorization model.
- **D-02:** Passwordless email is the universal sign-in path. Google and Microsoft are convenience options. Enterprise SSO is configured per customer when needed; it never grants organization access by itself.
- **D-03:** Persist canonical Roster Scout accounts separately from external identities. Store provider issuer/subject mappings and verified normalized email; do not use email domains as authorization. Linking a new provider identity to an existing account requires a verified identity and a safe explicit or provider-supported account-linking flow.
- **D-04:** Use secure server-side sessions carried by `Secure`, `HttpOnly`, `SameSite=Lax` cookies. Ordinary refreshes retain the session, logout revokes it, session identifiers rotate after authentication, and sensitive state-changing requests require CSRF protection.
- **D-05:** Apply bounded one-time passwordless expiry, replay prevention, and request throttling. Authentication, callback, invitation, and logout failures return fixed sanitized messages and never log tokens, codes, assertions, or secrets.

### Organization Bootstrap and Membership
- **D-06:** Organization membership is invitation-only. A verified `.edu`, athletics, vendor, or personal email authenticates an account but never grants membership automatically.
- **D-07:** Bootstrap the first organization administrator through an operator-only provisioning command or equivalent restricted path. Phase 2 does not add public self-service organization creation.
- **D-08:** One account may belong to multiple organizations. The user selects an active organization from memberships; the server revalidates active membership on every protected request instead of trusting an organization ID from the browser.
- **D-09:** Roles are `admin` and `member`. Admins can invite, list, revoke, and remove. The system prevents removal or demotion of the last active admin. Membership removal immediately blocks new organization-scoped access and invalidates the removed membership as active session context.
- **D-10:** Invitation acceptance requires the signed-in account's verified normalized email to match the invitation. Invitations are single-use, expire, can be revoked, and grant membership only to the inviting organization.

### Report Library, Status, and Refresh
- **D-11:** The authenticated landing page is the organization's report library. Show opponent, division/season, latest snapshot time and completeness, active job status, snapshot count, and primary actions. Use a compact table on desktop and equivalent cards on narrow screens.
- **D-12:** Creating a report uses the existing exact NCAA selector and accepts one durable job before returning the report page. The UI polls a bounded status endpoint; WebSockets are unnecessary for this phase.
- **D-13:** A report page keeps the newest published snapshot visible while a refresh runs. It shows queued/running/partial/ready/failed state, per-section completeness, source freshness, predecessor lineage, and all dated snapshots.
- **D-14:** Refresh is always explicit and shows the latest snapshot timestamp before confirmation. An accepted refresh performs live collection, creates a linked successor, and never reuses an older build's source cache. Repeated clicks attach to the active job instead of creating another build.
- **D-15:** A failed build remains an auditable job and never replaces the latest published snapshot. Retry creates or reopens the safe durable work prescribed by the existing generation/outbox model without mutating historical snapshots or hiding the failed attempt.
- **D-16:** Partial snapshots remain reviewable with a prominent completeness warning and exact unavailable sections. Snapshot downloads resolve only the artifact bound to that authorized snapshot; an unavailable artifact is disclosed rather than rebuilt from current source data.

### Tenant Isolation, Audit, and Usage Measurement
- **D-17:** Every protected repository/service call receives organization context derived from the authenticated active membership. Browser-supplied organization IDs, account IDs, roles, and ownership claims are never authorization inputs.
- **D-18:** Cross-organization identifier substitution returns a non-enumerating not-found response across HTML, JSON, job, refresh, retry, snapshot, and download paths. Denials are tested with two organizations and recorded as sanitized security events.
- **D-19:** Add an append-only audit event boundary for sign-in outcome, membership/invitation changes, report creation, refresh/retry, snapshot access, and download actions. Record actor, organization, target type/ID, action, outcome, and time; never record tokens, raw assertions, invitation secrets, report data, or exception text.
- **D-20:** Record exactly one `authenticated_use` event after an authorized durable report job is accepted, keyed by the ReportJob UUID with canonical account and organization UUIDs. Page views, login, validation failures, and rejected requests do not count as users.
- **D-21:** Bind usage actors to the Phase 2 account table and preserve the existing aggregate-only operator report: successful downloads and distinct authenticated accounts over a bounded time window, optionally scoped to one organization.

### the agent's Discretion
- Select the managed identity vendor after current official-document research and keep the identity boundary replaceable behind a small adapter.
- Choose exact session idle/absolute lifetimes, passwordless/invitation expiry values, polling cadence, pagination sizes, and responsive visual details using secure, coach-friendly defaults.
- Choose module boundaries and migration numbering while preserving the Phase 1 connection-free import, caller-owned transaction, composite tenant-ownership, and fail-closed patterns.

### Deferred Ideas (OUT OF SCOPE)
- Last-10 and conference-only statistical scopes remain assigned to Phase 3 despite an automated todo keyword match.
- Detailed fielding, derived metrics, splits, career history, and game logs remain Phase 3.
- The ten-sheet workbook remains Phase 4; Power BI embedding and RLS remain Phase 5.

### Reviewed Todos (not folded)
- `Add last-10 and conference-only stats` — Explicitly declares `resolves_phase: 3`; it is out of scope for Phase 2.
</user_constraints>

## Summary

Use WorkOS AuthKit Hosted UI as the managed **authentication** boundary, while keeping Roster Scout's PostgreSQL `Account`, `OrganizationMembership`, session, invitation, and authorization records canonical. WorkOS directly documents passwordless six-digit Magic Auth, Google and Microsoft social login, SAML/OIDC SSO, a first-party Python/Flask integration, server-side refresh-token storage, invitation-only registration, staging/Test IdP support, and a local emulator. [CITED: https://workos.com/docs/authkit/overview] [CITED: https://workos.com/docs/authkit/vanilla/python] [CITED: https://workos.com/docs/authkit/magic-auth] [CITED: https://workos.com/docs/authkit/social-login] [CITED: https://workos.com/docs/authkit/sso]

The important separation is authentication versus authorization. WorkOS organization records should mirror local organizations only to route invitations and enterprise SSO; WorkOS `org_id`, provider roles, domain policies, and provider memberships must never authorize Roster Scout data. WorkOS enables domain and SSO just-in-time membership by default, and corporate-domain invitations may be accepted by a different address on the same domain, both of which conflict with the locked invitation-only/exact-email rules unless explicitly disabled or overridden by local checks. [CITED: https://workos.com/docs/authkit/jit-provisioning] [CITED: https://workos.com/docs/authkit/invitations] [CITED: https://workos.com/docs/authkit/organization-policies]

Preserve the Phase 1 job, outbox, snapshot, publication, and artifact contracts. Add a thin authorized application service that derives the organization from a revalidated local membership, creates or reuses the durable job and its `authenticated_use` event in one caller-owned transaction, and dispatches only after commit. The current job and usage primitives already support idempotent acceptance and UUID-keyed usage, but calling them in two separate transactions would create an unacceptable job-without-usage failure window. [VERIFIED: codebase grep]

**Primary recommendation:** adopt WorkOS AuthKit Hosted UI behind a replaceable `IdentityProvider` adapter; store only an opaque local session verifier in the browser, keep the sealed WorkOS refresh/session data server-side, and make local active membership the sole source of organization access. [CITED: https://workos.com/docs/authkit/sessions] [CITED: https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html]

## Project Constraints (from AGENTS.md)

- Treat this repository as independent of the surrounding Axis Baseball workspace. [VERIFIED: AGENTS.md]
- Do not read or commit `credentials.json`, `oauth_credentials.json`, or `token.json`; the app does not use them. [VERIFIED: AGENTS.md]
- For HTTP/auth/report-library work, inspect `app.py`, `templates/index.html`, and the relevant `roster_scout` service/model modules; do not load unrelated scraper internals. [VERIFIED: AGENTS.md]
- Preserve the documented ownership boundaries: `app.py` owns routes and workbook delivery, `roster_scraper.py` owns selection/scraping/normalization, and deployment files own hosted runtime configuration. [VERIFIED: AGENTS.md]
- Before implementation handoff, run the checks in root `CONTEXT.md`, including Python compilation, full unittest discovery, template existence, catalog validation where applicable, and `git diff --check`. [VERIFIED: AGENTS.md] [VERIFIED: CONTEXT.md]

<phase_requirements>
## Phase Requirements

Requirement descriptions below are copied verbatim from `.planning/REQUIREMENTS.md`. [VERIFIED: REQUIREMENTS.md]

| ID | Description | Research Support |
|---|---|---|
| AUTH-01 | A visitor must authenticate before accessing team search, report creation, report history, dashboards, exports, or refresh actions. | Public-route allowlist plus global local-session/membership loader; protect legacy `/scrape` and download acknowledgement too. [VERIFIED: REQUIREMENTS.md] |
| AUTH-02 | A user can sign in without a password through an emailed single-use code or magic link with bounded expiry and request throttling. | WorkOS Magic Auth provides a one-time six-digit code with 10-minute expiry and documented per-email/IP limits. [CITED: https://workos.com/docs/authkit/magic-auth] [CITED: https://workos.com/docs/reference/rate-limits] |
| AUTH-03 | A user can sign in through Microsoft or Google federation while retaining the same Roster Scout account and organization memberships. | WorkOS social login plus verified-email identity linking; local external-identity mapping preserves one canonical account. [CITED: https://workos.com/docs/authkit/social-login] [CITED: https://workos.com/docs/authkit/identity-linking] |
| AUTH-04 | An organization can use an approved OIDC or SAML SSO connection without changing report ownership or authorization rules. | WorkOS supports both protocols; local membership remains authoritative and JIT is disabled. [CITED: https://workos.com/docs/authkit/sso] [CITED: https://workos.com/docs/authkit/jit-provisioning] |
| AUTH-05 | A user session persists across ordinary browser refreshes and can be explicitly terminated through logout. | Server-side local session plus backend-stored WorkOS refresh data; local revoke first, then provider logout URL. [CITED: https://workos.com/docs/authkit/sessions] |
| ORG-01 | Every report, snapshot, artifact, job, and invitation belongs to exactly one organization. | Existing report/snapshot/job/artifact composite ownership plus new invitation composite FK. [VERIFIED: roster_scout/models.py] |
| ORG-02 | An organization administrator can invite a user at any valid email address; an `.edu` domain is not required. | WorkOS invitations support specific addresses; local normalized-email invitation is not domain-restricted. [CITED: https://workos.com/docs/authkit/invitations] |
| ORG-03 | Accepting an invitation grants membership only in the inviting organization. | Local invitation is keyed to one organization and consumed atomically into one membership. [ASSUMED] |
| ORG-04 | Organization roles distinguish administrators who manage membership from members who create and review reports. | Local membership constraint and service-level authorization matrix. [ASSUMED] |
| ORG-05 | An organization administrator can view pending invitations, revoke invitations, and remove members. | Local admin services, last-admin serialization, WorkOS invitation mirror/revoke. [ASSUMED] |
| ORG-06 | All active organization members can review the organization's report library and the snapshots they are authorized to access. | Organization-scoped repositories plus active membership revalidation on every request. [CITED: https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html] |
| ORG-07 | Email-domain matching may suggest an organization but can never grant membership automatically. | Disable WorkOS automatic domain and SSO JIT membership; ignore provider membership for local authorization. [CITED: https://workos.com/docs/authkit/jit-provisioning] |
| SNAP-05 | A user can explicitly refresh a report, producing a new linked snapshot without modifying prior snapshots. | Reuse Phase 1 `ReportJob` and `_new_or_existing_snapshot` predecessor behavior through an authorized refresh service. [VERIFIED: roster_scout/jobs.py] |
| SNAP-06 | A user can review all dated snapshots for a report and identify the newest complete or partial version. | Extend existing org-scoped published-snapshot repositories with report scoping and descending pagination. [VERIFIED: roster_scout/repositories.py] |
| SNAP-07 | Repeating an accepted report-build request with the same idempotency key does not create duplicate jobs or snapshots. | Existing `(organization_id, idempotency_key)` uniqueness and result-snapshot uniqueness remain the authority. [VERIFIED: roster_scout/models.py] |
| SNAP-08 | A failed report build leaves no snapshot falsely marked ready and can be retried safely. | Preserve Phase 1 terminal status/publication rules and generation/outbox retry; new UI must not infer readiness. [VERIFIED: roster_scout/models.py] [VERIFIED: roster_scout/jobs.py] |
| SECU-01 | Cross-organization access is denied for report HTML, JSON APIs, refresh actions, workbook downloads, job status, and embed-token endpoints. | Central scoped loaders return the same 404 for missing and foreign IDs; two-org matrix covers every Phase 2 HTML/JSON/action/download path, while the explicitly deferred embed-token surface remains absent/404 until Phase 5. [CITED: https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html] [VERIFIED: 02-CONTEXT.md] |
| SECU-02 | Authorization checks derive organization context from the authenticated membership, never from an untrusted request field alone. | Session stores only selected local org ID; middleware reselects active membership before each protected request. [CITED: https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html] |
| SECU-03 | Passwordless, invitation, session, Power BI, and service-principal secrets or tokens never appear in application logs. | Fixed error categories and safe structured fields; no callback query, code, token, assertion, raw email, cookie, or exception logging. [CITED: https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html] |
| SECU-04 | Security-relevant membership, report, refresh, export, and embed-token events are auditable by actor, organization, target, and time without recording secret material. | Append-only local audit table with allowlisted action/outcome/UUID fields. [ASSUMED] |
| SECU-05 | External failures from NCAA, Browserless, identity, object storage, database, and Power BI produce distinct sanitized operational states. | Add identity/database/queue/source/artifact categories now; the Power BI category remains dormant until Phase 5 because embedding is explicitly deferred. [VERIFIED: 02-CONTEXT.md] |
</phase_requirements>

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|---|---|---|---|
| Hosted passwordless/social/enterprise sign-in | External identity service | API / Backend | WorkOS executes credential and federation protocols; Flask initiates and validates the authorization-code callback. [CITED: https://workos.com/docs/authkit/modeling-your-app] |
| Canonical account linking | API / Backend | Database / Storage | Backend maps verified provider identity to a local account; unique constraints prevent identity/account duplication. [CITED: https://workos.com/docs/authkit/identity-linking] |
| Local sessions and CSRF | API / Backend | Database / Storage | The browser holds only an opaque cookie; server-side rows hold session state, provider sealed data, CSRF state, expiry, and revocation. [CITED: https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html] |
| Organization authorization | API / Backend | Database / Storage | Every request revalidates a local active membership; the client and WorkOS claims are not authority. [CITED: https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html] |
| Invitation administration | API / Backend | External identity service | Local invitation status is canonical; WorkOS supplies hosted signup and email delivery. [CITED: https://workos.com/docs/authkit/invitations] |
| Report library rendering | Frontend Server (SSR) | Browser / Client | Flask renders organization-scoped rows; small JS progressively polls job status and swaps status fragments. [ASSUMED] |
| Durable create/refresh/retry | API / Backend | Database / Storage | Authorized services reuse PostgreSQL job/outbox state; the browser never dispatches work directly. [VERIFIED: roster_scout/jobs.py] |
| Snapshot/artifact history | Database / Storage | API / Backend | Immutable published snapshots and bound artifacts are selected by organization and exposed through scoped routes. [VERIFIED: roster_scout/models.py] |
| Usage and security audit | Database / Storage | API / Backend | The acceptance transaction writes one idempotent usage event and append-only security events. [VERIFIED: roster_scout/usage.py] |

## Managed Identity Provider Decision

| Provider | Required capability evidence | Flask/Python and testability | Decision |
|---|---|---|---|
| **WorkOS AuthKit** | Official docs cover Magic Auth, Google/Microsoft social login, SAML/OIDC SSO, organizations, invitations, identity linking, and per-environment configuration. [CITED: https://workos.com/docs/authkit/overview] [CITED: https://workos.com/docs/authkit/social-login] [CITED: https://workos.com/docs/authkit/sso] | WorkOS publishes a Python/Flask quickstart, a first-party Python SDK, a staging Test IdP, and a local emulator that supports authorization, code exchange, sessions, organization selection, signed webhooks, and failure injection. [CITED: https://workos.com/docs/authkit/vanilla/python] [CITED: https://workos.com/docs/authkit/testing] | **Selected.** It has the best directly documented fit for the locked Flask, enterprise SSO, invite-only, and deterministic-test requirements. [VERIFIED: official-document comparison] |
| Auth0 | Official material documents email OTP/passwordless through Universal Login and a Flask quickstart using Authlib; Auth0 also has an organization/multitenancy model. [CITED: https://developer.auth0.com/resources/labs/authentication/passwordless-with-email] [CITED: https://developer.auth0.com/resources/guides/web-app/flask/basic-authentication] | The Flask guide uses the separate `Authlib` OIDC client rather than a provider-specific Flask SDK. [CITED: https://developer.auth0.com/resources/guides/web-app/flask/basic-authentication] | Viable fallback, but not selected because the researched WorkOS path provides a more direct first-party B2B organization/SSO model and a documented local emulator. [VERIFIED: official-document comparison] |
| Clerk | Official docs cover SAML/OIDC enterprise SSO, while the current pricing page lists email codes, social connections, automatic account linking, and SSO connections. [CITED: https://clerk.com/docs/guides/configure/auth-strategies/enterprise-connections/overview] [CITED: https://clerk.com/pricing] | This research did not establish an official Flask-specific quickstart or a provider emulator equivalent to the WorkOS testing path; that is a research limitation, not a claim that the capabilities do not exist. [VERIFIED: official-document review] | Not selected for this Flask phase because WorkOS has a better verified integration/testability fit. [VERIFIED: official-document comparison] |

### Capability Versus Commercial Assumptions

- Product capability is documented independently of price: WorkOS AuthKit supports passwordless, social, and SSO methods, and staging supports the Test IdP. [CITED: https://workos.com/docs/authkit/overview] [CITED: https://workos.com/docs/authkit/environments]
- The official pricing page currently lists AuthKit as free through one million active users, enterprise SSO at `$125` per connection for the first 15 connections, and a custom domain at `$99/month`; those figures are procurement inputs, **not** implementation guarantees, and must be rechecked before production purchase. [CITED: https://workos.com/pricing]
- WorkOS production requires billing information; staging is separate and free, and users, organizations, keys, connections, webhooks, and branding do not carry from staging to production. [CITED: https://workos.com/docs/authkit/environments]
- A production plan must therefore include an operator checkpoint for the WorkOS production environment, billing, redirect/sign-out URIs, API key, client ID, social-provider credentials, branding, and each paid enterprise connection; code completion alone cannot prove production readiness. [CITED: https://workos.com/docs/authkit/environments]

### Required WorkOS Environment Configuration

| Setting | Required Phase 2 value | Reason |
|---|---|---|
| Hosted methods | Enable Magic Auth, Google, and Microsoft; disable Email + Password so the universal path remains passwordless. | Magic Auth is the documented six-digit code path, social providers require explicit configuration, and Email + Password is otherwise enabled by default. [CITED: https://workos.com/docs/authkit/magic-auth] [CITED: https://workos.com/docs/authkit/social-login] [CITED: https://workos.com/docs/authkit/modeling-your-app] |
| Registration | Disable open signup; create organization invitations only. | WorkOS documents that a valid invitation temporarily opens registration when signup is disabled, matching the invite-only product decision. [CITED: https://workos.com/docs/authkit/invitations] |
| Automatic membership | Disable automatic membership by verified domain and disable SSO JIT for every mirrored organization. | Both are enabled by default and would violate local invitation-only authorization. [CITED: https://workos.com/docs/authkit/jit-provisioning] |
| Organization mapping | Set each WorkOS organization `external_id` to the local organization UUID. | WorkOS supports unique external IDs and lookup by that ID, making operator retries/reconciliation idempotent. [CITED: https://workos.com/docs/authkit/metadata] |
| Redirects | Register exact callback, default sign-out, and initiate-login URIs per environment; production web redirects use HTTPS. | WorkOS scopes configuration per environment and applies stricter production redirect rules. [CITED: https://workos.com/docs/authkit/environments] [CITED: https://workos.com/docs/reference/authkit/authentication/get-authorization-url] |
| Sessions | Align WorkOS maximum/inactivity settings with or beyond the local 7-day absolute/12-hour idle limits; keep access-token duration short enough for provider changes to surface. | WorkOS exposes maximum session, access-token, and inactivity configuration; local revocation remains authoritative. [CITED: https://workos.com/docs/authkit/sessions] [ASSUMED] |
| Enterprise SSO | Create per-customer WorkOS connections through the dashboard/Admin Portal; persist only connection IDs/status locally. | WorkOS supports SAML/OIDC and self-service setup; Phase 2 does not need to store assertions or IdP secrets. [CITED: https://workos.com/docs/authkit/sso] |
| Environments | Use emulator/local plus WorkOS staging for development; recreate settings and objects in production. | WorkOS environments are isolated and do not promote users, organizations, connections, keys, or webhooks. [CITED: https://workos.com/docs/authkit/environments] |

## Standard Stack

### Core

| Library / Service | Version | Purpose | Why Standard Here |
|---|---:|---|---|
| WorkOS AuthKit Hosted UI | Managed service | Passwordless email code, Google/Microsoft login, SAML/OIDC SSO, hosted auth UX, invitation delivery. | Keeps credential/federation protocols outside Roster Scout and is directly documented for Python/Flask. [CITED: https://workos.com/docs/authkit/vanilla/python] |
| `workos` | `10.8.0` | Official Python API client and session helpers. | Current PyPI release was published 2026-11-05; WorkOS docs explicitly install this package. [VERIFIED: PyPI registry] [CITED: https://workos.com/docs/authkit/vanilla/python] |
| `Flask-WTF` | `1.3.0` | Global CSRF enforcement for forms and JSON state changes. | WorkOS's Flask quickstart explicitly calls for it on state-changing routes; its official docs provide global CSRF protection. [VERIFIED: PyPI registry] [CITED: https://workos.com/docs/authkit/vanilla/python] [CITED: https://flask-wtf.readthedocs.io/en/latest/]
| Flask | `3.1.3` existing | Server-rendered pages, routes, cookies, callbacks, and JSON status. | Already pinned and deployed; no frontend framework is needed for bounded polling. [VERIFIED: requirements.txt] |
| SQLAlchemy / Alembic | `2.1.6` / `1.20.1` existing | Canonical account/org/session/invite/audit persistence and migrations. | Existing Phase 1 models and migrations use these packages with caller-owned PostgreSQL transactions. [VERIFIED: requirements.txt] [VERIFIED: codebase grep] |
| PostgreSQL | `16` container target | Server-side sessions, authorization data, report/job/snapshot state, append-only audit, usage events. | Existing schema relies on PostgreSQL constraints, partial indexes, JSONB, and locking; SQLite is explicitly forbidden for integration tests. [VERIFIED: compose.yaml] [VERIFIED: tests/db_support.py] |

### Supporting

| Tool | Version | Purpose | When to Use |
|---|---:|---|---|
| WorkOS Emulate | Latest checked official release at implementation time | Deterministic local AuthKit code exchange/session/failure tests. | Use for provider-bound integration and browser tests; retain a small staging suite because the emulator is not a complete copy. [CITED: https://workos.com/docs/authkit/testing] |
| WorkOS staging/Test IdP | Managed | Validate real hosted boundary and SAML/OIDC routing without production connections. | Use for smoke tests and SSO acceptance, never as the main unit-test dependency. [CITED: https://workos.com/docs/authkit/environments] [CITED: https://workos.com/docs/authkit/sso] |
| Python `secrets`, `hashlib`, `hmac` | Standard library | Generate opaque session verifiers, store one-way verifier hashes, constant-time compare. | Use only for local random session/invitation verifiers; do not implement identity protocol cryptography. [CITED: https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html] |
| Existing Celery/Redis outbox path | `5.6.3` / Redis 7 target | Opaque job dispatch after a PostgreSQL acceptance commit. | Reuse unchanged for create, refresh, and retry; Redis is not an authorization or session source of truth. [VERIFIED: requirements.txt] [VERIFIED: compose.yaml] [VERIFIED: roster_scout/jobs.py] |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|---|---|---|
| WorkOS Hosted UI | Custom AuthKit UI/API | A custom UI increases authentication UX and error-state surface without improving the locked customer workflow; Hosted UI is the documented default. [CITED: https://workos.com/docs/authkit/modeling-your-app] |
| Local canonical memberships | WorkOS membership/role claims as authorization | Provider memberships support B2B patterns, but default JIT/domain behaviors conflict with invitation-only exact local authorization; keep them routing/configuration aids only. [CITED: https://workos.com/docs/authkit/jit-provisioning] [CITED: https://workos.com/docs/authkit/organization-policies] |
| PostgreSQL local sessions | WorkOS sealed session directly in a browser cookie | The WorkOS example is valid generally, but D-04 specifically locks server-side sessions; storing sealed provider data in the local session row leaves only an opaque verifier in the browser. [CITED: https://workos.com/docs/authkit/vanilla/python] [CITED: https://workos.com/docs/authkit/sessions] |
| Server-rendered Flask + small JS | SPA framework | Phase 2 needs forms, tables/cards, and polling, all of which fit the existing Flask app without another client runtime. [VERIFIED: templates/index.html] [ASSUMED] |

**Installation:**

```bash
python3 -m pip install 'workos==10.8.0' 'Flask-WTF==1.3.0'
```

The exact versions were verified against PyPI on 2026-11-11; the planner should still run `pip index versions` before modifying `requirements.txt`. [VERIFIED: PyPI registry]

## Package Legitimacy Audit

| Package | Registry | Age / latest publish | Downloads | Source Repo | slopcheck | Disposition |
|---|---|---|---|---|---|---|
| `workos` | PyPI | Repository created 2019-12-09; `10.8.0` published 2026-11-05. [VERIFIED: GitHub API] [VERIFIED: PyPI registry] | PyPI JSON does not expose a usable download count. [VERIFIED: PyPI registry] | `github.com/workos/workos-python` [VERIFIED: PyPI registry] | OK | Approved; package name is also prescribed by WorkOS official docs. [CITED: https://workos.com/docs/authkit/vanilla/python] |
| `Flask-WTF` | PyPI | Repository created 2012-02-29; `1.3.0` published 2026-04-23. [VERIFIED: GitHub API] [VERIFIED: PyPI registry] | PyPI JSON does not expose a usable download count. [VERIFIED: PyPI registry] | `github.com/pallets-eco/flask-wtf` [VERIFIED: PyPI registry] | OK | Approved; WorkOS official Flask docs prescribe it for CSRF. [CITED: https://workos.com/docs/authkit/vanilla/python] |

**Packages removed due to slopcheck [SLOP] verdict:** none. [VERIFIED: slopcheck 0.6.1 local run]  
**Packages flagged as suspicious [SUS]:** none. [VERIFIED: slopcheck 0.6.1 local run]

## Architecture Patterns

### System Architecture Diagram

```mermaid
flowchart LR
    B[Browser] -->|GET /login| F[Flask web]
    F -->|authorization URL + one-time state| W[WorkOS Hosted AuthKit]
    W -->|passwordless / Google / Microsoft / SAML / OIDC| IDP[Email or customer IdP]
    W -->|authorization code + state| F
    F --> IA[Identity adapter]
    IA -->|exchange code / refresh / logout| W
    IA --> DB[(PostgreSQL)]
    DB -->|Account + ExternalIdentity + opaque local Session| F
    B -->|opaque Secure HttpOnly cookie| F
    F -->|revalidate active membership every request| AZ[Authorization service]
    AZ --> DB
    AZ -->|authorized org context| RS[Report application service]
    RS -->|one transaction: report/job + usage + audit + outbox| DB
    DB --> PUB[Existing outbox publisher]
    PUB --> R[(Redis opaque job UUID)]
    R --> WK[Existing worker]
    WK -->|live NCAA collection + immutable successor| DB
    B -->|bounded polling| ST[Org-scoped status endpoint]
    ST --> DB
    B -->|authorized snapshot artifact| DL[Download route]
    DL --> DB
```

The diagram preserves PostgreSQL as durable job/session/authorization authority and Redis as opaque dispatch only. [VERIFIED: roster_scout/jobs.py] [VERIFIED: 02-CONTEXT.md]

### Recommended Project Structure

```text
roster_scout/
├── identity.py           # provider-neutral interface and WorkOS adapter
├── accounts.py           # canonical account/external-identity linking
├── sessions.py           # opaque local sessions, rotation, refresh, revoke
├── authorization.py      # account + active-membership request context
├── organizations.py      # bootstrap, invite, accept, remove, last-admin guard
├── reports.py            # authorized report library/create/refresh/retry services
├── audit.py              # append-only sanitized security events
├── models.py             # new canonical rows and composite constraints
└── repositories.py       # organization-scoped loaders/list queries
templates/
├── sign_in.html
├── organization_select.html
├── report_library.html
├── report_detail.html
└── organization_members.html
static/
└── report_status.js      # bounded polling and progressive status updates
tests/
├── test_identity.py
├── test_organizations.py
├── test_report_library.py
├── test_phase2_tenant_isolation.py
├── test_phase2_release.py
└── js/report_status.test.mjs
```

This structure keeps provider calls replaceable and database modules connection-free; routes compose services but do not contain authorization SQL. [VERIFIED: 02-CONTEXT.md] [ASSUMED]

### Pattern 1: Provider Authentication, Local Authorization

**What:** WorkOS proves identity; local tables decide account, organization, and role. Never use `org_id`, provider role claims, email domain, request fields, or WorkOS membership alone to authorize a report. [CITED: https://workos.com/docs/authkit/sessions] [CITED: https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html]

**When to use:** Every callback, organization switch, protected HTML/JSON route, report action, status poll, and artifact download. [VERIFIED: 02-CONTEXT.md]

**Required callback order:** [ASSUMED]

1. Consume a one-time server-side login transaction and constant-time verify returned `state`; reject missing, expired, or reused state with one fixed error. [CITED: https://workos.com/docs/reference/authkit/authentication/get-authorization-url]
2. Exchange the one-time authorization code through the adapter; never log query parameters or provider exceptions. WorkOS documents a 10-minute authorization-code validity window. [CITED: https://workos.com/docs/authkit/vanilla/python]
3. Require the returned verified, lowercased/trimmed email; map `(issuer, subject/provider_user_id)` first, then link a new identity to an existing account only through WorkOS's verified identity-linking result. [CITED: https://workos.com/docs/authkit/identity-linking] [CITED: https://workos.com/docs/authkit/users-organizations]
4. Rotate into a new opaque local server-side session, storing the sealed WorkOS session/backend refresh material only in PostgreSQL. [CITED: https://workos.com/docs/authkit/sessions] [CITED: https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html]
5. If the flow carries an invitation, atomically require exact normalized-email equality and active unused local invitation state before creating the local membership. WorkOS's same-domain corporate invitation rule is deliberately insufficient. [CITED: https://workos.com/docs/authkit/invitations]
6. Select an active organization only from current local memberships; no memberships yields an access-pending/no-organization page with no team/report capability. [ASSUMED]

### Pattern 2: Opaque Server-Side Session with Provider Session Nested Inside

**What:** The cookie contains a high-entropy random verifier; PostgreSQL stores its one-way verifier hash, account ID, active local organization ID, sealed WorkOS session, CSRF secret, idle/absolute expiry, rotation family, last-seen time, and revocation. OWASP recommends meaningless client IDs with server-side state and documents one-way verifier storage. [CITED: https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html]

**Flask integration:** implement a small `SessionInterface` over the local session row so Flask-WTF's CSRF token uses the same server-side session rather than Flask's default signed client cookie. Flask officially supports replacing the session implementation through `open_session()`/`save_session()` and warns that concurrent requests for one session need synchronization; use compare-and-swap/row locking for refresh-token rotation rather than last-write-wins. [CITED: https://flask.palletsprojects.com/en/stable/api/] [CITED: https://flask-wtf.readthedocs.io/en/latest/csrf/] [ASSUMED]

**Prescriptive defaults:** use `__Host-rs_session`, `Secure`, `HttpOnly`, `SameSite=Lax`, `Path=/`, no `Domain`, 12-hour idle timeout, 7-day absolute timeout, and rotate after callback, organization switch, and role elevation. The cookie flags are locked; timeout values are coach-friendly recommendations that need implementation validation. [ASSUMED]

**Refresh behavior:** WorkOS Python is a bring-your-own refresh handler. Keep the local session on retryable `429`, `5xx`, and transport errors; update rotated sealed refresh material atomically after success; revoke locally and redirect to sign-in only for terminal invalid/revoked provider sessions. [CITED: https://workos.com/docs/authkit/session-resilience]

**Logout behavior:** a CSRF-protected POST revokes the local session first, clears the cookie, then redirects through WorkOS's logout URL to terminate the provider session; provider outage must not resurrect the already-revoked local session. [CITED: https://workos.com/docs/authkit/sessions] [CITED: https://workos.com/docs/authkit/vanilla/python]

### Pattern 3: Local Organization and WorkOS Organization Mirror

**What:** Each local organization may store one `workos_organization_id`; create the WorkOS mirror with the local UUID as `external_id`, which WorkOS requires to be unique and permits querying directly. [CITED: https://workos.com/docs/authkit/metadata]

**When to use:** provider invitation delivery, SSO connection routing, and optional Admin Portal configuration only. Local membership and role rows remain authoritative. [ASSUMED]

**Configuration invariant:** disable WorkOS automatic domain membership and SSO JIT for every mirrored organization. WorkOS documents both as enabled by default, and organization policy can otherwise add a user after SSO. [CITED: https://workos.com/docs/authkit/jit-provisioning] [CITED: https://workos.com/docs/authkit/organization-policies]

**Bootstrap:** an operator-only Flask CLI command creates/reselects the local organization, resolves the WorkOS mirror by `external_id`, and issues the first admin invitation. There is no public organization-create route. [CITED: https://workos.com/docs/authkit/metadata] [ASSUMED]

### Pattern 4: Exact-Email Local Invitation with Provider Delivery

**What:** Create a local `OrganizationInvitation` first, then mirror it to a WorkOS organization invitation for email delivery. Store the WorkOS invitation ID and status, not its raw token; the provider token remains an authentication secret. [CITED: https://workos.com/docs/authkit/invitations] [ASSUMED]

**Acceptance:** the invite route resolves the provider token through WorkOS, stores only the local invitation UUID in a one-time server-side login transaction, and sends the provider token to AuthKit. On callback, lock the local invitation, require `pending`, `expires_at > now`, no prior acceptance, exact normalized-email equality, and the same WorkOS/local organization mapping; then create/reselect membership and mark accepted in one transaction. [CITED: https://workos.com/docs/reference/authkit/invitation] [ASSUMED]

**Why local enforcement is mandatory:** WorkOS permits a corporate organization invitation to be accepted by another address on the same domain, while D-10 requires the exact verified normalized email. [CITED: https://workos.com/docs/authkit/invitations]

**Expiry:** choose a local seven-day invitation expiry, never later than the provider `expires_at`; revocation updates the local row first and then best-effort revokes the provider invitation. A failed provider revoke cannot grant local access because the local row is canonical. [ASSUMED]

### Pattern 5: Membership-Derived Request Context and Non-Enumerating Loads

**What:** Resolve local session → account → active membership → organization once per request, but make every repository query still require `organization_id`. OWASP says deny by default and validate permission on every request. [CITED: https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html]

**Response contract:** return identical `404` HTML/JSON bodies for absent and foreign report/job/snapshot/artifact IDs. Record a sanitized denial audit event with actor UUID, active org UUID, target type, opaque target UUID when syntactically valid, action, `denied`, and fixed reason; never reveal which part mismatched. [ASSUMED]

**Role changes:** lock the organization row before counting active admins and changing/removing an admin. This serializes concurrent last-admin mutations. Membership removal takes effect on the next request because membership is reselected each time; if the removed organization is the session's active context, clear that context. [ASSUMED]

### Pattern 6: Atomic Authorized Job Acceptance and Usage

**What:** add an application service that opens one transaction and performs: organization-scoped report lock/upsert → active-job convergence → `create_or_get_job()` → `record_authenticated_report_request_accepted(event_key=str(job.id))` → audit event → commit. Publish the existing dispatch outbox only after commit. [VERIFIED: roster_scout/jobs.py] [VERIFIED: roster_scout/usage.py] [ASSUMED]

**Why:** `accept_report_job()` currently owns and commits its transaction before returning, while usage recording is a separate caller-owned primitive. Calling them sequentially can commit a job and then fail before its required usage event. Reusing the lower-level `create_or_get_job()` and usage producer in one transaction eliminates that gap without changing the outbox authority. [VERIFIED: roster_scout/jobs.py] [VERIFIED: roster_scout/usage.py]

**Duplicate-click convergence:** lock the report row, return its queued/running/retrying job when one exists, and do not create another job even if the browser generated a second action UUID. The existing idempotency constraint still handles a repeated request with the same key. [ASSUMED]

**Retry:** preserve the failed job as history and accept a new job linked by `retry_of_job_id` (or an equivalent audit link) under a fresh idempotency key; do not mutate a terminal failed attempt into an apparently new attempt. The new job uses the same generation/outbox worker path. [ASSUMED]

### Pattern 7: Report Library Read Model

**What:** server-render a compact 25-row page of reports with opponent, division/season, newest published snapshot timestamp/status/completeness, active job state, snapshot count, and primary action. Use the same fields in narrow-screen cards; no separate mobile API model. [ASSUMED]

**Report detail:** query latest published snapshot independently from the active job so a refresh never hides the prior ready/partial snapshot. List per-section state and all dated snapshots newest-first. A historical artifact route selects only `(active_org_id, snapshot_id, artifact_id)` and returns unavailable instead of rebuilding. [VERIFIED: roster_scout/models.py] [ASSUMED]

**Polling:** poll an org-scoped JSON status endpoint at 3 seconds for 30 seconds, then 5 seconds to 2 minutes, then 10 seconds up to 15 minutes; stop on terminal state, tab-hidden state, or timeout and provide manual refresh. These are recommended UX defaults, not provider requirements. [ASSUMED]

### Canonical Schema Additions

| Table / Change | Required invariants |
|---|---|
| `accounts` | UUID PK; unique normalized email; verified timestamp; active/disabled status; no provider tokens. [ASSUMED] |
| `external_identities` | FK account; provider/issuer; subject or WorkOS user ID; last verified normalized email; unique `(issuer, subject)` and unique provider user ID where applicable. [CITED: https://workos.com/docs/authkit/identity-linking] [ASSUMED] |
| `account_sessions` | FK account; public lookup ID + verifier hash; sealed WorkOS session/backend refresh state; active org UUID; CSRF secret; idle/absolute/rotated/revoked timestamps; no raw verifier. [CITED: https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html] [ASSUMED] |
| `auth_transactions` | State digest, optional local invitation ID, safe return path, created/expires/consumed timestamps; one-time use. [CITED: https://workos.com/docs/reference/authkit/authentication/get-authorization-url] [ASSUMED] |
| `organization_memberships` | Composite uniqueness org/account; role check `admin|member`; status; joined/removed timestamps; `(id, organization_id)` ownership key. [ASSUMED] |
| `organization_invitations` | Organization FK; exact normalized email; role; provider invitation ID; pending/accepted/revoked/expired status; expiry; inviter/acceptor account FKs; composite ownership and one active invite per org/email. [ASSUMED] |
| `organization_identity_connections` or organization columns | Local org ↔ WorkOS org/connection IDs and status; WorkOS org uses local UUID `external_id`; no SAML assertions or client secrets in DB. [CITED: https://workos.com/docs/authkit/metadata] [ASSUMED] |
| `security_audit_events` | Append-only UUID/time/actor/org/target/action/outcome/fixed-reason fields; no raw email, IP, user agent, report data, token, assertion, or exception. [ASSUMED] |
| `report_jobs` | Add nullable requester account FK, request kind, and optional retry-of composite FK if needed for UI/audit; preserve existing generation/outbox/status constraints. [VERIFIED: roster_scout/models.py] [ASSUMED] |
| `report_snapshots` | Add nullable `created_by_account_id` FK rather than destructively interpreting legacy free-text `created_by`; preserve old rows and immutable published state. [VERIFIED: roster_scout/models.py] [ASSUMED] |
| `product_usage_events` | Add FK from nonnull `actor_account_id` to accounts; keep anonymous legacy rows null and preserve existing event/type/key uniqueness and aggregate query. [VERIFIED: roster_scout/models.py] [VERIFIED: roster_scout/usage.py] |

### Anti-Patterns to Avoid

- **Provider organization as tenant authority:** WorkOS JIT/domain policies can create memberships automatically; local membership is the only authorization input. [CITED: https://workos.com/docs/authkit/jit-provisioning]
- **Email-domain authorization:** domain matching is discovery/convenience only, never a local membership grant. [VERIFIED: 02-CONTEXT.md]
- **Trusting `org_id` from JWT, form, URL, or JSON:** it may choose a provider flow but cannot choose local data scope. [CITED: https://workos.com/docs/authkit/sessions] [VERIFIED: 02-CONTEXT.md]
- **Direct sealed provider cookie as the app session:** it contradicts the locked server-side-session decision and complicates immediate local membership revocation. [VERIFIED: 02-CONTEXT.md]
- **Logging provider exceptions:** the official quickstart prints an exception for demonstration; production must map exceptions to fixed categories without message/trace/query leakage. [CITED: https://workos.com/docs/authkit/vanilla/python] [VERIFIED: 02-CONTEXT.md]
- **Two-transaction job and usage writes:** a committed job can escape required adoption accounting. [VERIFIED: roster_scout/jobs.py] [VERIFIED: roster_scout/usage.py]
- **Latest-snapshot-only UI:** it hides failed/active jobs and makes refresh appear to erase a previously good snapshot. [VERIFIED: 02-CONTEXT.md]
- **Rebuilding historical downloads:** bind the authorized artifact to the selected historical snapshot and disclose absence. [VERIFIED: 02-CONTEXT.md]

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---|---|---|---|
| Passwordless email | Code generator, email sender, replay store | WorkOS Magic Auth Hosted UI | WorkOS supplies one-time code issuance, verification, 10-minute expiry, bot protection, and rate limits. [CITED: https://workos.com/docs/authkit/magic-auth] [CITED: https://workos.com/docs/reference/rate-limits] |
| Google/Microsoft federation | OAuth provider integrations | WorkOS social providers | Provider routing and identity linking are managed. [CITED: https://workos.com/docs/authkit/social-login] |
| SAML/OIDC | Assertion parsing, certificate/metadata rotation | WorkOS SSO/Admin Portal | WorkOS abstracts SAML/OIDC IdPs and provides a Test IdP. [CITED: https://workos.com/docs/authkit/sso] |
| Provider identity linking | Email-only merging without verification | WorkOS verified identity linking + local `(issuer, subject)` mapping | Unverified email linking creates account-takeover risk. [CITED: https://workos.com/docs/authkit/identity-linking] |
| CSRF | Ad hoc referer checks or custom token parsing | Flask-WTF global CSRF, plus SameSite cookie | Synchronizer-token validation is established and WorkOS prescribes Flask-WTF for logout. [CITED: https://workos.com/docs/authkit/vanilla/python] [CITED: https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html] |
| Async refresh pipeline | A second queue/job model | Existing ReportJob + PostgreSQL outbox + Redis dispatch | Existing generation and retry authority already protects snapshots. [VERIFIED: roster_scout/jobs.py] |
| Historical artifact generation | Re-run current scraper on download | Existing stored `ReportArtifact` bound to snapshot | Current data cannot reproduce an immutable historical artifact. [VERIFIED: roster_scout/models.py] |

**Key insight:** outsource identity protocols, but keep business authorization, tenant ownership, sessions, and report workflow explicit in Roster Scout because the locked access rules intentionally differ from the provider's default B2B membership behavior. [CITED: https://workos.com/docs/authkit/jit-provisioning] [VERIFIED: 02-CONTEXT.md]

## Runtime State Inventory

| Category | Items Found | Action Required |
|---|---|---|
| Stored data | Existing organizations/reports/snapshots/jobs/artifacts already use UUID organization ownership; snapshots have nullable free-text `created_by`; usage events have nullable actor UUID with no account FK; anonymous historical events have null actor/org. [VERIFIED: roster_scout/models.py] | Add new tables and nullable account FKs in additive revisions; preserve anonymous events; backfill only rows with a provable canonical account, otherwise leave null; do not reinterpret legacy `created_by`. [ASSUMED] |
| Live service config | No WorkOS project/environment configuration is represented in tracked deployment files. [VERIFIED: `.env.example`, `fly.toml`, `Dockerfile` grep] | Provision staging and production separately; set managed secrets and dashboard redirect/sign-out/initiate-login/social/SSO/JIT settings; record a deployment checklist because dashboard state cannot be migrated by Alembic. [CITED: https://workos.com/docs/authkit/environments] |
| OS-registered state | No Phase 2 identity daemon or OS registration was found in the task-relevant runtime/deployment files. [VERIFIED: Procfile, Dockerfile, compose.yaml, fly.toml grep] | None for the application; the optional WorkOS emulator is a development tool, not a production daemon. [CITED: https://workos.com/docs/authkit/testing] |
| Secrets/env vars | Tracked examples do not yet define WorkOS API/client/cookie/action secrets; local secret files were intentionally not read. [VERIFIED: `.env.example`, `fly.toml` grep] | Add documented names to `.env.example`, configure real values only in secret stores, and fail production startup when mandatory values are missing. [CITED: https://workos.com/docs/authkit/vanilla/python] |
| Build artifacts / installed packages | The project `.venv` has the existing Flask/SQLAlchemy/Alembic/psycopg stack but not `workos` or `Flask-WTF`; no `workos-emulate` executable is installed. [VERIFIED: local command] | Update pinned requirements, rebuild image/venv, and optionally install the official emulator for provider-bound tests. [CITED: https://workos.com/docs/authkit/testing] |

## Common Pitfalls

### Pitfall 1: WorkOS silently broadens membership
**What goes wrong:** a user with a verified domain or successful SSO appears as a provider organization member and is accidentally treated as a Roster Scout member. [CITED: https://workos.com/docs/authkit/jit-provisioning]  
**Why it happens:** automatic domain membership and SSO JIT are enabled by default. [CITED: https://workos.com/docs/authkit/jit-provisioning]  
**How to avoid:** disable both in every environment and keep local active membership authoritative even if a provider claim says otherwise. [VERIFIED: 02-CONTEXT.md]  
**Warning signs:** an uninvited same-domain account reaches the library, or authorization code reads WorkOS `org_id`/role directly. [ASSUMED]

### Pitfall 2: Provider invitation semantics violate exact-email acceptance
**What goes wrong:** a corporate-domain invitation can be accepted by another address at that domain. [CITED: https://workos.com/docs/authkit/invitations]  
**Why it happens:** this is WorkOS's documented organization-invitation behavior for corporate domains. [CITED: https://workos.com/docs/authkit/invitations]  
**How to avoid:** lock the local invite and require exact normalized-email equality after authentication before local membership creation. [ASSUMED]  
**Warning signs:** acceptance code compares only domains, trusts provider membership, or never reads the local invitation row. [ASSUMED]

### Pitfall 3: Session revocation is only cosmetic
**What goes wrong:** deleting a browser cookie leaves a stolen server/provider token usable. [CITED: https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html]  
**Why it happens:** logout clears client state but does not revoke the local session row or end the WorkOS session. [CITED: https://workos.com/docs/authkit/sessions]  
**How to avoid:** revoke local row first, clear cookie, and send the browser to WorkOS logout; enforce idle and absolute expiry server-side. [CITED: https://workos.com/docs/authkit/sessions] [CITED: https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html]  
**Warning signs:** a copied cookie still works after logout or removed membership remains usable until login expires. [ASSUMED]

### Pitfall 4: Transient provider outage logs everyone out
**What goes wrong:** a brief `429`, `5xx`, or timeout destroys otherwise valid local sessions. [CITED: https://workos.com/docs/authkit/session-resilience]  
**Why it happens:** the Python SDK returns typed refresh outcomes but leaves terminal-versus-transient handling to the application. [CITED: https://workos.com/docs/authkit/session-resilience]  
**How to avoid:** retain sealed session data on retryable errors, retry with bounded backoff, and revoke only on terminal invalid grant/revocation. [CITED: https://workos.com/docs/authkit/session-resilience]  
**Warning signs:** provider fault injection makes every next request redirect to login. [ASSUMED]

### Pitfall 5: Active organization is trusted from the browser
**What goes wrong:** changing a URL/form/JSON org UUID crosses tenants. [CITED: https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html]  
**Why it happens:** a route uses the requested org ID before loading the session's active membership. [ASSUMED]  
**How to avoid:** derive scope from revalidated membership and pass it explicitly to every repository/service; foreign and absent IDs return the same 404. [VERIFIED: 02-CONTEXT.md]  
**Warning signs:** repository functions accept a resource ID but no organization ID, or tests use only one organization. [VERIFIED: roster_scout/repositories.py] [ASSUMED]

### Pitfall 6: Last-admin protection races
**What goes wrong:** two concurrent demotions/removals each observe two admins and leave zero. [ASSUMED]  
**Why it happens:** count-then-update runs without a shared serialization lock. [ASSUMED]  
**How to avoid:** lock the organization row before counting/mutating active admins and test the concurrent case against PostgreSQL. [ASSUMED]  
**Warning signs:** the invariant exists only in UI validation or a nonlocking count query. [ASSUMED]

### Pitfall 7: Job success and authenticated-use accounting diverge
**What goes wrong:** a durable job exists but its required usage event does not, or a rejected request increments adoption. [VERIFIED: 02-CONTEXT.md]  
**Why it happens:** current high-level job acceptance commits internally, while usage is caller-owned. [VERIFIED: roster_scout/jobs.py] [VERIFIED: roster_scout/usage.py]  
**How to avoid:** write job/outbox and UUID-keyed usage event in the same authorization transaction, then dispatch after commit. [ASSUMED]  
**Warning signs:** route calls `accept_report_job()` and only afterward opens another session to write usage. [VERIFIED: roster_scout/jobs.py]

### Pitfall 8: Refresh hides or mutates the good snapshot
**What goes wrong:** queued/failed refresh replaces the latest published card, or historical download is regenerated. [VERIFIED: 02-CONTEXT.md]  
**Why it happens:** the UI treats the newest job as the newest snapshot or routes downloads through current collection. [ASSUMED]  
**How to avoid:** query active job and latest published snapshot independently; only ready/partial publication changes the latest snapshot, and artifact selection stays snapshot-bound. [VERIFIED: roster_scout/models.py]  
**Warning signs:** no previous snapshot is visible during running state, or download code invokes the scraper. [ASSUMED]

### Pitfall 9: Staging success is mistaken for production readiness
**What goes wrong:** production fails because keys, organizations, connections, redirect URIs, or branding were never recreated. [CITED: https://workos.com/docs/authkit/environments]  
**Why it happens:** WorkOS staging and production are fully separate. [CITED: https://workos.com/docs/authkit/environments]  
**How to avoid:** make environment provisioning a checked deploy step and run one production authentication smoke test before traffic. [CITED: https://workos.com/docs/authkit/environments]  
**Warning signs:** production config reuses staging IDs or no billing/HTTPS redirect is configured. [CITED: https://workos.com/docs/authkit/environments]

### Pitfall 10: Legacy public paths bypass the new gate
**What goes wrong:** `/api/teams`, `/scrape`, download acknowledgement, job/status, or artifact routes remain usable without a local session/membership. [VERIFIED: app.py]  
**Why it happens:** only new templates receive auth decorators. [ASSUMED]  
**How to avoid:** define a short explicit public allowlist (`healthz`, `readyz`, login, callback, invite entry, static, optional demo) and default every other route to authentication; test the complete route matrix. [ASSUMED]  
**Warning signs:** unauthenticated route tests cover only `/` and not JSON or POST endpoints. [ASSUMED]

## Code Examples

Verified source patterns and project-specific adaptations follow. [VERIFIED: official docs + codebase grep]

### Replaceable identity adapter

```python
from typing import Protocol

class IdentityProvider(Protocol):
    def authorization_url(self, *, state: str, invitation_token: str | None = None) -> str: ...
    def exchange_code(self, *, code: str) -> "VerifiedIdentity": ...
    def refresh(self, *, sealed_session: str) -> "RefreshResult": ...
    def logout_url(self, *, sealed_session: str) -> str: ...
```

The interface isolates provider behavior; the concrete WorkOS client uses `get_authorization_url`, `authenticate_with_code`, sealed session helpers, and logout URL methods documented by WorkOS. [CITED: https://workos.com/docs/authkit/vanilla/python] [CITED: https://workos.com/docs/reference/authkit/session-helpers]

### Opaque local session verifier

```python
import hashlib
import hmac
import secrets

lookup_id = secrets.token_urlsafe(16)
verifier = secrets.token_urlsafe(32)
stored_digest = hashlib.sha256(verifier.encode("ascii")).digest()
cookie_value = f"{lookup_id}.{verifier}"

def verifier_matches(candidate: str, stored: bytes) -> bool:
    digest = hashlib.sha256(candidate.encode("ascii")).digest()
    return hmac.compare_digest(digest, stored)
```

This applies OWASP's identifier-plus-secret-verifier pattern; the raw verifier must never be persisted or logged. [CITED: https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html]

### Membership-derived resource lookup

```python
def load_report_or_404(session, *, membership, report_id):
    return session.scalar(
        select(Report).where(
            Report.id == report_id,
            Report.organization_id == membership.organization_id,
        )
    ) or abort(404)
```

The key is that `membership` is reloaded from the server-side session on this request; no organization value from the request participates in authorization. [CITED: https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html] [VERIFIED: roster_scout/repositories.py]

### One transaction for accepted job and usage

```python
with Session.begin() as session:
    membership = require_active_membership(session, account_id, active_org_id)
    report = lock_or_create_scoped_report(session, membership.organization_id, selector)
    job = create_or_get_active_job(session, report, idempotency_key)
    record_authenticated_report_request_accepted(
        session,
        event_key=str(job.id),
        actor_account_id=membership.account_id,
        organization_id=membership.organization_id,
    )
    append_audit_event(session, actor=membership.account_id, organization=membership.organization_id,
                       target=job.id, action="report_job.accept", outcome="allowed")
# Existing outbox publisher runs only after the commit succeeds.
```

This composes existing connection-free primitives and preserves caller-owned transaction semantics. [VERIFIED: roster_scout/jobs.py] [VERIFIED: roster_scout/usage.py] [VERIFIED: 02-CONTEXT.md]

## State of the Art

| Old Approach | Current Approach | When Changed / Evidence | Impact |
|---|---|---|---|
| Email magic links | Six-digit Magic Auth codes | WorkOS documents Magic Links as deprecated because mail scanners may consume them. [CITED: https://workos.com/docs/authkit/modeling-your-app] | Use the code flow; do not plan a magic-link-only UX. [CITED: https://workos.com/docs/authkit/magic-auth] |
| Live provider API in every test | Local AuthKit emulator plus a small staging contract suite | WorkOS's current testing guide provides an in-memory emulator and failure hooks. [CITED: https://workos.com/docs/authkit/testing] | Most auth tests become deterministic and credential-free; staging verifies the real boundary. [CITED: https://workos.com/docs/authkit/testing] |
| Clear session on every refresh error | Preserve on transient failure; clear only terminal invalid/revoked session | WorkOS now documents resilience behavior explicitly and notes Python is bring-your-own handling. [CITED: https://workos.com/docs/authkit/session-resilience] | Prevents provider blips from logging out all coaches. [CITED: https://workos.com/docs/authkit/session-resilience] |
| Provider domain/JIT membership as convenience | Explicit local invitation/membership authority | WorkOS documents auto-membership defaults; project decisions reject domain authorization. [CITED: https://workos.com/docs/authkit/jit-provisioning] [VERIFIED: 02-CONTEXT.md] | Disable JIT and treat provider organization state as routing only. [ASSUMED] |

**Deprecated/outdated:**
- WorkOS Magic Links are deprecated in favor of Magic Auth codes. [CITED: https://workos.com/docs/authkit/modeling-your-app]
- WorkOS `App homepage URL` is deprecated as the sign-out fallback; configure a default Sign-out URI. [CITED: https://workos.com/docs/authkit/sessions]

## Assumptions Log

Every row in this table is an `[ASSUMED]` claim grouped from the inline assumptions above. [ASSUMED]

| # | Claim | Section | Risk if Wrong |
|---|---|---|---|
| A1 | A 12-hour idle and 7-day absolute local session balances weekend usability and revocation risk. | Session pattern | Coaches may be prompted too often or sessions may live longer than desired; values are configuration, not schema. |
| A2 | Local invitations expire after seven days and never later than provider expiry. | Invitation pattern | Product may prefer a shorter window; acceptance logic remains unchanged. |
| A3 | Report polling uses 3s → 5s → 10s and stops at 15 minutes. | Report read model | Job duration may warrant tuning; status endpoint contract remains stable. |
| A4 | 25 reports/snapshots per page is a suitable initial page size. | Report library | Large organizations may need a different default; cursor pagination should make this tunable. |
| A5 | WorkOS organization/invitation objects are mirrors for delivery/SSO routing while local membership is canonical. | Provider architecture | Operational sync/reconciliation complexity is higher, but using provider membership as authority violates locked decisions. |
| A6 | Failed manual retry should create a new linked job instead of rewriting the failed job. | Job pattern | Existing UI may prefer reopening a safe generation; planner must preserve failed-attempt auditability either way. |
| A7 | Server-rendered Flask plus small vanilla JS is sufficient for the customer-facing Phase 2 UI. | Standard stack | If UI design work reveals richer interactivity, a larger client layer could be needed, but Phase 2 workflows do not currently require it. |
| A8 | The proposed account, external-identity, local-session, login-transaction, membership, invitation, provider-mapping, audit, and actor/requester FK schema is the correct additive model. | Canonical Schema Additions | Incorrect cardinality or migration sequencing could force a later schema rewrite; validate against migration tests before route work. |
| A9 | The callback flow should consume server-side state, exchange the code, link identity, accept the exact local invitation, create a local session, and select only a current local membership in that order. | Pattern 1 | A different order could create replay, orphan-session, or unauthorized-membership windows. |
| A10 | A local session should use an opaque verifier/hash design and keep sealed WorkOS session data in PostgreSQL. | Pattern 2 | An incompatible WorkOS helper or operational storage constraint could require adapting the representation, while preserving D-04. |
| A11 | WorkOS invitation delivery plus a local exact-email invitation row is operationally preferable to adding a separate mail provider. | Pattern 4 / Open Questions | Provider/local partial failure needs reconciliation and resend tests. |
| A12 | Organization-row locking is sufficient to serialize last-admin changes and report-row locking is sufficient to converge active-job clicks. | Patterns 5–6 | A missing lock path could create a race; PostgreSQL concurrency tests must prove the invariant. |
| A13 | The authorized acceptance service can compose lower-level Phase 1 job and usage primitives in one transaction, then publish after commit. | Pattern 6 | If current internals cannot be safely exposed, they need a small refactor that preserves existing callers. |
| A14 | The explicit public-route allowlist and same-404 foreign/absent response contract cover the complete Flask surface. | Pitfalls / Security | An overlooked compatibility route could bypass authentication or reveal tenancy. |
| A15 | Audit logging should use the proposed strict allowlist and append-only PostgreSQL enforcement. | Audit / Security Logging | Too little data harms investigation; too much data violates the locked privacy/secrecy boundary. |
| A16 | The proposed new test files, focused commands, provider-test pyramid, and sampling rates fit the existing unittest/Node setup. | Validation Architecture | Focused class runtime or emulator behavior may require splitting tests differently. |
| A17 | WorkOS session configuration can be aligned at or beyond local timeout limits without making provider claims authoritative. | Required WorkOS Configuration | Provider environment settings may impose limits that require shorter local defaults. |
| A18 | WorkOS default email is acceptable for staging and initial implementation, with production deliverability checked before launch. | Open Questions | Poor deliverability or branding requirements could force a custom email provider before production. |
| A19 | Test IdP plus emulator coverage is sufficient Phase 2 acceptance when no real customer SSO connection exists. | Open Questions / Validation | A launch customer may require an additional real-IdP onboarding test. |
| A20 | Phase 2 should implement fixed identity/database/queue/source/artifact failure categories while leaving the Power BI path dormant until Phase 5. | Requirement SECU-05 | A strict requirement interpretation could demand a placeholder Power BI state earlier, despite embedding being deferred. |
| A21 | A custom Flask `SessionInterface` over the local session row is the cleanest way to integrate server-side auth state with Flask-WTF CSRF. | Pattern 2 | Concurrency and response-save ordering require careful tests; a vetted server-side session extension could replace it only if it supports the required account/org/revocation model. |

## Open Questions (RESOLVED)

1. **WorkOS production ownership, billing, secrets, and live hosted smoke**
   - Resolution: WorkOS production workspace ownership, billing, secrets, and a live hosted authentication smoke test are deployment checkpoints. They do not block deterministic Phase 2 code acceptance, and no billable resources are created automatically by the implementation or deterministic test suite. [CITED: https://workos.com/docs/authkit/environments] [CITED: https://workos.com/pricing]

2. **Email delivery and branding**
   - Resolution: use WorkOS default email delivery for emulator/staging implementation. Production deliverability and branding must be verified before launch; a second mail service is out of Phase 2 unless that verification fails. WorkOS documents default invitation delivery and separate shared-provider deliverability limits. [CITED: https://workos.com/docs/authkit/invitations] [CITED: https://workos.com/docs/reference/rate-limits]

3. **Enterprise SSO acceptance boundary**
   - Resolution: WorkOS emulator plus the staging Test IdP is the Phase 2 acceptance boundary when no real customer IdP exists. A real SAML/OIDC connection is a customer-onboarding/production checkpoint, not a deterministic code-acceptance blocker. [CITED: https://workos.com/docs/authkit/testing] [CITED: https://workos.com/docs/authkit/sso] [CITED: https://workos.com/docs/authkit/environments]

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|---|---|---:|---:|---|
| Python | Flask app/tests | ✓ | `3.14.7` | — [VERIFIED: local command] |
| Project virtualenv | Existing/test runtime | ✓ | Python `3.14.7`; Flask `3.1.3`, SQLAlchemy `2.1.6`, Alembic `1.20.1`, psycopg `3.3.6` | — [VERIFIED: local command] |
| `workos` | Provider adapter | ✗ | PyPI current `10.8.0` | Install pinned dependency. [VERIFIED: local command] [VERIFIED: PyPI registry] |
| `Flask-WTF` | CSRF | ✗ | PyPI current `1.3.0` | Install pinned dependency; no custom CSRF fallback recommended. [VERIFIED: local command] [VERIFIED: PyPI registry] |
| WorkOS Emulate | Provider integration tests | ✗ | — | Unit tests use injected fake adapter; install official emulator for provider-bound suite. [CITED: https://workos.com/docs/authkit/testing] |
| WorkOS staging project/Test IdP | Hosted smoke/SSO | Not represented locally | Managed | Provision in WorkOS Dashboard; emulator covers most tests. [CITED: https://workos.com/docs/authkit/environments] |
| Docker CLI / Compose | PostgreSQL/Redis test services | ✓ | Docker `29.9.2`; Compose `5.6.1` | Native services if separately installed. [VERIFIED: local command] |
| Docker daemon | Local services now | ✗ | No server response | Start Docker before DB integration tests. [VERIFIED: local command] |
| PostgreSQL/Redis native CLIs | Direct diagnostics | ✗ | — | Compose defines PostgreSQL 16 and Redis 7 on loopback and creates the `_test` DB. [VERIFIED: compose.yaml] |
| Node | Existing vanilla-JS tests | ✓ | `24.14.0` | — [VERIFIED: local command] |
| `uv` | Optional isolated Python execution | ✓ | `0.10.10` | Use project `.venv`/pip if preferred. [VERIFIED: local command] |

**Missing dependencies with no safe production fallback:** WorkOS production environment, billing, API/client credentials, registered HTTPS redirect/sign-out/initiate-login URIs, and social/enterprise connection configuration. [CITED: https://workos.com/docs/authkit/environments]

**Missing dependencies with fallback:** WorkOS emulator is absent, but injected adapter fakes cover unit tests until the official tool is installed; native PostgreSQL/Redis CLIs are absent, but Compose provides both services once the Docker daemon is running. [VERIFIED: local command] [VERIFIED: compose.yaml]

## Validation Architecture

### Test Framework

| Property | Value |
|---|---|
| Framework | Python standard `unittest` with PostgreSQL integration support; Node built-in `node:test` for browser modules. [VERIFIED: tests/] |
| Config file | No separate unittest config; `tests/db_support.py` requires a PostgreSQL database whose name ends in `_test`. [VERIFIED: tests/db_support.py] |
| Quick run command | `.venv/bin/python -m unittest tests.test_identity tests.test_organizations tests.test_report_library -v` [ASSUMED] |
| Full suite command | `.venv/bin/python -m unittest discover -s tests -v && node --test tests/js/*.test.mjs` [VERIFIED: CONTEXT.md] [ASSUMED] |

### Phase Requirements → Test Map

Every named Phase 2 test file/class below is a proposed Wave 0 addition and is therefore `[ASSUMED]`; requirement behavior comes from the locked requirements. [ASSUMED] [VERIFIED: REQUIREMENTS.md]

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|---|---|---|---|---|
| AUTH-01 | Every non-public HTML/JSON/action/export route requires auth and membership. | integration/route matrix | `.venv/bin/python -m unittest tests.test_identity.AuthGateRouteMatrixTests -v` | ❌ Wave 0 |
| AUTH-02 | Code expiry/replay/throttle and sanitized auth errors. | provider adapter + emulator integration | `.venv/bin/python -m unittest tests.test_identity.PasswordlessBoundaryTests -v` | ❌ Wave 0 |
| AUTH-03 | Google/Microsoft verified identities converge on the same local account. | unit + DB integration | `.venv/bin/python -m unittest tests.test_identity.ExternalIdentityLinkingTests -v` | ❌ Wave 0 |
| AUTH-04 | SAML/OIDC login still requires local membership; JIT/domain claims do not grant access. | adapter/emulator + two-org integration | `.venv/bin/python -m unittest tests.test_identity.EnterpriseSsoAuthorizationTests -v` | ❌ Wave 0 |
| AUTH-05 | Refresh persists; rotation/logout/revocation work; transient refresh preserves session. | integration + fault injection | `.venv/bin/python -m unittest tests.test_identity.ServerSessionLifecycleTests -v` | ❌ Wave 0 |
| ORG-01 | Invitation and all report resources have one organization owner and composite constraints. | migration/schema integration | `.venv/bin/python -m unittest tests.test_organizations.OrganizationOwnershipSchemaTests -v` | ❌ Wave 0 |
| ORG-02 | Admin can invite any normalized valid email, not only `.edu`. | service/route integration | `.venv/bin/python -m unittest tests.test_organizations.InvitationCreationTests -v` | ❌ Wave 0 |
| ORG-03 | Exact-email invite acceptance grants only the inviting organization. | two-org integration | `.venv/bin/python -m unittest tests.test_organizations.InvitationAcceptanceTests -v` | ❌ Wave 0 |
| ORG-04 | Admin/member authorization matrix is enforced. | table-driven service tests | `.venv/bin/python -m unittest tests.test_organizations.RoleAuthorizationTests -v` | ❌ Wave 0 |
| ORG-05 | List/revoke/remove plus concurrent last-admin protection. | integration + race test | `.venv/bin/python -m unittest tests.test_organizations.MembershipAdministrationTests -v` | ❌ Wave 0 |
| ORG-06 | Every active member sees the same scoped report library/history. | route integration | `.venv/bin/python -m unittest tests.test_report_library.SharedLibraryTests -v` | ❌ Wave 0 |
| ORG-07 | Domain/provider org never auto-grants local membership. | negative integration | `.venv/bin/python -m unittest tests.test_organizations.NoDomainAutoJoinTests -v` | ❌ Wave 0 |
| SNAP-05 | Explicit refresh creates a successor without modifying prior snapshot. | PostgreSQL/job integration | `.venv/bin/python -m unittest tests.test_report_library.RefreshLineageTests -v` | ❌ Wave 0 |
| SNAP-06 | Dated history identifies newest ready/partial and keeps it visible while active. | repository + route/UI tests | `.venv/bin/python -m unittest tests.test_report_library.SnapshotHistoryTests -v` | ❌ Wave 0 |
| SNAP-07 | Repeated key and concurrent clicks converge on one job/snapshot/usage event. | concurrency integration | `.venv/bin/python -m unittest tests.test_report_library.ReportAcceptanceIdempotencyTests -v` | ❌ Wave 0 |
| SNAP-08 | Failure never publishes ready; retry preserves failed attempt and uses outbox path. | job integration | `.venv/bin/python -m unittest tests.test_report_library.FailedBuildRetryTests -v` | ❌ Wave 0 |
| SECU-01 | Foreign report/job/snapshot/artifact substitution returns the same 404, and no Phase 2 embed-token route exists. | two-org route matrix | `.venv/bin/python -m unittest tests.test_phase2_tenant_isolation.CrossOrganizationDenialTests -v` | ❌ Wave 0 |
| SECU-02 | Browser org/account/role claims are ignored. | negative route/service tests | `.venv/bin/python -m unittest tests.test_phase2_tenant_isolation.UntrustedScopeInputTests -v` | ❌ Wave 0 |
| SECU-03 | Codes/tokens/assertions/cookies/raw email/exceptions never enter logs. | log-capture tests | `.venv/bin/python -m unittest tests.test_identity.SecretRedactionTests -v` | ❌ Wave 0 |
| SECU-04 | Allowlisted audit events exist for auth/member/report/refresh/retry/access/download/denial. | DB integration | `.venv/bin/python -m unittest tests.test_phase2_tenant_isolation.SecurityAuditEventTests -v` | ❌ Wave 0 |
| SECU-05 | Identity/database/queue/source/artifact failures map to distinct fixed states. | fault-injection integration | `.venv/bin/python -m unittest tests.test_phase2_release.ExternalFailureStateTests -v` | ❌ Wave 0 |

Every focused PostgreSQL command requires `DATABASE_URL=postgresql+psycopg://roster_scout:roster_scout@127.0.0.1:5432/roster_scout_test`; the repository already creates that database from Compose's init script. [VERIFIED: compose.yaml] [VERIFIED: tests/db_support.py]

### Provider-Test Pyramid

1. Use an in-memory fake `IdentityProvider` for local account/link/session/authorization unit tests so the core suite has no network or provider credentials. [ASSUMED]
2. Run WorkOS Emulate for callback, session refresh, organization selection, signed-event, and `429/5xx/timeout` behavior; the emulator is explicitly intended for these flows. [CITED: https://workos.com/docs/authkit/testing]
3. Run a small opt-in WorkOS staging smoke suite for hosted Magic Auth, Google/Microsoft configuration, and Test IdP SSO because the emulator is not a complete service copy. [CITED: https://workos.com/docs/authkit/testing] [CITED: https://workos.com/docs/authkit/environments]
4. Never use production credentials or real recipient email addresses in ordinary CI; WorkOS recommends reserved example domains for staging tests. [CITED: https://workos.com/docs/authkit/environments]

### Sampling Rate

- **Per task commit:** run the focused class named in the requirement row plus `git diff --check`. [ASSUMED]
- **Per wave merge:** run all new Phase 2 Python tests and `node --test tests/js/*.test.mjs`. [ASSUMED]
- **Phase gate:** run `.venv/bin/python -m unittest discover -s tests -v`, all Node tests, the existing catalog validation, and one opt-in WorkOS staging/Test IdP smoke; all deterministic suites must be green before `$gsd-verify-work`. [VERIFIED: CONTEXT.md] [CITED: https://workos.com/docs/authkit/testing]

### Wave 0 Gaps

- [ ] `tests/test_identity.py` — fake adapter, callback state, linking, session, logout, sanitization, provider failure fixtures. [ASSUMED]
- [ ] `tests/test_organizations.py` — account/org/invite/member schema, exact-email flow, roles, last-admin concurrency, no-domain-auto-join. [ASSUMED]
- [ ] `tests/test_report_library.py` — scoped list/detail, acceptance/usage atomicity, refresh/lineage/history/partial/retry/artifact behavior. [ASSUMED]
- [ ] `tests/test_phase2_tenant_isolation.py` — complete two-organization HTML/JSON/action/download substitution matrix and audit assertions. [ASSUMED]
- [ ] `tests/test_phase2_release.py` — full Phase 2 story, external failure categories, existing workflow regressions. [ASSUMED]
- [ ] `tests/js/report_status.test.mjs` — polling backoff, tab-hidden pause, terminal stop, retry/partial UI, desktop/mobile equivalence. [ASSUMED]
- [ ] `tests/fixtures/workos-emulate.config.yaml` — deterministic users, mirrored organizations, SSO connection, and callback/session fixtures. [CITED: https://workos.com/docs/authkit/testing]
- [ ] Install pinned runtime packages and the official WorkOS emulator; keep emulator/staging tests separately selectable when the tool or credentials are absent. [VERIFIED: local command] [CITED: https://workos.com/docs/authkit/testing]

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---|---|---|
| V2 Authentication | yes | WorkOS Hosted AuthKit, verified identity linking, one-time state/code, fixed errors, throttling. [CITED: https://workos.com/docs/authkit/overview] |
| V3 Session Management | yes | Opaque random local verifier, server-side state, rotation, idle/absolute expiry, provider refresh, local/provider logout. [CITED: https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html] |
| V4 Access Control | yes | Deny by default, validate active local membership every request, org-scoped repositories, non-enumerating 404. [CITED: https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html] |
| V5 Input Validation | yes | Normalize WorkOS email identically, strict UUID/selectors, safe return-path allowlist, bounded idempotency/state/pagination fields. [CITED: https://workos.com/docs/authkit/users-organizations] [VERIFIED: roster_scout/usage.py] |
| V6 Cryptography | yes | Do not hand-roll identity/federation crypto; use WorkOS SDK/session sealing and Python CSPRNG/hash only for random local verifiers. [CITED: https://workos.com/docs/authkit/vanilla/python] [CITED: https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html] |

OWASP ASVS 5.0.0 is the current standard listed by the official ASVS project at research time. [CITED: https://asvs.owasp.org/]

### Known Threat Patterns for Flask + Managed Identity + B2B Tenancy

| Pattern | STRIDE | Standard Mitigation |
|---|---|---|
| Callback CSRF/login substitution | Spoofing | One-time server-side `state`, exact callback URI, consume-before-session, fixed expiry; never treat arbitrary return URL as trusted. [CITED: https://workos.com/docs/reference/authkit/authentication/get-authorization-url] |
| Session fixation/theft | Spoofing | Rotate after authentication/privilege change, opaque ≥128-bit verifier, one-way store, Secure/HttpOnly/SameSite cookie, server revoke. [CITED: https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html] |
| CSRF on logout/invite/member/report/refresh/retry | Tampering | Flask-WTF synchronizer token on all unsafe methods; SameSite is defense-in-depth, not the only control. [CITED: https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html] |
| Cross-tenant IDOR | Information disclosure / Elevation | Derive org from active membership, scope every query by org, deny by default, two-org substitution matrix, same 404. [CITED: https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html] |
| Domain/JIT auto-join | Elevation | Disable WorkOS JIT and domain auto-membership; local exact invitation only. [CITED: https://workos.com/docs/authkit/jit-provisioning] |
| Invitation theft/misbinding | Spoofing / Elevation | Provider token secrecy + expiry + single use; local exact normalized email and organization check in locked transaction. [CITED: https://workos.com/docs/reference/authkit/invitation] |
| Last-admin race | Tampering / Denial | Lock organization, count active admins, then mutate in one transaction; race test. [ASSUMED] |
| Secret leakage in logs | Information disclosure | Allowlisted structured logs/audit; never callback query, raw email, cookie, code, token, assertion, provider response, or exception text. [CITED: https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html] |
| Replay/duplicate report request | Tampering | Existing org/idempotency uniqueness plus report-row serialization and UUID-keyed usage event. [VERIFIED: roster_scout/models.py] [VERIFIED: roster_scout/usage.py] |
| Provider outage interpreted as logout | Denial | Distinguish transient refresh failure from terminal invalid/revoked grant; retain session on transient. [CITED: https://workos.com/docs/authkit/session-resilience] |

### Security Logging Allowlist

Permit only event UUID/time, canonical account UUID (nullable on pre-account sign-in outcome), local organization UUID (nullable when unavailable), target type/UUID, allowlisted action, allowlisted outcome, and fixed reason category. [ASSUMED] Do not persist raw email, domain, IP, user agent, provider IDs unless the canonical external-identity table requires them, report/stat data, invitation token, auth code, state/verifier, session cookie, sealed provider session, JWT/assertion, API key, CSRF token, return URL, or exception text. [VERIFIED: 02-CONTEXT.md]

## Sources

### Primary (HIGH confidence)

- [WorkOS AuthKit overview](https://workos.com/docs/authkit/overview) — supported authentication model and hosted UI. [CITED: https://workos.com/docs/authkit/overview]
- [WorkOS Python/Flask quickstart](https://workos.com/docs/authkit/vanilla/python) — official package, callback, cookie/session helpers, logout CSRF. [CITED: https://workos.com/docs/authkit/vanilla/python]
- [WorkOS Magic Auth](https://workos.com/docs/authkit/magic-auth) — one-time code and 10-minute expiry. [CITED: https://workos.com/docs/authkit/magic-auth]
- [WorkOS Social Login](https://workos.com/docs/authkit/social-login) — Google/Microsoft federation. [CITED: https://workos.com/docs/authkit/social-login]
- [WorkOS SSO](https://workos.com/docs/authkit/sso) — SAML/OIDC and Test IdP. [CITED: https://workos.com/docs/authkit/sso]
- [WorkOS sessions and resilience](https://workos.com/docs/authkit/sessions), [session resilience](https://workos.com/docs/authkit/session-resilience) — backend refresh storage, rotation, logout, transient handling. [CITED: https://workos.com/docs/authkit/sessions]
- [WorkOS invitations](https://workos.com/docs/authkit/invitations), [invitation API](https://workos.com/docs/reference/authkit/invitation) — closed registration, delivery, exact-versus-domain semantics, acceptance. [CITED: https://workos.com/docs/authkit/invitations]
- [WorkOS identity linking](https://workos.com/docs/authkit/identity-linking) — verified email and provider identity safeguards. [CITED: https://workos.com/docs/authkit/identity-linking]
- [WorkOS JIT](https://workos.com/docs/authkit/jit-provisioning), [organization policies](https://workos.com/docs/authkit/organization-policies) — default automatic membership risks and policy routing. [CITED: https://workos.com/docs/authkit/jit-provisioning]
- [WorkOS testing](https://workos.com/docs/authkit/testing), [environments](https://workos.com/docs/authkit/environments) — emulator, staging/Test IdP, production differences. [CITED: https://workos.com/docs/authkit/testing]
- [WorkOS rate limits](https://workos.com/docs/reference/rate-limits) — authentication and email request limits. [CITED: https://workos.com/docs/reference/rate-limits]
- [WorkOS pricing](https://workos.com/pricing) — current commercial figures, separated from capability. [CITED: https://workos.com/pricing]
- [OWASP Session Management](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html), [Authorization](https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html), [CSRF](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html) — server-side session, access control, and CSRF patterns. [CITED: https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html]
- [Flask SessionInterface API](https://flask.palletsprojects.com/en/stable/api/) and [Flask-WTF CSRF](https://flask-wtf.readthedocs.io/en/latest/csrf/) — custom server-side Flask session contract and form/JSON CSRF integration. [CITED: https://flask.palletsprojects.com/en/stable/api/]
- Project `02-CONTEXT.md`, `REQUIREMENTS.md`, `V2-SPEC.md`, `STATE.md`, `ROADMAP.md`, `CONTEXT.md`, and task-relevant source/tests — locked decisions and existing contracts. [VERIFIED: codebase grep]

### Secondary (MEDIUM confidence)

- [Auth0 passwordless email lab](https://developer.auth0.com/resources/labs/authentication/passwordless-with-email) and [Flask guide](https://developer.auth0.com/resources/guides/web-app/flask/basic-authentication) — alternative-provider comparison. [CITED: https://developer.auth0.com/resources/guides/web-app/flask/basic-authentication]
- [Clerk enterprise SSO](https://clerk.com/docs/guides/configure/auth-strategies/enterprise-connections/overview) and [pricing/capability page](https://clerk.com/pricing) — alternative-provider comparison. [CITED: https://clerk.com/docs/guides/configure/auth-strategies/enterprise-connections/overview]

### Tertiary (LOW confidence)

- None; unverified design choices are explicitly tagged `[ASSUMED]` and summarized above. [VERIFIED: document review]

## Metadata

**Confidence breakdown:**
- Standard stack: **HIGH** — packages, versions, publish dates, source repositories, official provider docs, and slopcheck were verified. [VERIFIED: PyPI registry] [VERIFIED: slopcheck 0.6.1 local run]
- Architecture: **HIGH** for provider/local authorization separation and Phase 1 preservation; **MEDIUM** for exact timeout/pagination/UI defaults and retry linkage, which are deliberate assumptions. [CITED: https://workos.com/docs/authkit/jit-provisioning] [VERIFIED: codebase grep]
- Pitfalls: **HIGH** — critical provider defaults, invitation semantics, session resilience, and existing transaction boundaries were verified from official docs/source. [CITED: https://workos.com/docs/authkit/invitations] [VERIFIED: roster_scout/jobs.py]
- Validation: **MEDIUM-HIGH** — existing frameworks/DB rules are verified, while all Phase 2 test files are Wave 0 additions. [VERIFIED: tests/db_support.py]

**Research date:** 2026-11-11  
**Valid until:** 2026-12-11 for provider capabilities and package versions; recheck pricing immediately before procurement. [ASSUMED]





