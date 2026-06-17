# Sigma-Designs reshape — Foundation + Fleet — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the console the Sigma-Designs look (Apple-Intelligence living glass + AI rim-glow) as shared foundation primitives, with `/fleet` + `/monitor` as the first consumer, on a glow-as-accent intensity.

**Architecture:** Restyle the shared `lib/ui.ts` Tailwind-class constants (global re-skin of the 6 consuming routes + 4 fleet components) + add Sigma custom CSS (rotating rim halo, drifting aurora) to `globals.css` behind one reduced-motion gate + evolve `SpaceBackdrop` to a drifting aurora + apply glow to fleet "moments" (online strip, primary CTAs, the in-progress batch card and its live pipeline segment). FE-only; no backend, no new routes.

**Tech Stack:** React 19 + TypeScript, Tailwind v4 (`@theme`/`@layer`/`@keyframes` in `globals.css`), `motion/react`, Vite. No FE test runner exists (`web/` builds via `tsc -b && vite build`).

---

## How this plan verifies (read first)

This is a **pure-visual reshape**. `web/` has **no unit-test runner** (no vitest/jest/Playwright, zero `*.test.*` files — verified). Red-green TDD does not apply to CSS/Tailwind class strings. Per the spec's own Testing/acceptance section, every task is gated by:

1. **Typecheck:** `cd web && npx tsc -p tsconfig.app.json --noEmit` → clean.
2. **Build (CSS + TS):** `cd web && npm run build` → succeeds (this is `tsc -b && vite build`; it compiles `globals.css`, so it is the real syntax gate for CSS tasks).
3. **A specific visual observation** named in the task, checked against the approved mockup `docs/design/2026-06-15-sigma-fleet-mockup.html`, via `cd web && npm run dev` (Vite proxies `/api` to :8000; start the backend with `uv run uvicorn main:app --port 8000` if live data is needed, or just eyeball static surfaces).

The CLAUDE.md "real CLI smoke" acceptance gate does **not** apply here — nothing in this plan touches generation (`app/services/*`). This is FE-only.

## Two design principles that recur (apply everywhere)

- **Glow is brand; color is semantic.** Rim-glow / aurora / pulse / bloom use the Sigma palette (violet `#7c5cff` · blue `#4d8dff` · cyan `#36d1dc` · plum `#b16cff`). **Status colors stay semantic** via the existing `components/fleet/status.ts` `STATUS_COLOR` (done=green, running=blue, failed=red, …) and `book.tsx`'s `SectionStatusBadge` — both stay as-is, neither gets a Sigma chip palette (locked decision, spec lines 75–86). The mockup colored several *status* elements with brand hues (cyan "online" strip line 59, cyan pipeline-now line 94, cyan/violet chips lines 78–79) — **those hues are superseded**; we keep the semantic color and add the *glow effect* instead. This is the consistent extension of the locked status decision.
- **Do NOT retoken `@theme --color-accent`.** It is currently amber and consumed by 15 files including out-of-scope game/content components (boss-fight, flashcards, memory-sprint, reading, games) and the nav chrome. The approved mockup uses **no global accent color** (nav-active is neutral white, `rgba(255,255,255,.09)`, mockup line 41). Sigma brand colors land as **new** `@theme` tokens used only by glow/CTA CSS. The amber nav-active accent and the hardcoded navbar glass are **knowingly left not-fully-aligned** this sub-project (same treatment the spec gives the navbar glass).

---

## File structure (what changes, and why)

| File | Change | Responsibility |
|---|---|---|
| `web/src/styles/globals.css` | Add Sigma brand `@theme` tokens, `@property --ang`, `rim-spin` + `aurora-drift` + `seg-pulse` keyframes, `.glow-rim`/`.aurora-bloom`/`.seg-now` classes; extend the single reduced-motion block | Owns all custom Sigma CSS that Tailwind can't express |
| `web/src/lib/ui.ts` | Tune the 7 existing constants to mockup glass values; fix stale docstring; **no chip palette** | Shared class-string source of truth (global re-skin) |
| `web/src/components/space-backdrop.tsx` | Static gradients → drifting aurora bloom layer (CSS class); fix docstring; keep per-route mount | The per-route living-glass backdrop |
| `web/src/components/layout.tsx` | Drop the dead decorative Moon button + its unused import | App chrome |
| `web/src/components/fleet/online-strip.tsx` | Online dot gets a glow bloom (semantic emerald, brand glow) | Fleet "moment" |
| `web/src/components/fleet/batch-funnel.tsx` | Apply `.glow-rim` to the in-progress (`!batch.complete`) `BatchCard` | Monitor "moment" |
| `web/src/components/fleet/rollup-bar.tsx` | Running segment gets the `.seg-now` glow (semantic blue + pulse) | Monitor live-pipeline "moment" |

**Not edited (re-skin via `lib/ui.ts` tokens only):** routes `book`, `job`, `login`, `preview`, `section`, `upload`. They shift appearance through the shared constants; acceptance smokes them.

---

## Task 1: Sigma CSS foundation in `globals.css`

**Files:**
- Modify: `web/src/styles/globals.css` (add tokens at the end of `@theme` block ~line 43; add new keyframes/classes after the existing `tile-rise` block ~line 138; extend the reduced-motion `@media` block at lines 134–138)

- [ ] **Step 1: Add Sigma brand tokens to the `@theme` block**

In `web/src/styles/globals.css`, inside the existing `@theme { … }` block, immediately before the closing `}` (currently line 44, after the `--ease-soft` line), add:

```css
  /* Sigma brand — glow / CTA / aurora ONLY. NOT the global --color-accent
     (which stays amber; see plan principle 2). The AI rim-light sequence. */
  --color-sigma-violet: #7c5cff;
  --color-sigma-blue: #4d8dff;
  --color-sigma-cyan: #36d1dc;
  --color-sigma-plum: #b16cff;
  /* Sigma ink (brighter secondary than --color-ink-muted; used by ui.ts) */
  --color-sigma-ink: #f5f4fc;
  --color-sigma-mut: #b6b5cf;
```

- [ ] **Step 2: Add `@property --ang`, the Sigma keyframes, and the Sigma classes**

In `web/src/styles/globals.css`, after the existing `tile-rise` block and BEFORE the existing reduced-motion `@media` block (i.e. insert between line 133 `}` and line 134 `@media …`), add. These values are transcribed verbatim from the approved mockup (`@property` line 11, `.rim` lines 51–55, aurora `.screen::before`/`drift` lines 26–33, pipeline-now `.seglet.now`/`gl` lines 94–95):

```css
/* --- Sigma custom CSS (the rim halo + aurora can't be Tailwind classes) --- */

@property --ang {
  syntax: "<angle>";
  inherits: false;
  initial-value: 0deg;
}

@keyframes rim-spin {
  to {
    --ang: 360deg;
  }
}

@keyframes aurora-drift {
  to {
    transform: translate(2%, -2%) scale(1.06);
  }
}

@keyframes seg-pulse {
  0%,
  100% {
    opacity: 0.55;
  }
  50% {
    opacity: 1;
  }
}

/* GLOW_RIM — the "moment" treatment: a rotating conic halo behind a card.
   Apply to a glass card; the ::before blooms a blurred rim just outside the
   edge and softly tints the glass (z-index:-1 keeps it under the content, so
   text stays legible). Matches the mockup's .rim (blur 7px, opacity .28). */
.glow-rim {
  position: relative;
}
.glow-rim::before {
  content: "";
  position: absolute;
  inset: -1px;
  z-index: -1;
  border-radius: inherit;
  background: conic-gradient(
    from var(--ang),
    var(--color-sigma-violet),
    var(--color-sigma-blue),
    var(--color-sigma-cyan),
    var(--color-sigma-plum),
    var(--color-sigma-violet)
  );
  filter: blur(7px);
  opacity: 0.28;
  animation: rim-spin 9s linear infinite;
  pointer-events: none;
}

/* Aurora bloom — the evolved SpaceBackdrop's drifting layer. Fixed,
   full-viewport, four soft radial blobs that slowly drift. */
.aurora-bloom {
  position: fixed;
  inset: -15%;
  z-index: 0;
  pointer-events: none;
  background:
    radial-gradient(36% 36% at 18% 10%, rgba(124, 92, 255, 0.34), transparent 60%),
    radial-gradient(32% 32% at 88% 16%, rgba(77, 141, 255, 0.3), transparent 60%),
    radial-gradient(40% 40% at 72% 92%, rgba(54, 209, 220, 0.24), transparent 60%),
    radial-gradient(34% 34% at 10% 88%, rgba(177, 108, 255, 0.26), transparent 60%);
  filter: blur(40px);
  animation: aurora-drift 20s ease-in-out infinite alternate;
}

/* Live-pipeline "now" segment — semantic color stays (set inline by the
   caller); this only adds the brand glow + pulse. */
.seg-now {
  box-shadow: 0 0 8px currentColor;
  animation: seg-pulse 1.6s ease-in-out infinite;
}
```

- [ ] **Step 3: Extend the single reduced-motion gate to cover the new motion**

In `web/src/styles/globals.css`, replace the existing reduced-motion block (currently lines 134–138):

```css
@media (prefers-reduced-motion: reduce) {
  .animate-tile-rise {
    animation: none;
  }
}
```

with the consolidated block (one gate for ALL motion, per spec truth #2):

```css
@media (prefers-reduced-motion: reduce) {
  .animate-tile-rise,
  .glow-rim::before,
  .aurora-bloom,
  .seg-now {
    animation: none !important;
  }
}
```

- [ ] **Step 4: Verify the CSS compiles**

Run: `cd web && npm run build`
Expected: build succeeds (no Tailwind/CSS parse error). Nothing renders differently yet — the new classes are unused until Tasks 3 and 6.

- [ ] **Step 5: Commit**

```bash
git add web/src/styles/globals.css
git commit -m "feat(sigma): add rim-glow + aurora CSS foundation and reduced-motion gate

Sigma brand @theme tokens, @property --ang, rim-spin/aurora-drift/seg-pulse
keyframes, .glow-rim/.aurora-bloom/.seg-now classes; single prefers-reduced-
motion block gates all of them. Classes wired up in later tasks.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Restyle `lib/ui.ts` to Sigma glass values

**Files:**
- Modify: `web/src/lib/ui.ts` (whole file)

Mockup references: `.glass` line 49 (`rgba(255,255,255,.04)` bg, `rgba(255,255,255,.08)` border, radius 16px), `.cta` line 67 (`135deg` gradient, shadow `0 8px 22px -8px rgba(124,92,255,.7)`), `.inp` line 66 (`rgba(255,255,255,.05)` bg, `.08` border), `.ghost` line 68 (ink `#c7c6d8`).

- [ ] **Step 1: Replace the file contents**

Replace the entire contents of `web/src/lib/ui.ts` with (deltas: docstring fixed to the real consumers; `CARD` border `.09→.08`; `PRIMARY_BTN` gradient `to-r→to-br` + mockup shadow; `GLASS_BTN`/`BACK_PILL` border `.12→.1`; `GHOST_BTN` ink `white/55→white/70`; `INPUT_GLASS`/`SELECT_TRIGGER` `bg-black/25→bg-white/[0.05]`, border `.12→.1`):

```ts
/**
 * Shared Tailwind class strings for the Sigma "living glass" dark theme.
 * Consumed by routes Book, Job, Login, Preview, Section, Upload and the fleet
 * components (launcher, batch-funnel, batch-lesson-list, worker-cards). Single
 * source of truth so glass cards, buttons, and form fields stay identical.
 * Compose with `cn(CARD, "extra-classes")`. (Status colors are NOT here — they
 * stay semantic in components/fleet/status.ts and book.tsx's SectionStatusBadge.)
 */

export const CARD =
  "rounded-2xl border border-white/[0.08] bg-white/[0.04] p-5 shadow-[0_18px_50px_-36px_rgba(0,0,0,0.95)] backdrop-blur-xl";

export const PRIMARY_BTN =
  "inline-flex items-center justify-center gap-2 rounded-xl bg-gradient-to-br from-[#7c5cff] to-[#4d8dff] px-4 py-2.5 text-sm font-medium text-white shadow-[0_8px_22px_-8px_rgba(124,92,255,0.7)] transition-transform hover:-translate-y-0.5 disabled:opacity-50 disabled:hover:translate-y-0";

export const GLASS_BTN =
  "inline-flex items-center justify-center gap-2 rounded-xl border border-white/[0.1] bg-white/[0.05] px-4 py-2.5 text-sm font-medium text-white/80 transition-colors hover:bg-white/[0.1] hover:text-white disabled:opacity-50";

export const GHOST_BTN =
  "inline-flex items-center justify-center gap-2 rounded-xl px-3 py-2.5 text-sm font-medium text-white/70 transition-colors hover:text-white disabled:opacity-50";

export const INPUT_GLASS =
  "border-white/[0.1] bg-white/[0.05] text-white placeholder:text-white/35";

export const SELECT_TRIGGER =
  "h-10 border-white/[0.1] bg-white/[0.05] text-white data-[placeholder]:text-white/40";

/** Clear bordered back-nav pill with a hover arrow nudge (apply to a Link). */
export const BACK_PILL =
  "group inline-flex items-center gap-2 rounded-xl border border-white/[0.1] bg-white/[0.05] px-3 py-2 text-sm font-medium text-white/75 transition-colors hover:bg-white/[0.1] hover:text-white";
```

- [ ] **Step 2: Typecheck**

Run: `cd web && npx tsc -p tsconfig.app.json --noEmit`
Expected: clean (only class-string values changed; no API change).

- [ ] **Step 3: Build**

Run: `cd web && npm run build`
Expected: succeeds.

- [ ] **Step 4: Visual smoke**

Run `cd web && npm run dev`, open `/upload` and `/login` (no backend needed for static form chrome). Expected: inputs/selects now read as light glass (`bg-white/[0.05]`) instead of the old `bg-black/25`; primary buttons keep the violet→blue gradient with a softer violet shadow; everything still legible.

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/ui.ts
git commit -m "feat(sigma): tune shared ui.ts constants to Sigma glass values

Glass inputs/selects (bg-white/.05), 135deg CTA + mockup shadow, brighter
ghost ink, .08 borders; fix stale docstring to the real consumers. No status
chip palette added (status stays semantic). Global re-skin of the 6 consuming
routes + 4 fleet components.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Drifting aurora `SpaceBackdrop`

**Files:**
- Modify: `web/src/components/space-backdrop.tsx` (whole file)

- [ ] **Step 1: Replace the file contents**

Replace the entire contents of `web/src/components/space-backdrop.tsx` with (keeps the deep-navy base + starfield static; inserts the animated `.aurora-bloom` layer between them; fixes the stale docstring; motion is the CSS class from Task 1, NOT `useReducedMotion` — one mechanism per spec truth #2):

```tsx
/**
 * Sigma living-glass backdrop, mounted per-route as the first child of a
 * `relative` page wrapper (content sits in a sibling with `relative z-10`).
 * Used by all 10 routes. Renders three fixed, non-interactive layers: a deep
 * navy base, a slowly drifting aurora bloom (the `.aurora-bloom` CSS class —
 * its drift is frozen under prefers-reduced-motion via globals.css), and a
 * faint starfield. NOTE: keep this mounted per-route — hoisting it into
 * layout.tsx creates a containing block that breaks the fixed positioning.
 */
export function SpaceBackdrop() {
  return (
    <>
      <div
        aria-hidden
        className="pointer-events-none fixed inset-0 z-0"
        style={{
          background:
            "linear-gradient(160deg, oklch(0.165 0.022 275), oklch(0.12 0.016 268) 55%, oklch(0.15 0.02 250))",
        }}
      />
      <div aria-hidden className="aurora-bloom" />
      <div
        aria-hidden
        className="pointer-events-none fixed inset-0 z-0 opacity-70"
        style={{
          backgroundImage:
            "radial-gradient(1.5px 1.5px at 30px 40px, rgba(255,255,255,0.35), transparent), radial-gradient(1px 1px at 130px 120px, rgba(255,255,255,0.22), transparent), radial-gradient(1px 1px at 220px 70px, rgba(255,255,255,0.18), transparent), radial-gradient(1.5px 1.5px at 300px 200px, rgba(255,255,255,0.28), transparent)",
          backgroundSize: "260px 260px, 200px 200px, 340px 340px, 420px 420px",
        }}
      />
    </>
  );
}
```

- [ ] **Step 2: Typecheck + build**

Run: `cd web && npx tsc -p tsconfig.app.json --noEmit && npm run build`
Expected: clean + succeeds.

- [ ] **Step 3: Visual smoke (motion + reduced-motion)**

Run `cd web && npm run dev`, open any route (e.g. `/login`). Expected: soft violet/blue/cyan/plum blooms drift slowly behind the content. Then, in browser devtools, emulate `prefers-reduced-motion: reduce` (Chrome: Rendering tab → "Emulate CSS prefers-reduced-motion") and reload — the aurora should be **frozen** (static, no drift), not strobing.

- [ ] **Step 4: Commit**

```bash
git add web/src/components/space-backdrop.tsx
git commit -m "feat(sigma): drifting aurora SpaceBackdrop (CSS-gated reduced-motion)

Adds the .aurora-bloom drifting layer between the navy base and the starfield;
drift is a pure-CSS animation frozen by the globals.css reduced-motion block
(no useReducedMotion in the backdrop — one mechanism). Fix stale docstring;
keep per-route mounting.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Drop the dead Moon button (`layout.tsx`)

**Files:**
- Modify: `web/src/components/layout.tsx:1` (import), `web/src/components/layout.tsx:64-72` (button)

Rationale: the Moon button has **no `onClick`** (toggles nothing) and `.theme-light` in `globals.css:108` is applied **nowhere** (verified) — it's dead chrome.

- [ ] **Step 1: Remove the button**

In `web/src/components/layout.tsx`, delete the entire `<motion.button … >…</motion.button>` block (lines 64–72) — the one with `aria-label="Dark theme"` containing `<Moon className="size-4" />`. The surrounding `<div className="flex items-center gap-3">` and its `API`/`v0` spans stay.

- [ ] **Step 2: Remove the now-unused imports**

In `web/src/components/layout.tsx:1`, change:

```tsx
import { Activity, Gauge, Library, Moon, Plus, Rocket } from "lucide-react";
```

to (drop `Moon`):

```tsx
import { Activity, Gauge, Library, Plus, Rocket } from "lucide-react";
```

Then check whether `motion`, `AnimatePresence`, `useReducedMotion`, `tapScale` are still used elsewhere in the file (they are — the route cross-fade at lines 85–95 uses `AnimatePresence`/`motion`/`useReducedMotion`; `tapScale` was used ONLY by the deleted button). Remove `tapScale` from the import at line 5 if it is now unused:

```tsx
import { tapScale } from "@/lib/motion";
```
→ delete this line if `tapScale` no longer appears in the file.

- [ ] **Step 3: Typecheck (this is the real gate — catches unused imports)**

Run: `cd web && npx tsc -p tsconfig.app.json --noEmit`
Expected: clean. If it errors with "`tapScale` is declared but never read" or "`Moon` …", remove the offending import and re-run until clean.

- [ ] **Step 4: Build + visual smoke**

Run: `cd web && npm run build` (expected: succeeds). Then `npm run dev` — the navbar top-right shows `API` and `v0` but **no moon icon**; layout is otherwise unchanged.

- [ ] **Step 5: Commit**

```bash
git add web/src/components/layout.tsx
git commit -m "feat(sigma): remove dead decorative Moon button from navbar

The button had no onClick and .theme-light is applied nowhere — dead chrome.
Drop it plus its now-unused Moon/tapScale imports.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Glow on the OnlineStrip "moment"

**Files:**
- Modify: `web/src/components/fleet/online-strip.tsx:34-42` (the online state)

Principle: keep the **semantic emerald** "online" color; add the brand **glow** (a box-shadow bloom). The 0-online **amber warning** state gets NO glow (a warning is not a celebratory moment). Primary CTAs (Prepare/Launch) are already handled by `PRIMARY_BTN` from Task 2 — no change needed here.

- [ ] **Step 1: Add the glow to the online dot**

In `web/src/components/fleet/online-strip.tsx`, in the `data.online > 0` return (lines 34–42), change the dot span:

```tsx
      <span className="size-1.5 rounded-full bg-emerald-400" />
```

to (add the glow bloom via box-shadow; emerald hue unchanged):

```tsx
      <span className="size-1.5 rounded-full bg-emerald-400 shadow-[0_0_10px_2px_rgba(52,211,153,0.7)]" />
```

Leave the `data.online === 0` amber state (lines 22–32) untouched.

- [ ] **Step 2: Typecheck + build**

Run: `cd web && npx tsc -p tsconfig.app.json --noEmit && npm run build`
Expected: clean + succeeds.

- [ ] **Step 3: Visual smoke**

Run `cd web && npm run dev` with the backend up and at least one worker online (or temporarily check the online branch). Open `/fleet`. Expected: the "N machines online" dot has a soft green halo (a calm "moment"), not a flat dot; the amber no-machines state is unchanged.

- [ ] **Step 4: Commit**

```bash
git add web/src/components/fleet/online-strip.tsx
git commit -m "feat(sigma): glow bloom on the OnlineStrip online indicator

Semantic emerald stays; add a soft box-shadow halo (brand glow, not brand hue)
to mark worker-liveness as a calm 'moment'. Amber no-machines warning stays flat.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: GLOW_RIM on the in-progress batch + glowing live pipeline (`/monitor`)

**Files:**
- Modify: `web/src/components/fleet/batch-funnel.tsx:16` (the `BatchCard` wrapper)
- Modify: `web/src/components/fleet/rollup-bar.tsx:24-30` (the running segment)

Principle: `.glow-rim` (brand) frames the **in-progress** batch card (`!batch.complete`); the running rollup segment keeps its **semantic blue** (`colorFor("running")`) and gains the `.seg-now` glow/pulse. Finished/complete batches stay calm glass.

- [ ] **Step 1: Apply `.glow-rim` to the in-progress BatchCard**

In `web/src/components/fleet/batch-funnel.tsx`, change the `BatchCard` root (line 16):

```tsx
    <div className={cn(CARD, "space-y-3")}>
```

to (glow only while not complete):

```tsx
    <div className={cn(CARD, "space-y-3", !batch.complete && "glow-rim")}>
```

- [ ] **Step 2: Add the `.seg-now` glow to the running rollup segment**

In `web/src/components/fleet/rollup-bar.tsx`, change the segment map (lines 24–30):

```tsx
          segments.map((status) => (
            <div
              key={status}
              style={{ flex: rollup[status] ?? 0, background: colorFor(status) }}
            />
          ))
```

to (add the `.seg-now` class on the running segment only; `currentColor` for the glow needs the segment's color set as text color too, so add `color`):

```tsx
          segments.map((status) => (
            <div
              key={status}
              className={cn(status === "running" && "seg-now")}
              style={{
                flex: rollup[status] ?? 0,
                background: colorFor(status),
                color: colorFor(status),
              }}
            />
          ))
```

Then add the `cn` import at the top of `web/src/components/fleet/rollup-bar.tsx` (it currently imports only from `./status`):

```tsx
import { cn } from "@/lib/utils";
```

- [ ] **Step 3: Typecheck + build**

Run: `cd web && npx tsc -p tsconfig.app.json --noEmit && npm run build`
Expected: clean + succeeds.

- [ ] **Step 4: Visual smoke (the core "moment")**

Run `cd web && npm run dev` with the backend up and a batch that has a running job (launch one from `/fleet`). Open `/monitor`. Expected:
- The in-progress batch card shows a soft rotating violet→blue→cyan→plum rim bloom; the text stays fully legible (the halo is behind content).
- Complete batches are calm glass (no rim).
- In the rollup bar, the blue **running** segment softly pulses with a glow; other segments are flat. **Running is still blue, not cyan** (semantic preserved).
- Toggle `prefers-reduced-motion: reduce` → rim stops rotating and the running segment stops pulsing (both frozen, no strobe).

If the rim reads as a muddy filled glow rather than an edge bloom, confirm the card's `backdrop-blur-xl` stacking context is acceptable against the mockup; the mockup's `.rim` (opacity .28, blur 7px) is the target — tune opacity down slightly only if visibly muddy, and note it.

- [ ] **Step 5: Commit**

```bash
git add web/src/components/fleet/batch-funnel.tsx web/src/components/fleet/rollup-bar.tsx
git commit -m "feat(sigma): rim-glow the in-progress batch + glow the live pipeline

GLOW_RIM frames the !complete BatchCard; the running rollup segment keeps its
semantic blue and gains the .seg-now pulse/glow. Complete batches stay calm
glass. Both animations freeze under reduced-motion (globals.css gate).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Acceptance — global re-skin smoke + final gates

No code changes. This is the spec's mandatory acceptance gate (global-reskin safety net + fleet placement + reduced-motion).

- [ ] **Step 1: Typecheck + build clean**

Run: `cd web && npx tsc -p tsconfig.app.json --noEmit && npm run build`
Expected: clean + succeeds.

- [ ] **Step 2: Visual smoke the 6 globally re-skinned routes**

Run `cd web && npm run dev` (backend up for data-bearing routes). Open each and confirm it still reads correctly with the new glass tokens (inputs/selects are light glass, buttons keep the gradient, cards legible, no contrast regressions):
- `/upload`
- `/login`
- `/library` → click a book → `/book/:id`
- a `/job/:id` (from a launched lesson)
- a `/section/...` and `/preview/...` surface
Expected: all legible; no element looks broken or unreadable from the `bg-black/25 → bg-white/[0.05]` input change or the `.08` borders.

- [ ] **Step 3: Visual smoke `/fleet` + `/monitor` against the mockup**

Open `docs/design/2026-06-15-sigma-fleet-mockup.html` side-by-side. Confirm glow-as-accent placement matches: glow on OnlineStrip + primary CTAs + the in-progress batch card + running pipeline segment; calm glass on tray rows, worker cards, finished batches, inputs/selects. Status colors are semantic (running=blue, done=green), NOT the mockup's cyan/violet chip hues (intentional supersession).

- [ ] **Step 4: Reduced-motion full check**

With `prefers-reduced-motion: reduce` emulated, reload `/monitor` and `/fleet`: aurora drift, rim rotation, and the running-segment pulse are all **frozen** (no strobe). The route cross-fade (layout.tsx) is already reduced-motion-aware and should also be instant.

- [ ] **Step 5: Worklog + finish**

After the suite is green and smokes pass, follow `superpowers:finishing-a-development-branch` (push to `Nggaev-v2` per project convention). Then add a worklog entry to `docs/memory/MASTER_MEMORY.md`, a row to `docs/memory/INDEX.md`, and close the chunk-3 sub-project 2 item in `docs/memory/ROADMAP.md`.

---

## Self-review

**Spec coverage:**
- Locked decision 1 (foundation-first) → Tasks 1–2 build shared primitives; Tasks 5–6 are the fleet consumer. ✓
- Locked decision 2 (glow-as-accent) → glow on moments (Tasks 5, 6); calm glass on data (Task 2 tokens, untouched worker cards / tray rows / finished batches). ✓
- Locked decision 3 (global re-skin of 6 routes + 4 fleet components) → Task 2 + Task 7 Step 2 smoke. ✓
- Truth 1 (SpaceBackdrop per-route, don't hoist, fix docstring) → Task 3 (docstring fixed, per-route mounting kept, comment warns against hoisting). ✓
- Truth 2 (reduced-motion = new wiring, ONE mechanism, CSS-gated) → Task 1 Step 3 single `@media` block; Task 3 uses the CSS class not `useReducedMotion`. ✓
- Truth 3 (`/monitor` shifts via children; library/usage don't) → reflected in file table; Task 7 smokes monitor's children. ✓
- Design tokens (palette, ink, glass, GLOW_RIM, aurora, type scale, status) → Task 1 (tokens + rim + aurora) + Task 2 (glass/ink in ui.ts). **Type scale** (mockup line 45 `.h1` 24px etc.): the existing route headings already use `text-2xl` (24px) page titles (verified in fleet.tsx/monitor.tsx line 17); ui.ts carries no font-size, so no token change is required — noted, not a gap. Status = **semantic, no chip palette** per Task 1/2 + principle. ✓
- Glow-as-accent placement (OnlineStrip, CTAs, running batch + pipeline now / calm data) → Tasks 5, 6. ✓
- Components/files list → all covered; `launcher.tsx` CTAs are covered transitively via `PRIMARY_BTN` (Task 2), no direct edit needed (noted in Task 5). `online-strip.tsx`/`rollup-bar.tsx` self-style (don't import ui.ts) so they're edited directly (Tasks 5, 6). ✓
- Out-of-scope (no bespoke non-fleet layout, no fleet-ui-2/3/4, no backend) → respected; the amber `--color-accent` and navbar glass are explicitly left per principle 2. ✓

**Placeholder scan:** No TBD/TODO/"handle edge cases"/"similar to". Every code step shows the literal replacement. ✓

**Type consistency:** Class names used in components match those defined in Task 1 (`glow-rim`, `aurora-bloom`, `seg-now`). `cn` import added in rollup-bar (Task 6 Step 2) before use. ui.ts export names unchanged (CARD/PRIMARY_BTN/…) so all 10 importers keep compiling. ✓

**One judgment call flagged for review:** extending "semantic color + glow, not brand hue" from the locked chip decision to the OnlineStrip (emerald, not cyan) and the pipeline-now segment (blue, not cyan). This is the consistent application of the locked status decision, but it is a visible departure from the mockup's cyan treatment of those two elements — same category as the already-approved chip-hue supersession. Veto at plan review if you want those two to use the mockup's cyan instead.
