# Phase 1: Canonical Snapshot Foundation - Pattern Map

**Mapped:** 2026-11-01
**Purpose:** Connect proposed Phase 1 files to the closest existing Roster Scout patterns.

## Repository Shape

Roster Scout is a deliberately small, flat Flask application. Phase 1 should add a focused `roster_scout/` package for persistence and worker concerns while leaving `app.py`, `roster_scraper.py`, and their public imports operational. Do not reorganize the entire application as a prerequisite.

## New and Modified File Map

| Proposed file | Role/data flow | Closest analog | Pattern to preserve |
|---------------|----------------|----------------|---------------------|
| `requirements.txt` | Pin SQLAlchemy, Alembic, Psycopg, Celery/Redis | Existing dependency file | Exact direct dependency pins, one package per line. |
| `roster_scout/database.py` | Lazy engine and session factory | `usage_limits.py::_state_dir()` | Environment-driven resource location, no import-time external connection. |
| `roster_scout/enums.py` | Job/snapshot/section/artifact/identity states | `app.py` module constants | Small explicit constants near the service boundary. |
| `roster_scout/models.py` | PostgreSQL canonical schema | No current DB analog | SQLAlchemy 2.1 declarative models with named constraints and organization-scoped foreign keys. |
| `roster_scout/repositories.py` | Tenant-scoped queries and idempotent creation | `roster_scraper.py::_find_ncaa_team_by_season_id()` | Exact identifier lookup and explicit error behavior; never fuzzy-match ownership. |
| `roster_scout/canonical.py` | Parsed NCAA evidence to typed records | `roster_scraper.py::_parse_ncaa_stat_table()` + `_normalize_ncaa_players()` | Preserve source rows first, normalize deterministically, keep missing distinct from zero. |
| `roster_scout/snapshots.py` | Validate and publish immutable snapshots | `roster_scraper.py::_atomic_write()` | Stage work, validate, then expose with one atomic operation. |
| `roster_scout/celery_app.py` | Broker configuration | `app.py` environment configuration | Read secrets from environment, expose safe defaults only, avoid import-time network calls. |
| `roster_scout/tasks.py` | `job_id` worker entry | `app.py::scrape()` exception boundaries | Map known failures to safe categories, log unexpected failures server-side, never return/store secret exception text. |
| `roster_scout/evidence.py` | PostgreSQL-backed failed HTML compression and purge | `roster_scraper.py::_atomic_write()` | Record checksum and metadata atomically, store bytes only for failures, preserve metadata after purge. |
| `scripts/run_worker.py` | Validated Celery worker launcher | `scripts/run_local.sh` operational boundary | Read bounded environment configuration, reject unsafe values, then exec the long-running process. |
| `migrations/` + `alembic.ini` | Versioned PostgreSQL DDL | No current analog | Hand-review generated migration, use named constraints, include forward and downgrade path. |
| `tests/test_canonical_data.py` | Pure normalization tests | `tests/test_ncaa_workflow.py` | `unittest.TestCase`, fixture helper, deterministic D1/D2/D3 HTML, direct private-helper assertions. |
| `tests/test_snapshot_persistence.py` | PostgreSQL invariant tests | `tests/test_ncaa_workflow.py::setUp/tearDown` | Isolated setup/cleanup, explicit environment patching, no network. |
| `tests/test_report_jobs.py` | Job/idempotency/retry tests | `tests/test_app_routes.py` mocks | `unittest.mock.patch`, exact call counts, explicit response/state assertions. |
| `tests/test_canonical_publish.py` | Fixture-to-snapshot integration | `test_parses_and_normalizes_all_three_divisions` | Loop over D1/D2/D3 fixtures and assert one consistent schema/behavior. |
| `compose.yaml` | Local app + PostgreSQL + Redis | Existing Compose service | Named services/volumes, environment values only for safe local defaults. |
| `Dockerfile` | Include package, migrations, worker entry | Existing explicit `COPY` list | Continue unprivileged runtime; copy only required application paths. |
| `fly.toml` | Web/worker process groups and migration release | Existing app process config | HTTP and the `/data` volume target app only; worker has no public service or shared volume. |
| `.env.example` | Document safe configuration names | Existing safe-default comments | Empty secret values and explicit operational defaults; never include credentials. |
| `DEPLOYMENT.md` | Managed services, migrations, worker limits | Existing deployment guide | Concrete commands, external-boundary warnings, release checklist. |

## Existing Patterns to Reuse

### Atomic replace as the conceptual publication analog

`roster_scraper.py::_atomic_write()` writes a temporary file and exposes it only with `os.replace()` after successful completion. PostgreSQL publication should preserve that same stage-then-expose property through a draft snapshot and one transactionally validated status transition.

```python
with handle:
    handle.write(content)
os.replace(handle.name, path)
```

### Schema/version-aware cache

The existing cache rejects payloads whose version differs. Section cache keys should extend this pattern with source identity, schema version, parser version, and accepted job identity.

```python
if payload.get("cache_version") != _NCAA_CACHE_VERSION:
    return None
```

### Source ID first, conservative fallback

The current normalizer already prefers `_player_id` and falls back to a normalized name. Phase 1 must retain the first branch while tightening the fallback with team-season/supporting fields and explicit unresolved status.

```python
player_id = row.get("_player_id")
if player_id:
    return f"id:{player_id}"
return f"name:{_normalize_school_name(_stat(row, 'Player'))}"
```

### Exact selector identity

`/scrape` validates `season_team_id` as bounded ASCII digits and calls `scrape_ncaa_by_id()` directly. Canonical report creation should take the resolved catalog row/IDs, not reintroduce fuzzy name selection.

```python
if season_team_id and (
    not season_team_id.isascii() or not season_team_id.isdigit()
):
    return _json_error("'season_team_id' must contain only digits.", 400)
```

### Capacity context manager

`browserless_slot()` acquires before work and releases in `finally`. Worker tasks must call the existing fetch boundary so this cleanup behavior remains intact.

```python
try:
    yield
finally:
    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    handle.close()
```

### Known versus unexpected error handling

The route returns known NCAA/validation failures without leaking arbitrary exception text, and logs unexpected errors server-side. Job failure persistence should use an allowlisted category plus safe message, never `repr(exc)`.

```python
except (NcaaError, ValueError) as exc:
    return _json_error(str(exc), 502)
except Exception:
    app.logger.exception("Unexpected workbook generation failure")
    return _json_error("Roster Scout could not build that workbook.", 500)
```

### Test isolation

Current tests isolate filesystem state with `TemporaryDirectory`, patch environment variables, restore NCAA lookup globals, and patch external boundaries. Database tests should mirror this discipline with an isolated PostgreSQL schema/database and explicit cleanup.

```python
self.temp_dir = tempfile.TemporaryDirectory()
self.env = patch.dict(os.environ, {
    "ROSTER_SCOUT_CACHE_DIR": self.temp_dir.name,
    "NCAA_REQUEST_DELAY_SECONDS": "0",
})
```

## Integration Guardrails

1. Preserve imports used by `app.py`: `scrape_ncaa_by_id`, `scrape_ncaa_by_name`, `split_roster`, `HITTER_HEADERS`, and `PITCHER_HEADERS`.
2. Introduce a richer collection result beside the current tuple interface; adapt old callers rather than breaking them.
3. Keep `app.py::scrape()` synchronous in Phase 1. The durable job service exists for future Phase 2 routes and Phase 1 integration tests.
4. Do not move workbook formatting into the new package in this phase.
5. Keep current cache behavior for the legacy path while the durable path uses section/job-version-aware evidence.
6. Keep the Docker runtime unprivileged and ensure both web and worker use the same immutable image.
7. Do not horizontally scale worker process groups while Browserless capacity remains machine-local.

## Pattern Mapping Complete
