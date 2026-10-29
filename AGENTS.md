# Roster Scout

Roster Scout is an independent Flask app that turns an NCAA or JUCO baseball team name into a downloadable Excel workbook of roster and season statistics.

## Where things live

| Path | What it owns |
|---|---|
| `CONTEXT.md` | Runtime flow, system boundaries, and change-impact map |
| `app.py` | HTTP routes, validation, and Excel workbook download creation |
| `roster_scraper.py` | Team discovery, source-specific scraping, normalization, and hitter/pitcher output columns |
| `templates/index.html` | Browser UI and the `/scrape` request/response contract |
| `Procfile`, `requirements.txt` | Render process and Python dependencies |

## Route by task

| If you are changing | Open first | Also inspect |
|---|---|---|
| Browser form or progress states | `templates/index.html` | `app.py` route payloads |
| API validation or workbook delivery | `app.py` | `templates/index.html`, `roster_scraper.py` exports |
| NCAA, JUCO, Sidearm, or fallback scraping | `roster_scraper.py` | `app.py` dispatch |
| Workbook columns or player classification | `roster_scraper.py` | `app.py` worksheet writer |
| Deployment | `Procfile`, `requirements.txt` | `app.py` entry point |

## Boundaries

- This repository is functionally independent of the surrounding Axis Baseball workspace.
- Treat any legacy `credentials.json`, `oauth_credentials.json`, and `token.json` files as local secrets. They are not used by the app; do not read or commit them.
- Read only the files named for the task above; do not load the whole scraper for a UI-only change.
- Before handing off, run the checks in `CONTEXT.md`.
