# Roster Scout - application map

This is a minimal ICM system map. The repository is small enough that this file is the catalog; the source remains the specification.

## Runtime flow

1. `templates/index.html` is public and loads the exact current-season NCAA selector from `GET /api/teams`. A signed-in selection is submitted to the same authenticated `POST /reports/new` saved-report route used by the portal, then the homepage polls the shared report-status contract and opens the finished report with equal Google Sheets and Excel export choices. The unlisted `GET /demo` route renders `templates/demo.html` and serves its video from `static/demo/`; the main page does not link to it.
2. If a visitor submits a valid catalog team while anonymous, the existing builder card transforms in place from team selection to school email and then six-digit WorkOS Magic Auth code entry. The selected team remains in page memory, the verified session receives a signed CSRF token, and the saved-report request resumes automatically without a login-page redirect. Explicit navbar sign-in uses the same card and continues to Saved Reports. The compatibility `POST /scrape` endpoint remains authenticated for API and legacy clients but is no longer the homepage browser path.
3. Verified `.edu` identities resolve locally to an organization through `OrganizationDomain`. The reviewed NCAA registry personalizes one-school roots with the exact school name and logo; ordinary subdomains fold to that root. Roots shared by multiple baseball programs never guess a school and keep exact verified subdomains as separate tenant boundaries. Non-`.edu`, malformed, IDNA-invalid, and suffix-lookalike domains never grant membership. Membership provenance distinguishes verified-domain access from invitation access.
4. The report route revalidates the exact team catalog selection and durably accepts or reuses the organization-owned report/job before any external collection. It dispatches the durable job best-effort and returns bounded report/status URLs to the browser. `app.py` retains the compatibility workbook workflow for authenticated legacy `POST /scrape` clients.
5. The NCAA workflow resolves baseballr-maintained team/season IDs and checks its successful-result cache under a per-team file lock. Concurrent workbook requests sharing the same machine/cache reuse the first successful collection. Durable detailed builds use one bounded request ledger across batting, pitching, fielding, seven batting and pitching split families, exact last-10 and conference scopes, transfer-aware career seasons, and current-season game logs. The hosted deployment opens NCAA pages through the proven Browserless session, while deterministic D1/D2/D3 fixtures certify the canonical schema independently of the optional live smoke boundary. NCAA failures remain explicit partial or unavailable states; there is no D1-only fallback or synthetic zero.
6. `roster_scraper.py` normalizes the compatibility workbook rows. Durable reports persist detailed typed facts, exact source/player/game/scope lineage, shared metric definitions, and explicit constant availability in the immutable snapshot. `roster_scout/player_detail.py` serves authorized same-snapshot season, split, career, and game drill projections without exposing source rows or URLs. Excel and future Power BI consumers read this one canonical boundary.
7. Publishing a durable `ready` or `partial` snapshot creates one pending XLSX artifact in the same PostgreSQL transaction. The artifact worker projects the exact snapshot's player identities, batting/pitching facts, shared metric values, Last 10 scopes, and conference scopes into the established two-sheet renderer. `Hitters` and `Pitchers` retain the original school-branded presentation while grouping columns as Player, Season, Derived Metrics, Last 10 Games, and Conference Games; deeper splits and history remain dashboard data instead of extra workbook pages. New artifacts use the `detailed-two-sheet-xlsx-v2` key while historical `basic-xlsx-v1` objects remain valid exact reads. The scheduler discovers missing rows, reclaims stale builds, and retries bounded failures. Historical downloads authorize the exact organization/report/snapshot/artifact tuple, verify the stored SHA-256, and never rebuild from current NCAA data.
8. Authenticated users can open the minimal saved-report portal for organization-shared reports, immutable snapshot history, explicit refresh, and exact historical downloads. When optional Google OAuth client secrets are configured, the exact authorized XLSX artifact can also be converted to a native Google Sheet through the least-privilege `drive.file` scope; access tokens remain request-local and are never persisted. The public `/privacy` notice documents this limited use, and the fixed Google verification endpoint proves the production URL-prefix ownership required by the OAuth brand. Primary navigation is limited to New report, Saved reports, and Account; organization administration remains available only through protected direct routes.

## Universes

- **Live:** `app.py`, `roster_scraper.py`, `templates/`, `static/`, `Procfile`, `requirements.txt`, `Dockerfile`, `compose.yaml`, `fly.toml`, and the scripts in `scripts/`.
- **Local-only:** the ignored `.cache/` directory containing NCAA lookup data, successful results, and the dedicated browser profile.
- **Leftover:** none verified.
- **Ghost:** none verified. A file is not live merely because it exists; it must be imported, invoked, or named by the deployment process.

## Change impact

| Change | Hits | Does not automatically hit |
|---|---|---|
| Homepage sign-in/resume behavior | `templates/index.html`, `static/report_status.js`, auth routes, identity transactions, and `/reports/new` authorization | NCAA source adapters |
| School-domain organization resolution | identity services, `OrganizationDomain`, membership provenance, migration tests, and tenant-isolation tests | Homepage visual design |
| School name/logo personalization | `data/institution-domain-map.json`, `roster_scout/institution_registry.py`, account shells, and `scripts/build_institution_domain_map.py` | NCAA collection adapters |
| Saved-report portal navigation | report-library templates/routes and authorization services | Public selector behavior |
| Form fields or JSON response names | `templates/index.html` and `/scrape` in `app.py` | Scraper source adapters |
| Product demo page or video | `templates/demo.html`, `static/demo/`, and `/demo` in `app.py` | Main-page navigation and scraper behavior |
| NCAA team selector or catalog validation | `/api/teams`, selector helpers in `roster_scraper.py`, and `scripts/validate_ncaa_catalog.py` | Excel formatting |
| NCAA/JUCO selection | `/scrape` dispatch and the matching scraper entry point | Excel formatting |
| NCAA IDs, tables, cache, or verification | NCAA helpers in `roster_scraper.py`, tests, and `DEPLOYMENT.md` | JUCO source adapters |
| Detailed NCAA fields, splits, scopes, history, game logs, or formulas | `roster_scout/ncaa_detail.py`, `roster_scout/scopes.py`, `roster_scout/metrics.py`, `roster_scout/jobs.py`, `roster_scout/snapshots.py`, revision `0025`, and Phase 3 fixtures/release tests | Legacy JUCO collection and workbook presentation |
| Player drill JSON or tenant denial behavior | `roster_scout/player_detail.py`, protected report routes, canonical snapshot ownership, and `SnapshotPlayerDetailRouteTests` | Raw NCAA source responses |
| Collection budgets or operator diagnostics | request-ledger persistence, `scripts/inspect_report_jobs.py`, sanitized lifecycle tokens, and reconciliation timing | Customer-visible player/source data |
| Player keys or output columns | Scraper normalization, `split_roster()`, `roster_scout/artifacts.py`, and workbook column specifications | Team discovery |
| Workbook layout, grouped columns, or number types | `roster_scout/workbooks.py`, `roster_scout/artifacts.py`, the `_create_workbook()` compatibility wrapper, and `tests/test_phase4_release.py` | Browser progress animation |
| Download filename or response headers | `/scrape` in `app.py` and the browser download handler | Scraper source adapters |
| Google Sheets export | `roster_scout/google_sheets.py`, protected report routes, report-detail UI, and Google Cloud OAuth client configuration | Roster Scout passwordless identity and scraper source adapters |
| Historical workbook bytes or storage | `roster_scout/artifacts.py`, `roster_scout/workbooks.py`, `roster_scout/tasks.py`, `roster_scout/report_routes.py`, and artifact migrations | Already-published immutable artifact bytes |
| Python imports or entry point | `requirements.txt`, `Procfile`, and import sites | Cached NCAA data |
| Fly sizing, health checks, or persistent cache | `fly.toml`, `Dockerfile`, `/healthz`, and `DEPLOYMENT.md` | Local native runner |

## Checks

Run from the repository root:

1. `uv run python -m py_compile app.py roster_scraper.py roster_scout/artifacts.py roster_scout/workbooks.py roster_scout/metrics.py roster_scout/ncaa_detail.py roster_scout/player_detail.py roster_scout/scopes.py scripts/build_phase4_preview.py scripts/capture_phase3_ncaa_fixtures.py scripts/inspect_report_jobs.py scripts/live_ncaa_smoke.py scripts/validate_ncaa_catalog.py scripts/validate_phase21_release.py scripts/validate_phase3_release.py scripts/validate_phase4_release.py`
2. Confirm `templates/index.html` and `templates/demo.html` exist because `app.py` renders both templates.
3. With the executor-owned disposable loopback PostgreSQL test database running, run `PHASE2_DESTRUCTIVE_DATABASE=local-disposable DATABASE_URL=postgresql+psycopg://roster_scout:roster_scout@127.0.0.1:5432/roster_scout_test uv run python scripts/validate_phase4_release.py`. This is the authoritative workbook gate: it proves the two-sheet/five-group schema, D1/D2/D3 parity, numeric typing, safe strings, canonical season and scoped values, immutable schema-versioned artifacts, a production-renderer preview, and the complete inherited Phase 3/2.1 release gates. It refuses non-loopback, non-PostgreSQL, or non-`_test` databases and never deploys.
4. Optionally run `python3 scripts/live_ncaa_smoke.py` for the external NCAA boundary. Akamai or browser rejection can fail this smoke independently without invalidating the deterministic fixture and canonical release gates.
5. Run `python3 scripts/validate_ncaa_catalog.py` to validate every current-season D1/D2/D3 selector record in one pass.
6. Confirm `git diff --check` passes. The corrective gate does not deploy and does not authorize production launch.

External boundaries not represented in this tree: managed PostgreSQL/Redis,
private S3-compatible workbook storage and credentials, identity-provider
configuration, proxy account/credentials, and upstream sports websites/Akamai policy. The
October 29, 2026 Fly feasibility run verified the app infrastructure but also
verified that unproxied Fly egress receives NCAA `Access Denied` responses.
