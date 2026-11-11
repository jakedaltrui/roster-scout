# Deferred Items

## Repository-wide PostgreSQL suite isolation

- **Discovered during:** Plan 01-29 final verification
- **Scope:** Existing canonical-publication and migration test infrastructure
- **Observed behavior:** A full `unittest discover` run against the single shared `roster_scout_test` database produced schema-migration deadlocks, duplicate PostgreSQL type creation, and cross-test truncation races in `tests/test_canonical_publish.py`. The Plan 01-29 acceptance suite and complete app-route suite pass independently against the same database.
- **Why deferred:** The failures are outside the download-event route and browser contract changed by Plan 01-29. Correcting repository-wide database isolation would require coordinated changes across existing migration and publication test fixtures.
- **Suggested follow-up:** Give schema-mutating suites isolated disposable databases or serialize all migration lifecycle subprocesses before treating one shared-database discovery run as a reliable gate.

## PostgreSQL connection cleanup warning

- **Discovered during:** Plan 01-31 final verification
- **Scope:** Existing PostgreSQL test connection lifecycle
- **Observed behavior:** The complete 369-test discovery passed, then Python emitted one `ResourceWarning` for a psycopg connection deleted while still open.
- **Why deferred:** The warning appears after the green repository-wide suite and is outside the authoritative identity migration, publisher, and focused concurrency fixtures changed by Plan 01-31.
- **Suggested follow-up:** Run the suite with resource warnings promoted to errors and trace the owning pre-existing fixture before changing shared connection cleanup.
