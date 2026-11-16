# Roster Scout Power BI project

This folder is the source-controlled Power BI Project (PBIP) for the embedded
opponent dashboard. The report reads only the `pbi_*` PostgreSQL views created
by migration `0026`. Those views expose published immutable snapshots and repeat
the exact tenant/snapshot key on every row:

`<organization UUID>|<snapshot UUID>`

The `RosterScoutSnapshotReader` role independently applies
`security_key = CUSTOMDATA()` to every imported table. The Flask server is the
only component allowed to generate embed tokens and supplies that exact key as
the effective identity's `customData`. Browser requests cannot choose a tenant,
workspace, dataset, report, role, or security key.

## Validate locally

```sh
uv run python scripts/validate_power_bi_project.py
```

The gate checks the eight-page PBIR project with Microsoft's authoring CLI,
the 14-table semantic model, advanced measures, per-table RLS, the analytics
view boundary, and the absence of embedded credentials.

For a local UI preview, run the app with `POWER_BI_MODE=fake`. Synthetic values
are visibly labeled and this mode is rejected when `APP_ENV=production` or
`FLY_APP_NAME` is present.

## Publish without committing secrets

1. Open `RosterScout.pbip` in Power BI Desktop and set the two semantic-model
   parameters to the production PostgreSQL server and database.
2. Publish the semantic model and report to a dedicated workspace on Fabric or
   Power BI Embedded capacity.
3. Grant the app registration service principal the least workspace access it
   needs. Keep the client secret in the deployment secret store only.
4. Configure the environment variables documented in `DEPLOYMENT.md`.
5. Run a real two-organization RLS test: each token must see only its exact
   snapshot, and a mismatched `customData` key must return no rows.
6. Set `POWER_BI_CAPACITY_VERIFIED=true` only after that test passes.

The workbook and dashboard are intentionally independent. A failed or delayed
Power BI refresh never replaces, mutates, or blocks the immutable Excel artifact.
