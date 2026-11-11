# Phase 2: Identity, Organizations, and Report Library - Context

**Gathered:** 2026-11-11
**Status:** Ready for planning

<domain>
## Phase Boundary

Phase 2 turns the existing public workbook flow into a login-gated organization workspace. It adds managed passwordless and federated identity, durable accounts and sessions, explicit organization membership and administration, a tenant-isolated report library, asynchronous report status, dated snapshot history, explicit live refresh, safe retry, authenticated-use tracking, and security audit events.

This phase does not add detailed fielding, derived metrics, last-10 or conference scopes, splits, career history, game logs, the ten-sheet workbook, or Power BI. Those remain assigned to Phases 3–5.

</domain>

<decisions>
## Implementation Decisions

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

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Product and Phase Contract
- `.planning/V2-SPEC.md` § Phase 2 — Locks the login-gated organization workspace outcome, scope, exclusions, and acceptance criteria.
- `.planning/REQUIREMENTS.md` — Defines AUTH-01–05, ORG-01–07, SNAP-05–08, and SECU-01–05 assigned to Phase 2.
- `.planning/ROADMAP.md` § Phase 2 — Defines the goal, six success criteria, four planned capability slices, and Phase 1 dependency.
- `.planning/PROJECT.md` — Carries milestone-wide decisions on explicit invitations, organization sharing, immutable snapshots, refresh, and analytics consistency.
- `.planning/phases/01-canonical-snapshot-foundation/01-CONTEXT.md` — Defines the publication, cache, idempotency, identity, and evidence contracts Phase 2 must preserve.

### Existing Runtime and Persistence Contract
- `CONTEXT.md` — Defines current request flow, system boundaries, change impact, and required handoff checks.
- `roster_scout/models.py` — Existing organization, report, snapshot, job, artifact, and product-usage schema foundations.
- `roster_scout/repositories.py` — Existing organization-scoped snapshot/job and dispatch patterns.
- `roster_scout/jobs.py` — Durable accepted-job, idempotency, generation, retry, and publication service boundary.
- `roster_scout/usage.py` — Prebuilt authenticated-use producer and aggregate metric semantics awaiting Phase 2 account binding.
- `app.py` — Current public routes, workbook compatibility path, download acknowledgement, readiness, and security headers.
- `templates/index.html` — Existing NCAA selector and workbook interaction to migrate behind authenticated report creation.

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `Organization`, `Report`, `ReportSnapshot`, `ReportJob`, `ReportArtifact`, and `ProductUsageEvent` already provide organization-owned durable foundations.
- `list_published_snapshots()`, `get_published_snapshot()`, and `get_report_job()` demonstrate mandatory organization filtering.
- `record_authenticated_report_request_accepted()` already defines the exact usage event boundary Phase 2 must call after job acceptance.
- The current NCAA selector, logo endpoint, branded workbook path, and download acknowledgement can be retained behind authenticated pages.

### Established Patterns
- Database modules remain connection-free; callers create sessions and own transactions.
- Tenant-owned child rows use composite organization ownership constraints, and immutable published snapshots are enforced in PostgreSQL.
- Durable jobs use PostgreSQL as retry authority and Redis only for opaque dispatch; duplicate accepted requests converge by idempotency key.
- Customer-facing errors and operational logs use fixed sanitized categories rather than raw exceptions or upstream payloads.

### Integration Points
- Add authentication/session loading and organization authorization before team search, report creation, report history, refresh, status, and download routes.
- Extend the Phase 1 schema with accounts, external identities, memberships, invitations, sessions, and audit events through new Alembic revisions.
- Replace the public `/` workbook-first experience with sign-in or the active organization's report library while preserving `/healthz` and `/readyz` semantics.
- Reuse the Phase 1 job/outbox/publisher boundary for report creation, refresh, and retry rather than creating a second asynchronous workflow.

</code_context>

<specifics>
## Specific Ideas

- Coaches should reopen a saved opponent instantly during a weekend series, then explicitly refresh after each game to capture changed statistics.
- The report library is shared by the whole authorized staff, not tied to one creator's private account.
- School email domains may improve sign-in convenience or organization suggestions, but invitations remain the only authorization path.

</specifics>

<deferred>
## Deferred Ideas

- Last-10 and conference-only statistical scopes remain assigned to Phase 3 despite an automated todo keyword match.
- Detailed fielding, derived metrics, splits, career history, and game logs remain Phase 3.
- The ten-sheet workbook remains Phase 4; Power BI embedding and RLS remain Phase 5.

### Reviewed Todos (not folded)
- `Add last-10 and conference-only stats` — Explicitly declares `resolves_phase: 3`; it is out of scope for Phase 2.

</deferred>

---

*Phase: 2-Identity, Organizations, and Report Library*
*Context gathered: 2026-11-11*
