---
phase: 03-detailed-ncaa-intelligence
audited: 2026-11-16T19:25:12Z
status: secured
asvs_level: 1
block_on: high
register_authored_at_plan_time: true
threats_total: 47
threats_mitigated: 38
threats_accepted: 9
threats_transferred: 0
threats_closed: 47
threats_open: 0
unregistered_flags: 0
---

# Phase 3 Security Audit

## SECURED

**Phase:** 3 — Detailed NCAA Intelligence
**Threats Closed:** 47/47
**ASVS Level:** 1
**Blocking policy:** high

All 38 declared `mitigate` threats are present in implementation and executable tests. The nine plan-authored package-supply-chain acceptances are documented below. There are no open or transferred risks and no unregistered threat flags.

## Scope and method

This audit built the register from every `<threat_model>` in Plans 03-01 through 03-09, inspected every corresponding SUMMARY including deviations and `## Threat Flags`, and verified the controls in the actual implementation. Documentation and prior review claims were treated only as context; a threat closed only when its mitigation was found in runtime/database code in the correct boundary and, where applicable, exercised by a focused test.

The audit specifically checked composite organization/snapshot/player/source ownership, immutable publication, exact source provenance, untrusted NCAA parsing and fixed-host construction, durable request budgets and hard deadlines, same-build cache identity, protected customer endpoint authorization, sanitized diagnostics, failure-evidence retention, migration preservation, and secret handling. No credential or token file was read, no external capture was initiated, and no deployment was performed.

## Trust boundaries

| Plan | Boundary | Data crossing |
|---|---|---|
| 03-01 | NCAA counts → metric engine | Untrusted nullable/count values become Decimal metric results. |
| 03-01 | Draft metric facts → PostgreSQL | Tenant-owned values and methodology become immutable snapshot truth. |
| 03-02 | NCAA fielding HTML → parser | Untrusted markup, links, headers, rows, and counts. |
| 03-02 | Fielding evidence → published snapshot | New source and fact rows cross tenant and publication boundaries. |
| 03-03 | CLI arguments → capture runtime | Operator IDs and output paths can influence network access and writes. |
| 03-03 | Browserless/NCAA response → fixture | Remote HTML can contain scripts, secrets, or misleading controls. |
| 03-04 | Discovered control → split fetch | A source identifier may be stale, wrong-season, or mislabeled. |
| 03-04 | Split evidence → scoped facts | Facts must remain bound to the exact tenant, snapshot, player, and scope. |
| 03-05 | Stable player/category IDs → NCAA URL | Numeric identifiers must not become arbitrary outbound URLs. |
| 03-05 | Historical labels → canonical identity | Ambiguous team/game text can merge transfers or contests. |
| 03-05 | Player detail → canonical storage | Career/log rows cross tenant, snapshot, source, and Game boundaries. |
| 03-06 | Schedule/conference HTML → Game | Contest, opponent, and conference identity are untrusted. |
| 03-06 | Games → scope membership | Selection changes recent/conference aggregates. |
| 03-06 | Membership → published snapshot | Membership must not cross snapshots or mutate after publish. |
| 03-07 | Detail fan-out → Browserless/worker | Unbounded pages or time can exhaust shared infrastructure. |
| 03-07 | Customer/job state → operator CLI | Diagnostics could expose customer data, evidence, URLs, or secrets. |
| 03-08 | Revision 0024 schema → revision 0025 | Migration must preserve existing published evidence exactly. |
| 03-08 | Draft Phase 3 rows → published snapshot | New ownership and provenance links require atomic validation. |
| 03-08 | Environment/commands → release validator | Unsafe database targets or shell interpolation could be destructive. |
| 03-09 | Authenticated route IDs → projection | Report, snapshot, player, and view identifiers are untrusted selectors. |
| 03-09 | Canonical detail → JSON | Only same-tenant, same-snapshot allowlisted data may leave storage. |

## Evidence index

| Evidence | Implementation evidence | Executable evidence |
|---|---|---|
| E-03-01 | `roster_scout/metrics.py:10-425` validates integer/Decimal inputs, negative/impossible states, denominator semantics, verified exact-scope constants, and lineage; `roster_scout/canonical.py:25-75,335-374`; `roster_scout/models.py:1740-1900`; `roster_scout/jobs.py:1762-2052`; `roster_scout/snapshots.py:184-281`; `migrations/versions/0025_detailed_ncaa_intelligence.py:844-990,1248-1290` | `tests/test_detailed_stats.py:30-360`; `tests/test_report_jobs.py:962-1449`; `tests/test_phase3_release.py:371-460` |
| E-03-02 | `roster_scout/ncaa_detail.py:24-120,780-815,1430-1608`; `roster_scout/models.py:939-1001`; `roster_scout/jobs.py:1484-1706,3132-3250`; `roster_scout/snapshots.py:479-560`; migration fielding constraints/trigger at `migrations/versions/0025_detailed_ncaa_intelligence.py:325-380` | `tests/test_ncaa_detail.py:34-246,1827-2043`; `tests/test_phase3_release.py:322-369` |
| E-03-03 | `scripts/capture_phase3_ncaa_fixtures.py:41-50,120-215,343-450,494-680,1655-1777,1990-2140` confines paths, allowlists output, bounds pages/time/bytes, uses fixed NCAA paths, consumes secrets without output, and atomically writes sanitized bundles; `roster_scraper.py:2230-2455` retains one Browserless slot and sanitized errors | `tests/test_ncaa_detail.py:248-965`; `tests/test_ncaa_workflow.py:177-219`; fixture secret scan over `tests/fixtures/ncaa_detail` returned no matches |
| E-03-04 | `roster_scout/ncaa_detail.py:792-1165` binds split controls/results to season, type, label, headers, numeric IDs, player links, and checksums; `roster_scout/models.py:1140-1487,1820-1900`; `roster_scout/jobs.py:1126-1443,1530-1585`; `roster_scout/snapshots.py:561-692` enforces source/fact/metric parity and no fabricated unavailable facts | `tests/test_ncaa_detail.py:966-1826`; `tests/test_player_history.py:286-446`; `tests/test_report_jobs.py:1690-1987` |
| E-03-05 | `roster_scout/ncaa_detail.py:520-760` and `scripts/capture_phase3_ncaa_fixtures.py:1590-1986` accept only stable numeric player paths and typed sanitized history/log evidence; `roster_scout/models.py:1489-1738`; `roster_scout/jobs.py:2079-2358`; exact included/excluded aggregate lineage is persisted at `roster_scout/jobs.py:2124-2185` | `tests/test_player_history.py:43-284,448-498`; `tests/test_phase3_release.py:322-369` |
| E-03-06 | `roster_scout/scopes.py:1-229` implements deterministic completed-game ordering, same-season conference identity, explicit partial/unavailable outcomes, count-first aggregation, and exact membership; `roster_scout/models.py:1003-1487`; `roster_scout/jobs.py:2389-2537`; `roster_scout/snapshots.py:67-182` validates membership and contributor lineage | `tests/test_scoped_stats.py:55-373`; `tests/test_report_jobs.py:3986-4148`; `tests/test_phase3_release.py:322-369` |
| E-03-07 | `roster_scout/collection_control.py:1-93` enforces exact remaining deadline at every blocking boundary; `roster_scout/jobs.py:145-335,2831-3555` reserves and settles durable page budgets before requests; `roster_scout/models.py:2039-2228` constrains tenant-owned ledgers and safe diagnostics; `scripts/inspect_report_jobs.py:1-259` emits a fixed metadata-only projection | `tests/test_ncaa_detail.py:248-965`; `tests/test_browserless_lifecycle.py:102-204`; `tests/test_player_history.py:286-446`; `tests/test_phase3_release.py:462-758`; `tests/test_report_jobs.py:3397-3818` |
| E-03-08 | revision `0025` is additive/reversible and installs named checks, composite ownership, lifecycle, constant, and immutable-child triggers in `migrations/versions/0025_detailed_ncaa_intelligence.py`; `roster_scout/snapshots.py:285-774` validates materialized status and exact evidence parity before atomic publish; `scripts/validate_phase3_release.py:21-45,177-207,251-268,282-339` uses argument arrays, rejects non-loopback/non-`_test` databases, constructs each child environment from seven safe runtime keys, gives `DATABASE_URL` only to database-dependent commands, and gives the destructive sentinel only to the inherited Phase 2.1 gate | `tests/test_phase3_release.py:46-133` proves every child excludes provider/storage/auth/proxy/signing and arbitrary parent values and proves database/sentinel scoping; migration preservation/lifecycle and the authoritative release gate passed against disposable PostgreSQL |
| E-03-09 | `roster_scout/report_routes.py:860-890` derives scope only from `g.authorization_context`, allowlists views, and normalizes denials; `roster_scout/player_detail.py:1-600` validates the exact organization/report/published-snapshot/player chain and applies tenant/snapshot predicates to every projection without live NCAA access | `tests/test_report_library.py:674-1291`; `tests/test_identity.py:1018-1140`; `tests/test_phase2_tenant_isolation.py:349-449` |
| E-CROSS | Published-child immutability and composite ownership are inventoried across every Phase 3 snapshot family; failure HTML is failure-only, compressed, bounded to the database retention window, and physically purged while checksum/timestamps remain | `tests/test_phase3_release.py:322-369`; `tests/test_snapshot_persistence.py:2469-2586`; `tests/test_report_jobs.py:2396-2630` |

## Complete threat register

| Threat ID | Category | Component | Disposition | Declared mitigation | Status | Evidence |
|---|---|---|---|---|---|---|
| T-03-01-01 | Tampering | canonical/metric engine | mitigate | Strict Decimal/integer parsing, nonnegative and incompatible-input rejection, golden formulas | CLOSED | E-03-01 |
| T-03-01-02 | Information Disclosure | `MetricValue` ownership | mitigate | Composite organization/snapshot/player/scope references and negative substitution checks | CLOSED | E-03-01, E-CROSS |
| T-03-01-03 | Repudiation | metric methodology | mitigate | Definition version, exact inputs/sample, reason, and constant version persisted per value | CLOSED | E-03-01 |
| T-03-01-04 | Tampering | NCAA constant sets | mitigate | Verified exact-season/division sets, checksum provenance, composite definition FK, append-only lock, no seeds | CLOSED | E-03-01 |
| T-03-01-SC | Tampering | package supply chain | accept | No dependency install; existing pinned environment reused | ACCEPTED | AR-03-01 |
| T-03-02-01 | Tampering | fielding parser | mitigate | Exact selectors/headers/row shapes, nonnegative parsing, checksums, fail-closed fixtures | CLOSED | E-03-02 |
| T-03-02-02 | Information Disclosure | outbound fetch | mitigate | Fixed `stats.ncaa.org` paths constructed only from validated numeric IDs | CLOSED | E-03-02 |
| T-03-02-03 | Elevation of Privilege | `FieldingFact` ownership | mitigate | Composite organization/snapshot/player/source keys and negative substitution test | CLOSED | E-03-02, E-CROSS |
| T-03-02-04 | Repudiation | partial publication | mitigate | Exact status/reason/time/row/parser/schema/checksum before publication | CLOSED | E-03-02 |
| T-03-02-SC | Tampering | package supply chain | accept | Existing pinned parser/browser dependencies reused | ACCEPTED | AR-03-02 |
| T-03-03-01 | Information Disclosure | fixture sanitizer | mitigate | Allowlists, script/secret removal, secret-pattern scan, sanitized evidence only | CLOSED | E-03-03 |
| T-03-03-02 | Tampering | split identifier discovery | mitigate | Identifier bound to season/type/label/header/player-link/checksum contracts | CLOSED | E-03-03, E-03-04 |
| T-03-03-03 | Denial of Service | Browserless capture | mitigate | Shared slot, validated page count, timeout/deadline, and output-size caps | CLOSED | E-03-03, E-03-07 |
| T-03-03-04 | Elevation of Privilege | output path/source host | mitigate | Writes confined to exact division fixture root; fixed numeric NCAA paths only | CLOSED | E-03-03 |
| T-03-03-SC | Tampering | package supply chain | accept | Existing pinned Browserless/parser code reused | ACCEPTED | AR-03-03 |
| T-03-04-01 | Spoofing/Tampering | split discovery | mitigate | Season/type/label/header/player-link validation for every control and response | CLOSED | E-03-04 |
| T-03-04-02 | Information Disclosure | scoped fact ownership | mitigate | Composite scope/snapshot/player/source ownership and substitution rejection | CLOSED | E-03-04, E-CROSS |
| T-03-04-03 | Repudiation | unavailable split | mitigate | Requested scope, safe reason, source identity/time, versions, and zero fact count persisted | CLOSED | E-03-04 |
| T-03-04-04 | Tampering | retry cache | mitigate | Exact same-job/source/parser/schema cache identity; explicit refresh forces live work | CLOSED | E-03-04, E-03-07 |
| T-03-04-SC | Tampering | package supply chain | accept | No dependency install; existing source boundary reused | ACCEPTED | AR-03-04 |
| T-03-05-01 | Spoofing | career team identity | mitigate | Only source links or exact season lookup accepted; ambiguity stays partial/unavailable | CLOSED | E-03-05 |
| T-03-05-02 | Tampering | two-way/transfer linkage | mitigate | Stable NCAA Player ID is authoritative; typed facts and team-seasons stay distinct | CLOSED | E-03-05 |
| T-03-05-03 | Information Disclosure | career/log persistence | mitigate | Composite tenant/snapshot/player/source ownership and same-snapshot Game linkage | CLOSED | E-03-05, E-CROSS |
| T-03-05-05 | Repudiation | aggregate completeness | mitigate | Included/excluded career row IDs and reasons retained with definition/source evidence | CLOSED | E-03-05 |
| T-03-05-SC | Tampering | package supply chain | accept | No new package or scraper runtime | ACCEPTED | AR-03-05 |
| T-03-06-01 | Spoofing | conference classification | mitigate | Same-season source-backed team/opponent conference IDs required | CLOSED | E-03-06 |
| T-03-06-02 | Tampering | last-10 selector | mitigate | Pure deterministic ordering and exact normalized membership, persisted once | CLOSED | E-03-06 |
| T-03-06-03 | Information Disclosure | scope membership ownership | mitigate | Composite organization/snapshot/game/scope keys and family-wide substitution checks | CLOSED | E-03-06, E-CROSS |
| T-03-06-04 | Repudiation | partial scope | mitigate | Exact game IDs/dates/count/range, status/reason, checksum, and collection time | CLOSED | E-03-06 |
| T-03-06-SC | Tampering | package supply chain | accept | Existing source/runtime reused | ACCEPTED | AR-03-06 |
| T-03-07-01 | Denial of Service | Phase 3 collection | mitigate | Hard page/time budgets, shared slot, exact pre-request reservation, bounded backoff, durable settlement | CLOSED | E-03-07 |
| T-03-07-02 | Information Disclosure | diagnostics projection | mitigate | Fixed field allowlist, safe categories/messages, no raw URL query or exception output | CLOSED | E-03-07 |
| T-03-07-03 | Elevation of Privilege | request-ledger ownership | mitigate | Composite organization/job/snapshot references with cross-tenant rejection | CLOSED | E-03-07, E-CROSS |
| T-03-07-04 | Repudiation | cache/live/budget outcome | mitigate | Timestamped immutable ledger with content-free source digest, outcome, and remaining budget | CLOSED | E-03-07 |
| T-03-07-05 | Information Disclosure | external refresh diagnostics | mitigate | Composite tenant ownership, safe fields, opaque bounded IDs, one-way lifecycle, no provider payload/token | CLOSED | E-03-07 |
| T-03-07-SC | Tampering | package supply chain | accept | Existing SQLAlchemy/CLI patterns reused | ACCEPTED | AR-03-07 |
| T-03-08-01 | Tampering | Alembic revision 0025 | mitigate | Additive named constraints/triggers, exact prior evidence preservation, reversible lifecycle/drift checks | CLOSED | E-03-08 |
| T-03-08-02 | Elevation of Privilege | new snapshot-child FKs | mitigate | Composite organization/snapshot/player/source/scope ownership on every Phase 3 family | CLOSED | E-03-08, E-CROSS |
| T-03-08-03 | Repudiation | publication validation | mitigate | Materialized status and exact row/source/fact/membership parity before atomic publish | CLOSED | E-03-08 |
| T-03-08-04 | Information Disclosure | release subprocesses | mitigate | Argument arrays, inherited sanitized environment, `_test` database guard, and no secret interpolation/output | CLOSED | E-03-08; commit `6c6d55a` |
| T-03-08-05 | Tampering | constant/refresh lifecycle | mitigate | Verified-reference immutability and controlled tenant-owned refresh transitions | CLOSED | E-03-08 |
| T-03-08-SC | Tampering | package supply chain | accept | Existing locked environment reused | ACCEPTED | AR-03-08 |
| T-03-09-01 | Elevation of Privilege | player detail route | mitigate | Membership-derived tenant plus exact report/snapshot/player chain; normalized audited 404 | CLOSED | E-03-09 |
| T-03-09-02 | Information Disclosure | JSON projection | mitigate | Fixed field/view allowlist, published-snapshot joins, no raw HTML or source URLs | CLOSED | E-03-09 |
| T-03-09-03 | Denial of Service | detail query | mitigate | Bounded view vocabulary, indexed ownership predicates, fixed projection, no live NCAA access | CLOSED | E-03-09 |
| T-03-09-04 | Tampering | historical snapshot response | mitigate | Read-only loader plus database child immutability and predecessor/successor regression | CLOSED | E-03-09, E-CROSS |
| T-03-09-SC | Tampering | package supply chain | accept | Existing Flask/SQLAlchemy authorization patterns reused | ACCEPTED | AR-03-09 |

## Accepted risks log

These are the only accepted risks. Each was authored as `accept` in its PLAN threat model; this audit did not convert a missing mitigation into an accepted risk.

| Risk ID | Threat ref | Rationale | Accepted by | Date |
|---|---|---|---|---|
| AR-03-01 | T-03-01-SC | Plan changed no dependency manifest and reused the locked metric/database stack. | Plan 03-01 threat model | 2026-11-16 |
| AR-03-02 | T-03-02-SC | Fielding reused the existing pinned parser and Browserless dependencies. | Plan 03-02 threat model | 2026-11-16 |
| AR-03-03 | T-03-03-SC | Capture reused installed Browserless/parser code; no install path was added. | Plan 03-03 threat model | 2026-11-16 |
| AR-03-04 | T-03-04-SC | Split collection reused the existing source boundary and lockfile. | Plan 03-04 threat model | 2026-11-16 |
| AR-03-05 | T-03-05-SC | History/log parsing introduced no package or second scraper runtime. | Plan 03-05 threat model | 2026-11-16 |
| AR-03-06 | T-03-06-SC | Schedule/scope work reused the established runtime. | Plan 03-06 threat model | 2026-11-16 |
| AR-03-07 | T-03-07-SC | Budget/diagnostic work reused SQLAlchemy and standard-library CLI patterns. | Plan 03-07 threat model | 2026-11-16 |
| AR-03-08 | T-03-08-SC | Release validation runs the existing locked environment without installing. | Plan 03-08 threat model | 2026-11-16 |
| AR-03-09 | T-03-09-SC | The endpoint reused the existing Flask/SQLAlchemy authorization stack. | Plan 03-09 threat model | 2026-11-16 |

## Transferred risks

No threats have a `transfer` disposition.

## Resolved threat

T-03-08-04 was closed by commit `6c6d55a`. The release validator now builds a new allowlisted environment for every command instead of copying the parent environment. Database access is limited to the seven database-dependent commands, and `PHASE2_DESTRUCTIVE_DATABASE` reaches only the inherited Phase 2.1 gate. Negative tests inject Browserless, WorkOS, Google, AWS, Fly, session, signing, proxy, and arbitrary values and prove none reach a child. A separate captured-output probe verified that neither the injected secret marker nor database/sentinel values are logged.

## Open threats

None.

## Summary threat flags and deviations

| Source | Result |
|---|---|
| 03-07-SUMMARY `## Threat Flags` | Maps to T-03-07-02 through T-03-07-05; no unregistered surface. |
| 03-08-SUMMARY `## Threat Flags` | Maps to T-03-08-01 through T-03-08-05; no unregistered surface. |
| 03-01 through 03-06 and 03-09 summaries | No explicit `## Threat Flags` entry. Deviations were reviewed and map to registered constant immutability, Browserless/capture, composite ownership, migration, budget, or protected-route threats. |

## Focused verification

| Check | Result |
|---|---|
| Disposable PostgreSQL preflight using the documented loopback `_test` target | PASS |
| Main focused security suite: metrics, migration preservation, schema ownership/immutability, constants, diagnostics, capture safety, history, scopes, route authorization, cache and retention | 126 tests PASS |
| Supplemental owner/deadline/budget/retry/immutability checks | 10 substantive tests PASS |
| Corrected safe-failure selector | 1 test PASS |
| Fixture secret scan (`token=`, cookie, authorization, authenticated Browserless URL, `.edu` email patterns) | PASS, no matches |
| `git diff --check` | PASS |
| T-03-08-04 negative secret-inheritance and database/sentinel scoping tests | 2 tests PASS |
| Captured-output probe for secret, database, and sentinel values | PASS, no values emitted |
| Authoritative Phase 3 release validator | PASS: revision 0025, migration preservation/drift, 132 focused tests, inherited Phase 2.1 gate, 774-test clean discovery, 942-team catalog, compilation, and diff hygiene |

One supplemental command initially named the safe-failure test under a stale class and produced an import-selector error. No implementation test failed; the current selector `CeleryConcurrentDeliveryTests.test_task_failure_persists_only_allowlisted_safe_text` was run immediately and passed.

## Security audit trail

| Audit date | Threats total | Mitigated | Accepted | Transferred | Open | Run by |
|---|---:|---:|---:|---:|---:|---|
| 2026-11-16 | 47 | 37 | 9 | 0 | 1 | Codex / gsd-security-auditor |
| 2026-11-16 | 47 | 38 | 9 | 0 | 0 | Codex / gsd-security-auditor re-audit of `6c6d55a` |

## Sign-off

- [x] All plan-time threats have a disposition.
- [x] Every `mitigate` disposition has implementation and executable evidence.
- [x] Plan-authored accepted risks are documented without auto-accepting gaps.
- [x] SUMMARY threat flags and deviations were incorporated.
- [x] `threats_open: 0` confirmed.
- [x] `status: secured` set in frontmatter.

**Approval:** verified; all 47 registered threats are closed or documented as plan-authored accepted risks.
