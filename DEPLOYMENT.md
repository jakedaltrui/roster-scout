# Roster Scout deployment

## Recommended on macOS: native local runner

This is the free, no-cold-start option that works with NCAA on a Mac. NCAA
rejects ordinary HTTP, headless browsers, and the Linux browser bundled in the
Docker image, but the native runner can use installed Google Chrome through
Patchright.

1. Run `scripts/setup_local.sh` once.
2. Run `scripts/run_local.sh`.
3. Open `http://localhost:5050`.

No Google account, OAuth token, or cloud storage is required. Each successful
request downloads a two-tab Excel workbook directly in the browser.

The ignored project `.cache/` directory persists:

- baseballr's NCAA team and season lookup CSVs;
- successful normalized team results (six-hour default);
- a dedicated NCAA browser profile and its verification state.

The first uncached NCAA request can briefly open Chrome while the dedicated
profile accepts NCAA's statistics terms. Later requests reuse that profile and
successful team results.

## Docker and hosted deployment

The included `Dockerfile` can run on Render, Fly.io, Railway, or another Docker host. Expose port 5000. A small instance should use one worker because Chromium can require roughly 1 GB while NCAA verification is active.

Docker remains useful for consistent packaging and for cached results, but its
Linux browser is currently rejected by NCAA from this network. Treat live
D2/D3 scraping in Docker as unsupported unless `NCAA_BROWSER_PROXY` points to a
network/browser path that NCAA accepts. The native macOS runner is the verified
free deployment.

Free hosted services commonly suspend inactive containers. Docker solves packaging and browser consistency, but it does not prevent a provider-imposed cold start. The local Compose service avoids that tradeoff.

## Relevant environment variables

| Variable | Default | Purpose |
|---|---:|---|
| `ROSTER_SCOUT_CACHE_DIR` | project `.cache/` | Persistent cache and browser state location |
| `NCAA_RESULT_CACHE_TTL_SECONDS` | `21600` | Successful team result lifetime (six hours) |
| `NCAA_LOOKUP_CACHE_TTL_SECONDS` | `86400` | Team/season metadata lifetime (one day) |
| `NCAA_BROWSER_FALLBACK` | `1` | Set to `0` to disable Playwright |
| `NCAA_BROWSER_HEADLESS` | `1` in Python; `0` in Docker | NCAA blocks headless fingerprints; Docker supplies a virtual display for headed Patchright |
| `NCAA_BROWSER_VERIFY_TIMEOUT_SECONDS` | `45` | Maximum challenge wait per page |
| `NCAA_TERMS_DWELL_MILLISECONDS` | `8000` | Delay before accepting NCAA's statistics terms page |
| `NCAA_BROWSER_EXECUTABLE_PATH` | unset | Use a specific Chromium/Chrome executable |
| `NCAA_BROWSER_CHANNEL` | unset; `chrome` in local runner | Patchright browser channel |
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

NCAA's Akamai policy remains an external boundary. Roster Scout uses Patchright
with a headed browser fingerprint and accepts NCAA's statistics terms in its
dedicated profile; failed or blocked pages are never cached. D1 requests can
still use the existing D1Baseball fallback; D2/D3 report a block instead of
returning invented or stale data.
