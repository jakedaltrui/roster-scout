---
phase: 01-canonical-snapshot-foundation
reviewed: 2026-11-11T17:54:38Z
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
  critical: 1
  warning: 1
  info: 0
  total: 2
status: issues_found
---

# Phase 1: Code Review Report

**Reviewed:** 2026-11-11T17:54:38Z
**Depth:** standard
**Files Reviewed:** 13
**Status:** issues_found

## Summary

The round-five changes close the five previously reported gaps in their covered schedules: global Player serialization, Fly public-origin selection, definitive acknowledgement handling, storage fallback, and committed-range release validation all have passing focused regressions. The review nevertheless found a reproducible PostgreSQL deadlock in publication versus child insertion, plus an accepted public-origin configuration that can never match a browser's serialized origin.

Focused evidence run during review: 27 PostgreSQL global-identity/publication tests passed, 16 Node acknowledgement tests passed, 8 origin/release-range tests passed, and both committed-range and working-tree `git diff --check` passed. A separate two-connection probe reproduced SQLSTATE `40P01` for the uncovered lock schedule.

## Narrative Findings (AI reviewer)

## Critical Issues

### CR-01: Publication reverses the foreign-key/advisory lock order and can deadlock

**Classification:** BLOCKER

**File:** `roster_scout/snapshots.py:28-32`

**Issue:** `publish_snapshot` acquires the snapshot advisory lock and then takes `FOR UPDATE` on the `report_snapshots` row. A concurrent child insert necessarily takes a PostgreSQL `KEY SHARE` lock on that parent row while checking its foreign key *before* its AFTER-statement trigger can call `lock_snapshot_mutation`. This permits the cycle: publisher holds advisory lock → child holds parent `KEY SHARE` → child waits for advisory lock → publisher waits for `FOR UPDATE`. A disposable two-connection probe against the Phase 1 test database produced SQLSTATE `40P01`, while the current tests at `tests/test_canonical_publish.py:1639` and `tests/test_canonical_publish.py:1693` only start the second path after the first has already passed this vulnerable interval.

**Fix:** Keep the advisory lock as the serialization authority, but read the snapshot with a row mode compatible with foreign-key `KEY SHARE` (PostgreSQL `FOR NO KEY UPDATE`) or without a row lock. For SQLAlchemy/PostgreSQL, use the option that emits `FOR NO KEY UPDATE`, then add a two-connection regression that pauses publication after `lock_snapshot_mutation`, lets a real `SourceRecord` or `PlayerTeamSeason` insert acquire its parent FK lock, and asserts both threads finish without SQLSTATE `40P01`/`55P03`.

```python
snapshot = session.scalar(
    select(ReportSnapshot)
    .where(ReportSnapshot.id == snapshot_id)
    .with_for_update(key_share=True)  # PostgreSQL: FOR NO KEY UPDATE
)
```

## Warnings

### WR-01: Default ports are preserved even though browsers remove them from Origin

**Classification:** WARNING

**File:** `app.py:109-113`

**Issue:** `_canonical_public_origin` preserves every explicit port. Consequently, accepted settings such as `https://reports.example:443/` and `http://reports.example:80/` become `https://reports.example:443` and `http://reports.example:80`, but browsers serialize those origins without their default ports. The exact comparison at `app.py:388` then rejects every legitimate acknowledgement with HTTP 403. The current configuration test covers only non-default port 8443 (`tests/test_app_routes.py:398-408`), so this accepted-but-unusable configuration is not detected. The checked-in Fly value is unaffected, but the documented custom-domain/multi-worker contract is not robust.

**Fix:** Remove port 443 for HTTPS and port 80 for HTTP during canonicalization, while retaining non-default ports. Add clean-process and route tests for both default-port forms and retain the existing 8443 case.

```python
default_port = (scheme == "https" and port == 443) or (
    scheme == "http" and port == 80
)
canonical_port = "" if port is None or default_port else f":{port}"
```

---

_Reviewed: 2026-11-11T17:54:38Z_
_Reviewer: the agent (gsd-code-reviewer)_
_Depth: standard_
