---
phase: 01-canonical-snapshot-foundation
reviewed: 2026-11-11T18:18:10Z
depth: standard
files_reviewed: 13
files_reviewed_list:
  - migrations/versions/0014_global_authoritative_player_serialization.py
  - roster_scout/snapshots.py
  - tests/test_snapshot_persistence.py
  - tests/test_canonical_publish.py
  - app.py
  - fly.toml
  - DEPLOYMENT.md
  - tests/test_app_routes.py
  - tests/test_deployment_config.py
  - static/download_tracking.js
  - templates/index.html
  - tests/js/download_tracking.test.mjs
  - scripts/validate_phase1_release.py
findings:
  critical: 0
  warning: 1
  info: 0
  total: 1
status: issues_found
---

# Phase 1: Code Review Report

**Reviewed:** 2026-11-11T18:18:10Z
**Depth:** standard
**Files Reviewed:** 13
**Status:** issues_found

## Summary

The two prior findings are fixed. Publication now uses PostgreSQL `FOR NO KEY UPDATE`, so the real child foreign-key `KEY SHARE`/snapshot-advisory interleaving completes without SQLSTATE `40P01` or `55P03`, while the mutation-before-publication and publication-before-mutation schedules still serialize correctly. Configured HTTPS `:443` and HTTP `:80` origins now reduce to the browser's portless form, and non-default `:8443` remains part of the origin.

All five earlier round-five gaps remain closed: global Player serialization, Fly public-origin selection, definitive acknowledgement handling, browser-storage fallback, and committed-range release validation. Focused evidence included 15 publication serialization tests, 17 global-lock/migration tests, 5 public-origin tests, 4 template acknowledgement-contract tests, 16 Node state-machine tests, 12 release-validator tests, and both committed-range and working-tree `git diff --check`.

One narrower accepted-but-unusable public-origin form remains: hostnames are lowercased but are not canonicalized using browser/WHATWG host rules. This does not affect the checked-in Fly hostname, but it breaks acknowledgement tracking for valid IDN custom domains and other noncanonical host spellings.

## Narrative Findings (AI reviewer)

## Warnings

### WR-01: Public-origin host canonicalization still differs from browsers

**Classification:** WARNING

**File:** `/Users/jakedaltrui/Library/Mobile Documents/com~apple~CloudDocs/projects/axis-baseball/roster-scout/app.py:109-116`

**Issue:** `_canonical_public_origin` only lowercases `parsed.hostname`. Browsers additionally apply WHATWG host canonicalization, including IDNA/Punycode conversion, IPv6 compression, and normalization of legacy numeric IPv4 spellings. For example, the accepted configuration `https://bücher.example` is stored as that exact Unicode origin, while a browser sends `Origin: https://xn--bcher-kva.example`; the exact comparison at `app.py:391` therefore returns HTTP 403 for every valid acknowledgement. The same mismatch occurs for accepted values such as `https://[0:0:0:0:0:0:0:1]` (browser origin `https://[::1]`) and `https://127.1` (browser origin `https://127.0.0.1`).

**Fix:** Canonicalize the hostname according to browser URL rules before constructing `_DOWNLOAD_ACK_PUBLIC_ORIGIN`, or fail startup unless the configured hostname is already in the exact canonical ASCII form browsers emit. At minimum, convert DNS names through IDNA, canonicalize IPv6 through `ipaddress.IPv6Address(...).compressed`, reject noncanonical numeric IPv4 spellings, and add clean-process/route tests that prove the configured form matches the browser-origin form.

```python
import ipaddress

try:
    address = ipaddress.ip_address(hostname)
except ValueError:
    canonical_host = hostname.encode("idna").decode("ascii").lower()
else:
    canonical_host = address.compressed
    if address.version == 6:
        canonical_host = f"[{canonical_host}]"
```

---

_Reviewed: 2026-11-11T18:18:10Z_
_Reviewer: the agent (gsd-code-reviewer)_
_Depth: standard_
