# Phase 2 — UI Review

**Audited:** 2026-11-13
**Baseline:** Approved `02-UI-SPEC.md`
**Screenshots:** Not captured (code-only audit; no dev server responded on ports 3000, 5173, or 8080)

---

## Pillar Scores

| Pillar | Score | Key Finding |
|--------|-------|-------------|
| 1. Copywriting | 2/4 | Core auth copy is accurate, but required empty, partial, timeout, role-action, and time-zone copy diverges from the contract. |
| 2. Visuals | 2/4 | The shell and responsive structures exist, but the dog mark, active navigation, state variants, and report-detail hierarchy are not implemented as specified. |
| 3. Color | 2/4 | Base light/dark tokens match, but all semantic status colors are absent and several accent/destructive treatments violate their reserved roles. |
| 4. Typography | 2/4 | The product shell uses the intended scale, but page-local CSS introduces six undeclared sizes and prohibited letter-spaced uppercase labels. |
| 5. Spacing | 2/4 | The 1200px shell and 44px targets are mostly present, but spacing/radius values drift from the scale and the refresh dialog exceeds the 480px contract. |
| 6. Experience Design | 1/4 | Destructive/admin workflows, refresh pending behavior, non-JavaScript report creation, and download feedback do not meet the interaction or accessibility contract. |

**Overall: 11/24**

---

## Top 3 Priority Fixes

1. **Replace the member/invitation `<details>` controls with the specified accessible confirmation dialogs and add demotion/last-admin behavior** — admins can currently demote or attempt to remove critical accounts without the required deliberate confirmation or visible guardrail — implement labelled dialogs with safe initial focus, focus trap/restoration, Escape handling, `Save role`, demotion confirmation, and visibly disabled last-admin controls.
2. **Implement the complete report state system** — coaches cannot reliably distinguish queued, collecting, saving, ready, partial, and failed states at a glance, and partial/timeout copy omits required recovery detail — add semantic status tokens/variants, exact copy, unavailable-section lists, time-zone-labelled timestamps, and accent treatment for refresh/retry/current progress only.
3. **Complete pending/error and progressive-enhancement behavior for report actions** — refresh and download submissions provide no required pending label or actionable failure state, while report creation is unusable without JavaScript — add server-rendered selector fallback, `Starting refresh…` / `Accepting retry…` states, atomic error restoration/focus, and visible download progress/failure feedback.

---

## Detailed Findings

### Pillar 1: Copywriting (2/4)

- **WARNING — Empty-state body is not the approved copy.** `templates/report_library.html:49` says “start a shared scouting library for your organization,” while the contract requires “save a dated opponent snapshot for your staff.” This removes the key immutable-snapshot promise.
- **WARNING — Partial state copy is incomplete.** `templates/report_detail.html:110-112` substitutes “usable” copy and does not render the required heading, “You can still review the saved sections,” or the exact unavailable-section list.
- **WARNING — Poll-timeout copy diverges.** `static/report_status.js:56-60` says “Automatic updates paused. Check status to continue.” It omits the contract reassurance that checking again will not start another build.
- **WARNING — Member role action is mislabeled.** `templates/organization_members.html:13` uses `Update role`; the approved label is `Save role`, with a separate confirmation when changing an Admin to Member.
- **WARNING — Customer-facing dates omit time-zone abbreviations.** Examples include `templates/report_library.html:76`, `templates/report_detail.html:104-108`, and `templates/organization_members.html:78,99,106`. The `<time datetime>` values are present, but the visible labels do not meet the organization/browser-time-zone disclosure contract.
- **PASS evidence.** Sign-in, no-membership, generic not-found, invitation mismatch, and historical artifact-unavailable text substantially match the required language (`templates/sign_in.html:44-51`, `templates/auth_error.html:31-42`, `templates/invitation_outcome.html:3-15`, `templates/report_detail.html:126`).

### Pillar 2: Visuals (2/4)

- **WARNING — The required outlined dog mark is replaced by text initials.** The header, sign-in transition, and empty state render `RS` boxes instead of the existing outlined brand asset (`templates/product_base.html:20-23`, `templates/sign_in.html:18-20,42`, `templates/report_library.html:47`).
- **WARNING — Active navigation has semantics but no visible active treatment.** `templates/product_base.html:25` emits `aria-current="page"`, but `static/product.css:84-106` contains no `[aria-current]` styling or active accent marker. `Members` also never receives `aria-current`.
- **WARNING — Status and banner components have no visual variants.** Every state uses the same neutral `.status-chip` (`static/product.css:177`) and banners differ only by an error border (`static/product.css:170-172`). Queued/collecting/saving/ready/partial/failed are therefore visually undifferentiated.
- **WARNING — Contract primary actions are visually secondary.** `Start live refresh` is a plain `.button` and `Retry build` uses `.button--secondary`, which has no stylesheet definition (`templates/report_detail.html:81,168`; `static/product.css:117-132`).
- **WARNING — Latest snapshot and active build share the same panel treatment.** Both use `.report-panel` with equal padding, border, surface, and heading structure (`templates/report_detail.html:88-153`), so code does not establish the specified dominant latest-snapshot anchor and subordinate active build.
- **needs_human_review: true —** Without screenshots, rendered focal hierarchy, the effective 60/30/10 balance, logo fidelity, and visual continuity with hosted WorkOS AuthKit cannot be conclusively judged.

### Pillar 3: Color (2/4)

- **PASS evidence.** The seven core light tokens and seven dark equivalents in `static/product.css:4-11,207-230` match the contract values for canvas, surface, ink, muted ink, border, accent, and destructive color.
- **WARNING — Zero semantic status-role tokens are implemented.** The stylesheet has no success, warning, informational, or failed foreground/background tokens; ready, partial, active, and failed all fall back to the neutral canvas chip (`static/product.css:177`).
- **WARNING — Accent is missing from reserved actions.** `Start live refresh`, `Retry build`, active navigation, and current job progress do not use the accent (`templates/report_detail.html:81,140,168`; `templates/product_base.html:24-27`).
- **WARNING — Accent is used decoratively outside the allowlist.** Exact-selection helper text is accent-colored at `templates/report_new.html:27`; the contract reserves accent for primary action, selected division, active navigation/organization, current progress, and focus.
- **WARNING — Auth pages bypass product tokens.** The audited CSS/templates contain 31 unique hardcoded colors; `templates/sign_in.html:12-35` and `templates/auth_error.html:12-24` account for many of the extras, including non-contract focus green `#4f6f52` and alternate page/surface neutrals.
- **needs_human_review: true —** Actual contrast after font rendering and the perceived 60/30/10 distribution require rendered screenshots or browser inspection.

### Pillar 4: Typography (2/4)

- **PASS evidence.** The shared shell defines contract body, label, heading, and display sizes (`16/14/20/28px`) and limits explicit weight usage to 600 with inherited 400 body text (`static/product.css:16-23,91-105,117-125,158-168`).
- **WARNING — The implementation uses ten distinct font sizes rather than the four declared roles.** Undeclared `11, 12, 13, 15, 30, and 32px` sizes occur in `templates/report_new.html:22,25`, `templates/sign_in.html:21,24,27-28`, `templates/auth_error.html:18-19`, and `static/product.css:177`.
- **WARNING — Letter-spaced uppercase labels violate the sentence-case rule.** The report-library eyebrow uses `text-transform: uppercase` and letter spacing (`templates/report_library.html:9,31`); the auth error eyebrow does the same (`templates/auth_error.html:18,30`). The contract permits all caps only for short semantic status chips.
- **WARNING — Sign-in/error headings exceed the product display role.** `32px` and `30px` headings (`templates/sign_in.html:21`, `templates/auth_error.html:19`) diverge from the 28px display token and fragment the local auth transition from the product type system.

### Pillar 5: Spacing (2/4)

- **PASS evidence.** The authenticated shell caps content at 1200px, uses 64/56px headers, 16px mobile gutters, and generally enforces 44px interactive targets (`static/product.css:46-63,91-105,117-128,152-156,191-205`).
- **WARNING — The declared spacing scale is not consistently used.** Audited styles contain repeated 6px, 10px, 12px, 20px, 28px, and 40px spacing values, including form help/error gaps (`static/product.css:165,168-170`), table/card padding (`static/product.css:175-179`), and library offsets (`templates/report_library.html:8,13`).
- **WARNING — Undeclared radii recur on primary surfaces.** Empty state and report panels use 14px radii (`templates/report_library.html:14`, `templates/report_detail.html:18,29`) even though the contract allows 10px/12px controls and 22px feature/empty cards.
- **WARNING — Refresh dialog dimensions violate the responsive contract.** `templates/report_detail.html:29` sets `max-width: 620px`, omits `max-height: calc(100vh - 32px)` and internal overflow, and therefore contradicts the required 480px maximum.
- **WARNING — Mobile form padding is 20px instead of 16px.** `templates/report_new.html:32-35` applies the 20px padding through 520px, including the contract’s <=480px range.
- **needs_human_review: true —** Reflow at 200% zoom, sticky-header overlap, long organization/opponent names, and dialog viewport containment require browser verification.

### Pillar 6: Experience Design (1/4)

- **BLOCKER — Destructive confirmations do not meet the accessible-dialog contract.** Remove and revoke actions are `<details>` popovers with inline `onclick` close buttons (`templates/organization_members.html:15-28,99,106`). They provide no dialog semantics, labelled description relationship, focus trap, safe initial focus, Escape handling, or focus restoration.
- **BLOCKER — Admin role safety is incomplete in the UI.** Role changes submit immediately with no Admin-to-Member confirmation, and last-admin rows show neither `Last admin` nor “Add another admin…” disabled controls (`templates/organization_members.html:2-28,70-86`). Server rejection is not an adequate substitute for the required pre-action guardrail.
- **BLOCKER — Refresh confirmation/pending behavior is incomplete.** The server emits `<dialog open>` rather than invoking a modal interaction, with no focus-management script; submit does not disable or relabel to `Starting refresh…`, and retry does not relabel to `Accepting retry…` (`templates/report_detail.html:72-85,164-169`).
- **BLOCKER — Report creation has no progressive non-JavaScript path.** The form’s identity field and enabled CTA depend entirely on `static/report_form.js`; the fallback only says JavaScript is required (`templates/report_new.html:54-100`). This contradicts the contract that JavaScript enhance server-rendered, CSRF-protected actions.
- **WARNING — Download submissions have no visible pending or error state.** `static/download_tracking.js:530-563` only disables/re-enables the button; it does not change its label, announce progress, focus an error, or explain a failed preparation/acknowledgement.
- **WARNING — Header/mobile menu focus behavior is incomplete.** The account/mobile menu is native `<details>` without close/focus-return logic (`templates/product_base.html:35-50`, `static/product_shell.js:25-40`), despite the explicit menu-button focus-restoration contract.
- **PASS evidence.** The polling module implements the required 3/5/10-second cadence, hidden-page pause/resume, two-failure notice, terminal stop, 15-minute timeout, and transition-only live-region updates (`static/report_status.js:8-121,135-208`). The report library and member/snapshot views also render separate desktop tables and mobile lists rather than mutating table display semantics.
- **needs_human_review: true —** Keyboard traversal, focus containment/restoration, screen-reader announcements, touch targets in dense member rows, and reduced-motion behavior need browser/assistive-technology verification.

---

## Files Audited

- Design baseline and execution context: `02-UI-SPEC.md`, `02-CONTEXT.md`, all Phase 02 `PLAN.md` and `SUMMARY.md` artifacts, root `CONTEXT.md`, and `AGENTS.md`.
- Templates: `templates/product_base.html`, `templates/sign_in.html`, `templates/auth_error.html`, `templates/invitation_outcome.html`, `templates/organization_select.html`, `templates/organization_members.html`, `templates/report_library.html`, `templates/report_new.html`, and `templates/report_detail.html`.
- Frontend assets: `static/product.css`, `static/product_shell.js`, `static/report_form.js`, `static/report_status.js`, and `static/download_tracking.js`.
- Route/view-model composition: relevant protected-route sections of `app.py`, `roster_scout/auth_routes.py`, `roster_scout/organization_routes.py`, and `roster_scout/report_routes.py`.

Registry audit skipped: `components.json` is absent and the approved UI spec declares no shadcn or third-party registry blocks.
