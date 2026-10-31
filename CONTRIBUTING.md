# Contributing

Thanks for improving Roster Scout.

1. Create a focused branch from `main`.
2. Keep source-specific parsing inside `roster_scraper.py` and HTTP/workbook behavior inside `app.py`.
3. Add or update a deterministic fixture test for behavior changes. Do not make the default suite depend on live sports sites.
4. Run the checks from `CONTEXT.md`.
5. Open a pull request that explains the user-visible change, test evidence, and any upstream-data assumptions.

Never commit tokens, proxy URLs with credentials, browser profiles, live caches, or generated player workbooks. See [SECURITY.md](SECURITY.md) for private vulnerability reports.
