# Phase 3: Detailed NCAA Intelligence - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-11-15
**Phase:** 3-Detailed NCAA Intelligence
**Areas discussed:** Career and game-log depth, last-10 and conference derivation, small-sample metric treatment, supplemental section failure behavior

---

## Career and Game-Log Depth

| Option | Description | Selected |
|--------|-------------|----------|
| All career season totals plus current-season game logs | Preserves transfer-aware career context while bounding per-player requests and snapshot latency. | ✓ |
| All career season totals and all historical game logs | Maximizes detail but substantially increases requests, source exposure, and build time. | |
| Current season only | Fastest but does not deliver the required career context. | |

**User's choice:** Use the recommended default: all available career season totals and current-season detailed game logs.
**Notes:** Current-roster players are the collection boundary. Historical seasons remain separately labeled and transfer-aware.

---

## Last-10 and Conference Derivation

| Option | Description | Selected |
|--------|-------------|----------|
| Derive from exact audited game logs | Supports recent and conference scopes even when NCAA does not expose ready-made aggregates; preserves included-game evidence. | ✓ |
| Use source-published aggregates only | Simpler provenance, but commonly leaves the requested views unavailable. | |
| Do not provide these scopes | Keeps the original Phase 3 scope but omits the explicitly requested coaching views. | |

**User's choice:** Use exact audited game logs when source-published aggregates are absent.
**Notes:** Conference classification requires season-specific evidence. Last-10 means completed games, with exact IDs/dates/count retained.

---

## Small-Sample Metric Treatment

| Option | Description | Selected |
|--------|-------------|----------|
| Show with sample-size context | Preserves all valid values while exposing PA, innings/outs, opportunities, or game count. | ✓ |
| Suppress below fixed thresholds | Reduces noisy values but imposes arbitrary product rules and hides real observations. | |
| Show only counting statistics | Avoids rate-stat interpretation but weakens the detailed intelligence goal. | |

**User's choice:** Show mathematically valid values with their sample size.
**Notes:** Undefined denominators and unavailable inputs stay unavailable; valid zero remains distinct.

---

## Supplemental Section Failure Behavior

| Option | Description | Selected |
|--------|-------------|----------|
| Publish partial with exact unavailable sections | Retains trustworthy core season facts and clearly identifies missing enrichment. | ✓ |
| Fail the complete report | Maximizes completeness but can discard useful core evidence due to one supplemental source failure. | |
| Silently omit unavailable sections | Appears cleaner but is misleading and violates the snapshot completeness contract. | |

**User's choice:** Carry forward the recommended Phase 1 partial-publication behavior.
**Notes:** Published snapshots remain immutable; later enrichment requires an explicit successor refresh.

---

## the agent's Discretion

- Exact normalized table layout and migration boundaries.
- Bounded request-budget, concurrency, retry, and cache values.
- Whether audited scoped aggregates share one general scoped-fact model with source-provided splits or use closely related typed tables.

## Deferred Ideas

- Workbook presentation remains Phase 4.
- Embedded Power BI presentation remains Phase 5.
