---
phase: 01-canonical-snapshot-foundation
fixed_at: 2026-11-11T18:28:09Z
review_path: .planning/phases/01-canonical-snapshot-foundation/01-REVIEW.md
iteration: 2
findings_in_scope: 1
fixed: 1
skipped: 0
status: all_fixed
---

# Phase 1: Code Review Fix Report

**Fixed at:** 2026-11-11T18:28:09Z
**Source review:** `.planning/phases/01-canonical-snapshot-foundation/01-REVIEW.md`
**Iteration:** 2

**Summary:**

- Findings in scope: 1
- Fixed: 1
- Skipped: 0

## Fixed Issues

### WR-01: Public-origin host canonicalization still differs from browsers

**Files modified:** `app.py`, `tests/test_app_routes.py`
**Commits:** `4bf0631`, `d6d8c25`
**Status:** fixed: requires human verification
**Applied fix:** DNS hosts are lowercased and converted to ASCII IDNA, IPv6 hosts are compressed and bracketed with `ipaddress`, canonical dotted IPv4 is accepted, and ambiguous numeric IPv4 spellings fail startup with a fixed safe error. Existing browser-compatible default-port stripping and non-default-port retention remain unchanged. Clean-process route coverage proves Unicode IDN, expanded IPv6, dotted IPv4, default ports, and port 8443 match browser `Origin` serialization while shorthand decimal and hexadecimal IPv4 forms are rejected.

---

_Fixed: 2026-11-11T18:28:09Z_
_Fixer: the agent (gsd-code-fixer)_
_Iteration: 2_
