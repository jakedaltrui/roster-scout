# Product

<!-- impeccable:product-schema 1 -->

> This record was inferred from the repository, planning artifacts, and the
> user's explicit product decisions while they asked the implementation to
> continue autonomously. Undecided items remain labeled as open.

## Platform

web

## Users

College baseball coaches and staff use Roster Scout while preparing for an
opponent or series. They need trustworthy, current statistics that can be
reviewed quickly in a browser and carried into existing staff workflows as a
spreadsheet.

## Product Purpose

Roster Scout turns one exact NCAA opponent selection into an immutable,
auditable scouting snapshot. A successful experience lets staff review that
snapshot interactively, understand freshness and missing data, and export the
same facts to a polished workbook without learning a complicated application.

## Positioning

Roster Scout combines live NCAA collection, canonical player identity,
truthful completeness, organization-shared history, and portable exports in
one lightweight customer flow. The dashboard and workbook are two views of the
same saved snapshot, not separate calculations.

## Operating Context

- The public homepage and exact-team picker remain the primary entry point.
- A verified school `.edu` email unlocks generation and the shared staff portal.
- Staff may run the same opponent on consecutive game days; each explicit
  refresh creates a new immutable version while older versions remain usable.
- Coaches review in the browser, download Excel, or export a copy to Google
  Sheets. The browser dashboard is a lightweight native Roster Scout view,
  with no separate BI account, iframe, or customer setup.

## Capabilities and Constraints

- Flask, server-rendered templates, vanilla JavaScript, PostgreSQL, Redis,
  background report workers, private workbook storage, and WorkOS are the
  established stack.
- Canonical data distinguishes zero from missing and retains source identity,
  URLs, timestamps, metric methodology, scope status, and snapshot lineage.
- Reports are organization-owned, immutable by snapshot, explicitly refreshed,
  and isolated across organizations. Saved report data and workbook objects are
  retained for 30 days; raw usage events are retained for 90 days.
- The native dashboard reads only the already-authorized immutable snapshot and
  adds no secondary analytics store or customer-visible Power BI dependency.
- Production deployment and billing/pricing are separate decisions and require
  explicit user approval.

## Brand Commitments

The existing polished homepage is the product's visual and interaction gold
standard. Preserve the Roster Scout name, dog mark, DM Sans typography,
cream/black neutral palette, restrained green success language, light/dark
themes, compact rounded controls, subtle state motion, and lightweight tone.
The portal and dashboard must feel like additions to that interface, not a
replacement product or generic enterprise BI shell.

## Evidence on Hand

- The homepage, selector, sign-in transformation, account menu, portal, report
  detail, loading states, and export controls are implemented in `templates/`
  and `static/`.
- Canonical season, derived, fielding, split, career, game-log, last-10, and
  conference data contracts are implemented and covered by fixtures/tests.
- The two-sheet workbook renderer and immutable artifact flow are implemented
  and release-tested.
- The native dashboard is server-projected from canonical snapshot facts and
  rendered with dependency-free JavaScript.

## Product Principles

1. Make the common path obvious and keep administration secondary.
2. Show the state and freshness of data without teaching internal terminology.
3. Never trade auditability or tenant isolation for convenience.
4. Keep interactive analysis and portable exports numerically identical.
5. Preserve the homepage's quiet, cohesive experience across every surface.

## Accessibility & Inclusion

Maintain keyboard operation, visible focus, semantic status announcements,
44px touch targets, reduced-motion support, color-independent state labels,
and usable layouts from 320px mobile widths through desktop.
