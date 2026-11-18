# Deployment guide

Roster Scout runs as a standard Flask/Gunicorn service. Live NCAA collection may require Browserless or an approved proxy because upstream anti-bot behavior varies by network.

## Native dashboard and data lifecycle

The customer dashboard is rendered inside Roster Scout from the same
organization-authorized immutable snapshot used by the workbook. It requires no
Power BI/Fabric capacity, service principal, iframe, analytics database, or
customer BI account. Keep the legacy `POWER_BI_MODE` disabled; the checked-in
`powerbi/` project is not part of the production runtime.

The dashboard projection is request-local and read-only. It does not duplicate
snapshot facts or create a separate cache, so its storage cost is limited to the
existing PostgreSQL snapshot and private workbook object.

Production defaults to `REPORT_RETENTION_DAYS=30` and
`USAGE_EVENT_RETENTION_DAYS=90`. The purge process removes private workbook
objects first, then deletes the matching snapshot graph and any report that has
no remaining snapshot or job. The setting is measured from snapshot publication
(or creation for an unpublished abandoned snapshot), not last access. Retention
values are fail-closed and bounded to 14-90 days for reports and 30-365 days for
raw usage events.

## Native local runner

The bundled scripts create an isolated environment, collect the Browserless token without echoing it, and bind Gunicorn to localhost.

```bash
./scripts/setup_local.sh
./scripts/set_browserless_token.sh
./scripts/run_local.sh
```

Open [http://localhost:5050](http://localhost:5050). The token is stored with owner-only permissions in the Git-ignored `.browserless-token` file. The runner never opens or reuses a personal Chrome profile.

`scripts/run_local.sh` and the legacy native `POST /scrape` workbook remain
usable when `DATABASE_URL` is unset. The route builds and returns the two-sheet
workbook without opening PostgreSQL; analytics are attempted only after the
browser acknowledges the prepared response. If that local acknowledgement
cannot persist, the endpoint returns `persisted=false` and emits only the fixed
sanitized telemetry token
`usage_event_dropped route=download_ack reason=database_unavailable`. It never
logs request data, identity data, a database URL, exception text, or a
traceback.

That database-optional boundary applies only to the legacy/native workbook and
its best-effort local analytics. Production durable report jobs, migrations,
`/readyz`, usage reports, and authoritative analytics require PostgreSQL and
must fail closed when it is unavailable.

### Download acknowledgement secret and success semantics

Fly production is detected by a non-empty `FLY_APP_NAME`; multi-worker mode is
exactly `WEB_CONCURRENCY>1`. Either condition requires
`ROSTER_SCOUT_DOWNLOAD_ACK_SECRET` before the app serves traffic. The value
must be valid URL-safe-base64 material that decodes to at least 32 random
bytes. A missing, malformed, or short value fails startup before serving.
Only non-Fly native/local mode with `WEB_CONCURRENCY==1` may omit it and create
one process-random secret. Every worker in one deployment must share the exact
same configured value.

A non-empty `FLY_APP_NAME` or `WEB_CONCURRENCY>1` requires
`ROSTER_SCOUT_PUBLIC_ORIGIN` before the app serves traffic. Fly requires the
configured origin to use HTTPS; only non-Fly `WEB_CONCURRENCY==1` may fall back
to the direct request origin when the setting is absent. The configured value
is one absolute HTTP(S) origin and contains no credentials, path, query, or
fragment. `Forwarded`, `X-Forwarded-Proto`, `X-Forwarded-Host`, and `Host` are
not trusted as origin authority. This keeps a public HTTPS browser
acknowledgement valid when Fly forwards it to Flask over internal HTTP without
letting request headers redefine the trust boundary.

The checked-in Fly origin is
`ROSTER_SCOUT_PUBLIC_ORIGIN=https://roster-scout.fly.dev`. Treat a custom-domain
change as a coordinated cutover: set the exact HTTPS origin; internationalized
domains are canonicalized with UTS46 and STD3 rules to the exact ASCII A-label
form browsers send; malformed or noncanonical A-labels fail startup. Then restart
every app worker with the same new value before directing browser traffic to the
custom domain. Do not configure an origin list or overlap old and new domains;
the acknowledgement boundary intentionally accepts one canonical browser
origin.

Secret rotation invalidates every outstanding acknowledgement proof. Drain
downloads or wait the exact 15-minute proof TTL, then perform one coordinated
restart with the new value. Mixed-secret rolling workers are unsupported.
Neither the shared secret nor an acknowledgement proof may be logged.

A successful workbook download means the browser read the response blob,
initiated the anchor download, and idempotently acknowledged the same opaque
UUID with its exact 15-minute signed proof. This is the strongest outcome the
browser can observe; it is not proof of a filesystem save. The proof is
stateless: there is no receipt registry, table, cache, collection, list, or set.
The acknowledgement must be same-origin, its JSON body is limited to 1 KiB,
and its token is limited to 512 characters. Ambiguous scrape, blob, or
acknowledgement failures reuse the same UUID. PostgreSQL's unique usage-event
key makes proof replay idempotent.

HTTP 400 and 403 acknowledgement responses are definitive: the browser drops
the rejected attempt and asks the operator to submit again with a new UUID.
HTTP 5xx and network failures are retryable, so the same pending UUID and proof
remain available for a bounded retry. Browser storage exceptions fall back to
page memory without blocking the workbook request; they do not prove a
filesystem save and cannot make an acknowledgement definitive.

The UUID and proof are deduplication material, never identity. Do not attach or
derive an IP address, cookie, email address, user-agent, device fingerprint,
pseudonymous identifier, account, organization, opponent, or report data from
them. Browser session storage is limited to the opaque UUID, lifecycle state,
signed proof, and bounded timestamps for at most the proof lifetime.

## Docker Compose

### Phase 2 managed identity readiness

Roster Scout uses WorkOS AuthKit as a replaceable managed identity boundary.
The customer-facing path is passwordless email verification with a six-digit
Magic Auth code entered inside the existing report-builder card.
The application owns canonical accounts, organization memberships, and opaque
server sessions. Provider organization, domain, and JIT claims never authorize
a Roster Scout organization. After subject linking, only the provider-verified
email address enters the local institutional-domain resolver. The checked-in
NCAA directory registry personalizes only `.edu` roots that map to one baseball
school; ordinary subdomains fold into that root. A root shared by multiple NCAA
programs remains neutral and keeps each exact verified subdomain isolated as a
separate tenant boundary. Malformed, lookalike, and non-`.edu` addresses never
create automatic membership. Local and automated verification use the injected
fake adapter and do not contact a WorkOS workspace.

Configure these names only through an untracked local environment or the
hosting provider's secret store:

- `ROSTER_SCOUT_IDENTITY_MODE=workos`
- `ROSTER_SCOUT_SESSION_SECRET` — shared high-entropy Flask-WTF signing key
- `WORKOS_API_KEY`
- `WORKOS_CLIENT_ID`
- `WORKOS_REDIRECT_URI` — the exact HTTPS `/auth/callback` URL
- `WORKOS_COOKIE_PASSWORD` — one padded URL-safe Base64 Fernet key encoding
  exactly 32 random bytes (for example, the output of `Fernet.generate_key()`)

A hosted runtime with missing or incomplete identity configuration sets the
authentication boundary to unavailable and fails customer product access
closed. It does not fall back to the fake provider. Never place credential
values in `.env.example`, `fly.toml`, source, logs, screenshots, or release
evidence.

### Google Sheets export

Native Google Sheets export is optional and independent of Roster Scout sign-in.
Create a Google Cloud **Web application** OAuth client, enable the Google Drive
API, and register this exact redirect URI:

`https://roster-scout.fly.dev/integrations/google/callback`

Store the client values only in the hosting secret manager as
`GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_CLIENT_SECRET`. The integration asks
for only `https://www.googleapis.com/auth/drive.file`, exchanges one online
authorization code, converts the exact authorized XLSX artifact into a native
Google Sheet, and immediately redirects to it. Roster Scout does not request a
refresh token and does not persist Google access tokens. When the two secrets
are absent, the product retains the manual download-and-import fallback. A
partial secret configuration fails startup rather than silently weakening the
boundary.

Run the deterministic identity and route-gate verification locally without a
WorkOS workspace:

```bash
DATABASE_URL=postgresql+psycopg://roster_scout:roster_scout@127.0.0.1:5432/roster_scout_test \
  .venv/bin/python -m unittest \
  tests.test_identity.AuthGateRouteMatrixTests \
  tests.test_identity.ServerSessionLifecycleTests \
  tests.test_identity.SecretRedactionTests \
  tests.test_deployment_config -v
```

For a non-production staging runtime, inject the six settings above from the
platform secret store, register that runtime's exact HTTPS `/auth/callback`
URL as the OAuth redirect URI, and set the WorkOS **User Invitation URL** to
that runtime's exact canonical HTTPS `/auth/start` URL. These are distinct
dashboard settings: the callback URL cannot replace the User Invitation URL.
WorkOS must append `invitation_token` to the `/auth/start` link so Roster Scout
can resolve the token and issue one signed, one-time local state before the
callback. Run the same readiness and route tests before any live IdP smoke. Do
not reuse production keys or cookie passwords in staging.

The resulting entry shape is `/auth/start?invitation_token=...`; the token is
consumed into signed, one-time local state and is never accepted as direct
organization authority.

Phase 2 records two deliberately separate readiness states:

- `code_verified` means deterministic fake/emulator-backed authentication,
  callback replay protection, local membership authorization, CSRF, logout,
  migrations, and configuration tests passed. It creates no external service.
- `production_auth_verified` is set only after an operator provisions the
  production WorkOS workspace, confirms billing, installs secrets, registers
  the exact OAuth redirect URL, sets the User Invitation URL to
  `https://roster-scout.fly.dev/auth/start`, enables WorkOS Magic Auth email
  codes, and completes a real customer OIDC or SAML smoke test when enterprise
  SSO is introduced.

Before recording `production_auth_verified`, verify outbound six-digit Magic
Auth email delivery and confirm provider organization/JIT claims remain ignored. Complete a
live `.edu` smoke in which `coach@school.edu` and
`assistant@athletics.school.edu` reach the same local organization, while a
lookalike and a non-`.edu` address gain no automatic access. Also verify the
exact invitation exception path, local-first logout during provider failure,
and the public-select → sign-in → resumed-download journey. A green
`code_verified` result never implies that these live checks passed.

Run the complete deterministic corrective Phase 2.1 gate from the repository root against
the disposable PostgreSQL database:

```bash
PHASE2_DESTRUCTIVE_DATABASE=local-disposable \
  DATABASE_URL=postgresql+psycopg://roster_scout:roster_scout@127.0.0.1:5432/roster_scout_test \
  .venv/bin/python scripts/validate_phase21_release.py
```

The earlier `scripts/validate_phase2_release.py` remains the lower-level Phase
2 identity, organization, report-library, and deployment contract, including
its revision `0022` lifecycle. Phase 2.1's validator extends that release
surface with the restored homepage journey, the revision `0023` lifecycle, and
the school-domain authorization model. The current release head is revision
`0024`, which adds bounded durable workbook-artifact delivery state.

Only after the full public-select/sign-in/resume/download/shared-portal story,
domain and tenant security tests, all three Node state machines, the revision `0024` lifecycle, full Python and Node suites,
catalog, workbook, configuration, compilation, template, and diff checks pass
does the corrective gate pass. Production launch remains blocked until the
separate live authentication evidence is complete and the user explicitly
approves deployment.

In release-state terms, `production launch remains blocked` until those live
checks and that explicit approval are recorded.

The separate live evidence requires an operator-owned WorkOS production workspace,
confirmed billing, installed secrets, verified dashboard
redirect/sign-out/initiate-login URIs, the exact User Invitation URL
`https://roster-scout.fly.dev/auth/start`, live six-digit Magic Auth email,
and one real customer OIDC or SAML connection if SSO is enabled. It must also confirm
the provider-sent invitation traversed token-bearing `/auth/start`, signed
local state issuance, and `/auth/callback` into the exact-email local
membership; provider domain/JIT claims remain ignored as authorization; the
verified institutional email resolver is exercised separately; and a foreign
organization cannot open reports. No validator creates a workspace,
sends real email, configures a customer IdP, deploys the app, or creates a
billable resource.

To perform the explicit launch check, point
`ROSTER_SCOUT_PRODUCTION_AUTH_SMOKE_RECORD` at reviewed JSON evidence and set
`PHASE2_PRODUCTION_LAUNCH_CHECK=1` when running the same command. A missing,
malformed, non-production, or incomplete record fails closed; never add that
record or any credential value to Git. The record must name
`"user_invitation_url": "https://roster-scout.fly.dev/auth/start"` and set
`"invitation_email_handoff_verified": true`; a callback-only smoke or a record
that omits either field remains `production_auth_verified=false`.

```bash
docker compose up --build
docker compose --profile worker up --build
docker compose --profile worker --profile scheduler up --build
```

The app is available at [http://localhost:5050](http://localhost:5050). Compose
persists lookup/successful-result caches and immutable snapshot workbooks in
separate named volumes. The app and worker share only the workbook-artifact
volume; the worker still does not mount the browser/result cache. Pass secrets
at runtime through your shell, an untracked env file, or a secret manager; do
not add them to `compose.yaml`.

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

PostgreSQL is the sole retry authority. `REPORT_JOB_MAX_ATTEMPTS` is enforced
by a generation compare-and-swap, and the outbox stores every retry due time
and backoff. Each broker delivery carries exactly `(job_id, generation)`;
workers never call Celery `self.retry` and Redis owns no independent retry
clock or customer-visible job state.

### Retention purge readiness

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

The same independent process also applies the 30-day saved-report/workbook and
90-day raw-usage policies. Object deletion is idempotent and precedes database
deletion; a storage failure leaves the database graph intact for a later retry.
Published snapshot mutation is still rejected outside the transaction-local
`roster_scout.retention_purge=on` guard installed by migration `0027`.

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

Eligible failure-evidence writers and final complete-scan certification share
one stable transaction advisory lock. Bounded `FOR UPDATE SKIP LOCKED` deletion
batches remain outside that singleton lock, while the final ordinary count and
heartbeat upsert are serialized. PostgreSQL `GREATEST` keeps the purge
heartbeat monotonic, and `last_purged_count` changes only when its certification
timestamp wins; an older purge completion cannot move readiness backward.

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
the app process has the HTTP service and `/data` cache volume; the worker,
scheduler, and purge processes have neither. Durable workbooks use private
S3-compatible object storage shared by the app and worker instead of the app
cache volume. Both background commands clear the inherited
environment. Scheduler receives PostgreSQL, Redis, and four dispatch timings;
purge receives only PostgreSQL and its cadence, so app-wide Browserless and
broker credentials are not exposed to the deletion runtime.

```bash
fly auth login
fly launch --copy-config --no-deploy
# Provision one private S3-compatible bucket first (for Fly, `fly storage create`).
# Install all values through secure input or the Fly dashboard; do not paste
# real credentials into shell history.
fly scale count app=1 worker=1 scheduler=1 purge=1
fly deploy
```

Avoid placing real secret values in shell history. Use the Fly dashboard or a
secure input method to install the identity-secret checklist:
`ROSTER_SCOUT_SESSION_SECRET`, `WORKOS_API_KEY`, `WORKOS_CLIENT_ID`,
`WORKOS_REDIRECT_URI`, `WORKOS_COOKIE_PASSWORD`,
`BUCKET_NAME`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`, and
`AWS_ENDPOINT_URL_S3`. The checked-in
configuration deliberately keeps TLS verification enabled.

`ROSTER_SCOUT_DOWNLOAD_ACK_SECRET=your-url-safe-base64-32-byte-secret` is a
documentation placeholder only. Generate unique random material and install it
through secure input; never copy the placeholder into production.

Before deployment, verify required names without printing values:

```bash
fly secrets list | awk 'NR > 1 { print $1 }' | sort
```

The name-only output must include `BROWSERLESS_TOKEN`, `DATABASE_URL`,
`CELERY_BROKER_URL`, `ROSTER_SCOUT_DOWNLOAD_ACK_SECRET`,
`ROSTER_SCOUT_SESSION_SECRET`, `WORKOS_API_KEY`, `WORKOS_CLIENT_ID`,
`WORKOS_REDIRECT_URI`, `WORKOS_COOKIE_PASSWORD`,
`BUCKET_NAME`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`, and
`AWS_ENDPOINT_URL_S3`. This is configuration
readiness only; it does not change `production_auth_verified=false` without the
separate live smoke record.

Before making a deployment public:

1. Keep production launch blocked until all four prerequisites are verified:
   managed PostgreSQL, managed Redis, private workbook object storage, and
   uninterrupted Fly worker billing/capacity. Local Compose is development-only
   and does not satisfy this production provisioning gate. Do not deploy a
   durable worker on trial capacity that can stop after five minutes.
   The private bucket satisfies the managed object/evidence storage
   prerequisite; failure HTML evidence itself remains separately bounded in
   PostgreSQL.
2. Confirm the release migration succeeded before either process group starts.
3. Run the deterministic suite and catalog validation.
4. Run `python scripts/live_ncaa_smoke.py` from the target network.
5. Confirm `/healthz` returns `{"status":"ok"}` over HTTPS.
6. Confirm `/readyz` returns HTTP 200 only after persisted purge readiness is
   `fresh`; verify each non-fresh state returns HTTP 503.
7. Confirm rate limiting returns HTTP 429 with `Retry-After`.
8. Confirm a real workbook download opens in Excel and contains both worksheets.
9. Open the same historical snapshot after a worker/app restart and confirm the
   downloaded SHA-256 matches the `report_artifacts` row. Confirm an explicit
   refresh creates a new artifact key without changing the prior object.
10. Review memory use during one uncached browser session.

### Workbook object storage and recovery

Every published `ready` or `partial` snapshot receives one pending XLSX
artifact row in the same PostgreSQL transaction that publishes the snapshot.
The worker renders that exact snapshot's immutable `source_records`, writes a
private create-only object with SHA-256 metadata, and marks the row `ready`
only after storage confirms the write. Historical download routes read only
the authorized row's stored object key; they never rebuild from current NCAA
data. The scheduler discovers missing rows, reclaims stale builds after 15
minutes, and retries queue/storage failures at most three times. Stored object
keys contain opaque organization/report/snapshot UUIDs and are never public
URLs.

Production uses `ROSTER_SCOUT_ARTIFACT_BACKEND=s3`. The app and worker require
the same bucket, endpoint, region, and credentials. `/readyz` returns 503 and
the worker refuses startup when Fly is detected but artifact storage is
missing. Keep the bucket private, block public access, retain server-side
encryption. The application-owned purge is the authoritative 30-day workbook
lifecycle; do not add a shorter bucket rule that can delete objects before the
database graph. Failure HTML remains separately compressed and time-bounded in
PostgreSQL; it is not written to the workbook bucket.

Before first production traffic, exercise this staging sequence against a
non-production database, Redis instance, WorkOS environment, and bucket:

1. Upgrade a disposable copy to migration `0024`, then run `alembic check`.
2. Generate one report, wait for its artifact row to become `ready`, download
   it, and record its SHA-256 outside the repository.
3. Restart app, worker, and scheduler; download the same snapshot again and
   compare the SHA-256.
4. Force a refresh, confirm the successor has a different key, and confirm the
   prior object and digest are unchanged.
5. Stop Redis during dispatch, restore it, and confirm PostgreSQL reconciliation
   publishes the pending artifact exactly once.
6. Temporarily deny bucket writes, restore access, and confirm bounded retry
   reaches `ready` without overwriting an existing object.
7. Remove one artifact credential and confirm app readiness is 503 and the
   worker exits before consuming jobs; restore it and confirm recovery.

Back up PostgreSQL and enable bucket versioning/provider durability before
cutover. A database restore must be paired with a bucket state from the same
recovery point. Never restore only object keys or only artifact rows and call
the result complete. Rollback may return application code to the prior image,
but migration `0024` columns are additive; keep them and stop new traffic while
the old image is evaluated. Do not delete historical objects during rollback.

## Deterministic release verification

Run the complete local gate from the repository root before a release. The
PostgreSQL URL must point to the disposable database ending in `_test`:

```bash
DATABASE_URL=postgresql+psycopg://roster_scout:roster_scout@localhost:5432/roster_scout_test .venv/bin/python scripts/validate_phase1_release.py
```

The committed-history check defaults to the audited baseline `95776ed`. To
broaden the audit to an older reviewed commit, set `PHASE1_RELEASE_DIFF_BASE`
to its full commit ID before running the same command. An override must resolve
to a commit that is equal to or an ancestor of `95776ed`; newer commits,
`HEAD`, `HEAD^`, abbreviated/non-commit refs, and an audited baseline no longer
ancestral to `HEAD` fail closed before validation begins. Record the selected
full commit ID in the release evidence. The two final checks are distinct and
ordered: `git diff --check <resolved-base>..HEAD --` audits committed changes,
then `git diff --check` audits the working tree.

The validator first requires `valid: true` from the centralized user-story
validator for the exact Phase 1 ROADMAP goal and requires a disposable
PostgreSQL database. It performs only the minimal upgrade-to-head setup and
current-head assertion before running every round-four and round-five Python
counterexample,
followed immediately by
`node --test tests/js/download_tracking.test.mjs`. Only after both focused
groups pass does it run the revision `0014` global Player serialization class, full
downgrade/base-to-head/re-upgrade lifecycle, head and drift assertions,
clean-process discovery, catalog, rendered Compose and deployment contracts,
two-sheet workbook validation, Python compilation and template contracts, and
finally the committed-range and working-tree diff checks described above.

The Fly route contract requires the exact configured
`ROSTER_SCOUT_PUBLIC_ORIGIN` and never trusts forwarded request headers to
redefine that browser origin. The focused release group covers both startup
configuration and the acknowledgement route before the slower full groups.

Full discovery retains the Plan 14-29 regressions and verifies exact source-row
traceability, duplicate delivery, same-job recovery, failure evidence and purge
deadlines, distributed Browserless capacity, dispatch and purge scheduling,
secure deployment configuration, legacy cache and selector behavior, and the
unchanged two-sheet workbook contract. Catalog validation must report
`"valid": true`, 942
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

### Usage metrics operator report

The usage report reads committed aggregate events from PostgreSQL. Set
`DATABASE_URL` to the intended database, then choose a half-open UTC window:
`--from` is inclusive and `--to` is exclusive. Run the global report with:

```bash
python scripts/report_usage.py --from 2026-11-01T00:00:00Z --to 2026-12-01T00:00:00Z
```

Filter the same window to one canonical organization UUID with:

```bash
python scripts/report_usage.py --from 2026-11-01T00:00:00Z --to 2026-12-01T00:00:00Z --organization-id 00000000-0000-0000-0000-000000000000
```

Output is aggregate-only: window bounds, successful workbook downloads, unique
authenticated users, and the organization UUID when a filter is supplied. A
successful workbook download becomes one committed, deduplicated
`workbook_download_served` event only after blob read, anchor initiation, and
same-origin acknowledgement. Replaying the same opaque per-submission UUID
does not increase the count. A unique authenticated user is one distinct,
nonnull canonical `actor_account_id` from `authenticated_use` events only;
actor-bearing download events and anonymous downloads never increase that
metric.

Phase 1 does not authenticate users. Its legacy workbook events therefore keep
both actor and organization null, so `unique_authenticated_users` remains zero
until Phase 2 supplies canonical identities. After authentication and
membership authorization, Phase 2 records exactly one `authenticated_use` only
when the application accepts and durably creates an authorized report/job
request. The event key is the accepted `ReportJob` UUID, and the event carries
the canonical account UUID plus the authorized organization UUID. Page views,
login, rejected requests, and validation failures are not usage events. Never
substitute an IP address, cookie, email address, user-agent, fingerprint, or
pseudonymous identifier for the canonical account UUID.

A successful local report proves only the query and event contracts. It does
not provision or validate managed PostgreSQL, authentication, authorization,
object storage, Redis, or Fly billing/capacity; those production prerequisites
remain independently blocked until verified.

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
| `WEB_CONCURRENCY` | `1` | HTTP worker count; values greater than one require a shared acknowledgement secret |
| `ROSTER_SCOUT_DOWNLOAD_ACK_SECRET` | local random fallback | URL-safe-base64 secret decoding to at least 32 bytes; required on Fly or with multiple workers |
| `ROSTER_SCOUT_PUBLIC_ORIGIN` | direct request origin in local single-process mode | Exact canonical HTTP(S) browser origin; required with `FLY_APP_NAME` or multiple workers, and HTTPS-only on Fly |
| `ROSTER_SCOUT_IDENTITY_MODE` | `fake` locally; `workos` hosted | Select the managed provider boundary; hosted runtimes must use `workos` |
| `ROSTER_SCOUT_SESSION_SECRET` | required for hosted identity | Shared high-entropy Flask-WTF signing key stored only in the secret store |
| `WORKOS_API_KEY` | required in `workos` mode | WorkOS API credential stored only in the secret store |
| `WORKOS_CLIENT_ID` | required in `workos` mode | WorkOS AuthKit client identifier |
| `WORKOS_REDIRECT_URI` | required in `workos` mode | Exact HTTPS callback URL ending in `/auth/callback` |
| `WORKOS_COOKIE_PASSWORD` | required in `workos` mode | High-entropy key for sealed provider session state |
| `GOOGLE_OAUTH_CLIENT_ID` | optional | Google Web application OAuth client for native Sheets export; configure with the exact callback URI |
| `GOOGLE_OAUTH_CLIENT_SECRET` | optional | Google OAuth client secret stored only in the hosting secret manager |
| `ROSTER_SCOUT_ARTIFACT_BACKEND` | unset locally; `s3` on Fly | `filesystem` for local Compose/tests or private S3-compatible storage for hosted app/worker |
| `ROSTER_SCOUT_ARTIFACT_DIRECTORY` | required for `filesystem` | Absolute shared local/Compose directory for immutable snapshot workbooks |
| `ROSTER_SCOUT_ARTIFACT_BUCKET` / `BUCKET_NAME` | required for `s3` | Private workbook bucket name; `fly storage create` installs `BUCKET_NAME` |
| `ROSTER_SCOUT_ARTIFACT_ENDPOINT_URL` / `AWS_ENDPOINT_URL_S3` | provider default | HTTPS S3-compatible endpoint; Fly/Tigris may install `AWS_ENDPOINT_URL_S3` |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | required for `s3` | Object-store credentials held only in the secret manager |
| `AWS_REGION` | provider default | Object-store signing region |
| `ROSTER_SCOUT_ARTIFACT_SSE` | `AES256` | Server-side encryption passed on immutable object creation |
| `NCAA_BROWSER_PROVIDER` | local browser | Set to `browserless` to use the hosted browser workflow |
| `BROWSERLESS_TOKEN` | unset | Browserless API token |
| `BROWSERLESS_CAPACITY_BACKEND` | `local` | `local` for single-machine development; hosted app and worker must use `postgresql` |
| `BROWSERLESS_CAPACITY_NAMESPACE` | `roster-scout-browserless-account` | Stable PostgreSQL advisory-lock namespace shared by every app/worker machine |
| `BROWSERLESS_ACCOUNT_MAX_SESSIONS` | `2` | Account-wide Browserless session limit shared by all process groups (1-2) |
| `CELERY_WORKER_CONCURRENCY` | `1` | Per-worker Celery concurrency, never greater than the account limit |
| `DATABASE_URL` | required for jobs | Managed PostgreSQL URL; canonical job/snapshot source of truth |
| `CELERY_BROKER_URL` | `redis://localhost:6379/0` | Redis broker URL; never used as customer-visible job state |
| `REPORT_JOB_MAX_ATTEMPTS` | `3` | Total bounded job attempts (1-10), enforced by PostgreSQL generation compare-and-swap |
| `FAILURE_EVIDENCE_RETENTION_DAYS` | `28` | Failed/blocked compressed HTML retention, strictly limited to 1-28 days |
| `FAILURE_EVIDENCE_PURGE_SECONDS` | `3600` | Independent purge cadence, strictly limited to 60-3600 seconds |
| `REPORT_RETENTION_DAYS` | `30` | Saved snapshot and private workbook retention, strictly limited to 14-90 days |
| `USAGE_EVENT_RETENTION_DAYS` | `90` | Raw product-usage event retention, strictly limited to 30-365 days |
| `REPORT_SCHEMA_VERSION` | `schema-1` | Same-build section-cache schema identity |
| `NCAA_PARSER_VERSION` | `parser-2+ncaa-core-v2` | Same-build parser and accepted core-header contract identity |
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

The legacy successful-result cache uses contract version 3. A cached payload is
trusted only when its cache format, parser identity, accepted core-header
identity, and report-schema identity all exactly match the running collector.
Any mismatch or pre-contract payload is a miss and forces recollection; the
artifact key and same-team lock use the same contract identity so incompatible
collectors neither share results nor coalesce.

Authoritative persistence and publication use one global lock order:
metric-definition (when applicable) -> snapshot -> tenant-scoped Player ->
PlayerTeamSeason. Every lock set is deterministic. Do not introduce a source,
canonical fact, identity mutation, or publication path that acquires these
domains in a different order.

Failed or blocked HTML is operator evidence, not canonical report data. It is
gzip-compressed in PostgreSQL, has no public file path or object key, expires no
later than 28 days after collection, and is purged by nulling the bytes while
retaining its checksum and sanitized metadata. Successful HTML is never stored.

NCAA availability is an external boundary. A healthy container and `/healthz` response do not guarantee that the current hosting network can retrieve a live stats page. Keep the credential-free smoke test in the release checklist.

## Browserless hotfix — November 10, 2026

Production image `deployment-01M732E8XAKJPEE6EDYGZF5CPA` is a focused patch
on live revision `2929a4fc68471a4884369681fadddd5b39d2dfe7`, not a release
of the newer database/worker architecture in this workspace. It adds per-team
file locking around the legacy result-cache check/collection/write, an
in-flight form guard, explicit CDP `Browser.close` before capacity release,
and a retryable HTTP 503 response for upstream Browserless 429s. The per-team
file lock coalesces callers sharing one machine/cache; it is not a distributed
lock for multiple machines. Explicit force-refresh requests remain fresh.

Verification: all 51 tests against the focused production tree passed. The
workspace suite ran 119 tests successfully with 35 database-dependent skips;
the catalog validator accepted all 942 teams. Two simultaneous production
Scranton requests both returned valid workbooks with 44 players in about 41
seconds. Browserless logs showed exactly one unblock/reconnect pair and no
429s for that verification. Fly health checks and HTTPS `/healthz` passed.

The original burst callers cannot be identified retrospectively from the
Browserless logs. Account capacity may still be exhausted by other consumers
of the same Browserless account. Genuine contention returns the busy response
instead of an upstream-data error.
