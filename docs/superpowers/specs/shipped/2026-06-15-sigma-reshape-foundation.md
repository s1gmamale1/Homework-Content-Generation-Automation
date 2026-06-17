# Sigma-Designs reshape — Foundation + Fleet (chunk-3 sub-project 2)

- **Date:** 2026-06-15
- **Branch:** Nggaev-v2 (execution on a feature branch off it)
- **Status:** design approved (pending written-spec review)
- **Backlog:** chunk-3 sub-project 2 (the visual reshape; sub-project 1 = the
  `/fleet`→`/monitor` restructure, shipped worklog [0057])
- **Approved mockup (the reference the build matches):**
  `docs/design/2026-06-15-sigma-fleet-mockup.html` (final: rim-glow opacity
  0.28, the bumped type scale, `--mut #b6b5cf`). Direction chosen over the
  older "Quiet Precision" via `docs/design/2026-06-15-direction-comparison.html`.

## Goal

Give the console the Sigma-Designs look (Apple-Intelligence living glass + AI
rim-glow) as **shared foundation primitives**, with the fleet pages
(`/fleet` + `/monitor`) as the first consumer. Intensity = **glow-as-accent**:
glow only on "moments," calm glass on data.

## Locked decisions (from the brainstorm)

1. **Scope = foundation-first (C).** Build the Sigma language as shared
   primitives; fleet is the first consumer; other routes adopt incrementally
   in later sub-projects.
2. **Intensity = glow-as-accent (1).** Rim-glow + cinematic motion reserved for
   moments (live/running surfaces, status, primary actions); dense data
   (lists, tables, worker cards, finished batches) is calm glass.
3. **Blast radius = global re-skin (X).** Restyling the shared `lib/ui.ts`
   constants re-skins all consumers at once (verified: 6 routes — book, job,
   login, preview, section, upload — plus the 4 fleet components). Those 6
   **change appearance in this sub-project** (base material only, no bespoke
   layout) → acceptance MUST visually smoke them.

## Three verified truths the build must honor

- **`SpaceBackdrop` stays per-route.** It is mounted in each of the 10 route
  files, NOT in `layout.tsx` (the `layout.tsx` reference is an explanatory
  comment; hoisting it there creates a containing block that breaks the fixed
  backdrop — known-broken). Evolve the component in place; do NOT hoist. Fix
  its stale docstring (it claims "Library, Usage, Section" — all 10 use it).
- **Reduced-motion is NEW wiring, via ONE mechanism.** `space-backdrop.tsx` is
  currently 100% static (zero animation, no `useReducedMotion`). The aurora
  drift and the `GLOW_RIM` rotation are both new, both **pure CSS keyframes in
  `globals.css`** (GLOW_RIM needs `@property --ang`, so it's CSS regardless;
  the aurora joins it as CSS for consistency). Gate BOTH behind a single
  `@media (prefers-reduced-motion: reduce){ animation:none }` block. Do NOT
  also wire `useReducedMotion` into the backdrop — that would be dead/redundant
  code. (The hook stays where it already is, in `layout.tsx` for the route
  cross-fade.)
- **The shared-constant restyle is global.** "Other routes out of scope" means
  no bespoke Sigma *layout* for them — but their *appearance* shifts via the
  shared tokens. (library/usage self-style and do NOT consume the shared
  constants, so they don't shift from the restyle. `/monitor` DOES shift — it
  doesn't import `@/lib/ui` itself, but it renders `WorkerCards` + `BatchFunnel`
  which do; it's in scope and restyled anyway.)

## Design tokens (final, from the approved mockup)

- **Fonts:** Geist / Geist Mono (already in `index.html`) — unchanged.
- **Palette (AI rim-light sequence):** violet `#7c5cff` · blue `#4d8dff` ·
  cyan `#36d1dc` · plum `#b16cff`. Primary action gradient = violet→blue.
- **Ink:** primary `#f5f4fc`; secondary `--mut #b6b5cf`.
- **Glass surface:** `rgba(255,255,255,.04)` bg, `rgba(255,255,255,.08)` border,
  radius 16px (panels) / 22px (page frame).
- **GLOW_RIM (the moment treatment):** a rotating conic-gradient halo
  (violet→blue→cyan→plum), `blur(7px)`, `opacity .28`, `inset -1px`, ~9s spin.
  Custom CSS (needs `@property --ang` + keyframes) — NOT expressible as a pure
  Tailwind class; lives in `web/src/styles/globals.css` (Tailwind v4; already
  hosts `@keyframes` e.g. `tile-rise`), referenced by class name.
- **Aurora backdrop:** 4 drifting radial blobs (violet/blue/cyan/plum),
  `blur(~40px)`, ~20s ease-in-out alternate drift; per-route via SpaceBackdrop.
- **Type scale:** page title 24px · section head 14px · item name 15.5px ·
  body/nav 14px · input 14.5px · metadata (mono) 12–12.5px · chips 11px ·
  labels 11.5px.
- **Status colors — stay SEMANTIC; do NOT add a Sigma status palette.** Status
  must read by traffic-light convention for scannability (this IS glow-as-
  accent: data stays semantic, glow is brand-only). There are **TWO existing
  semantic implementations — both stay as-is, neither gets rewired:**
  - `components/fleet/status.ts` (`STATUS_COLOR` + `colorFor`/`STATUS_ORDER`):
    done=green, running=blue `#4d8dff`, cancelling=amber, pending/cancelled
    faint/muted, failed=red — used by the `/monitor` surfaces (`rollup-bar`,
    `batch-lesson-list`).
  - `book.tsx`'s local `SectionStatusBadge` (`book.tsx:442`): already semantic
    AND already the glass-tint treatment we want (done `emerald-400/10`,
    running `#5b8dff`, failed `rose-500/10`). It does NOT consume `STATUS_COLOR`
    — leave it; do not "unify" it into `STATUS_COLOR` (nobody asked).
  Either may have hues *gently tuned* to sit on Sigma glass, keeping the hue.
  **No new chip palette in `ui.ts`.** This **supersedes the mockup's chip hues**
  (it showed cyan "complete"/violet "running" — dropped as inconsistent; the
  mockup's glow/glass/layout still stand). The Sigma violet→blue→cyan palette
  is for GLOW / accent / CTA only.

## Glow-as-accent placement (the rule)

- **Glow (moments):** OnlineStrip; primary CTAs (Prepare, Launch); on
  `/monitor` the currently-running batch card + its live pipeline "now" segment.
- **Calm glass (data):** tray/book rows, worker cards, finished/failed batches,
  inputs, selects, secondary buttons.

## Components / files

### Foundation (shared)
- **`web/src/lib/ui.ts`** — tune the 7 existing constants (`CARD`,
  `PRIMARY_BTN`, `GLASS_BTN`, `GHOST_BTN`, `INPUT_GLASS`, `SELECT_TRIGGER`,
  `BACK_PILL`) to Sigma values; names unchanged → no structural breakage.
  **Delta is smaller than "restyle" implies** — `PRIMARY_BTN` is ALREADY
  `from-[#7c5cff] to-[#4d8dff]` and `CARD` already `bg-white/[0.04]` border
  `white/[0.09]`. Real changes: `INPUT_GLASS`/`SELECT_TRIGGER` (`bg-black/25`
  → glass), `GHOST_BTN` ink (`white/55` → the brighter `--mut`), small ink/
  border nudges. Add a calm-glass helper if useful. **Status chips reuse
  `STATUS_COLOR` — do NOT add a chip palette here.** Also fix `ui.ts`'s stale
  docstring (it lists Library/Usage, which don't even consume it).
- **`web/src/components/space-backdrop.tsx`** — static gradients → drifting
  aurora, where the drift is a CSS keyframe (the `aurora-drift` class from
  `globals.css`), NOT a `useReducedMotion`/motion-react animation (see truth
  #2 — one mechanism). Fix the stale docstring; keep per-route mounting (do NOT
  hoist to layout).
- **`web/src/styles/globals.css`** (the global stylesheet, imported by
  `main.tsx`; Tailwind v4 `@theme`/`@layer`/`@keyframes`) — add `@property
  --ang`, the rim-spin + aurora-drift keyframes, and the `GLOW_RIM` class;
  gate all motion behind `@media (prefers-reduced-motion: reduce){
  animation:none }`. Sigma palette/ink can also land as `@theme` tokens.
- **`web/src/components/layout.tsx`** — drop / no-op the decorative Moon button
  (dark-only; no light theme exists).

### First consumer (fleet)
- **`web/src/routes/fleet.tsx`** + **`components/fleet/launcher.tsx`** +
  **`online-strip.tsx`** — glass launcher, glow on OnlineStrip + Prepare/Launch.
- **`web/src/routes/monitor.tsx`** + **`components/fleet/worker-cards.tsx`** +
  **`batch-funnel.tsx`** + **`batch-lesson-list.tsx`** + **`rollup-bar.tsx`** —
  calm glass worker cards + finished batches; GLOW_RIM on the running batch +
  glowing live pipeline.

## Testing / acceptance

- Gate: `npx tsc -p tsconfig.app.json --noEmit` clean + `npm run build` ok.
- **Visual smoke of the 6 globally re-skinned routes** (book, job, login,
  preview, section, upload) — they must still read correctly with the new glass
  token values (this is the global-reskin safety net, not optional).
- **Visual smoke of `/fleet` + `/monitor`** — glow-as-accent placement matches
  the mockup; data surfaces stay calm/legible.
- **Reduced-motion check** — with `prefers-reduced-motion: reduce`, aurora
  drift + rim-glow freeze (no strobe).
- Reference: the approved mockup file.

## Known not-fully-aligned this sub-project (acknowledged, not regressions)

- **The navbar glass is hardcoded** (`layout.tsx:25` `bg-white/[0.065]`, not a
  token), so it won't move with the token restyle. It's close to the glass
  values; if the palette lands as `@theme` tokens, tokenizing the navbar glass
  is a small optional add here, else a one-line follow-up.
- **library/usage carry inline accents** (a third blue `#4d9bff`, `tint=`),
  untouched by the token restyle — they re-align in their own later sub-project.
- So **app-wide visual consistency is NOT fully reached here** — foundation +
  fleet are; the rest trends consistent but isn't pixel-aligned yet. Expected.
- **`@property --ang` support:** Safari ≥16.4, Firefox ≥128, all Chromium.
  Fine for an internal console; the glow simply doesn't rotate on older
  browsers (degrades to a static gradient, no breakage).

## Out of scope (later sub-projects)

- Bespoke Sigma *layout/glow* for the non-fleet routes (they re-skin via shared
  tokens now; per-route polish — Library, Usage, Job, etc. — comes later).
- `fleet-ui-2/3/4` (live SSE dashboard, historical batches, richer PC cards).
- Any backend change (this is FE-only).
