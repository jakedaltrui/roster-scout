---
phase: 2
slug: identity-organizations-and-report-library
status: approved
nyquist_compliant: true
wave_0_complete: false
created: 2026-11-11
---

# Phase 2 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | Python standard `unittest` with PostgreSQL integration; Node built-in `node:test` for browser behavior |
| **Config file** | `tests/db_support.py`; database tests require `DATABASE_URL` naming a PostgreSQL database ending in `_test` |
| **Quick run command** | `DATABASE_URL=postgresql+psycopg://roster_scout:roster_scout@127.0.0.1:5432/roster_scout_test .venv/bin/python -m unittest tests.test_identity tests.test_organizations tests.test_report_library -v` |
| **Full suite command** | `DATABASE_URL=postgresql+psycopg://roster_scout:roster_scout@127.0.0.1:5432/roster_scout_test .venv/bin/python -m unittest discover -s tests -v` followed by `node --test tests/js/*.test.mjs` |
| **Estimated runtime** | Focused suite under 90 seconds; full suite under 4 minutes |

## Sampling Rate

- **After every task commit:** Run the focused test class named by that task plus `git diff --check`.
- **After every plan wave:** Run all Phase 2 Python tests and `node --test tests/js/*.test.mjs`.
- **Before verification:** Run full Python and Node suites, catalog validation, migration lifecycle/drift checks, and the Phase 2 release story.
- **Max feedback latency:** 90 seconds for focused task feedback.

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 02-W0-01 | Wave 0 | 0 | AUTH-01 | T-AUTH-GATE | Public allowlist only; every product route requires a current local session and active membership | route matrix | `.venv/bin/python -m unittest tests.test_identity.AuthGateRouteMatrixTests -v` | ❌ W0 | ⬜ pending |
| 02-W0-02 | Wave 0 | 0 | AUTH-02 | T-AUTH-REPLAY | Passwordless state/code is bounded, one-time, throttled, and sanitized | adapter/integration | `.venv/bin/python -m unittest tests.test_identity.PasswordlessBoundaryTests -v` | ❌ W0 | ⬜ pending |
| 02-W0-03 | Wave 0 | 0 | AUTH-03 | T-IDENTITY-LINK | Verified provider identities converge without unverified-email takeover | DB integration | `.venv/bin/python -m unittest tests.test_identity.ExternalIdentityLinkingTests -v` | ❌ W0 | ⬜ pending |
| 02-W0-04 | Wave 0 | 0 | AUTH-04 | T-SSO-JIT | SAML/OIDC success never bypasses local invitation membership | emulator/two-org | `.venv/bin/python -m unittest tests.test_identity.EnterpriseSsoAuthorizationTests -v` | ❌ W0 | ⬜ pending |
| 02-W0-05 | Wave 0 | 0 | AUTH-05 | T-SESSION | Rotation, idle/absolute expiry, transient refresh, revocation, and logout are server-enforced | integration | `.venv/bin/python -m unittest tests.test_identity.ServerSessionLifecycleTests -v` | ❌ W0 | ⬜ pending |
| 02-W0-06 | Wave 0 | 0 | ORG-01 | T-TENANT-OWNER | Invitations and every report resource have one organization owner | migration/schema | `.venv/bin/python -m unittest tests.test_organizations.OrganizationOwnershipSchemaTests -v` | ❌ W0 | ⬜ pending |
| 02-W0-07 | Wave 0 | 0 | ORG-02 | T-INVITE-INPUT | Admin may invite any valid normalized address without domain authorization | service/route | `.venv/bin/python -m unittest tests.test_organizations.InvitationCreationTests -v` | ❌ W0 | ⬜ pending |
| 02-W0-08 | Wave 0 | 0 | ORG-03 | T-INVITE-BIND | Exact-email acceptance grants only the inviting organization | two-org integration | `.venv/bin/python -m unittest tests.test_organizations.InvitationAcceptanceTests -v` | ❌ W0 | ⬜ pending |
| 02-W0-09 | Wave 0 | 0 | ORG-04 | T-ROLE | Admin/member capabilities are deny-by-default | table-driven | `.venv/bin/python -m unittest tests.test_organizations.RoleAuthorizationTests -v` | ❌ W0 | ⬜ pending |
| 02-W0-10 | Wave 0 | 0 | ORG-05 | T-LAST-ADMIN | Admin mutation is serialized and cannot leave zero active admins | DB concurrency | `.venv/bin/python -m unittest tests.test_organizations.MembershipAdministrationTests -v` | ❌ W0 | ⬜ pending |
| 02-W0-11 | Wave 0 | 0 | ORG-06 | T-LIBRARY-SCOPE | Active members share only their organization library/history | route integration | `.venv/bin/python -m unittest tests.test_report_library.SharedLibraryTests -v` | ❌ W0 | ⬜ pending |
| 02-W0-12 | Wave 0 | 0 | ORG-07 | T-DOMAIN-JOIN | Domain/provider organization claims never create local membership | negative integration | `.venv/bin/python -m unittest tests.test_organizations.NoDomainAutoJoinTests -v` | ❌ W0 | ⬜ pending |
| 02-W0-13 | Wave 0 | 0 | SNAP-05 | T-REFRESH-LINEAGE | Explicit refresh creates an immutable successor and preserves the prior snapshot | job integration | `.venv/bin/python -m unittest tests.test_report_library.RefreshLineageTests -v` | ❌ W0 | ⬜ pending |
| 02-W0-14 | Wave 0 | 0 | SNAP-06 | T-HISTORY | History identifies newest ready/partial while active refresh remains separate | repository/route | `.venv/bin/python -m unittest tests.test_report_library.SnapshotHistoryTests -v` | ❌ W0 | ⬜ pending |
| 02-W0-15 | Wave 0 | 0 | SNAP-07 | T-IDEMPOTENCY | Duplicate/concurrent clicks converge on one job, snapshot, and usage event | DB concurrency | `.venv/bin/python -m unittest tests.test_report_library.ReportAcceptanceIdempotencyTests -v` | ❌ W0 | ⬜ pending |
| 02-W0-16 | Wave 0 | 0 | SNAP-08 | T-RETRY | Failed builds never publish ready and retry preserves failed-attempt auditability | job integration | `.venv/bin/python -m unittest tests.test_report_library.FailedBuildRetryTests -v` | ❌ W0 | ⬜ pending |
| 02-W0-17 | Wave 0 | 0 | SECU-01 | T-IDOR | Foreign and absent resource IDs return the same 404 on every surface | two-org matrix | `.venv/bin/python -m unittest tests.test_phase2_tenant_isolation.CrossOrganizationDenialTests -v` | ❌ W0 | ⬜ pending |
| 02-W0-18 | Wave 0 | 0 | SECU-02 | T-UNTRUSTED-SCOPE | Browser-supplied org/account/role fields are ignored | negative integration | `.venv/bin/python -m unittest tests.test_phase2_tenant_isolation.UntrustedScopeInputTests -v` | ❌ W0 | ⬜ pending |
| 02-W0-19 | Wave 0 | 0 | SECU-03 | T-SECRET-LOG | Tokens, codes, assertions, cookies, raw email, and exceptions never enter logs | log capture | `.venv/bin/python -m unittest tests.test_identity.SecretRedactionTests -v` | ❌ W0 | ⬜ pending |
| 02-W0-20 | Wave 0 | 0 | SECU-04 | T-AUDIT | Allowlisted append-only events cover membership, report, refresh, retry, access, download, and denial | DB integration | `.venv/bin/python -m unittest tests.test_phase2_tenant_isolation.SecurityAuditEventTests -v` | ❌ W0 | ⬜ pending |
| 02-W0-21 | Wave 0 | 0 | SECU-05 | T-FAILURE-STATE | Identity/database/queue/source/artifact failures map to distinct fixed states | fault injection | `.venv/bin/python -m unittest tests.test_phase2_release.ExternalFailureStateTests -v` | ❌ W0 | ⬜ pending |

## Wave 0 Requirements

- [ ] `tests/test_identity.py` — provider fake, callback state, identity linking, session/logout, secret redaction, and provider failures.
- [ ] `tests/test_organizations.py` — ownership schema, invitations, roles, last-admin concurrency, and no-domain-auto-join.
- [ ] `tests/test_report_library.py` — scoped list/detail, atomic acceptance/usage, refresh, history, partial, retry, and artifacts.
- [ ] `tests/test_phase2_tenant_isolation.py` — two-organization substitution matrix and security audit assertions.
- [ ] `tests/test_phase2_release.py` — complete Phase 2 story, failure categories, migrations, and compatibility regressions.
- [ ] `tests/js/report_status.test.mjs` — bounded polling, hidden-tab pause, terminal stop, retry/partial states, and responsive equivalence.
- [ ] `tests/fixtures/workos-emulate.config.yaml` — deterministic users, organizations, SSO connection, callback/session, and failure fixtures.
- [ ] Pin `workos` and `Flask-WTF`; keep emulator and WorkOS staging smoke tests opt-in when the tool or credentials are unavailable.

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Hosted passwordless email delivery and branding | AUTH-02 | Requires a provisioned WorkOS staging environment and inbox | Request a code for a reserved staging address, confirm fixed expiry/copy, complete once, verify replay rejection, and inspect that no secret appears in logs. |
| Google and Microsoft buttons | AUTH-03 | Provider dashboard configuration is external state | In WorkOS staging, complete each provider flow and verify both map to the expected canonical test account without creating membership. |
| Test IdP SAML/OIDC connection | AUTH-04 | Requires WorkOS staging Test IdP | Authenticate through Test IdP, verify an invited account enters only its local organization, then verify an uninvited same-domain account is denied. |

## Validation Sign-Off

- [x] All requirements have a focused automated test target.
- [x] Sampling continuity prevents three consecutive tasks without automated verification.
- [x] Wave 0 identifies every missing test fixture and dependency.
- [x] Commands are non-watch and deterministic by default.
- [x] Deterministic feedback latency target is under 90 seconds per task.
- [x] `nyquist_compliant: true` is set in frontmatter.

**Approval:** approved 2026-11-11
