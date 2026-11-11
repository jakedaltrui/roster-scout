---
phase: 01-canonical-snapshot-foundation
fixed_at: 2026-11-11T19:02:53Z
review_path: .planning/phases/01-canonical-snapshot-foundation/01-REVIEW.md
iteration: 4
findings_in_scope: 1
fixed: 1
skipped: 0
status: all_fixed
---

# Phase 1: Code Review Fix Report

**Fixed at:** 2026-11-11T19:02:53Z
**Source review:** `.planning/phases/01-canonical-snapshot-foundation/01-REVIEW.md`
**Iteration:** 4

**Summary:**

- Findings in scope: 1
- Fixed: 1
- Skipped: 0

## Fixed Issues

### WR-01: Punycode round-trip accepts A-labels browsers reject

**Files modified:** `requirements.txt`, `app.py`, `DEPLOYMENT.md`, `tests/test_app_routes.py`, `tests/test_deployment_config.py`
**Commits:** `9bb3345`, `daf67e4`
**Status:** fixed: requires human verification
**Applied fix:** Declared and pinned `idna==3.20` as a direct runtime dependency, then replaced the raw standard-library Punycode checks with UTS46 and STD3 canonicalization. Configured Unicode DNS names now serialize to the exact lowercase ASCII A-label form used in browser `Origin` headers, and every A-label is decoded and re-encoded under the same rules before it enters the trusted-origin comparison. Clean-process regressions cover `faß.de`, `bücher.example`, Greek final sigma, canonical A-label input, malformed `xn--a-ecp.example`, normalization-invalid `xn--a-ccb`, and an invalid joiner context. The existing fixed startup error remains sanitized, while IPv6, strict IPv4, default and non-default ports, exact-origin route matching, and shared-runtime fail-closed behavior remain intact.

**Verification:** Focused origin/dependency/runbook tests passed (13 tests). The PostgreSQL-backed `scripts/validate_phase1_release.py` gate passed: 95 focused Python tests, 16 Node tests, 4 revision-0014 migration tests, 429 clean-process tests, 942 valid catalog teams, 64 deployment-contract tests, the two-sheet workbook check, full downgrade/base-to-head/re-upgrade and drift checks, Python compilation, required templates/contracts, and committed-range plus working-tree diff hygiene.

---

_Fixed: 2026-11-11T19:02:53Z_
_Fixer: the agent (gsd-code-fixer)_
_Iteration: 4_
