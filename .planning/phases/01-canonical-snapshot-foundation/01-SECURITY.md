---
phase: 01-canonical-snapshot-foundation
audited: 2026-11-11T19:31:25Z
status: secured
asvs_level: 1
block_on: high
threats_total: 169
threats_mitigated: 164
threats_accepted: 5
threats_closed: 169
threats_open: 0
unregistered_flags: 0
---

# Phase 1 Security Audit

## SECURED

**Phase:** 1 — Canonical Snapshot Foundation  
**Threats Closed:** 169/169  
**ASVS Level:** 1  
**Blocking policy:** high

All 164 declared `mitigate` threats are present in implementation and executable tests. The five plan-authored accepted risks are documented below. No threat remains open.

## Scope and method

This audit built the register from every parseable `<threat_model>` in Plans 01-01 through 01-41 and checked every SUMMARY `## Threat Flags` section. Repeated threat IDs in early plans are qualified by plan number so no declaration is collapsed. Mitigations were verified in the cited runtime/database/deployment code and executable tests. Canonical `01-REVIEW.md`, `01-REVIEW-FIX.md`, and `01-VERIFICATION.md` were used only as corroborating test history, not as substitutes for code evidence.

All SUMMARY threat-flag sections that exist say `None`; no unregistered implementation flag was declared.

## Evidence index

Each `E-xx` entry below is concrete implementation and test evidence for every threat in that plan's register.

| Evidence | Implementation evidence | Executable evidence |
|---|---|---|
| E-01-01 | `roster_scout/models.py:117-175,189-220,225-369`; `roster_scout/snapshots.py:22-218`; migration 0001 | `tests/test_snapshot_persistence.py:382-615,2865-2954` |
| E-01-02 | `roster_scout/models.py:407-582`; `roster_scout/jobs.py:350-446,958-1063`; `roster_scout/tasks.py:22-50`; `roster_scout/evidence.py:149-225` | `tests/test_report_jobs.py:79-178,1610-1641,1845-1935` |
| E-01-03 | `roster_scout/canonical.py:181-389`; `roster_scout/jobs.py:446-735,1244-1294`; `roster_scraper.py:2288-2495` | `tests/test_canonical_data.py:36-333`; `tests/test_canonical_publish.py:106-286` |
| E-01-04 | migration 0003 integrity triggers/checks; `roster_scout/models.py:313-369`; `roster_scout/canonical.py:95-118` | `tests/test_snapshot_persistence.py:153-219,427-534,2851-2865`; `tests/test_canonical_data.py:222-254` |
| E-01-05 | migration 0004 traceability; `roster_scout/jobs.py:735-958`; `roster_scout/snapshots.py:93-218` | `tests/test_canonical_publish.py:494-842`; `tests/test_snapshot_persistence.py:382-615` |
| E-01-06 | `roster_scout/database.py:30-78`; `roster_scout/jobs.py:1071-1233` | `tests/test_report_jobs.py:1437-1641` |
| E-01-07 | migration 0005; `roster_scout/repositories.py:59-232`; `roster_scout/jobs.py:148-350` | `tests/test_report_jobs.py:178-316,1641-1845` |
| E-01-08 | `roster_scraper.py:2079-2185`; `roster_scout/jobs.py:446-735` | `tests/test_ncaa_workflow.py:662-824`; `tests/test_report_jobs.py:963-1437` |
| E-01-09 | `roster_scout/evidence.py:95-225`; `roster_scraper.py:1027-1055` | `tests/test_evidence.py:20-257`; `tests/test_report_jobs.py:1934-2050` |
| E-01-10 | `usage_limits.py:57-171`; loopback bindings in `compose.yaml`; managed URLs/process scopes in `fly.toml` | `tests/test_deployment_config.py:66-168`; `tests/test_usage_limits.py` |
| E-01-11 | `scripts/validate_phase1_release.py:13-367` | `tests/test_phase1_release.py:57-562`; release gate recorded PASS in canonical `01-VERIFICATION.md` |
| E-01-12 | `scripts/run_scheduler.py:18-188`; `compose.yaml:59-81`; `fly.toml:10-14` | `tests/test_deployment_config.py:239-356,974-1069` |
| E-01-13 | `roster_scout/evidence.py:229-380`; `scripts/run_purge.py`; `fly.toml:14` | `tests/test_evidence.py:257-383,513-729`; `tests/test_deployment_config.py:356-522` |
| E-01-14 | `roster_scraper.py:2079-2185`; safe failure carrier at `roster_scraper.py:1027-1055` | `tests/test_ncaa_workflow.py:662-824`; `tests/test_report_jobs.py:2079-2296` |
| E-01-15 | migration 0006 snapshot/definition advisory-lock protocol; `roster_scout/snapshots.py:22-218` | `tests/test_snapshot_persistence.py:788-1198,2823-2851` |
| E-01-16 | migration 0007 generation CAS; `roster_scout/jobs.py:139-350,1063-1233`; `roster_scout/tasks.py:33-50` | `tests/test_report_jobs.py:316-854` |
| E-01-17 | migration 0008 source/identity composite bindings; `roster_scout/models.py:225-369` | `tests/test_snapshot_persistence.py:1198-1409,2655-2698`; `tests/test_canonical_publish.py:1335-1566` |
| E-01-18 | `roster_scout/canonical.py:133-281`; identity status/key persistence in `roster_scout/jobs.py:807-958` | `tests/test_canonical_data.py:36-220`; `tests/test_canonical_publish.py:870-1335` |
| E-01-19 | retention parser and physical purge in `roster_scout/evidence.py:76-90,229-380`; migration 0009 | `tests/test_evidence.py:513-729,1044-1178` |
| E-01-20 | independent purge runner `scripts/run_purge.py`; isolated Fly command `fly.toml:14` | `tests/test_deployment_config.py:356-522,705-870` |
| E-01-21 | `app.py:391-422`; `roster_scout/evidence.py:76-90,356-380`; role-specific health checks in `compose.yaml`/`fly.toml` | `tests/test_app_routes.py:884-1053`; `tests/test_deployment_config.py:821-974,1069-1122` |
| E-01-22 | parser-2 contract `roster_scraper.py:2079-2185`; exact reuse `roster_scout/jobs.py:446-735` | `tests/test_ncaa_workflow.py:662-824`; `tests/test_report_jobs.py:963-1091` |
| E-01-23 | reciprocal corroboration `roster_scout/canonical.py:133-281`; source/fact bindings `roster_scout/models.py:225-369` | `tests/test_canonical_data.py:36-220`; `tests/test_canonical_publish.py:1149-1566` |
| E-01-24 | migrations 0010/0013/0014 authoritative-source triggers; publisher recheck `roster_scout/snapshots.py:169-218` | `tests/test_snapshot_persistence.py:638-788,1409-1664,2698-2823` |
| E-01-25 | migration 0011 database retention constraint; ORM check in `roster_scout/models.py:552-582` | `tests/test_evidence.py:383-513`; `tests/test_snapshot_persistence.py:2455-2573` |
| E-01-26 | fail-closed retention preflight `roster_scout/evidence.py:76-90`; terminal-state fallback `roster_scout/jobs.py:976-1063`; worker preflight `scripts/run_worker.py:36-56` | `tests/test_report_jobs.py:1959-2079`; `tests/test_app_routes.py:971-1038`; `tests/test_deployment_config.py:191-239` |
| E-01-27 | bounded deterministic SKIP LOCKED purge `roster_scout/evidence.py:229-355` | `tests/test_evidence.py:729-859`; `tests/test_deployment_config.py:522-705` |
| E-01-28 | privacy-bounded ledger `roster_scout/models.py:585-622`; service/scoped aggregate `roster_scout/usage.py:18-217` | `tests/test_usage_metrics.py:23-634` |
| E-01-29 | canonical UUID parsing and fail-before-200 route `app.py:434-490,541-628`; idempotent insert `roster_scout/usage.py:66-155` | `tests/test_app_routes.py:33-228,644-832`; `tests/test_usage_metrics.py:195-337` |
| E-01-30 | ordered fail-fast validator `scripts/validate_phase1_release.py:13-367`; aggregate semantics `roster_scout/usage.py:179-217` | `tests/test_deployment_config.py:1122-1513`; `tests/test_usage_metrics.py:337-634` |
| E-01-31 | migration 0013 ordered identity locks; publisher identity locks `roster_scout/snapshots.py:22-218` | `tests/test_snapshot_persistence.py:1664-2167`; `tests/test_canonical_publish.py:1566-1911` |
| E-01-32 | cache contract `roster_scraper.py:1092-1136,2403-2456` | `tests/test_ncaa_workflow.py:537-622`; `tests/test_browserless_lifecycle.py:16-78` |
| E-01-33 | event-type-qualified metrics `roster_scout/usage.py:179-217`; best-effort fixed-token native telemetry `app.py:367-390,541-644` | `tests/test_usage_metrics.py:337-478`; `tests/test_app_routes.py:832-884` |
| E-01-34 | secret/origin preflight `app.py:58-233`; signed proof `app.py:236-259`; bounded same-origin ack `app.py:434-490` | `tests/test_app_routes.py:228-339,644-832` |
| E-01-35 | bounded opaque state and order `static/download_tracking.js:12-340`; template wiring `templates/index.html:1283-1394` | `tests/js/download_tracking.test.mjs:105-318`; `tests/test_app_routes.py:33-102` |
| E-01-36 | eligibility/finalization lock and monotonic upsert `roster_scout/evidence.py:28,149-160,241-355` | `tests/test_evidence.py:859-1044,1178` |
| E-01-37 | complete release manifest `scripts/validate_phase1_release.py:13-367`; privacy/semantics runbook `DEPLOYMENT.md:245-301,347-431` | `tests/test_deployment_config.py:1236-1668`; canonical `01-VERIFICATION.md` release PASS |
| E-01-38 | migration 0014 global Player UUID locks; publication order `roster_scout/snapshots.py:22-218` | `tests/test_snapshot_persistence.py:2167-2455`; `tests/test_canonical_publish.py:1566-1911` |
| E-01-39 | direct pin `requirements.txt:4`; startup-only UTS46/STD3 canonicalization and fixed fail-closed error `app.py:21,80-233`; Fly anchor `fly.toml:18` | `tests/test_app_routes.py:339-644`; `tests/test_deployment_config.py:168-191,1236-1513,1590-1635`; mandatory release gate in `scripts/validate_phase1_release.py:66-367` |
| E-01-40 | storage shadow and three-outcome state machine `static/download_tracking.js:31-340`; guarded template storage `templates/index.html:1283-1394` | `tests/js/download_tracking.test.mjs:18-431` |
| E-01-41 | audited-base resolution and ordered full manifest `scripts/validate_phase1_release.py:121-367` | `tests/test_deployment_config.py:1122-1513`; canonical `01-VERIFICATION.md` |
 
## Complete threat register

| Qualified threat | Category | Component | Severity | Disposition | Status | Evidence |
|---|---|---|---|---|---|---|
| 01-01/T-01-01 | Information Disclosure / Elevation of Privilege | Organization-scoped facts | HIGH | mitigate | CLOSED | E-01-01 |
| 01-01/T-01-03 | Tampering | Snapshot publication | HIGH | mitigate | CLOSED | E-01-01 |
| 01-01/T-01-07 | Tampering / Denial of Service | Schema migration | HIGH | mitigate | CLOSED | E-01-01 |
| 01-02/T-01-02 | Tampering / Repudiation | Idempotent report job | HIGH | mitigate | CLOSED | E-01-02 |
| 01-02/T-01-04 | Information Disclosure | Failure persistence/logging | HIGH | mitigate | CLOSED | E-01-02 |
| 01-02/T-01-05 | Information Disclosure | Failed HTML evidence | MEDIUM | mitigate | CLOSED | E-01-02 |
| 01-02/T-01-06 | Information Disclosure | Celery message | MEDIUM | mitigate | CLOSED | E-01-02 |
| 01-03/T-01-03 | Tampering | NCAA canonical adapter | HIGH | mitigate | CLOSED | E-01-03 |
| 01-03/T-01-04 | Information Disclosure | Parser/worker errors | HIGH | mitigate | CLOSED | E-01-03 |
| 01-03/T-01-02 | Repudiation / Tampering | Refresh/cache boundary | HIGH | mitigate | CLOSED | E-01-03 |
| 01-04/T-01-04-01 | Tampering | snapshot child triggers | — | mitigate | CLOSED | E-01-04 |
| 01-04/T-01-04-02 | Tampering | metric definitions | — | mitigate | CLOSED | E-01-04 |
| 01-04/T-01-04-03 | Tampering | canonical counts | — | mitigate | CLOSED | E-01-04 |
| 01-04/T-01-04-04 | Elevation of Privilege | migration history | — | mitigate | CLOSED | E-01-04 |
| 01-05/T-01-05-01 | Tampering | `_persist_canonical` | — | mitigate | CLOSED | E-01-05 |
| 01-05/T-01-05-02 | Information Disclosure | fact ownership links | — | mitigate | CLOSED | E-01-05 |
| 01-05/T-01-05-03 | Tampering | `publish_snapshot` | — | mitigate | CLOSED | E-01-05 |
| 01-05/T-01-05-04 | Denial of Service | concurrent unique-key races | — | mitigate | CLOSED | E-01-05 |
| 01-06/T-01-06-01 | Denial of Service | duplicate task delivery | — | mitigate | CLOSED | E-01-06 |
| 01-06/T-01-06-02 | Tampering | attempt_count/job state | — | mitigate | CLOSED | E-01-06 |
| 01-06/T-01-06-03 | Repudiation | duplicate execution | — | mitigate | CLOSED | E-01-06 |
| 01-06/T-01-06-04 | Information Disclosure | task payload/failures | — | mitigate | CLOSED | E-01-06 |
| 01-07/T-01-07-01 | Denial of Service | lost broker message | — | mitigate | CLOSED | E-01-07 |
| 01-07/T-01-07-02 | Tampering | outbox lease acknowledgement | — | mitigate | CLOSED | E-01-07 |
| 01-07/T-01-07-03 | Spoofing | broker payload | — | mitigate | CLOSED | E-01-07 |
| 01-08/T-01-08-01 | Tampering | NCAA parser | — | mitigate | CLOSED | E-01-08 |
| 01-08/T-01-08-02 | Tampering | section cache | — | mitigate | CLOSED | E-01-08 |
| 01-08/T-01-08-03 | Denial of Service | final-attempt crash | — | mitigate | CLOSED | E-01-08 |
| 01-08/T-01-08-04 | Repudiation | freshness/checksums | — | mitigate | CLOSED | E-01-08 |
| 01-09/T-01-09-01 | Information Disclosure | failure HTML | — | mitigate | CLOSED | E-01-09 |
| 01-09/T-01-09-02 | Information Disclosure | source URL | — | mitigate | CLOSED | E-01-09 |
| 01-09/T-01-09-03 | Information Disclosure | failure metadata | — | mitigate | CLOSED | E-01-09 |
| 01-10/T-01-10-01 | Denial of Service | Browserless account | — | mitigate | CLOSED | E-01-10 |
| 01-10/T-01-10-02 | Elevation of Privilege | Redis/PostgreSQL host ports | — | mitigate | CLOSED | E-01-10 |
| 01-10/T-01-10-03 | Tampering | broker/database services | — | mitigate | CLOSED | E-01-10 |
| 01-10/T-01-10-04 | Spoofing | capacity backend fallback | — | mitigate | CLOSED | E-01-10 |
| 01-11/T-01-11-01 | Tampering | end-to-end snapshot | — | mitigate | CLOSED | E-01-11 |
| 01-11/T-01-11-02 | Denial of Service | duplicate/failure flow | — | mitigate | CLOSED | E-01-11 |
| 01-11/T-01-11-03 | Information Disclosure | failure path | — | mitigate | CLOSED | E-01-11 |
| 01-11/T-01-11-04 | Elevation of Privilege | release gate | — | mitigate | CLOSED | E-01-11 |
| 01-12/T-01-12-01 | Information Disclosure | scheduler config/errors | — | mitigate | CLOSED | E-01-12 |
| 01-12/T-01-12-02 | Denial of Service | scheduler duplication | — | mitigate | CLOSED | E-01-12 |
| 01-12/T-01-12-03 | Elevation of Privilege | scheduler process | — | mitigate | CLOSED | E-01-12 |
| 01-12/T-01-12-04 | Spoofing | local services as production | — | mitigate | CLOSED | E-01-12 |
| 01-13/T-01-13-01 | Information Disclosure | expired failure HTML | — | mitigate | CLOSED | E-01-13 |
| 01-13/T-01-13-02 | Tampering | purge audit trail | — | mitigate | CLOSED | E-01-13 |
| 01-13/T-01-13-03 | Repudiation | purge execution | — | mitigate | CLOSED | E-01-13 |
| 01-13/T-01-13-04 | Denial of Service | scheduler configuration | — | mitigate | CLOSED | E-01-13 |
| 01-14/T-01-14-01 | Tampering | NCAA header schema | — | mitigate | CLOSED | E-01-14 |
| 01-14/T-01-14-02 | Repudiation | verified-empty evidence | — | mitigate | CLOSED | E-01-14 |
| 01-14/T-01-14-03 | Denial of Service | schema drift | — | mitigate | CLOSED | E-01-14 |
| 01-14/T-01-14-04 | Information Disclosure | failed HTML | — | mitigate | CLOSED | E-01-14 |
| 01-15/T-01-15-01 | Tampering | snapshot child triggers | — | mitigate | CLOSED | E-01-15 |
| 01-15/T-01-15-02 | Tampering | metric definitions | — | mitigate | CLOSED | E-01-15 |
| 01-15/T-01-15-03 | Denial of Service | multi-snapshot locking | — | mitigate | CLOSED | E-01-15 |
| 01-15/T-01-15-04 | Repudiation | concurrency proof | — | mitigate | CLOSED | E-01-15 |
| 01-16/T-01-16-01 | Spoofing | task generation | — | mitigate | CLOSED | E-01-16 |
| 01-16/T-01-16-02 | Tampering | attempt_count | — | mitigate | CLOSED | E-01-16 |
| 01-16/T-01-16-03 | Denial of Service | duplicate/stale delivery | — | mitigate | CLOSED | E-01-16 |
| 01-16/T-01-16-04 | Repudiation | retry scheduling | — | mitigate | CLOSED | E-01-16 |
| 01-16/T-01-16-05 | Information Disclosure | broker payload | — | mitigate | CLOSED | E-01-16 |
| 01-16/T-01-16-06 | Denial of Service | worker crash after CAS | — | mitigate | CLOSED | E-01-16 |
| 01-17/T-01-17-01 | Tampering | fact/source identity | — | mitigate | CLOSED | E-01-17 |
| 01-17/T-01-17-02 | Information Disclosure | tenant ownership | — | mitigate | CLOSED | E-01-17 |
| 01-17/T-01-17-03 | Repudiation | source provenance | — | mitigate | CLOSED | E-01-17 |
| 01-17/T-01-17-04 | Elevation of Privilege | migration backfill | — | mitigate | CLOSED | E-01-17 |
| 01-18/T-01-18-01 | Spoofing | ID-less player matching | — | mitigate | CLOSED | E-01-18 |
| 01-18/T-01-18-02 | Tampering | fallback deduplication | — | mitigate | CLOSED | E-01-18 |
| 01-18/T-01-18-03 | Repudiation | identity confidence | — | mitigate | CLOSED | E-01-18 |
| 01-18/T-01-18-04 | Information Disclosure | cross-team linkage | — | mitigate | CLOSED | E-01-18 |
| 01-19/T-01-19-01 | Information Disclosure | expired HTML | — | mitigate | CLOSED | E-01-19 |
| 01-19/T-01-19-02 | Tampering | purge heartbeat | — | mitigate | CLOSED | E-01-19 |
| 01-19/T-01-19-03 | Repudiation | purge execution | — | mitigate | CLOSED | E-01-19 |
| 01-19/T-01-19-04 | Denial of Service | blocked delete | — | mitigate | CLOSED | E-01-19 |
| 01-20/T-01-20-01 | Denial of Service | shared scheduler loop | — | mitigate | CLOSED | E-01-20 |
| 01-20/T-01-20-02 | Information Disclosure | purge errors | — | mitigate | CLOSED | E-01-20 |
| 01-20/T-01-20-03 | Repudiation | process restart | — | mitigate | CLOSED | E-01-20 |
| 01-20/T-01-20-04 | Elevation of Privilege | purge environment | — | mitigate | CLOSED | E-01-20 |
| 01-21/T-01-21-01 | Spoofing | `/readyz` | — | mitigate | CLOSED | E-01-21 |
| 01-21/T-01-21-02 | Information Disclosure | readiness response | — | mitigate | CLOSED | E-01-21 |
| 01-21/T-01-21-03 | Elevation of Privilege | purge process | — | mitigate | CLOSED | E-01-21 |
| 01-21/T-01-21-04 | Denial of Service | inherited health check | — | mitigate | CLOSED | E-01-21 |
| 01-21/T-01-21-05 | Tampering | retention configuration | — | mitigate | CLOSED | E-01-21 |
| 01-22/T-01-22-01 | Tampering | duplicate NCAA headers | — | mitigate | CLOSED | E-01-22 |
| 01-22/T-01-22-02 | Spoofing | stale parser-1 evidence | — | mitigate | CLOSED | E-01-22 |
| 01-22/T-01-22-03 | Repudiation | source provenance | — | mitigate | CLOSED | E-01-22 |
| 01-22/T-01-22-04 | Denial of Service | retry authority | — | mitigate | CLOSED | E-01-22 |
| 01-23/T-01-23-01 | Spoofing | same-name ID-less player | — | mitigate | CLOSED | E-01-23 |
| 01-23/T-01-23-02 | Tampering | reciprocal grouping | — | mitigate | CLOSED | E-01-23 |
| 01-23/T-01-23-03 | Repudiation | source/fact ownership | — | mitigate | CLOSED | E-01-23 |
| 01-24/T-01-24-01 | Spoofing | authoritative player identity | — | mitigate | CLOSED | E-01-24 |
| 01-24/T-01-24-02 | Tampering | maintenance/direct SQL | — | mitigate | CLOSED | E-01-24 |
| 01-24/T-01-24-03 | Information Disclosure | cross-organization identity | — | mitigate | CLOSED | E-01-24 |
| 01-24/T-01-24-04 | Repudiation | historical migration | — | mitigate | CLOSED | E-01-24 |
| 01-25/T-01-25-01 | Tampering | evidence expiry | — | mitigate | CLOSED | E-01-25 |
| 01-25/T-01-25-02 | Information Disclosure | retained HTML | — | mitigate | CLOSED | E-01-25 |
| 01-25/T-01-25-03 | Repudiation | migration cleanup | — | mitigate | CLOSED | E-01-25 |
| 01-26/T-01-26-01 | Spoofing | readiness | — | mitigate | CLOSED | E-01-26 |
| 01-26/T-01-26-02 | Tampering | worker configuration | — | mitigate | CLOSED | E-01-26 |
| 01-26/T-01-26-03 | Denial of Service | `_record_failure` rollback | — | mitigate | CLOSED | E-01-26 |
| 01-26/T-01-26-04 | Information Disclosure | config errors | — | mitigate | CLOSED | E-01-26 |
| 01-27/T-01-27-01 | Denial of Service | locked eligible row | — | mitigate | CLOSED | E-01-27 |
| 01-27/T-01-27-02 | Spoofing | purge heartbeat | — | mitigate | CLOSED | E-01-27 |
| 01-27/T-01-27-03 | Tampering | batch order | — | mitigate | CLOSED | E-01-27 |
| 01-27/T-01-27-04 | Information Disclosure | purge logs | — | mitigate | CLOSED | E-01-27 |
| 01-28/T-01-28-01 | Spoofing | actor_account_id | — | mitigate | CLOSED | E-01-28 |
| 01-28/T-01-28-02 | Tampering | repeated callbacks | — | mitigate | CLOSED | E-01-28 |
| 01-28/T-01-28-03 | Information Disclosure | event schema/output | — | mitigate | CLOSED | E-01-28 |
| 01-28/T-01-28-04 | Elevation of Privilege | organization filter | — | mitigate | CLOSED | E-01-28 |
| 01-29/T-01-29-01 | Spoofing | download_event_id | — | mitigate | CLOSED | E-01-29 |
| 01-29/T-01-29-02 | Tampering | repeated callback | — | mitigate | CLOSED | E-01-29 |
| 01-29/T-01-29-03 | Information Disclosure | route errors/logs | — | mitigate | CLOSED | E-01-29 |
| 01-29/T-01-29-04 | Denial of Service | tracking database failure | — | mitigate | CLOSED | E-01-29 |
| 01-30/T-01-30-01 | Spoofing | release completeness | — | mitigate | CLOSED | E-01-30 |
| 01-30/T-01-30-02 | Repudiation | verification evidence | — | mitigate | CLOSED | E-01-30 |
| 01-30/T-01-30-03 | Information Disclosure | usage runbook/output | — | mitigate | CLOSED | E-01-30 |
| 01-30/T-01-30-04 | Tampering | metric interpretation | — | mitigate | CLOSED | E-01-30 |
| 01-31/T-01-31-01 | Tampering | SourceRecord/Player/PlayerTeamSeason writes | — | mitigate | CLOSED | E-01-31 |
| 01-31/T-01-31-02 | Denial of Service | cross-domain concurrent writes | — | mitigate | CLOSED | E-01-31 |
| 01-31/T-01-31-03 | Elevation of Privilege | ownership predicates | — | mitigate | CLOSED | E-01-31 |
| 01-31/T-01-31-04 | Repudiation | publication certification | — | mitigate | CLOSED | E-01-31 |
| 01-32/T-01-32-01 | Tampering | result JSON | — | mitigate | CLOSED | E-01-32 |
| 01-32/T-01-32-02 | Spoofing | cache version identity | — | mitigate | CLOSED | E-01-32 |
| 01-32/T-01-32-03 | Denial of Service | stale-cache recollection | — | mitigate | CLOSED | E-01-32 |
| 01-32/T-01-32-04 | Repudiation | force refresh | — | mitigate | CLOSED | E-01-32 |
| 01-33/T-01-33-01 | Spoofing | unique-user aggregate | — | mitigate | CLOSED | E-01-33 |
| 01-33/T-01-33-02 | Denial of Service | native `/scrape` | — | mitigate | CLOSED | E-01-33 |
| 01-33/T-01-33-03 | Information Disclosure | dropped-event logs | — | mitigate | CLOSED | E-01-33 |
| 01-33/T-01-33-04 | Repudiation | dropped local analytics | — | mitigate | CLOSED | E-01-33 |
| 01-34/T-01-34-01 | Spoofing | `/api/downloads/ack` | — | mitigate | CLOSED | E-01-34 |
| 01-34/T-01-34-02 | Tampering | acknowledgement payload | — | mitigate | CLOSED | E-01-34 |
| 01-34/T-01-34-03 | Repudiation | ambiguous ack retry | — | mitigate | CLOSED | E-01-34 |
| 01-34/T-01-34-04 | Denial of Service | proof validation | — | mitigate | CLOSED | E-01-34 |
| 01-34/T-01-34-05 | Information Disclosure | receipt/logging | — | mitigate | CLOSED | E-01-34 |
| 01-34/T-01-34-06 | Spoofing | cross-worker proof signing | — | mitigate | CLOSED | E-01-34 |
| 01-35/T-01-35-01 | Spoofing | echoed receipt header | — | mitigate | CLOSED | E-01-35 |
| 01-35/T-01-35-02 | Repudiation | ambiguous retries | — | mitigate | CLOSED | E-01-35 |
| 01-35/T-01-35-03 | Information Disclosure | browser storage | — | mitigate | CLOSED | E-01-35 |
| 01-35/T-01-35-04 | Tampering | client acknowledgement order | — | mitigate | CLOSED | E-01-35 |
| 01-35/T-01-35-05 | Denial of Service | stuck pending ack | — | mitigate | CLOSED | E-01-35 |
| 01-36/T-01-36-01 | Tampering | final count/upsert | — | mitigate | CLOSED | E-01-36 |
| 01-36/T-01-36-02 | Denial of Service | purge batching | — | mitigate | CLOSED | E-01-36 |
| 01-36/T-01-36-03 | Repudiation | heartbeat timestamp | — | mitigate | CLOSED | E-01-36 |
| 01-36/T-01-36-04 | Information Disclosure | readiness | — | accept | ACCEPTED | E-01-36 |
| 01-37/T-01-37-01 | Tampering | release manifest | — | mitigate | CLOSED | E-01-37 |
| 01-37/T-01-37-02 | Repudiation | download metric definition | — | mitigate | CLOSED | E-01-37 |
| 01-37/T-01-37-03 | Information Disclosure | analytics operations | — | mitigate | CLOSED | E-01-37 |
| 01-37/T-01-37-04 | Denial of Service | release/migration safety | — | mitigate | CLOSED | E-01-37 |
| 01-37/T-01-37-05 | Spoofing | package/tool supply chain | — | accept | ACCEPTED | E-01-37 |
| 01-38/T-01-38-01 | Tampering | authoritative NCAA identity | — | mitigate | CLOSED | E-01-38 |
| 01-38/T-01-38-02 | Elevation of Privilege | tenant visibility boundary | — | mitigate | CLOSED | E-01-38 |
| 01-38/T-01-38-03 | Denial of Service | advisory-lock ordering | — | mitigate | CLOSED | E-01-38 |
| 01-38/T-01-38-04 | Repudiation | migration lifecycle | — | mitigate | CLOSED | E-01-38 |
| 01-38/T-01-38-SC | Tampering | package supply chain | — | accept | ACCEPTED | E-01-38 |
| 01-39/T-01-39-01 | Spoofing | acknowledgement Origin | — | mitigate | CLOSED | E-01-39 |
| 01-39/T-01-39-02 | Tampering | shared-runtime deployment configuration | — | mitigate | CLOSED | E-01-39 |
| 01-39/T-01-39-03 | Information Disclosure | startup errors | — | mitigate | CLOSED | E-01-39 |
| 01-39/T-01-39-04 | Denial of Service | custom-domain change | — | mitigate | CLOSED | E-01-39 |
| 01-39/T-01-39-SC | Tampering | package supply chain | — | mitigate | CLOSED | E-01-39 |
| 01-40/T-01-40-01 | Repudiation | acknowledgement terminal state | — | mitigate | CLOSED | E-01-40 |
| 01-40/T-01-40-02 | Tampering | pending UUID | — | mitigate | CLOSED | E-01-40 |
| 01-40/T-01-40-03 | Denial of Service | sessionStorage and 4xx trap | — | mitigate | CLOSED | E-01-40 |
| 01-40/T-01-40-04 | Information Disclosure | storage fallback | — | mitigate | CLOSED | E-01-40 |
| 01-40/T-01-40-SC | Tampering | package supply chain | — | accept | ACCEPTED | E-01-40 |
| 01-41/T-01-41-01 | Tampering | release diff base | — | mitigate | CLOSED | E-01-41 |
| 01-41/T-01-41-02 | Repudiation | committed phase changes | — | mitigate | CLOSED | E-01-41 |
| 01-41/T-01-41-03 | Denial of Service | full release gate | — | mitigate | CLOSED | E-01-41 |
| 01-41/T-01-41-04 | Information Disclosure | release logging | — | mitigate | CLOSED | E-01-41 |
| 01-41/T-01-41-SC | Tampering | package supply chain | — | accept | ACCEPTED | E-01-41 |

## Accepted risks

Only plan-authored `accept` dispositions are eligible here.

| Qualified threat | Accepted risk | Verification |
|---|---|---|
| 01-36/T-01-36-04 | Readiness exposes fixed health states and aggregate counts, not evidence content. | `app.py:391-422`; `roster_scout/evidence.py:356-380`; `tests/test_evidence.py:1178` |
| 01-37/T-01-37-05 | Plan 37 added no npm/pip/cargo package; Node uses its built-in test runner. | Plan 37 key files; `node --test tests/js/download_tracking.test.mjs` passed 16/16. |
| 01-38/T-01-38-SC | Plan 38 added no package; the change is PostgreSQL/Alembic/Python code and tests. | Plan 38 key files and migration 0014. |
| 01-40/T-01-40-SC | Plan 40 added no package; existing JavaScript and Node's built-in test runner are used. | `static/download_tracking.js`; `tests/js/download_tracking.test.mjs` passed 16/16. |
| 01-41/T-01-41-SC | Plan 41 added no package; existing Git/Python/Node/Alembic tools are used. | `scripts/validate_phase1_release.py`; validator contract tests passed. |

## Unregistered flags

None. Every SUMMARY threat-flag section present reports no new unmapped trust surface.

## Audit trail

- Loaded the phase project/requirements context, all 41 plan threat registers, all SUMMARY threat-flag sections, canonical review/fix/verification artifacts, and the implementation/tests referenced above.
- Parsed 169 declarations after the Plan 01-39 correction: 164 `mitigate`, 5 `accept`, 0 `transfer`.
- Verified all 164 mitigations against runtime/database/deployment code and counterexample tests; all are CLOSED.
- Locally reran 43 non-PostgreSQL parser/cache/identity/origin/secret/browser contract tests (all observed passed; the PostgreSQL acknowledgement class was skipped because `DATABASE_URL` was not set), 18 deployment/release-contract tests (18 passed), and 16 Node state-machine tests (16 passed).
- Corroborated PostgreSQL-only coverage with canonical `01-VERIFICATION.md`: full release gate PASS, 95 focused Python, 16 Node, revision-0014 lifecycle/drift, 429 full tests, 942-team catalog, 64 deployment tests, workbook/compile/template/diff gates.
- Reran the corrected origin/dependency/release-contract surface: 18 tests passed, covering the direct pin, Unicode and A-label canonicalization, malformed labels, fixed safe startup errors, browser-origin parity, deployment configuration, and mandatory release-manifest inclusion.
- Canonical `01-REVIEW.md` is clean (0 findings); `01-REVIEW-FIX.md` and `01-VERIFICATION.md` corroborate the pinned dependency and complete release-gate pass.

## Conclusion

Phase 1 is secured against its complete plan-authored threat register. No implementation security gap or unregistered threat flag remains.

