# Phase 2: Identity, Organizations, and Report Library - Pattern Map

**Mapped:** 2026-11-11  
**Files analyzed:** 30 expected new/modified files, grouped into 15 implementation roles  
**Analogs found:** 13 / 15 roles  
**Primary analogs:** `roster_scout/models.py`, `roster_scout/jobs.py`, `roster_scout/repositories.py`, `app.py`, `templates/index.html`, `static/download_tracking.js`, existing unittest/release infrastructure

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|---|---|---|---|---|
| `roster_scout/models.py` | model | CRUD / event-ledger | Existing `Organization`, `Report`, `ReportSnapshot`, `ReportJob`, `ProductUsageEvent` in same file | exact |
| `migrations/versions/0015_phase2_identity_organizations.py` | migration | schema DDL / transform | `migrations/versions/0012_product_usage_events.py`; `0001_canonical_snapshot_foundation.py` | exact |
| `roster_scout/identity.py` | provider / adapter | request-response | Research `IdentityProvider` protocol; no provider adapter exists in repository | no analog |
| `roster_scout/accounts.py` | service | CRUD / identity linking | `roster_scout/usage.py` validation and idempotent insert style | role-match |
| `roster_scout/sessions.py` | service / session provider | CRUD / request-response | `roster_scout/database.py`; research opaque verifier contract | partial |
| `roster_scout/authorization.py` | middleware / service | request-response | `roster_scout/repositories.py` organization-scoped loaders | role-match |
| `roster_scout/organizations.py` | service | CRUD / event-driven audit | `roster_scout/jobs.py` locked mutations and caller-owned primitives | role-match |
| `roster_scout/reports.py` | service | CRUD / request-response / event-driven | `roster_scout/jobs.py:create_or_get_job`; `roster_scout/usage.py` | exact |
| `roster_scout/audit.py` | service | append-only event ledger | `roster_scout/usage.py:record_usage_event` | exact |
| `roster_scout/repositories.py` | repository | CRUD / request-response | Existing scoped snapshot/job queries in same file | exact |
| `app.py` | controller / route / middleware | request-response / file I/O | Existing JSON routes, validation, download, headers, error mapping | exact |
| `templates/sign_in.html`, `organization_select.html`, `report_library.html`, `report_detail.html`, `organization_members.html` | component / view | request-response | `templates/index.html` | role-match |
| `static/report_status.js` | utility / state machine | request-response / polling | `static/download_tracking.js` and its Node tests | role-match |
| `tests/test_identity.py`, `test_organizations.py`, `test_report_library.py`, `test_phase2_tenant_isolation.py`, `test_phase2_release.py`, `tests/js/report_status.test.mjs`, `tests/fixtures/workos-emulate.config.yaml` | test / fixture | CRUD / concurrency / request-response | `tests/db_support.py`, `test_report_jobs.py`, `test_app_routes.py`, `tests/js/download_tracking.test.mjs` | role-match |
| `requirements.txt`, `.env.example`, `fly.toml`, `DEPLOYMENT.md`, `scripts/validate_phase2_release.py`, `tests/test_deployment_config.py` | config / release validation | batch / deployment | Existing Phase 1 versions of the same files | exact |

## Pattern Assignments

### `roster_scout/models.py` (model, CRUD and event-ledger)

**Analog:** existing canonical ownership models in `roster_scout/models.py`.

**Imports and declarative conventions** (`roster_scout/models.py:3-24`):

```python
import uuid

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
```

**UUID and timestamp mixins** (`roster_scout/models.py:47-56`):

```python
class Base(DeclarativeBase):
    pass

class UUIDPrimaryKey:
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

class Timestamped:
    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
```

**Organization ownership key and uniqueness** (`roster_scout/models.py:117-125`):

```python
class Report(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "reports"
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", name="fk_reports_organization"), nullable=False
    )
    team_season_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("team_seasons.id", name="fk_reports_team_season"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    __table_args__ = (
        UniqueConstraint("id", "organization_id", name="uq_reports_id_organization"),
        UniqueConstraint("organization_id", "team_season_id", name="uq_reports_organization_team_season"),
    )
```

**Composite tenant foreign key and state check** (`roster_scout/models.py:128-155`):

```python
ForeignKeyConstraint(
    ["report_id", "organization_id"],
    ["reports.id", "reports.organization_id"],
    name="fk_report_snapshots_report_organization",
),
CheckConstraint(_allowed("status", SNAPSHOT_STATUSES), name="ck_report_snapshots_status"),
```

Apply this shape to memberships, invitations, identity connections, sessions with an active organization, and organization-owned security audit events. Give every organization-owned row a composite `(id, organization_id)` uniqueness key when another table must prove same-organization ownership.

Add these canonical rows from the research schema: `Account`, `ExternalIdentity`, `AccountSession`, `AuthTransaction`, `OrganizationMembership`, `OrganizationInvitation`, `OrganizationIdentityConnection`, and `SecurityAuditEvent`. Extend `ReportJob`, `ReportSnapshot`, and `ProductUsageEvent` additively; preserve legacy nullable/free-text fields rather than reinterpreting them.

**Event-ledger pattern** (`roster_scout/models.py:579-618`):

```python
class ProductUsageEvent(UUIDPrimaryKey, Base):
    __tablename__ = "product_usage_events"
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    event_key: Mapped[str] = mapped_column(String(200), nullable=False)
    occurred_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    actor_account_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id", name="fk_product_usage_events_organization")
    )
    __table_args__ = (
        UniqueConstraint("event_type", "event_key", name="uq_product_usage_events_type_key"),
        CheckConstraint(
            "(actor_account_id IS NULL) = (organization_id IS NULL)",
            name="ck_product_usage_events_actor_scope",
        ),
    )
```

`SecurityAuditEvent` should copy the bounded allowlist/check/foreign-key style, while adding database enforcement that published audit rows are append-only. It must store only fixed action/outcome/reason tokens and canonical UUIDs—not raw email, IP, user agent, provider payload, token, assertion, or exception text.

---

### `migrations/versions/0015_phase2_identity_organizations.py` (migration, schema DDL)

**Analogs:** `migrations/versions/0012_product_usage_events.py` and `0001_canonical_snapshot_foundation.py`.

**Revision and reversible additive DDL** (`migrations/versions/0012_product_usage_events.py:7-18,44-68`):

```python
import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table(
        "product_usage_events",
        # columns and named constraints
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"],
            name="fk_product_usage_events_organization",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_product_usage_events"),
    )

def downgrade():
    op.drop_index(..., table_name="product_usage_events")
    op.drop_table("product_usage_events")
```

**Composite ownership DDL** (`migrations/versions/0001_canonical_snapshot_foundation.py:127-148`):

```python
sa.ForeignKeyConstraint(
    ["report_id", "organization_id"],
    ["reports.id", "reports.organization_id"],
    name="fk_report_snapshots_report_organization",
),
sa.UniqueConstraint("id", "organization_id", name="uq_report_snapshots_id_organization"),
```

Use named constraints and indexes, upgrade from head `0014`, and add rather than rewrite legacy data. The migration must preserve anonymous `product_usage_events` rows, add the account FK only for non-null actors, retain `ReportSnapshot.created_by`, and introduce nullable canonical account FKs. Add a partial unique index for one active invitation per `(organization_id, normalized_email)` and an append-only trigger for audit events if the model metadata declares it.

---

### `roster_scout/identity.py`, `accounts.py`, and `sessions.py` (provider and identity services)

**Closest in-repo style:** connection-free validation/services in `roster_scout/usage.py`; lazy database construction in `roster_scout/database.py`. There is no existing external identity adapter or Flask server-side session implementation.

**Connection-free service contract** (`roster_scout/usage.py:1-6,66-75`):

```python
"""This module is connection-free. Callers own the SQLAlchemy session and its
transaction."""

def record_usage_event(
    session,
    *,
    event_type,
    event_key,
    occurred_at=None,
    actor_account_id=None,
    organization_id=None,
):
```

**Bounded canonical validation** (`roster_scout/usage.py:35-55`):

```python
def _canonical_uuid(value, *, field, required=False):
    if value is None:
        if required:
            raise ValueError(f"{field} is required")
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"{field} must be a UUID") from exc
```

`identity.py` should define the research protocol exactly at the provider boundary:

```python
class IdentityProvider(Protocol):
    def authorization_url(self, *, state: str, invitation_token: str | None = None) -> str: ...
    def exchange_code(self, *, code: str) -> "VerifiedIdentity": ...
    def refresh(self, *, sealed_session: str) -> "RefreshResult": ...
    def logout_url(self, *, sealed_session: str) -> str: ...
```

Keep provider exceptions inside this adapter and return fixed categories. Never log provider exception messages, callback parameters, codes, tokens, assertions, or sealed session contents.

`sessions.py` should accept a caller-owned SQLAlchemy session and store only the lookup ID, verifier digest, sealed provider session, CSRF secret, active organization, and expiry/revocation timestamps. The cookie carries `lookup_id.verifier`; compare its SHA-256 digest with `hmac.compare_digest`. Rotate on login and organization switch; revoke the local row before provider logout. Production session cookies must be `Secure`, `HttpOnly`, and `SameSite=Lax`.

---

### `roster_scout/authorization.py` and `repositories.py` (authorization and scoped repository)

**Analog:** existing organization-scoped loaders in `roster_scout/repositories.py`.

**Scope is part of every lookup** (`roster_scout/repositories.py:27-56`):

```python
def get_published_snapshot(session, organization_id, snapshot_id):
    return session.scalar(
        select(ReportSnapshot).where(
            ReportSnapshot.id == snapshot_id,
            ReportSnapshot.organization_id == organization_id,
            ReportSnapshot.status.in_(PUBLISHED_SNAPSHOT_STATUSES),
        )
    )

def get_report_job(session, organization_id, job_id, *, for_update=False):
    statement = select(ReportJob).where(
        ReportJob.id == job_id,
        ReportJob.organization_id == organization_id,
    )
    if for_update:
        statement = statement.with_for_update()
    return session.scalar(statement)
```

Every Phase 2 report, job, snapshot, artifact, invitation, and member loader must take the server-revalidated active membership or its organization UUID. Never accept organization authority from a route parameter, form, JSON body, provider claim, email domain, or browser session value without reloading the local session/membership row. Routes convert both absent and foreign resources to the same 404.

**Row-lock mutation pattern** (`roster_scout/repositories.py:59-86`):

```python
existing = session.scalar(
    select(ReportJobDispatch)
    .where(...)
    .with_for_update()
)
if existing is not None:
    return existing
session.add(intent)
session.flush()
return intent
```

Use the same pattern to lock the organization before counting/changing active admins. The last-admin invariant must be enforced inside the locked transaction, not only by UI controls.

---

### `roster_scout/organizations.py`, `reports.py`, and `audit.py` (caller-owned transaction services)

**Analogs:** `roster_scout/jobs.py:create_or_get_job`, `roster_scout/usage.py:record_usage_event`, and the outbox dispatcher.

**Idempotent insert without committing** (`roster_scout/jobs.py:350-390`):

```python
def create_or_get_job(session, organization_id, report_id, idempotency_key):
    statement = (
        postgresql_insert(ReportJob)
        .values(
            id=job_id,
            organization_id=organization_id,
            report_id=report_id,
            idempotency_key=idempotency_key,
            status="queued",
        )
        .on_conflict_do_nothing(
            constraint="uq_report_jobs_organization_idempotency"
        )
        .returning(ReportJob.id)
    )
    inserted_id = session.scalar(statement)
    # reselect/lock existing row on conflict
```

**Idempotent event insert without committing** (`roster_scout/usage.py:106-133`):

```python
statement = (
    insert(ProductUsageEvent)
    .values(...)
    .on_conflict_do_nothing(
        constraint="uq_product_usage_events_type_key"
    )
    .returning(ProductUsageEvent.id)
)
inserted_id = session.scalar(statement)
event = session.scalar(select(ProductUsageEvent).where(...))
```

The authorized create/refresh/retry service must own one `Session.begin()` that does all of the following atomically: reload active membership; load/create the organization-owned report; call the lower-level `create_or_get_job`; append the exactly-once `authenticated_use` keyed by the accepted job UUID; append a sanitized audit event; and leave the job outbox intent durable. Dispatch runs only after commit.

The existing high-level `accept_report_job()` is the compatibility analog but cannot wrap the new Phase 2 transaction because it commits internally (`roster_scout/jobs.py:393-419`):

```python
with session_factory.begin() as session:
    job = create_or_get_job(session, organization_id, report_id, idempotency_key)
    job_id = job.id
# publish only after commit
drain_report_dispatch_outbox(session_factory, dispatch=dispatch, limit=1, job_id=job_id)
```

Refactor by composing the existing lower-level primitive, not by opening a second transaction for usage or audit. Preserve `accept_report_job()` for existing callers if needed.

**Safe dispatch failure handling** (`roster_scout/jobs.py:148-193`): external dispatch happens outside the acceptance transaction; failure releases the durable lease in a fresh transaction and never persists raw broker text. Use this unchanged for create, refresh, and retry.

`organizations.py` follows the same caller-owned style for invitation creation/acceptance, role changes, removal, and last-admin protection. `audit.py` should expose one append-only primitive and reject unrecognized actions/outcomes/reasons before insertion.

---

### `app.py` (Flask routes, authentication gate, and error responses)

**Analog:** existing route and validation helpers.

**Consistent error envelope and bounded input** (`app.py:285-302`):

```python
def _json_error(message: str, status: int):
    return jsonify({"error": message}), status

def _string_field(body: dict, key: str, *, max_length: int) -> str:
    value = body.get(key, "")
    if not isinstance(value, str):
        raise ValueError(f"'{key}' must be a string.")
    value = value.strip()
    if any(ord(character) < 32 for character in value):
        raise ValueError(f"'{key}' cannot contain control characters.")
    if len(value) > max_length:
        raise ValueError(f"'{key}' must be {max_length} characters or fewer.")
    return value
```

**POST contract** (`app.py:447-477`): verify content type and bounded body, require an exact key set, validate canonical values, then return fixed errors. Phase 2 POST forms additionally use global Flask-WTF CSRF; JSON mutations need the same CSRF boundary.

**Fixed external-error mapping** (`app.py:633-645`):

```python
except requests.RequestException:
    app.logger.warning("Upstream request failed while building a workbook", exc_info=True)
    return _json_error("A roster data source is temporarily unavailable.", 502)
except Exception:
    app.logger.exception("Unexpected workbook generation failure")
    return _json_error("Roster Scout could not build that workbook.", 500)
```

For identity routes, tighten this pattern: map provider/database/queue/source/artifact failures to fixed safe categories and never attach provider exception details or callback query values to logs. Auth and access-denial audit events remain sanitized.

**Security headers** (`app.py:305-334`) apply to all responses. Preserve them when adding hosted-auth redirects and external styles; deliberately update CSP only for required WorkOS redirects/resources.

Implement a short explicit public allowlist (`healthz`, `readyz`, sign-in, callback, invitation entry, static, optional demo). Protect every other current and new route—including `/`, `/api/teams`, `/scrape`, acknowledgement, report status, and artifact download—by default. Membership context must be derived server-side for each protected request.

---

### Phase 2 templates (Jinja views and progressive enhancement)

**Analog:** `templates/index.html`.

**Accessible server-rendered state regions** (`templates/index.html:478-490`):

```html
<form id="scout-form" novalidate>...</form>
<div id="state-loading" class="state state-center" role="status" aria-live="polite">...</div>
<div id="state-error" class="state state-center" role="alert">...</div>
```

**Accessible combobox semantics** (`templates/index.html:482-485,982-1008`): use explicit labels, `role="combobox"`, `aria-controls`, `aria-expanded`, `aria-invalid`, listbox options, keyboard focus, and text errors. Move the existing exact NCAA selector into `report_library.html`/the create-report flow without changing its exact-ID semantics.

**Safe DOM rendering** (`templates/index.html:1011-1071`): build dynamic entries with `document.createElement`, set `textContent`, and use event listeners. Do not inject server/provider strings through `innerHTML`.

Create shared shell/macros/partials if the planner splits the templates further, but keep the UI server-rendered. The full report detail and snapshot history must exist without JavaScript. Desktop tables and mobile cards must expose equivalent data/actions. All organization switch, logout, invitation, membership, create, refresh, retry, and download actions are POST/authorized forms with CSRF.

---

### `static/report_status.js` (vanilla JS polling state machine)

**Analog:** browser/Node-compatible module in `static/download_tracking.js`.

**Dual browser/CommonJS wrapper** (`static/download_tracking.js:1-9`):

```javascript
(function exposeDownloadTracking(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  } else {
    root.RosterScoutDownloadTracking = api;
  }
})(typeof globalThis === "object" ? globalThis : this, function buildApi() {
  "use strict";
```

Copy this shape so `tests/js/report_status.test.mjs` can import the same pure state machine used by the browser.

**Injected side effects for deterministic tests** (`static/download_tracking.js:268-340`):

```javascript
async function runDownloadSubmission({
  tracker,
  retryPendingAcknowledgement,
  requestWorkbook,
  initiateDownload,
  acknowledgeDownload,
}) {
  // state transitions call injected request/UI functions
}
```

Implement polling with injected `fetch`, clock/timer, visibility, and view-update functions. Cadence is 3 seconds through 30 seconds, 5 seconds through 2 minutes, then 10 seconds through 15 minutes. Pause while `document.hidden`, poll immediately when visible, stop on `ready|partial|failed`, and stop at 15 minutes with manual `Check status`. Treat every response as a complete safe view model and announce only state transitions.

**Fetch/error UI pattern** (`templates/index.html:1079-1117`): decode JSON, check `response.ok`, ignore superseded requests with a request ID, and render fixed fallback text. Do not infer job state from HTTP 200 or elapsed time.

---

### Phase 2 Python and Node tests (fixtures, concurrency, route matrix)

**Analogs:** `tests/db_support.py`, `tests/test_report_jobs.py`, `tests/test_app_routes.py`, and `tests/js/download_tracking.test.mjs`.

**Disposable PostgreSQL guard** (`tests/db_support.py:15-25`):

```python
def require_test_database_url():
    raw_url = os.environ.get("DATABASE_URL", "").strip()
    if not raw_url:
        raise unittest.SkipTest("DATABASE_URL is required for PostgreSQL integration tests")
    url = make_url(raw_url)
    if url.get_backend_name() != "postgresql":
        raise ValueError("Database integration tests require PostgreSQL; SQLite is forbidden")
    if not (url.database or "").endswith("_test"):
        raise ValueError("DATABASE_URL must name a disposable database ending in '_test'")
    return raw_url
```

**Shared DB test case and fixture** (`tests/test_report_jobs.py:32-75`): upgrade once in `setUpClass`, create a shared engine/session factory, truncate in `setUp`, and create org/team/season/report rows inside `Session.begin()`.

**Idempotency and concurrency evidence** (`tests/test_report_jobs.py:85-121,178-238`): assert duplicate requests converge on one UUID/dispatch; force rollback and assert job plus outbox both disappear; use `threading.Barrier` and distinct engines/sessions to test concurrent acceptance. Reuse that exact pattern for last-admin races and concurrent create/refresh/retry clicks.

**Flask route test style** (`tests/test_app_routes.py:1053-1094`): configure `TESTING=True`, use `app.test_client()`, patch only the external boundary, and assert status, headers, rendered content, and absence of prohibited content. Phase 2 needs a table-driven public/protected route matrix and two-organization resource substitution matrix.

**Node test style** (`tests/js/download_tracking.test.mjs:1-16,64-79`):

```javascript
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import test from "node:test";

const require = createRequire(import.meta.url);
const { createDownloadTracker } = require("../../static/download_tracking.js");
```

Use fake time, fake visibility, fake fetch, and captured view calls; do not require a browser runtime for cadence/state-machine coverage.

`tests/fixtures/workos-emulate.config.yaml` has no repository analog. Keep it deterministic and synthetic; never place real credentials or recipient email addresses in it. Unit tests should primarily inject a fake `IdentityProvider`; emulator and staging suites stay separately selectable.

---

### Dependencies, configuration, deployment, and release contracts

**Analogs:** `requirements.txt`, `.env.example`, `fly.toml`, `DEPLOYMENT.md`, `scripts/validate_phase1_release.py`, and `tests/test_deployment_config.py`.

Pin direct dependencies one per line as the current file does; research specifies `workos==10.8.0` and `Flask-WTF==1.3.0`, subject to implementation-time registry verification.

Document names/defaults in `.env.example` but keep secrets out of tracked files. WorkOS API key, client ID, cookie/action secret, redirect/sign-out URI, and environment mapping must be supplied by secret stores in deployed environments. Staging and production WorkOS resources are separate and require an operator checklist.

**Fly release and process contract** (`fly.toml:7-14,47-65`):

```toml
[deploy]
  release_command = "alembic upgrade head"

[processes]
  app = "xvfb-run -a gunicorn ... app:app"
  worker = "python scripts/run_worker.py"

[http_service]
  processes = ["app"]
```

Identity adds app configuration, not a new long-running process. Keep real secrets out of `[env]`; preserve the release migration before traffic and app-only HTTP exposure.

**Fail-fast release manifest** (`scripts/validate_phase1_release.py:245-350`): the existing gate runs minimal upgrade/current, focused regressions, full downgrade/upgrade/current, `alembic check`, Python discovery, Node tests, catalog validation, deployment tests, compilation, template validation, and both committed/working-tree `git diff --check`. `validate_phase2_release.py` should preserve this ordering and add Phase 2 story/auth/tenant-isolation/status tests plus the required new templates.

**Configuration regression style** (`tests/test_deployment_config.py:109-165`): parse `fly.toml` with `tomllib`, assert exact nonsecret env/process topology, and scan source text to ensure credential URLs/secrets are absent. Add assertions that WorkOS secrets are not in `fly.toml`, mandatory production settings fail closed, and the release command remains `alembic upgrade head`.

Deployment remains blocked until managed PostgreSQL, Redis, object/evidence storage, Fly worker capacity, and WorkOS production ownership/billing/configuration exist. Emulator/staging implementation can proceed; production activation requires a human-owned live authentication smoke test.

## Shared Patterns

### Caller-Owned Transactions

**Sources:** `roster_scout/jobs.py:350-390`, `roster_scout/usage.py:66-133`  
**Apply to:** accounts, sessions, organizations, reports, audit, repositories.

Lower-level services accept an existing `session`, validate inputs, flush/reselect, and never commit. The route/application service owns the transaction. Job acceptance, outbox intent, authenticated-use, and audit are one atomic unit; broker publication happens afterward.

### Tenant Scope and Non-Enumeration

**Source:** `roster_scout/repositories.py:27-56`  
**Apply to:** all report/job/snapshot/artifact/member/invitation reads and writes.

Every query includes both resource identity and membership-derived organization identity. Foreign and absent identifiers produce the same 404 and response shape. Tests always create two organizations.

### Idempotency and Concurrency

**Sources:** `roster_scout/jobs.py:350-390`, `roster_scout/repositories.py:59-86`, `tests/test_report_jobs.py:178-238`  
**Apply to:** login transactions, invitations, report creation, refresh, retry, usage, and last-admin mutation.

Use PostgreSQL uniqueness plus `ON CONFLICT DO NOTHING`/reselect for deduplication, row locks for invariants, and real PostgreSQL barrier tests for races. Do not rely on client disabling or Redis for correctness.

### Safe Error Handling and Logging

**Sources:** `app.py:285-302,633-645`, `roster_scout/repositories.py:192-213`  
**Apply to:** all identity, authorization, queue, source, artifact, and audit paths.

Expose fixed customer-safe messages/categories. Persist only allowlisted reason tokens. Never log provider exception text, callback/query data, raw email, cookie, code, token, assertion, sealed session, request body, or credential-bearing URL.

### Server-Rendered Progressive Enhancement

**Sources:** `templates/index.html:478-490,982-1117`, `static/download_tracking.js:1-9,268-350`  
**Apply to:** sign-in transition, organization selection, report library/detail, refresh/retry, and membership administration.

Render a complete page first. JavaScript enhances bounded status/action regions, uses safe text APIs, retains accessible roles/live regions/focus, and exposes pure injectable state machines to Node tests.

### Test and Release Discipline

**Sources:** `tests/db_support.py:15-54`, `scripts/validate_phase1_release.py:245-350`, `fly.toml:7-14`  
**Apply to:** every Phase 2 plan.

PostgreSQL integration tests run only against a database ending in `_test`; SQLite is forbidden. Migrations must pass base-to-head, re-upgrade, head, drift, and rollback coverage. Release migrations execute before traffic, and tracked deployment files contain no real secrets.

## No Analog Found

| File / Concern | Role | Data Flow | Planner Guidance |
|---|---|---|---|
| `roster_scout/identity.py` concrete WorkOS adapter | provider | external request-response | Use the `IdentityProvider` protocol and official WorkOS SDK contract from `02-RESEARCH.md`; keep provider calls replaceable and provider errors sanitized. |
| `roster_scout/sessions.py` custom Flask server-side `SessionInterface` | provider / service | request-response / CRUD | Use the research opaque lookup/verifier design and Flask-WTF integration; add focused rotation, expiry, logout, revocation, and concurrent save tests. |
| `tests/fixtures/workos-emulate.config.yaml` | fixture | provider simulation | Follow official WorkOS Emulate schema; use only synthetic users/orgs/failures and keep emulator tests optional/separate from credential-free unit tests. |

## Metadata

**Analog search scope:** `app.py`, `roster_scout/`, `migrations/versions/`, `templates/index.html`, `static/download_tracking.js`, task-relevant tests, release validator, and deployment/configuration files. Scraper internals and local credential/token files were not inspected.  
**Files scanned:** 24 source/context files selected from 74 files in the task-relevant directories.  
**Strong analog families used:** 5  
**Pattern extraction date:** 2026-11-11
