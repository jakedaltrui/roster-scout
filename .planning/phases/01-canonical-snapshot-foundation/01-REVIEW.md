---
phase: 01-canonical-snapshot-foundation
reviewed: 2026-11-11T15:52:49Z
depth: standard
files_reviewed: 19
files_reviewed_list:
  - migrations/versions/0013_authoritative_identity_serialization.py
  - roster_scout/snapshots.py
  - tests/test_snapshot_persistence.py
  - tests/test_canonical_publish.py
  - roster_scraper.py
  - tests/test_ncaa_workflow.py
  - tests/test_browserless_lifecycle.py
  - roster_scout/usage.py
  - app.py
  - tests/test_usage_metrics.py
  - tests/test_app_routes.py
  - static/download_tracking.js
  - tests/js/download_tracking.test.mjs
  - templates/index.html
  - roster_scout/evidence.py
  - tests/test_evidence.py
  - scripts/validate_phase1_release.py
  - tests/test_deployment_config.py
  - DEPLOYMENT.md
findings:
  critical: 3
  warning: 2
  info: 0
  total: 5
status: issues_found
---

# Phase 1: Code Review Report

**Reviewed:** 2026-11-11T15:52:49Z
**Depth:** standard
**Files Reviewed:** 19
**Status:** issues_found

## Summary

The round-four work closes the six previously recorded counterexamples for the tested schedules, but it is not ready to ship. The acknowledgement endpoint rejects the real HTTPS origin when Flask runs behind Fly's TLS-terminating proxy, the browser treats every acknowledgement HTTP failure as ambiguous and can block reports for the full proof lifetime, and the authoritative identity protocol still has a write-skew schedule when the tenant organization is created concurrently. Two additional robustness gaps leave storage-restricted browsers unusable and let the release validator's diff gate pass without inspecting any committed change.

No implementation file was modified by this review.

## Narrative Findings (AI reviewer)

## Critical Issues

### CR-01 [BLOCKER]: Fly HTTPS acknowledgements are rejected as cross-origin

**Files:** `app.py:329-331`; `fly.toml:46-49`

**Issue:** The endpoint constructs its expected origin from `request.scheme` and `request.host`. Fly terminates HTTPS at its edge and forwards the request to Gunicorn over the configured plain HTTP `internal_port`; the application installs neither `ProxyFix` nor an equivalent trusted-proxy scheme resolver. The browser therefore sends `Origin: https://roster-scout.fly.dev`, while Flask sees the internal request scheme as `http` and expects `http://roster-scout.fly.dev`. A direct reproduction with `X-Forwarded-Proto: https` returns 403 because that header is ignored. Every production workbook can start downloading, but its acknowledgement is rejected and no durable successful-download event is recorded.

**Fix:** Establish one trusted external-origin contract. On Fly, either wrap the app with a one-hop `ProxyFix` configured for the platform's forwarded scheme/host headers, derive the origin from Fly's trusted forwarded-proto header, or compare against a validated configured public origin. Do not trust arbitrary forwarded headers when running directly. Add a route test that models the Fly hop (`http` internal request, public HTTPS Origin, trusted forwarded proto) and requires 200/202.

### CR-02 [BLOCKER]: Definitive acknowledgement failures trap the browser in `needs_ack`

**Files:** `templates/index.html:1289-1300`; `static/download_tracking.js:190-203,230-239`

**Issue:** `postDownloadAcknowledgement()` maps every non-2xx response to the same `{acknowledged: false}` result. The state machine then preserves `needs_ack` for all such responses and every later submission retries that acknowledgement before refusing to start another report. Retaining state is correct for a timeout, network error, or 5xx, but a 400/403 response is definitive: an expired/invalid proof or rejected origin cannot succeed on retry. The current implementation repeatedly sends the same invalid proof and blocks all new reports until the client-side 15-minute TTL expires. This contradicts the plan's explicit requirement to retain only ambiguous/network/5xx acknowledgement failures and allocate a new UUID after definitive failure.

**Fix:** Return a classified acknowledgement result from the template callback (success, retryable ambiguity/5xx, or definitive 4xx). Add a state-machine operation that clears the matching `needs_ack` record on a definitive acknowledgement failure, then require a later explicit submission to allocate a fresh UUID. Add Node tests for 400/403 versus 500/network outcomes and a template contract that preserves the HTTP status classification.

### CR-03 [BLOCKER]: A concurrently created organization bypasses the Player identity lock

**File:** `migrations/versions/0013_authoritative_identity_serialization.py:292-310`

**Issue:** A direct Player source-ID update tries to cover invisible PlayerTeamSeason rows by cross-joining the changed Player with every *visible* organization and taking each `(organization_id, player_id)` advisory lock. That is still visibility-dependent. A transaction can create a new organization, its report/snapshot/PlayerTeamSeason, and an authoritative SourceRecord for Player ID `42`; its source trigger locks `(new_org, player)`. Concurrently, another transaction updates the existing global Player to ID `99`. Because `new_org` is uncommitted, the Player trigger's cross join cannot see it and takes no matching advisory lock. The source transaction validates the old Player value, the Player transaction cannot see the new source row, and both can commit the same `42 != 99` mismatch that revision 0013 is intended to make impossible. The tests create the organization before the two workers, so they do not exercise this schedule.

**Fix:** Add a visibility-independent global Player lock keyed only by Player UUID and acquire it on every authoritative source, Player, PlayerTeamSeason, and publication path before the tenant-scoped Player locks (while retaining the documented snapshot-first ordering), or otherwise enforce that tenant creation cannot race these paths. Add a real two-connection regression where the organization/snapshot/identity/source are all created in the uncommitted source transaction while the other connection mutates the global Player.

## Warnings

### WR-01 [WARNING]: Unavailable session storage prevents the form from being initialized

**Files:** `templates/index.html:1281-1287`; `static/download_tracking.js:40-49`

**Issue:** The page passes `sessionStorage` directly into the tracker at top level, and every `getItem`, `setItem`, and `removeItem` call is unguarded. Browsers can expose the Storage interface but throw `SecurityError` for access (privacy policy, sandboxing, disabled storage), and writes can throw quota errors. An exception during tracker construction or the first load/save aborts the enclosing script before the submit listener is installed, so the core workbook form becomes unusable. The page already treats local-storage availability as optional for theme state, but the new download path does not provide the same resilience.

**Fix:** Wrap storage acquisition and all operations in a safe adapter, with an in-memory per-page fallback when session storage is unavailable. Reload retry can be best-effort in that mode, but current-page workbook generation and acknowledgement must continue. Add Node tests using a storage object whose methods throw and a browser/template test proving the submit handler still initializes.

### WR-02 [WARNING]: The release diff gate checks only an already-clean working tree

**File:** `scripts/validate_phase1_release.py:257-260`

**Issue:** The final command is `git diff --check` without a commit range or `--cached`. The execution workflow commits each plan before running the final validator, so the tracked working tree is clean and this command inspects none of the release's committed changes. A whitespace error committed in any round-four file therefore produces a green “Git diff hygiene” gate. The test suite asserts command ordering but not that the command has a nonempty, release-scoped range.

**Fix:** Resolve and validate an explicit release/phase base commit, then run `git diff --check <base>..HEAD` (and optionally a second working-tree check). Add a release-manifest test that requires the range and a fixture/repository probe demonstrating a committed whitespace defect fails the gate.

---

_Reviewed: 2026-11-11T15:52:49Z_
_Reviewer: the agent (gsd-code-reviewer)_
_Depth: standard_
