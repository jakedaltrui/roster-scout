---
phase: 05-embedded-opponent-dashboard
slug: embedded-opponent-dashboard
status: approved
shadcn_initialized: false
preset: none
created: 2026-11-16
---

# Phase 5 — UI Design Contract

> Operate-mode contract inferred from the approved homepage/portal system and
> the user's instruction to continue autonomously. The dashboard extends the
> incumbent visual world; it does not create a new one.

## Design System

| Property | Value |
|---|---|
| Tool | Existing server-rendered Flask templates + vanilla JavaScript |
| Component library | None; reuse product-shell controls and tokens |
| Icon library | Existing inline SVG vocabulary only |
| Font | DM Sans in the Roster Scout shell; Segoe UI/DM Sans-compatible fallback inside PBIR |
| Visual authority | Current homepage and its light/dark implementations |

## Surface Structure

The chosen structure is a **snapshot control strip over a full-width analytical
canvas**:

1. Existing global navbar.
2. Compact report identity row: opponent logo/name, division/season, snapshot
   age, completeness state, refresh action.
3. App-owned analysis tabs: Overview, Roster, Hitting, Pitching, Fielding,
   Splits, Player, Data Quality.
4. Full-width Power BI canvas with Power BI page navigation hidden.
5. Quiet export pair and snapshot history below/alongside the analysis rather
   than repeated inside every dashboard page.

The signature interaction is scope comparison: one familiar segmented control
switches Full season, Last 10, and Conference across the relevant visuals while
retaining the selected player. It uses standard tabs/segmented controls, not a
novel dashboard metaphor.

## Spacing Scale

Reuse product tokens and current rounded-card rhythm.

| Token | Value | Usage |
|---|---:|---|
| xs | 4px | icon/label separation |
| sm | 8px | compact control gaps |
| md | 16px | tab and metadata grouping |
| lg | 24px | panel padding and mobile section gaps |
| xl | 32px | desktop section separation |
| 2xl | 48px | page-level breathing room only |

Exceptions: existing 6px, 10px, 12px, 14px, 18px, and 20px product-shell
values remain valid when reusing an existing component. Do not introduce a
parallel spacing system.

## Typography

| Role | Size | Weight | Line height |
|---|---:|---:|---:|
| Data/meta | 12–13px | 500–650 | 1.35 |
| Body | 14–16px | 450–600 | 1.5 |
| Section heading | 20–24px | 700 | 1.2 |
| Report title | 28–32px | 700 | 1.1 |
| KPI value | 28–36px | 700 | 1.0 |

Product typography is fixed-size, not fluid display type. Use tabular numerals
for metrics and time.

## Color

- Reuse `--canvas`, `--surface`, `--ink`, `--muted`, `--border`, and existing
  semantic state tokens from `static/product.css`.
- School colors may accent the opponent lockup and one selected/chart series,
  but never recolor the global shell or reduce contrast.
- Green is success/ready only; amber is partial/pending; red is failure or
  destructive only; blue is informational/active refresh only.
- The PBIR theme ships light-neutral. Roster Scout's outer dark theme uses a
  neutral frame around the embedded light analytical canvas until a separately
  validated dark PBIR theme exists; never invert an iframe with CSS.

## Component and State Contract

- **Tabs:** ordinary buttons with `aria-selected`, horizontal scroll on narrow
  widths, and the same hover/focus/active timing as product-shell nav.
- **Scope switch:** Full season / Last 10 / Conference; retain selection across
  page changes when Power BI supports the corresponding slicer.
- **Loading:** render a dashboard skeleton immediately. No empty iframe, giant
  spinner, or re-running homepage build animation.
- **Pending refresh:** workbook actions remain active; analytics area explains
  “Interactive report is being updated” with source freshness and a restrained
  progress indicator.
- **Failed refresh:** fixed sanitized copy, workbook actions remain active, and
  one retry/status action appears only when authorized.
- **Partial data:** visible text plus status icon; color alone is insufficient.
- **Embed error:** the Power BI error surface is hidden and replaced by a fixed
  Roster Scout message with retry. Never show provider payload text.
- **Token renewal:** silent while successful; one inline recoverable message if
  reauthorization fails. Do not reload the whole page.

## Motion

- 180–220ms product easing for tabs, menus, state changes, and skeleton reveal.
- Dashboard content fades/raises by at most 4px after Power BI reports `loaded`.
- Tab changes use opacity only; no page-scale slides or orchestrated entrance.
- Preserve `prefers-reduced-motion`; content remains visible without animation.

## Responsive Contract

- Desktop: full-width analysis canvas, tab row above, metadata/actions on one
  row when space allows.
- Tablet: metadata wraps; export controls stay paired; dashboard maintains a
  minimum readable aspect and uses internal report scrolling.
- Mobile: tabs become horizontally scrollable, scope control becomes full
  width, report canvas uses the Power BI mobile layout when present, and export
  actions stack. No desktop canvas squeezed into an unreadable thumbnail.
- Validate at 1440px, 768px, 390px, and 320px, in light and dark shell themes.

## Copywriting Contract

| Element | Copy |
|---|---|
| Analysis heading | Opponent analysis |
| Ready support | Interactive report updated from this saved version. |
| Pending heading | Interactive report is being updated |
| Pending support | Your workbook is ready. Analysis will appear when the update finishes. |
| Failed heading | Interactive report unavailable |
| Failed support | Your saved workbook is still ready to use. Try the analysis again in a moment. |
| Embed retry | Try analysis again |
| Empty/unsupported page | No verified data for this view |
| Data-quality link | Review data quality |

Do not use internal terms such as semantic model, dataset, embed token, RLS, or
snapshot in primary customer copy. “Saved version” is acceptable where version
history matters.

## Accessibility

- Tabs implement the WAI-ARIA tab pattern with arrow-key navigation.
- The embedded report container has a stable accessible name identifying the
  opponent and saved version.
- Loading and readiness changes use one polite live region; failures use an
  alert only once.
- Focus never moves into the iframe until the customer activates the analysis
  region. Focus returns to the owning tab/control after an embed reload.
- Minimum touch target is 44px; no interaction depends on hover.

## Checker Sign-Off

- [x] Dimension 1 Copywriting: PASS — customer language hides provider internals
- [x] Dimension 2 Visuals: PASS — one clear report shell and one analysis canvas
- [x] Dimension 3 Color: PASS — incumbent tokens and semantic color discipline
- [x] Dimension 4 Typography: PASS — existing DM Sans product scale
- [x] Dimension 5 Spacing: PASS — current shell rhythm retained
- [x] Dimension 6 Registry Safety: PASS — no third-party UI registry/components

**Approval:** approved 2026-11-16 under the user's autonomous execution instruction
