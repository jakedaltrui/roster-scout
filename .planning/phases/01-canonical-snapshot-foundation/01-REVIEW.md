---
phase: 01-canonical-snapshot-foundation
reviewed: 2026-11-11T18:34:45Z
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

**Reviewed:** 2026-11-11T18:34:45Z
**Depth:** standard
**Files Reviewed:** 13
**Status:** issues_found

## Summary

The publication deadlock is fixed. `publish_snapshot` now takes PostgreSQL `FOR NO KEY UPDATE` on the parent snapshot row after the snapshot advisory lock, which is compatible with the `KEY SHARE` acquired by a concurrent child foreign-key check. The deterministic two-connection regression completes without SQLSTATE `40P01` or `55P03`, while the existing mutation-first and publication-first schedules continue to serialize correctly.

Default-port canonicalization is also fixed: HTTPS `:443` and HTTP `:80` are removed, while non-default ports remain part of the trusted origin. Browser-host handling now correctly compresses IPv6 and rejects noncanonical shorthand/numeric IPv4 forms. Unicode DNS handling remains incomplete, however, because Python's built-in `idna` codec applies IDNA2003 mappings that differ from browser WHATWG/UTS46 serialization.

All five original round-five verification gaps remain closed: global Player serialization, Fly public-origin selection across the public-HTTPS/internal-HTTP boundary, definitive acknowledgement handling, browser-storage fallback, and committed-range release validation. The focused re-review ran 56 Python tests covering the global locks and migration lifecycle, publication interleavings, origin routes/configuration, template contract, and release gate, plus all 16 Node download-state-machine tests. Both `git diff --check 95776ed..HEAD --` and the working-tree diff check passed.

## Narrative Findings (AI reviewer)

## Warnings

### WR-01: Built-in IDNA2003 canonicalization disagrees with browser origins

**Classification:** WARNING

**File:** `/Users/jakedaltrui/Library/Mobile Documents/com~apple~CloudDocs/projects/axis-baseball/roster-scout/app.py:138-140`

**Issue:** `_canonical_public_hostname` uses Python's built-in `hostname.encode("idna")`, which implements the older IDNA2003 mapping rather than the WHATWG/UTS46 behavior browsers use when serializing an `Origin`. A shared runtime configured with the accepted origin `https://faß.de` stores `https://fass.de`, while a browser sends `Origin: https://xn--fa-hia.de`; a clean-process route probe therefore returns HTTP 403 for a valid same-origin acknowledgement. Other divergent cases include Greek final sigma (`ς.gr`) and joiner characters that the built-in codec silently removes even though browser URL parsing rejects them. The current `bücher.example` regression does not expose the problem because that hostname happens to serialize identically under both rule sets. This does not affect the checked-in ASCII Fly hostname, but it makes some otherwise-valid IDN custom domains unusable for download acknowledgement.

**Fix:** Canonicalize DNS names with an explicitly declared UTS46-capable dependency and add browser-parity route tests for divergent mappings and invalid contextual characters. Alternatively, reject every non-ASCII configured hostname and require the operator to supply an already-canonical A-label.

```python
import idna

try:
    canonical_dns = idna.encode(
        hostname,
        uts46=True,
        std3_rules=True,
    ).decode("ascii").lower()
except idna.IDNAError as exc:
    raise RuntimeError("Roster Scout public origin is invalid.") from exc
```

Declare `idna` directly in `requirements.txt` if it is imported by application code, then cover at least `faß.de` -> `xn--fa-hia.de`, Greek final sigma, and rejection of invalid joiner input in a clean-process acknowledgement-route test.

---

_Reviewed: 2026-11-11T18:34:45Z_
_Reviewer: the agent (gsd-code-reviewer)_
_Depth: standard_
