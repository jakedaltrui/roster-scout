# Phase 1: Canonical Snapshot Foundation - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-11-01
**Phase:** 1-Canonical Snapshot Foundation
**Areas discussed:** Partial snapshot publication, Cache and freshness rules, Ambiguous player identity, Audit evidence depth

---

## Partial Snapshot Publication

### Core-section failure

| Option | Description | Selected |
|--------|-------------|----------|
| Publish a partial snapshot | Preserve the successful section, mark the other unavailable, and label the snapshot partial. | |
| Fail the entire build | Publish nothing unless both batting and pitching succeed. | ✓ |
| You decide | Leave the core-section policy to planning. | |

**User's choice:** Fail the entire build.

### Verified empty section

| Option | Description | Selected |
|--------|-------------|----------|
| Accept verified empty sections | Treat zero rows as complete only when the NCAA page loaded and explicitly contains no player data. | ✓ |
| Require player rows | Any zero-row core section fails the build. | |
| You decide | Apply a conservative validation rule during planning. | |

**User's choice:** Accept verified empty sections.

### Core versus supplemental sections

| Option | Description | Selected |
|--------|-------------|----------|
| Core versus supplemental | Batting and pitching are required; future supplemental sections may be unavailable in a partial snapshot. | ✓ |
| Everything required | Every report section must succeed before any snapshot is published. | |
| You decide | Define critical sections as capabilities are added. | |

**User's choice:** Batting and pitching are the publication-critical core.

### Snapshot and artifact readiness

| Option | Description | Selected |
|--------|-------------|----------|
| Publish snapshot; retry artifact | Canonical data becomes ready while workbook generation has an independent retryable state. | |
| Hold snapshot | Do not publish until canonical data and workbook generation both succeed. | |
| You decide | Delegate the boundary to the agent. | ✓ |

**User's choice:** Agent discretion.
**Notes:** The agent selected independent canonical publication with separately retryable artifacts because canonical facts and artifacts are distinct failure domains.

---

## Cache and Freshness Rules

### Initial cache preference

| Option | Description | Selected |
|--------|-------------|----------|
| Fresh-window cache | Publish from a successful cache entry while recording its original source time and age. | |
| Live every time | Contact NCAA for every new build. | |
| Retry-only cache | New builds collect live; retries of the same job may reuse successful sections. | |
| Free-text response | Cache for 36-48 hours if that makes repeated reports during a three-day series faster. | ✓ |

**User's choice:** Initially proposed a 36-48 hour cache for three reports in three days.

### Three-game-series clarification

| Option | Description | Selected |
|--------|-------------|----------|
| 48 hours | Maximize cache reuse across three consecutive days. | |
| 36 hours | Shorter default freshness window. | |
| Configurable 36-48 hours | Default to 48 hours and allow operations to shorten it. | |
| Free-text clarification | Each run after a game needs updated statistics because the numbers changed. | ✓ |

**User's choice:** Post-game runs must use updated data.
**Notes:** Hypothetical schedule was Friday at 7 PM, Saturday at 2 PM, and Sunday at 1 PM. The user asked for help reconciling speed with changing statistics.

### Explicit refresh

| Option | Description | Selected |
|--------|-------------|----------|
| Always collect live | Reopening is instant from saved snapshots; explicit refresh bypasses older source data. | ✓ |
| Short refresh cache | Reuse source data collected within approximately 15 minutes. | |
| Different rule | Supply another freshness policy. | |

**User's choice:** Every explicit refresh performs a live NCAA collection.

### Duplicate refresh clicks

| Option | Description | Selected |
|--------|-------------|----------|
| Return active job | Duplicate clicks share the active job and result. | ✓ |
| Start another refresh | Every click starts an independent NCAA session and snapshot. | |
| Reject the request | Tell the coach a refresh is already running. | |

**User's choice:** Return the active refresh job.

---

## Ambiguous Player Identity

### Missing-ID batting/pitching match

| Option | Description | Selected |
|--------|-------------|----------|
| Conservative team-season fallback | Merge only when name and supporting details are uniquely consistent within one team-season. | ✓ |
| Name-only matching | Merge whenever normalized names match. | |
| Never merge without an ID | Keep all ID-less source rows separate. | |

**User's choice:** Conservative team-season fallback.

### Cross-team transfer inference

| Option | Description | Selected |
|--------|-------------|----------|
| Do not auto-link | Require a stable NCAA ID or another authoritative source. | ✓ |
| Link with bio details | Infer a transfer from name plus class, position, hometown, or prior school. | |
| Link by name | Assume matching normalized names belong to one career. | |

**User's choice:** Do not auto-link ID-less records across teams.

### Conflicting names under one NCAA ID

| Option | Description | Selected |
|--------|-------------|----------|
| Block publication | Preserve evidence and stop publication as an identity conflict. | ✓ |
| Trust the ID | Merge and retain source names as aliases. | |
| Split the records | Ignore the shared ID and create unresolved players. | |

**User's choice:** Block publication.

### Same-name teammates

| Option | Description | Selected |
|--------|-------------|----------|
| Keep separate | Use supporting fields for distinct snapshot-scoped fallback identities. | ✓ |
| Fail the build | Refuse publication without authoritative identities. | |
| Merge by name | Treat both rows as one player. | |

**User's choice:** Keep distinguishable same-name teammates separate.

---

## Audit Evidence Depth

### Permanent snapshot evidence

| Option | Description | Selected |
|--------|-------------|----------|
| Exact rows plus provenance | Preserve raw NCAA values, source identity, collection time, parser/schema version, and checksum. | ✓ |
| Normalized facts only | Discard exact extracted rows after normalization. | |
| Full source pages | Permanently retain extracted rows and complete NCAA HTML. | |

**User's choice:** Exact extracted source rows plus provenance.

### Temporary HTML

| Option | Description | Selected |
|--------|-------------|----------|
| Failed or blocked builds only | Temporarily retain relevant pages with restricted access and automatic deletion. | ✓ |
| Never retain HTML | Keep only extracted rows, checksums, and diagnostics. | |
| Retain every page | Archive successful and failed HTML permanently. | |

**User's choice:** Retain HTML only for failed or blocked builds.

### HTML retention period

| Option | Description | Selected |
|--------|-------------|----------|
| 30 days | Balanced investigation window and retention footprint. | ✓ |
| 14 days | Shorter exposure and investigation window. | |
| 90 days | Longer intermittent-failure history at greater cost. | |

**User's choice:** 30 days.

### Permanent failed-build record

| Option | Description | Selected |
|--------|-------------|----------|
| Sanitized operational record | Keep states, sources, timestamps, versions, counts, checksums, and safe failure categories. | ✓ |
| Delete the failed-build record | Retain evidence only for published snapshots. | |
| Keep partial extracted rows | Permanently retain source rows from unpublished builds. | |

**User's choice:** Keep a sanitized operational record.

---

## the agent's Discretion

- The user delegated whether canonical data may publish when workbook generation fails. The agent selected independent canonical snapshot readiness with a separately retryable artifact state.
- Technical vendor and library choices were intentionally left to research and planning.

## Deferred Ideas

None — discussion stayed within Phase 1.
