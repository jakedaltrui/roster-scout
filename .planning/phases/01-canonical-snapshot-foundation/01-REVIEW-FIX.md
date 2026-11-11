---
phase: 01-canonical-snapshot-foundation
fixed_at: 2026-11-11T18:44:56Z
review_path: .planning/phases/01-canonical-snapshot-foundation/01-REVIEW.md
iteration: 3
findings_in_scope: 1
fixed: 1
skipped: 0
status: all_fixed
---

# Phase 1: Code Review Fix Report

**Fixed at:** 2026-11-11T18:44:56Z
**Source review:** `.planning/phases/01-canonical-snapshot-foundation/01-REVIEW.md`
**Iteration:** 3

**Summary:**

- Findings in scope: 1
- Fixed: 1
- Skipped: 0

## Fixed Issues

### WR-01: Public-origin host canonicalization still differs from browsers

**Files modified:** `app.py`, `DEPLOYMENT.md`, `tests/test_app_routes.py`, `tests/test_deployment_config.py`
**Commits:** `310c456`, `1b39a62`
**Status:** fixed: requires human verification
**Applied fix:** Configured DNS hosts must now be ASCII. Operators provide internationalized domains in their canonical A-label/Punycode form, avoiding the standard library's browser-incompatible IDNA 2003 mapping. The validator accepts canonical ASCII custom domains such as `xn--fa-hia.de`, rejects Unicode hostnames and detectable malformed A-labels with the existing fixed safe error, and preserves lowercase DNS, strict dotted IPv4, compressed/bracketed IPv6, default-port stripping, and non-default ports. Regression tests and the full PostgreSQL-backed Phase 1 release validator pass.

---

_Fixed: 2026-11-11T18:44:56Z_
_Fixer: the agent (gsd-code-fixer)_
_Iteration: 3_
