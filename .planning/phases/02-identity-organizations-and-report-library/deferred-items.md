# Deferred Items

## Time-dependent identity route fixtures

- **Discovered during:** Plan 02-08 full-suite verification on 2026-11-12.
- **Scope:** Pre-existing tests in `tests/test_identity.py` and `tests/test_organizations.py`.
- **Evidence:** Full discovery ran 513 tests; 11 identity/organization route tests received the expected login redirect instead of their post-auth response. Their shared fixture creates local sessions at the fixed time `2026-11-11T19:00:00Z`, which is now beyond the production 12-hour idle-session limit.
- **Why deferred:** The failure is unrelated to refresh, snapshot history, or any file behavior introduced by Plan 02-08. Changing identity fixture time semantics belongs to the identity test boundary.
- **Suggested correction:** Freeze request-time resolution to the fixture clock or create browser-route sessions relative to the actual test clock while retaining separate exact-boundary session tests.

