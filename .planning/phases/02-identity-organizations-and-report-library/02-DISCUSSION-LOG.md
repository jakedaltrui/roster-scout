# Phase 2: Identity, Organizations, and Report Library - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-11-11
**Phase:** 2-identity-organizations-and-report-library
**Areas discussed:** Sign-in and sessions, Organization membership, Report library and refresh, Tenant isolation and auditing

---

## Sign-in and Sessions

| Option | Description | Selected |
|--------|-------------|----------|
| Managed identity adapter | Passwordless email, Google/Microsoft, and enterprise OIDC/SAML through a managed provider; canonical local accounts remain authoritative for authorization | ✓ |
| Build authentication locally | Implement email codes, OAuth, session issuance, and enterprise connections directly in Flask | |
| Federation only | Require Microsoft or Google and omit universal passwordless email | |

**User's choice:** Auto-selected the managed identity adapter as the secure recommended default under the user's explicit instruction to proceed autonomously.
**Notes:** Provider selection is delegated to current official-document research. Sessions remain server-side and revocable.

---

## Organization Membership

| Option | Description | Selected |
|--------|-------------|----------|
| Explicit invitation membership | Invitation-only membership, multi-organization accounts, active organization switcher, and admin/member roles | ✓ |
| Email-domain auto-join | Grant access based on a matching school domain | |
| One organization per account | Permanently bind each account to one organization | |

**User's choice:** Explicit invitations were already locked by the milestone interview and carried forward.
**Notes:** Operator-only first-admin bootstrap and last-admin protection are secure implementation defaults.

---

## Report Library and Refresh

| Option | Description | Selected |
|--------|-------------|----------|
| Persistent library with explicit refresh | Keep prior snapshots visible, show asynchronous state, and create a live successor only when the user refreshes | ✓ |
| Automatic freshness replacement | Silently rebuild or replace reports when cache age expires | |
| Download-only history | Preserve files without a shared report/snapshot web experience | |

**User's choice:** Persistent snapshots and explicit refresh were already locked and carried forward.
**Notes:** The latest published snapshot remains usable while refresh runs; duplicate clicks converge on the active job.

---

## Tenant Isolation and Auditing

| Option | Description | Selected |
|--------|-------------|----------|
| Membership-derived tenant context | Server derives organization from the authenticated membership, filters every lookup, returns non-enumerating denials, and writes safe audit events | ✓ |
| Request-provided organization context | Trust organization IDs supplied by the browser after login | |
| Shared records with UI-only filtering | Rely on the frontend to hide other organizations' records | |

**User's choice:** Auto-selected membership-derived tenant context as the required secure default.
**Notes:** Two-organization negative tests cover every protected resource type. Usage counts only accepted authorized report jobs.

## the agent's Discretion

- Managed identity provider selection after official-document research.
- Exact secure expiry, session lifetime, polling, pagination, and responsive presentation values.
- Internal module and migration boundaries that preserve Phase 1 contracts.

## Deferred Ideas

- Last-10 and conference-only statistics remain a Phase 3 todo.
- Detailed statistics, detailed Excel, and Power BI remain in their existing later phases.
