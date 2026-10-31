# Deployment guide

Roster Scout runs as a standard Flask/Gunicorn service. Live NCAA collection may require Browserless or an approved proxy because upstream anti-bot behavior varies by network.

## Native local runner

The bundled scripts create an isolated environment, collect the Browserless token without echoing it, and bind Gunicorn to localhost.

```bash
./scripts/setup_local.sh
./scripts/set_browserless_token.sh
./scripts/run_local.sh
```

Open [http://localhost:5050](http://localhost:5050). The token is stored with owner-only permissions in the Git-ignored `.browserless-token` file. The runner never opens or reuses a personal Chrome profile.

## Docker Compose

```bash
docker compose up --build
```

The app is available at [http://localhost:5050](http://localhost:5050). Compose persists lookup and successful-result caches in a named volume. Pass secrets at runtime through your shell, an untracked env file, or a secret manager; do not add them to `compose.yaml`.

The image:

- uses Python 3.12 and pinned direct application dependencies;
- installs Chrome for the Patchright fallback;
- writes only to the configured cache directory; and
- serves the app as the unprivileged `roster-scout` user.

## Fly.io

`fly.toml` defines the service, persistent cache volume, HTTPS enforcement, health check, and one-machine concurrency limits.

```bash
fly auth login
fly launch --copy-config --no-deploy
fly secrets set BROWSERLESS_TOKEN=your-token
fly deploy
```

Keep secrets out of command history when possible by using the Fly dashboard or a secure shell input method. The checked-in configuration deliberately keeps TLS verification enabled.

Before making a deployment public:

1. Run the deterministic suite and catalog validation.
2. Run `python scripts/live_ncaa_smoke.py` from the target network.
3. Confirm `/healthz` returns `{"status":"ok"}` over HTTPS.
4. Confirm rate limiting returns HTTP 429 with `Retry-After`.
5. Confirm a real workbook download opens in Excel and contains both worksheets.
6. Review memory use during one uncached browser session.

## Environment variables

| Variable | Default | Purpose |
|---|---:|---|
| `PORT` | `5000` | HTTP port |
| `ROSTER_SCOUT_CACHE_DIR` | project `.cache/` | Lookup data, successful results, rate-limit state, and browser locks |
| `LOG_LEVEL` | `INFO` | Python log level for the development entry point |
| `SCRAPE_RATE_LIMIT` | `10` | Allowed workbook requests per client and window |
| `SCRAPE_RATE_WINDOW_SECONDS` | `600` | Rate-limit window |
| `TRUST_FLY_CLIENT_IP` | unset | Trust Fly.io's platform-provided client IP header when set to `1` |
| `NCAA_RESULT_CACHE_TTL_SECONDS` | `21600` | Successful team result lifetime |
| `NCAA_LOOKUP_CACHE_TTL_SECONDS` | `86400` | Team and season catalog lifetime |
| `NCAA_SEASON_YEAR` | current season | Optional season override |
| `NCAA_BROWSER_PROVIDER` | local browser | Set to `browserless` to use the hosted browser workflow |
| `BROWSERLESS_TOKEN` | unset | Browserless API token |
| `BROWSERLESS_MAX_SESSIONS` | `2` | Concurrent Browserless slots on one machine |
| `BROWSERLESS_PROXY` | `residential` | Browserless proxy network |
| `BROWSERLESS_PROXY_COUNTRY` | `us` | Browserless proxy country |
| `NCAA_BROWSER_FALLBACK` | `1` | Enable the local Patchright fallback |
| `NCAA_BROWSER_HEADLESS` | `1` | Run the local browser headlessly |
| `NCAA_BROWSER_CHANNEL` | unset | Optional installed browser channel, such as `chrome` |
| `NCAA_BROWSER_PROFILE_DIR` | cache subdirectory | Dedicated browser profile; never point this at a personal profile |
| `NCAA_BROWSER_PROXY` | unset | Optional credentialed proxy used by the browser workflow |
| `NCAA_HTTP_PROXY` | browser proxy | Optional separate proxy for direct NCAA HTTP requests |
| `NCAA_PROXY_TLS_VERIFY` | `1` | Verify TLS certificates for proxied NCAA HTTP |
| `NCAA_SITE_UNBLOCKER` | `0` | Enable supported Site Unblocker session behavior |
| `NCAA_BROWSER_VERIFY_TIMEOUT_SECONDS` | `45` | Maximum browser verification wait per page |

See `.env.example` for a safe starting configuration. Only successful parsed results are cached; blocks, malformed pages, and empty results are discarded.

## Scaling boundaries

The rate limiter and Browserless capacity locks coordinate processes on one machine through the cache directory. They are not global across multiple machines. Before horizontal scaling, move both controls to a shared store such as Redis or a transactional database.

NCAA availability is an external boundary. A healthy container and `/healthz` response do not guarantee that the current hosting network can retrieve a live stats page. Keep the credential-free smoke test in the release checklist.
