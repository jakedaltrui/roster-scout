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
docker compose --profile worker --profile scheduler up --build
```

The app is available at [http://localhost:5050](http://localhost:5050). Compose persists lookup and successful-result caches in a named volume. Pass secrets at runtime through your shell, an untracked env file, or a secret manager; do not add them to `compose.yaml`.

The `worker` profile starts the separate Celery process with PostgreSQL as the
durable job store and Redis only as its broker. Both app and worker use the same
PostgreSQL advisory-lock namespace for Browserless account capacity; the worker
does not mount the app's persistent `/data` cache. PostgreSQL and Redis host
ports are development-only and bind to `127.0.0.1`.

The `scheduler` profile starts exactly one local reconciliation process and one
independently supervised purge process from the same immutable image. Neither
has an HTTP port or `/data` mount. The reconciliation process receives only
`DATABASE_URL`, `CELERY_BROKER_URL`, and dispatch timing configuration; the
purge process receives only `DATABASE_URL` and its nonsecret cadence. Neither
is given `BROWSERLESS_TOKEN` or any NCAA/browser configuration.

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

### Failed HTML retention and purge readiness

Failed or blocked NCAA HTML is retained for 1-28 days for restricted operator
troubleshooting; 28 days (672 hours) is the default and hard maximum. The
standalone purge process deletes eligible bytes immediately on startup and then
at least hourly, leaving at least 47 hours before the 720-hour hard deadline.
Purging nulls the compressed HTML and sets `purged_at`; the checksum and
sanitized D-18 audit metadata remain permanently available.

`FAILURE_EVIDENCE_RETENTION_DAYS` is the single operational retention control.
The production parser accepts only integer days from 1 through 28. A permitted
shorter value moves each new evidence row's `expires_at` earlier; the default
and non-extendable hard cap are 28 days (672 hours). Blank, fractional,
malformed, zero, negative, or greater-than-28 values fail closed before evidence
insertion and never silently fall back to a longer retention period. Only the
app and worker evidence-creator processes receive this setting; scheduler and
purge do not.

Configure `FAILURE_EVIDENCE_PURGE_SECONDS` from 60 through 3600 seconds; the
default is 3600. The purge runner validates this setting before constructing a
database engine, retries failures after a bounded 60-second delay, and never
imports Redis, dispatch reconciliation, or Browserless configuration.

Treat production readiness as blocked until the persisted PostgreSQL heartbeat
reports one successful purge. Readiness remains fresh for exactly six hours
only when no expired bytes remain, and otherwise returns `never_succeeded`,
`stale`, `expired_bytes_present`, or `database_unavailable`. Compose checks this
contract with `python scripts/run_purge.py --check`; operators can run the same
safe command in the Fly purge machine. Logs expose only fixed health/status
tokens and aggregate counts. Never copy raw exception text, source payloads, or
credential-bearing URLs into an alert or incident note.

Fly gates app traffic on `/readyz`, which projects the same persisted purge
state and returns HTTP 503 for every non-fresh state or database/configuration
failure. `/healthz` remains a shallow process-liveness check and must not be
used as the production traffic-readiness gate. Alert immediately on a 503,
`never_succeeded`, `stale`, `expired_bytes_present`, or `database_unavailable`:
keep traffic blocked, verify the independently supervised purge process and
PostgreSQL connectivity, run the safe `--check`, and confirm eligible bytes are
physically absent before 720 hours after collection. Do not waive readiness or
extend retention while responding.

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

`fly.toml` defines separate `app`, `worker`, singleton `scheduler`, and singleton
`purge` process groups. Its release command
runs `alembic upgrade head` once before the new release receives traffic. Only
the app process has the HTTP service and `/data` volume; the worker, scheduler,
and purge processes have neither. Both background commands clear the inherited
environment. Scheduler receives PostgreSQL, Redis, and four dispatch timings;
purge receives only PostgreSQL and its cadence, so app-wide Browserless and
broker credentials are not exposed to the deletion runtime.

```bash
fly auth login
fly launch --copy-config --no-deploy
fly secrets set BROWSERLESS_TOKEN=your-token DATABASE_URL=your-managed-postgres-url CELERY_BROKER_URL=your-managed-redis-url
fly scale count app=1 worker=1 scheduler=1 purge=1
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
6. Confirm `/readyz` returns HTTP 200 only after persisted purge readiness is
   `fresh`; verify each non-fresh state returns HTTP 503.
7. Confirm rate limiting returns HTTP 429 with `Retry-After`.
8. Confirm a real workbook download opens in Excel and contains both worksheets.
9. Review memory use during one uncached browser session.

## Deterministic release verification

Run the complete local gate from the repository root before a release. The
PostgreSQL URL must point to the disposable database ending in `_test`:

```bash
DATABASE_URL=postgresql+psycopg://roster_scout:roster_scout@localhost:5432/roster_scout_test .venv/bin/python scripts/validate_phase1_release.py
```

The validator first requires `valid: true` from the centralized user-story
validator for the exact Phase 1 ROADMAP goal. It then fails fast on Python
compilation, required template existence, migration upgrade/downgrade/re-upgrade
or model drift, any full-suite test (including all 16 review regressions and the
goal-level durable D1/D2/D3 flow), catalog validation, rendered Compose
configuration, or `git diff --check`.

The deterministic suite explicitly runs every Plan 14-20 gap-regression class,
then full discovery for exact source-row traceability, duplicate delivery,
same-job recovery, failure evidence and purge deadlines, distributed Browserless
capacity, dispatch and purge scheduling, secure deployment configuration,
legacy cache and selector behavior, and the unchanged two-sheet workbook
contract. Catalog validation must report `"valid": true`, 942
selectable teams, and the reviewed D1/D2/D3 counts unless a catalog update is
part of the release.

Passing this local gate does not authorize production launch. Production
remains blocked until managed PostgreSQL, Redis, object/evidence storage, and
uninterrupted Fly worker billing/capacity are all verified. Local Compose is
development-only and cannot satisfy those production prerequisites.

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
| `FAILURE_EVIDENCE_RETENTION_DAYS` | `28` | Failed/blocked compressed HTML retention, strictly limited to 1-28 days |
| `FAILURE_EVIDENCE_PURGE_SECONDS` | `3600` | Independent purge cadence, strictly limited to 60-3600 seconds |
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
later than 28 days after collection, and is purged by nulling the bytes while
retaining its checksum and sanitized metadata. Successful HTML is never stored.

NCAA availability is an external boundary. A healthy container and `/healthz` response do not guarantee that the current hosting network can retrieve a live stats page. Keep the credential-free smoke test in the release checklist.
