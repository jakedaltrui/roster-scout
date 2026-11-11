---
phase: 01-canonical-snapshot-foundation
reviewed: 2026-11-11T18:51:28Z
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

**Reviewed:** 2026-11-11T18:51:28Z
**Depth:** standard
**Files Reviewed:** 13
**Status:** issues_found

## Summary

The publication deadlock is closed without weakening serialization. Publication now takes the snapshot advisory lock and a PostgreSQL `FOR NO KEY UPDATE` row lock, which remains incompatible with competing snapshot updates but is compatible with the `KEY SHARE` used by child foreign-key checks. The deterministic deadlock regression and the existing mutation-first/publication-first interleavings all pass.

The configured-origin fixes also cover the reported cases: HTTP `:80` and HTTPS `:443` are stripped, non-default ports are retained, IPv6 is compressed and bracketed, noncanonical numeric IPv4 is rejected, every non-ASCII configured hostname is rejected, and canonical ASCII A-labels including `xn--fa-hia.de` are accepted. The deployment guide matches that operator contract. One malformed-A-label edge remains: the custom standard-library Punycode test does not enforce all browser IDNA validity rules and therefore accepts some hostnames a browser rejects before navigation.

All five original round-five gaps remain closed: global Player serialization, public-HTTPS/internal-HTTP Fly origin selection, definitive acknowledgement handling, browser-storage fallback, and committed-range release validation. Focused verification passed 32 PostgreSQL concurrency/migration tests, 8 public-origin/deployment tests, and all 16 Node download-state-machine tests. Both `git diff --check 95776ed..HEAD --` and the working-tree diff check passed.

## Narrative Findings (AI reviewer)

## Warnings

### WR-01: Punycode round-trip accepts A-labels browsers reject

**Classification:** WARNING

**File:** `/Users/jakedaltrui/Library/Mobile Documents/com~apple~CloudDocs/projects/axis-baseball/roster-scout/app.py:164-187`

**Issue:** `_valid_ascii_dns_label` proves only that an `xn--` payload round-trips through the raw Punycode codec and avoids a small set of Unicode categories. Punycode encoding validity is weaker than the browser's IDNA/WHATWG host validity rules: it does not enforce normalization, contextual, or disallowed-code-point checks. For example, a clean process accepts `ROSTER_SCOUT_PUBLIC_ORIGIN=https://xn--a-ecp.example` and stores that exact trusted origin, while Node's WHATWG `new URL('https://xn--a-ecp.example')` rejects the URL as invalid. Similar normalization-invalid labels such as `xn--a-ccb` also pass this helper. A typo or invalid custom-domain value can therefore pass startup validation even though no browser can navigate to the origin or emit its acknowledgement header, leaving workbook download acknowledgements unusable. The checked-in Fly hostname and the required `xn--fa-hia.de` case are unaffected.

**Fix:** Validate each A-label with an explicitly declared IDNA implementation that enforces UTS46/STD3 rules, then require the re-encoded ASCII label to equal the configured lowercase label. Add clean-process regressions for `xn--a-ecp.example` and a normalization-invalid A-label while retaining the `xn--fa-hia.de` acceptance case. Declare `idna` directly in `requirements.txt` rather than relying on a transitive dependency.

```python
import idna

def _valid_ascii_dns_label(label: str) -> bool:
    if not label.startswith("xn--"):
        return True
    try:
        decoded = idna.decode(label, uts46=True, std3_rules=True)
        canonical = idna.encode(
            decoded,
            uts46=True,
            std3_rules=True,
        ).decode("ascii")
    except idna.IDNAError:
        return False
    return canonical == label
```

---

_Reviewed: 2026-11-11T18:51:28Z_
_Reviewer: the agent (gsd-code-reviewer)_
_Depth: standard_
