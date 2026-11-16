# Roster Scout - application map

This is a minimal ICM system map. The repository is small enough that this file is the catalog; the source remains the specification.

## Runtime flow

1. `templates/index.html` is public and loads the exact current-season NCAA selector from `GET /api/teams`. It sends `team`, `division`, and `season_team_id` to `POST /scrape`; JUCO remains free text. The unlisted `GET /demo` route renders `templates/demo.html` and serves its video from `static/demo/`; the main page does not link to it.
2. `POST /scrape` requires an authenticated session and same-origin CSRF token. If a visitor submits a valid catalog team while anonymous, the existing builder card transforms in place from team selection to school email and then six-digit WorkOS Magic Auth code entry. The selected team remains in page memory, the verified session receives a signed CSRF token, and the workbook request resumes automatically without a login-page redirect. Explicit navbar sign-in uses the same card and continues to Saved Reports.
3. Verified `.edu` identities resolve locally to an organization through `OrganizationDomain`. The reviewed NCAA registry personalizes one-school roots with the exact school name and logo; ordinary subdomains fold to that root. Roots shared by multiple baseball programs never guess a school and keep exact verified subdomains as separate tenant boundaries. Non-`.edu`, malformed, IDNA-invalid, and suffix-lookalike domains never grant membership. Membership provenance distinguishes verified-domain access from invitation access.
4. `app.py` revalidates the exact team catalog selection and durably accepts or reuses the organization-owned report/job before any external collection. It dispatches the durable job best-effort, then runs the compatibility workbook workflow so the original immediate `.xlsx` download remains available. The response includes the saved-report URL for the minimal report portal.
5. The NCAA workflow resolves baseballr-maintained team/season IDs and checks its successful-result cache under a per-team file lock. Concurrent workbook requests sharing the same machine/cache reuse the first successful collection. The hosted deployment opens batting and pitching in one Browserless sticky residential browser, accepts NCAA's terms in that browser, and requires `#stat_grid` before parsing. Cleanup explicitly sends CDP `Browser.close` before releasing capacity; upstream Browserless 429s return the retryable busy response. The Site Unblocker HTTP workflow and a dedicated local Patchright profile remain fallback options for other deployments. NCAA failures are returned consistently for D1, D2, and D3; there is no D1-only external stats fallback.
6. `roster_scraper.py` normalizes player rows and splits hitters from pitchers. Both the immediate homepage download and saved-report artifacts use `roster_scout/workbooks.py` for the same branded Hitters/Pitchers workbook renderer; the acknowledgement endpoint records a successful download exactly once.
7. Publishing a durable `ready` or `partial` snapshot creates one pending XLSX artifact in the same PostgreSQL transaction. The worker runs that snapshot's exact batting and pitching source rows through the homepage normalization/split boundary, then renders the canonical workbook into private immutable storage. The scheduler discovers missing rows, reclaims stale builds, and retries bounded failures. Historical downloads authorize the exact organization/report/snapshot/artifact tuple, verify the stored SHA-256, and never rebuild from current NCAA data.
8. Authenticated users can open the minimal saved-report portal for organization-shared reports, immutable snapshot history, explicit refresh, and exact historical downloads. When optional Google OAuth client secrets are configured, the exact authorized XLSX artifact can also be converted to a native Google Sheet through the least-privilege `drive.file` scope; access tokens remain request-local and are never persisted. Primary navigation is limited to New report, Saved reports, and Account; organization administration remains available only through protected direct routes.

## Universes

- **Live:** `app.py`, `roster_scraper.py`, `templates/`, `static/`, `Procfile`, `requirements.txt`, `Dockerfile`, `compose.yaml`, `fly.toml`, and the scripts in `scripts/`.
- **Local-only:** the ignored `.cache/` directory containing NCAA lookup data, successful results, and the dedicated browser profile.
- **Leftover:** none verified.
- **Ghost:** none verified. A file is not live merely because it exists; it must be imported, invoked, or named by the deployment process.

## Change impact

| Change | Hits | Does not automatically hit |
|---|---|---|
| Homepage sign-in/resume behavior | `templates/index.html`, auth routes, identity transactions, and `/scrape` authorization | NCAA source adapters |
| School-domain organization resolution | identity services, `OrganizationDomain`, membership provenance, migration tests, and tenant-isolation tests | Homepage visual design |
| School name/logo personalization | `data/institution-domain-map.json`, `roster_scout/institution_registry.py`, account shells, and `scripts/build_institution_domain_map.py` | NCAA collection adapters |
| Saved-report portal navigation | report-library templates/routes and authorization services | Public selector behavior |
| Form fields or JSON response names | `templates/index.html` and `/scrape` in `app.py` | Scraper source adapters |
| Product demo page or video | `templates/demo.html`, `static/demo/`, and `/demo` in `app.py` | Main-page navigation and scraper behavior |
| NCAA team selector or catalog validation | `/api/teams`, selector helpers in `roster_scraper.py`, and `scripts/validate_ncaa_catalog.py` | Excel formatting |
| NCAA/JUCO selection | `/scrape` dispatch and the matching scraper entry point | Excel formatting |
| NCAA IDs, tables, cache, or verification | NCAA helpers in `roster_scraper.py`, tests, and `DEPLOYMENT.md` | JUCO source adapters |
| Player keys or output columns | Scraper normalization, `split_roster()`, and workbook headers | Team discovery |
| Workbook layout or number types | `roster_scout/workbooks.py` and the `_create_workbook()` compatibility wrapper | Browser progress animation |
| Download filename or response headers | `/scrape` in `app.py` and the browser download handler | Scraper source adapters |
| Google Sheets export | `roster_scout/google_sheets.py`, protected report routes, report-detail UI, and Google Cloud OAuth client configuration | Roster Scout passwordless identity and scraper source adapters |
| Historical workbook bytes or storage | `roster_scout/artifacts.py`, `roster_scout/workbooks.py`, `roster_scout/tasks.py`, `roster_scout/report_routes.py`, and artifact migrations | Already-published immutable artifact bytes |
| Python imports or entry point | `requirements.txt`, `Procfile`, and import sites | Cached NCAA data |
| Fly sizing, health checks, or persistent cache | `fly.toml`, `Dockerfile`, `/healthz`, and `DEPLOYMENT.md` | Local native runner |

## Checks

Run from the repository root:

1. `uv run python -m py_compile app.py roster_scraper.py roster_scout/artifacts.py roster_scout/workbooks.py scripts/live_ncaa_smoke.py scripts/validate_ncaa_catalog.py scripts/validate_phase21_release.py`
2. Confirm `templates/index.html` and `templates/demo.html` exist because `app.py` renders both templates.
3. With the disposable local PostgreSQL test database running, run `PHASE2_DESTRUCTIVE_DATABASE=local-disposable DATABASE_URL=postgresql+psycopg://roster_scout:roster_scout@127.0.0.1:5432/roster_scout_test uv run python scripts/validate_phase21_release.py`. This is the authoritative Phase 2.1 gate and includes migrations, the complete customer story, Python and Node regressions, catalog checks, deployment contracts, compilation, and diff hygiene.
4. Optionally run `python3 scripts/live_ncaa_smoke.py` for the external NCAA boundary; it can fail when Akamai rejects the current IP/browser.
5. Run `python3 scripts/validate_ncaa_catalog.py` to validate every current-season D1/D2/D3 selector record in one pass.
6. Confirm `git diff --check` passes. The corrective gate does not deploy and does not authorize production launch.

External boundaries not represented in this tree: managed PostgreSQL/Redis,
private S3-compatible workbook storage and credentials, identity-provider
configuration, proxy account/credentials, and upstream sports websites/Akamai policy. The
October 29, 2026 Fly feasibility run verified the app infrastructure but also
verified that unproxied Fly egress receives NCAA `Access Denied` responses.
