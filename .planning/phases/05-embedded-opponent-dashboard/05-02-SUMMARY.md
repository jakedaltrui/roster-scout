# Plan 05-02 Summary — Secure embedding and refresh

Added a fail-closed app-owns-data provider using service-principal credentials,
fixed Microsoft endpoints and resource IDs, short-lived view-only embed tokens,
and the exact `RosterScoutSnapshotReader` effective identity.

Added tenant-scoped, no-store embed and dashboard-status endpoints plus an
independent semantic-refresh lifecycle. Refresh failure records sanitized
diagnostics without changing snapshot or workbook readiness. Synthetic preview
mode is deterministic, visibly labeled, and rejected in production.

Verification: provider payload, token, refresh, foreign-ID denial, no-provider-
call-on-denial, audit, and secret-redaction tests pass.
