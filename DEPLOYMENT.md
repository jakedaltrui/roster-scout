# Roster Scout deployment

## Recommended: always-on local Docker

This is the free, no-cold-start option. Docker keeps the Python app, Chromium fallback, and successful NCAA cache running on the same machine.

1. Put `GOOGLE_TOKEN_JSON` in an ignored `.env` file as one line.
2. Run `docker compose up --build -d`.
3. Open `http://localhost:5000`.

The named `roster-scout-cache` volume persists:

- baseballr's NCAA team and season lookup CSVs;
- successful normalized team results (six-hour default);
- a dedicated NCAA browser profile and its verification state.

## Hosted Docker

The included `Dockerfile` can run on Render, Fly.io, Railway, or another Docker host. Configure `GOOGLE_TOKEN_JSON` as a secret and expose port 5000. A small instance should use one worker because Chromium can require roughly 1 GB while NCAA verification is active.

Free hosted services commonly suspend inactive containers. Docker solves packaging and browser consistency, but it does not prevent a provider-imposed cold start. The local Compose service avoids that tradeoff.

## Relevant environment variables

| Variable | Default | Purpose |
|---|---:|---|
| `ROSTER_SCOUT_CACHE_DIR` | project `.cache/` | Persistent cache and browser state location |
| `NCAA_RESULT_CACHE_TTL_SECONDS` | `21600` | Successful team result lifetime (six hours) |
| `NCAA_LOOKUP_CACHE_TTL_SECONDS` | `86400` | Team/season metadata lifetime (one day) |
| `NCAA_BROWSER_FALLBACK` | `1` | Set to `0` to disable Playwright |
| `NCAA_BROWSER_HEADLESS` | `1` | Set to `0` locally for visible NCAA verification |
| `NCAA_BROWSER_VERIFY_TIMEOUT_SECONDS` | `45` | Maximum challenge wait per page |
| `NCAA_BROWSER_EXECUTABLE_PATH` | unset | Use a specific Chromium/Chrome executable |
| `NCAA_BROWSER_CHANNEL` | unset | Playwright browser channel such as `chrome` |
| `NCAA_BROWSER_PROFILE_DIR` | cache directory | Dedicated persistent browser profile; do not point this at a personal Chrome profile |
| `NCAA_BROWSER_PROXY` | unset | Optional browser proxy server URL |
| `NCAA_SEASON_YEAR` | current NCAA season | Override the requested season |

Only successful parsed team results are cached. Blocks, empty pages, and parser failures are never cached.

## Verification

Run deterministic tests:

```bash
python -m unittest discover -s tests -v
```

Run the credential-free live smoke matrix:

```bash
python scripts/live_ncaa_smoke.py
```

The live smoke test uses Florida State (D1), Tampa (D2), and Scranton (D3). NCAA may require a visible verification once on a new IP. The dedicated profile retains that verification for later runs:

```bash
NCAA_BROWSER_HEADLESS=0 python scripts/live_ncaa_smoke.py
```

NCAA's Akamai policy remains an external boundary: it can reject direct HTTP,
bundled Chromium, and installed Chrome even after a visible attempt. Failed or
blocked pages are never cached. D1 requests can still use the existing
D1Baseball fallback; D2/D3 report the block instead of returning invented or
stale data.
