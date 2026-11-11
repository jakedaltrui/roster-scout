---
phase: 02-identity-organizations-and-report-library
plan: "01"
subsystem: identity
tags: [authentication, postgresql, sqlalchemy, alembic, opaque-sessions]
requires:
  - phase: 01-canonical-snapshot-foundation
    provides: connection-free persistence and caller-owned transaction patterns
provides:
  - canonical local Account and verified ExternalIdentity schema
  - replay-safe hashed AuthTransaction callback state
  - opaque revocable AccountSession lifecycle with fixed cookie contract
  - deterministic provider-neutral identity fake and sanitized failure boundary
affects: [02-13-auth-browser-composition, 02-02-identity-backend, organizations, authorization]
tech-stack:
  added: []
  patterns: [provider-auth-local-account, hashed-bearer-verifier, caller-owned-transactions]
key-files:
  created:
    - migrations/versions/0015_phase2_identity_accounts.py
    - roster_scout/identity.py
    - roster_scout/accounts.py
    - roster_scout/sessions.py
    - tests/test_identity.py
  modified:
    - roster_scout/models.py
key-decisions:
  - "Provider subjects resolve before email; a matching verified email links only when the provider explicitly approves linking."
  - "Callback state and browser session verifiers are persisted only as SHA-256 digests."
  - "Local sessions use a 12-hour sliding idle lifetime, a 7-day absolute lifetime, and the __Host-rs_session cookie contract."
patterns-established:
  - "Identity services remain connection-free and require a caller-owned SQLAlchemy transaction."
  - "Provider exceptions cross the application boundary only as fixed safe categories."
requirements-completed: [AUTH-01, AUTH-02, AUTH-03, AUTH-05, SECU-03]
duration: 14 min
completed: 2026-11-11
---

# Phase 2 Plan 1: Identity Account and Session Foundation Summary

**Canonical local accounts with verified provider-subject linking, one-time hashed callback state, and revocable opaque server sessions backed by PostgreSQL**

## Performance

- **Duration:** 14 min
- **Started:** 2026-11-11T22:12:00Z
- **Completed:** 2026-11-11T22:26:06Z
- **Tasks:** 2
- **Files modified:** 6

## Accomplishments

- Added a reversible 0015 migration for canonical accounts, external identities, one-time authentication transactions, and opaque local sessions without changing Phase 1 data semantics.
- Defined a provider-neutral identity protocol and deterministic fake, with provider exceptions reduced to fixed redacted categories.
- Enforced verified issuer/subject-first account linking so email alone cannot claim an existing account.
- Stored only session lookup IDs and verifier digests, with authentication rotation, revocation, 12-hour idle expiry, 7-day absolute expiry, and an exact secure host-only cookie contract.
- Added 14 focused identity/schema tests and passed the complete 443-test repository suite plus NCAA catalog validation.

## Task Commits

| Task | Commit | Description |
|---|---|---|
| 1 | `2c80ad6` | Define the failing identity, linking, and session security contracts. |
| 2 | `49c717d` | Add the canonical identity schema and connection-free services. |

## Files Created/Modified

- `tests/test_identity.py` - Provider-neutral callback, account-linking, session, redaction, and schema contracts.
- `migrations/versions/0015_phase2_identity_accounts.py` - Reversible additive identity-only migration from revision 0014.
- `roster_scout/models.py` - Account, ExternalIdentity, AuthTransaction, and AccountSession ORM models.
- `roster_scout/identity.py` - Managed-provider protocol/fake, hashed state lifecycle, and sanitized exchange boundary.
- `roster_scout/accounts.py` - Verified canonical account linking and email normalization.
- `roster_scout/sessions.py` - Opaque verifier creation, constant-time lookup, rotation, expiry, and revocation.

## Decisions Made

- Provider issuer/subject and provider user ID are the authoritative external identity keys; normalized email is only a verified linking input.
- Existing email accounts require an explicit provider-supported linking approval, while a first verified identity may create the canonical account.
- The browser cookie contains `lookup_id.verifier`; PostgreSQL stores only the SHA-256 verifier digest and sealed provider session state.
- Session cookie flags are `Secure`, `HttpOnly`, `SameSite=Lax`, `Path=/`, with no Domain attribute.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

- The local PostgreSQL test database required approved local-network access from the managed execution environment; tests passed once run with that access.

## User Setup Required

None - this slice uses a deterministic provider fake. WorkOS credentials and production provider configuration belong to later Phase 2 plans and deployment checkpoints.

## Next Phase Readiness

- Plan 02-13 can compose sign-in/callback routes against the provider-neutral state, account, and session services.
- Plan 02-02 can add the real WorkOS adapter, resilient provider refresh handling, memberships, and authorization without replacing the canonical account/session model.
- Production identity remains intentionally unconfigured and production deployment was not touched.

## Self-Check: PASSED

- All six planned source/test artifacts exist.
- Task commits `2c80ad6` and `49c717d` are present in repository history.
- Focused identity tests, reversible migration lifecycle, Alembic drift check, compilation, all 443 repository tests, catalog validation, and `git diff --check` passed.

---
*Phase: 02-identity-organizations-and-report-library*
*Completed: 2026-11-11*
