---
phase: 01-canonical-snapshot-foundation
reviewed: 2026-11-11T19:08:52Z
depth: standard
files_reviewed: 14
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
  - requirements.txt
findings:
  critical: 0
  warning: 0
  info: 0
  total: 0
status: clean
---

# Phase 1: Code Review Report

**Reviewed:** 2026-11-11T19:08:52Z
**Depth:** standard
**Files Reviewed:** 14
**Status:** clean

## Summary

The round-five implementation and every subsequent review fix were re-reviewed adversarially. All prior critical and warning findings are closed, and no new correctness, security, or maintainability defects were found in the reviewed files.

The publication deadlock is closed without weakening serialization. `publish_snapshot` keeps the required snapshot -> global Player -> tenant Player -> PlayerTeamSeason advisory-lock order, while SQLAlchemy's `with_for_update(key_share=True)` compiles to PostgreSQL `FOR NO KEY UPDATE`. That mode remains incompatible with competing snapshot updates but is compatible with the `KEY SHARE` acquired by child foreign-key checks. The deterministic source-insert/publication regression and the recorded PostgreSQL mutation-first and publication-first interleavings cover the repaired behavior.

Configured public origins now match the browser-relevant canonicalization contract. HTTP `:80` and HTTPS `:443` are removed, non-default ports remain, IPv6 is compressed and bracketed, canonical IPv4 is retained, and noncanonical numeric IPv4 forms fail closed. The direct, pinned `idna==3.20` dependency applies UTS46 with STD3 rules to Unicode and ASCII A-label inputs. Focused cross-runtime probes matched Python output to Node's WHATWG URL serialization for `bücher.example`, `faß.de`, final sigma, canonical A-labels, default ports, case folding, Unicode dot/fullwidth mappings, and compressed IPv6. Both runtimes rejected the malformed A-labels `xn--a-ecp.example` and `xn--a-ccb`; joiner-invalid input also failed closed without leaking IDNA diagnostic detail.

All five original round-five verification gaps remain closed:

- Global Player UUID serialization covers authoritative writes across concurrently invisible organizations, including reversible revision `0014` lifecycle coverage.
- Fly's public HTTPS acknowledgement origin is explicit even when Flask receives internal HTTP, and request-controlled forwarding/host headers cannot redefine it.
- Definitive acknowledgement responses discard rejected attempts, while retryable responses and network failures preserve the same bounded pending UUID.
- Browser storage exceptions fall back to page memory without issuing duplicate workbook requests or overstating filesystem-save success.
- The release validator audits the full committed range from the immutable `95776ed` baseline before separately checking the working tree, and its override rules fail closed against truncated ranges.

Verification during this review passed the public-origin and deployment contract tests, release-range and validator contract tests, template contract tests, all 16 Node download-state-machine tests, both committed-range and working-tree `git diff --check` checks, and direct Python/Node origin-parity probes. PostgreSQL-only concurrency classes were unavailable in this review process because `DATABASE_URL` was not present; the current round-five summaries record their successful execution in the full release validator, and the relevant SQL, lock ordering, deterministic regressions, and generated `FOR NO KEY UPDATE` statement were independently inspected here.

All reviewed files meet quality standards. No issues found.

## Narrative Findings (AI reviewer)

No critical, warning, or informational findings.

---

_Reviewed: 2026-11-11T19:08:52Z_
_Reviewer: the agent (gsd-code-reviewer)_
_Depth: standard_
