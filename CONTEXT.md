# Roster Scout - application map

This is a minimal ICM system map. The repository is small enough that this file is the catalog; the source remains the specification.

## Runtime flow

1. `templates/index.html` sends `team`, `email`, and `division` to `POST /scrape`.
2. `app.py` validates the request and dispatches D1/D2/D3 to the NCAA workflow or JUCO to its existing workflow in `roster_scraper.py`.
3. The NCAA workflow resolves baseballr-maintained team/season IDs, checks its successful-result cache, requests batting and pitching grids directly, and uses a dedicated persistent Playwright profile only when NCAA blocks HTTP. D1Baseball remains a D1-only fallback.
4. `roster_scraper.py` normalizes player rows and splits hitters from pitchers.
5. `app.py` creates two Google Sheet tabs, shares the Sheet with the requested email, and returns its URL.

## Universes

- **Live:** `app.py`, `roster_scraper.py`, `templates/index.html`, `get_token.py`, `Procfile`, `requirements.txt`, `Dockerfile`, and `compose.yaml`.
- **Local-only:** ignored OAuth JSON files used by `app.py` and `get_token.py`.
- **Leftover:** none verified.
- **Ghost:** none verified. A file is not live merely because it exists; it must be imported, invoked, or named by the deployment process.

## Change impact

| Change | Hits | Does not automatically hit |
|---|---|---|
| Form fields or JSON response names | `templates/index.html` and `/scrape` in `app.py` | Scraper source adapters |
| NCAA/JUCO selection | `/scrape` dispatch and the matching scraper entry point | Google Sheet authorization |
| NCAA IDs, tables, cache, or verification | NCAA helpers in `roster_scraper.py`, tests, and `DEPLOYMENT.md` | JUCO source adapters |
| Player keys or output columns | Scraper normalization, `split_roster()`, and Sheet headers | OAuth token generation |
| Google scopes or credentials | `_get_creds()`, `get_token.py`, and Render environment | Team discovery |
| Sheet tab layout or sharing | `_write_tab()` and `_create_and_share()` | Browser progress animation |
| Python imports or entry point | `requirements.txt`, `Procfile`, and import sites | OAuth JSON contents |

## Checks

Run from the repository root:

1. `python3 -m py_compile app.py roster_scraper.py get_token.py scripts/live_ncaa_smoke.py`
2. Confirm `templates/index.html` exists because `app.py` uses Flask's `render_template("index.html")`.
3. Run `python3 -m unittest discover -s tests -v`; tests use local NCAA fixtures and mocked Google delivery.
4. Optionally run `python3 scripts/live_ncaa_smoke.py` for the external NCAA boundary; it can fail when Akamai rejects the current IP/browser.
5. Confirm `git diff --check` passes and ignored OAuth files remain untracked.

External boundaries not represented in this tree: hosting-provider configuration, Google Cloud OAuth configuration, the target Drive folder, and upstream sports websites/Akamai policy.
