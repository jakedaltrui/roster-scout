# Security policy

## Supported version

Security fixes are applied to the latest commit on `main`.

## Reporting a vulnerability

Please do not open a public issue for a suspected vulnerability. Use [GitHub's private vulnerability reporting form](https://github.com/daltruibaseball-cpu/roster-scout/security/advisories/new) and include reproduction steps, impact, and any suggested mitigation.

## Deployment checklist

- Store `BROWSERLESS_TOKEN` and any proxy credentials in the hosting provider's secret manager, never in source control or `fly.toml`.
- Keep TLS verification enabled. Disable it only for a specific, trusted proxy product that requires a managed certificate and only after reviewing the risk.
- Enable `TRUST_FLY_CLIENT_IP` only behind Fly.io, where the platform controls that header.
- Keep the app at one machine unless request and Browserless limits are moved to a shared store.
- Apply dependency updates promptly and review automated audit failures before deployment.
- Rotate any credential that appears in logs, shell history, screenshots, or issue reports.

This repository intentionally contains no OAuth credentials, API tokens, browser profiles, cached player results, or generated workbooks.
