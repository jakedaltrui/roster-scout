# Roster Scout - application map

This is a minimal ICM system map. The repository is small enough that this file is the catalog; the source remains the specification.

## Runtime flow

1. `templates/index.html` sends `team` and `division` to `POST /scrape`.
2. `app.py` validates the request and dispatches D1/D2/D3 to the NCAA workflow or JUCO to its existing workflow in `roster_scraper.py`.
3. The NCAA workflow resolves baseballr-maintained team/season IDs, checks its successful-result cache, requests batting and pitching grids directly, and uses a dedicated persistent Patchright profile on a headed browser/virtual display when NCAA blocks HTTP. It handles NCAA's statistics-terms screen before retrying the grid. D1Baseball remains a D1-only fallback.
4. `roster_scraper.py` normalizes player rows and splits hitters from pitchers.
5. `app.py` creates an in-memory `.xlsx` with Hitters and Pitchers worksheets and returns it as a browser download.

## Universes

- **Live:** `app.py`, `roster_scraper.py`, `templates/index.html`, `Procfile`, `requirements.txt`, `Dockerfile`, `compose.yaml`, and the scripts in `scripts/`.
- **Local-only:** the ignored `.cache/` directory containing NCAA lookup data, successful results, and the dedicated browser profile.
- **Leftover:** none verified.
- **Ghost:** none verified. A file is not live merely because it exists; it must be imported, invoked, or named by the deployment process.

## Change impact

| Change | Hits | Does not automatically hit |
|---|---|---|
| Form fields or JSON response names | `templates/index.html` and `/scrape` in `app.py` | Scraper source adapters |
| NCAA/JUCO selection | `/scrape` dispatch and the matching scraper entry point | Excel formatting |
| NCAA IDs, tables, cache, or verification | NCAA helpers in `roster_scraper.py`, tests, and `DEPLOYMENT.md` | JUCO source adapters |
| Player keys or output columns | Scraper normalization, `split_roster()`, and workbook headers | Team discovery |
| Workbook layout or number types | `_create_workbook()` and `_write_worksheet()` | Browser progress animation |
| Download filename or response headers | `/scrape` in `app.py` and the browser download handler | Scraper source adapters |
| Python imports or entry point | `requirements.txt`, `Procfile`, and import sites | Cached NCAA data |

## Checks

Run from the repository root:

1. `python3 -m py_compile app.py roster_scraper.py scripts/live_ncaa_smoke.py`
2. Confirm `templates/index.html` exists because `app.py` uses Flask's `render_template("index.html")`.
3. Run `python3 -m unittest discover -s tests -v`; tests use local NCAA fixtures and inspect the generated workbook response.
4. Optionally run `python3 scripts/live_ncaa_smoke.py` for the external NCAA boundary; it can fail when Akamai rejects the current IP/browser.
5. Confirm `git diff --check` passes.

External boundaries not represented in this tree: hosting-provider configuration and upstream sports websites/Akamai policy.
