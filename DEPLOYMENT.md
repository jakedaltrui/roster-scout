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
docker compose --profile worker up --build
```

The app is available at [http://localhost:5050](http://localhost:5050). Compose persists lookup and successful-result caches in a named volume. Pass secrets at runtime through your shell, an untracked env file, or a secret manager; do not add them to `compose.yaml`.

The `worker` profile starts the separate Celery process with PostgreSQL as the
durable job store and Redis only as its broker. Both app and worker use the same
PostgreSQL advisory-lock namespace for Browserless account capacity; the worker
does not mount the app's persistent `/data` cache. PostgreSQL and Redis host
ports are development-only and bind to `127.0.0.1`.

The dispatch scheduler is a separate long-running process. It calls the durable
outbox drain and stale-job reconciliation services at bounded intervals; it
does not duplicate job-state SQL. Configure it with nonsecret timing values:

| Variable | Default | Valid range | Purpose |
|---|---:|---:|---|
| `REPORT_DISPATCH_POLL_SECONDS` | `5` | 1-300 | Delay between reconciliation cycles |
| `REPORT_DISPATCH_LEASE_SECONDS` | `60` | 5-600 | Publisher lease held by one bounded drain |
| `REPORT_QUEUED_REDISPATCH_SECONDS` | `300` | 30-86400 | Deadline before a lost queued delivery is eligible again |
| `REPORT_RUNNING_STALE_SECONDS` | `1800` | 60-604800 | Minimum age before unclaimed running work is recovered |

The poll interval may not exceed the lease duration, and the queued deadline
must remain below the running stale threshold. Invalid values stop the process
before database or broker clients are created. Scheduler logs contain health
and aggregate counts only, never connection URLs, raw exceptions, or task data.

Apply migrations before starting either process and verify rollback safety in
the disposable test database:

```bash
DATABASE_URL=postgresql+psycopg://roster_scout:roster_scout@localhost:5432/roster_scout alembic upgrade head
DATABASE_URL=postgresql+psycopg://roster_scout:roster_scout@localhost:5432/roster_scout_test alembic downgrade base
DATABASE_URL=postgresql+psycopg://roster_scout:roster_scout@localhost:5432/roster_scout_test alembic upgrade head
```

The image:

- uses Python 3.12 and pinned direct application dependencies;
- installs Chrome for the Patchright fallback;
- writes only to the configured cache directory; and
- serves the app as the unprivileged `roster-scout` user.

## Fly.io

`fly.toml` defines separate `app` and `worker` process groups. Its release command
runs `alembic upgrade head` once before the new release receives traffic. Only
the app process has the HTTP service and `/data` volume; the worker has neither.

```bash
fly auth login
fly launch --copy-config --no-deploy
fly secrets set BROWSERLESS_TOKEN=your-token DATABASE_URL=your-managed-postgres-url CELERY_BROKER_URL=your-managed-redis-url
fly scale count app=1 worker=1
fly deploy
```

Keep secrets out of command history when possible by using the Fly dashboard or a secure shell input method. The checked-in configuration deliberately keeps TLS verification enabled.

Before making a deployment public:

1. Keep production launch blocked until all four prerequisites are verified:
   managed PostgreSQL, managed Redis, managed object/evidence storage, and
   uninterrupted Fly worker billing/capacity. Local Compose is development-only
   and does not satisfy this production provisioning gate. Do not deploy a
   durable worker on trial capacity that can stop after five minutes.
2. Confirm the release migration succeeded before either process group starts.
3. Run the deterministic suite and catalog validation.
4. Run `python scripts/live_ncaa_smoke.py` from the target network.
5. Confirm `/healthz` returns `{"status":"ok"}` over HTTPS.
6. Confirm rate limiting returns HTTP 429 with `Retry-After`.
7. Confirm a real workbook download opens in Excel and contains both worksheets.
8. Review memory use during one uncached browser session.

## Deterministic release verification

Run the complete local gate from the repository root before a release. The
PostgreSQL URL must point to the disposable database ending in `_test`:

```bash
.venv/bin/python -m py_compile app.py roster_scraper.py usage_limits.py roster_scout/*.py scripts/live_ncaa_smoke.py scripts/validate_ncaa_catalog.py
DATABASE_URL=postgresql+psycopg://roster_scout:roster_scout@localhost:5432/roster_scout_test .venv/bin/alembic upgrade head
DATABASE_URL=postgresql+psycopg://roster_scout:roster_scout@localhost:5432/roster_scout_test .venv/bin/alembic check
DATABASE_URL=postgresql+psycopg://roster_scout:roster_scout@localhost:5432/roster_scout_test .venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/validate_ncaa_catalog.py
git diff --check
```

The deterministic suite includes D1, D2, and D3 fixture publication, exact
source-row traceability, duplicate delivery, same-job recovery, legacy cache,
selector, workbook, branding, error, rate-limit, and Browserless-capacity
regressions. Catalog validation must report `"valid": true`, 942 selectable
teams, and the reviewed D1/D2/D3 counts unless a catalog update is part of the
release.

Confirm the worker stays inside the account-wide capacity boundary without
starting a process:

```bash
BROWSERLESS_ACCOUNT_MAX_SESSIONS=2 CELERY_WORKER_CONCURRENCY=2 .venv/bin/python -c "from scripts.run_worker import worker_command; command = worker_command(); assert command[-2:] == ['--concurrency', '2']; print('worker concurrency: 2')"
docker compose config --quiet
```

After deterministic verification, the optional external smoke is:

```bash
.venv/bin/python scripts/live_ncaa_smoke.py
```

The smoke depends on NCAA/Akamai and the configured Browserless or proxy path;
its external failure does not invalidate a green deterministic release gate,
but a production launch still requires one successful target-network smoke.

## Environment variables

| Variable | Default | Purpose |
|---|---:|---|
| `PORT` | `5000` | HTTP port |
| `ROSTER_SCOUT_CACHE_DIR` | project `.cache/` | Lookup data, successful results, rate-limit state, and local-development browser locks |
| `LOG_LEVEL` | `INFO` | Python log level for the development entry point |
| `SCRAPE_RATE_LIMIT` | `10` | Allowed workbook requests per client and window |
| `SCRAPE_RATE_WINDOW_SECONDS` | `600` | Rate-limit window |
| `TRUST_FLY_CLIENT_IP` | unset | Trust Fly.io's platform-provided client IP header when set to `1` |
| `NCAA_RESULT_CACHE_TTL_SECONDS` | `21600` | Successful team result lifetime |
| `NCAA_LOOKUP_CACHE_TTL_SECONDS` | `86400` | Team and season catalog lifetime |
| `NCAA_SEASON_YEAR` | current season | Optional season override |
| `NCAA_BROWSER_PROVIDER` | local browser | Set to `browserless` to use the hosted browser workflow |
| `BROWSERLESS_TOKEN` | unset | Browserless API token |
| `BROWSERLESS_CAPACITY_BACKEND` | `local` | `local` for single-machine development; hosted app and worker must use `postgresql` |
| `BROWSERLESS_CAPACITY_NAMESPACE` | `roster-scout-browserless-account` | Stable PostgreSQL advisory-lock namespace shared by every app/worker machine |
| `BROWSERLESS_ACCOUNT_MAX_SESSIONS` | `2` | Account-wide Browserless session limit shared by all process groups (1-2) |
| `CELERY_WORKER_CONCURRENCY` | `1` | Per-worker Celery concurrency, never greater than the account limit |
| `DATABASE_URL` | required for jobs | Managed PostgreSQL URL; canonical job/snapshot source of truth |
| `CELERY_BROKER_URL` | `redis://localhost:6379/0` | Redis broker URL; never used as customer-visible job state |
| `REPORT_JOB_MAX_ATTEMPTS` | `3` | Total bounded job attempts (1-10); Celery retries are one fewer |
| `FAILURE_EVIDENCE_RETENTION_DAYS` | `30` | Failed/blocked compressed HTML retention, capped at 30 days |
| `REPORT_SCHEMA_VERSION` | `schema-1` | Same-build section-cache schema identity |
| `NCAA_PARSER_VERSION` | `parser-1` | Same-build section-cache parser identity |
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

The request rate limiter remains machine-local. Browserless capacity does not:
hosted app and worker processes use one PostgreSQL session-level advisory-lock
semaphore identified by `BROWSERLESS_CAPACITY_NAMESPACE`. Every holder keeps a
dedicated database connection for the Browserless operation, and normal exit,
connection loss, or process loss releases its slot. A database failure rejects
new Browserless work instead of falling back to machine-local locks.

**Run exactly one worker machine by default.** `scripts/run_worker.py` rejects a
`CELERY_WORKER_CONCURRENCY` greater than
`BROWSERLESS_ACCOUNT_MAX_SESSIONS`. Additional worker machines are unsupported
until an explicit deployment validation confirms every app/worker uses the same
PostgreSQL database, namespace, and account limit. The shared lease remains the
authoritative aggregate limit; per-process concurrency is not an independent
Browserless quota.

Snapshots that are already `ready` or `partial` reopen from PostgreSQL without an
NCAA request. An explicit refresh creates a new accepted job and successor build;
only retries for that same job may reuse a successfully collected section whose
job ID, source identity, schema version, and parser version still match. Reused
sections retain their original `collected_at` timestamp.

Failed or blocked HTML is operator evidence, not canonical report data. It is
gzip-compressed in PostgreSQL, has no public file path or object key, expires no
later than 30 days after collection, and is purged by nulling the bytes while
retaining its checksum and sanitized metadata. Successful HTML is never stored.

NCAA availability is an external boundary. A healthy container and `/healthz` response do not guarantee that the current hosting network can retrieve a live stats page. Keep the credential-free smoke test in the release checklist.
