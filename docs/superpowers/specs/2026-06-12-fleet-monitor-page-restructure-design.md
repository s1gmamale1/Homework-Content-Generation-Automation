# Fleet → Monitor page restructure (chunk-3, sub-project 1)

- **Date:** 2026-06-12
- **Branch:** Nggaev-v2
- **Status:** design approved (pending written-spec review)
- **Backlog:** `fleet-ui-5` (amended — batches AND workers move off `/fleet`)
- **Scope note:** this is sub-project 1 of "chunk 3". Sub-project 2 — the
  **sigma-designs visual reshape** — is a SEPARATE later spec→plan→implement
  cycle and is explicitly out of scope here.

## Problem

`/fleet` (`web/src/routes/fleet.tsx`) does three jobs on one scrolling page:
launch controls (`FleetLauncher`), live worker cards (`WorkerCards`), and the
batch funnel (`BatchFunnel`). Monitoring a long run means scrolling past the
launch form every time, and the page grows with batch history. The operator
asked to split monitoring onto its own page and move workers there too.

## Goals

- A dedicated **Monitor** page holding batches + workers.
- `/fleet` becomes launch-focused: launcher + a minimal liveness strip.
- No "did my launch work?" dead-zone after the funnel leaves `/fleet`.

## Non-goals

- **No sigma-designs / visual restyling** (sub-project 2).
- **No backend change** — no new endpoints, no schema change. Reuses the
  existing `listWorkers`/`listBatches`/`listBooks` API.
- **No change to `WorkerCards` / `BatchFunnel` / `batch-lesson-list` /
  `rollup-bar` internals.** They are relocated, not rewritten.

## Design

### 1. Routing & nav (three concrete wiring steps)

1. New page file `web/src/routes/monitor.tsx` exporting `MonitorPage`.
2. **Register the route in `web/src/App.tsx`** (routes are centralized there,
   not file-based): add `import { MonitorPage } from "@/routes/monitor"` and
   `<Route path="/monitor" element={<MonitorPage />} />` inside the protected
   route group (sibling of `/fleet`). Without this the route 404s.
3. **Add the nav item in `web/src/components/layout.tsx`** (NOT `routes/` —
   that path does not exist): a `<NavItem to="/monitor" icon={<Activity .../>}>`
   labelled **Monitor**, placed after Fleet. `Activity` from `lucide-react`
   (matches the existing all-lucide nav: Plus/Library/Gauge/Rocket).

### 2. `/fleet` after the split (`fleet.tsx`)

- Renders the new `OnlineStrip` as a one-line header directly under the
  page title, then `FleetLauncher` (full-width now that the worker grid leaves).
- Drops `<WorkerCards>` and `<BatchFunnel>` from this route.
- Queries kept: `books` + `batches` (the launcher needs both — `batches`
  drives its "already batched" state) + `workers` (strip count only).
- Subtitle reworded to launch-focused (e.g. "Launch a whole subject. Watch
  progress in Monitor.").

### 3. `/monitor` (`monitor.tsx`)

- Renders `WorkerCards` (top) then `BatchFunnel` (below) inside the standard
  `SpaceBackdrop` + `relative z-10` page shell, mirroring `fleet.tsx`'s frame.
- Queries: `batches` + `workers`, both at the existing `refetchInterval: 3500`.
- react-query keys `["workers"]`/`["batches"]` are shared with `/fleet`, so
  navigating between the pages dedupes fetches (no double-polling).
- Page heading "Monitor" + subtitle (e.g. "Workers and live batch progress.").

### 4. `OnlineStrip` component (`web/src/components/fleet/online-strip.tsx`)

- New, small, presentational. Reads the `workers` query result
  (`{ online, total }` — `online` is a direct field on the `listWorkers`
  response, no derivation).
- One line, linking to `/monitor`:
  - loading → muted `checking workers…`
  - `online === 0` → **amber** `⚠ no machines online — launches will queue
    until a worker starts` (the anti-footgun)
  - `online >= 1` → green `🟢 {online} machine[s] online` (pluralize)
- Styling uses existing `lib/ui.ts` conventions + status colors
  (emerald / amber). No new design tokens.

### 5. Post-launch feedback (actionable toast)

- The funnel that used to show a just-launched batch appear now lives on
  `/monitor`, so the launch needs on-page confirmation.
- Enhance the launcher's EXISTING success toast (`launcher.tsx:331`,
  `sonner`): `toast.success(\`Launched ${r.jobs_created} lessons\`, { action:
  { label: "View in Monitor", onClick: () => navigate("/monitor") } })`.
- Add `useNavigate` from `react-router-dom` to the component.
- User stays on `/fleet` (can launch more subjects); the action button is the
  one-click jump to see the batch. (Rejected: auto-navigate — breaks the
  launch-several-subjects-in-a-row flow.)
- This is the ONLY change to `launcher.tsx` — additive, in the success
  handler; no launch logic changes.

## Files touched

| File | Change |
|------|--------|
| `web/src/routes/monitor.tsx` | NEW — `MonitorPage` (WorkerCards + BatchFunnel) |
| `web/src/components/fleet/online-strip.tsx` | NEW — `OnlineStrip` |
| `web/src/App.tsx` | register `/monitor` route + import |
| `web/src/components/layout.tsx` | add Monitor nav item (Activity icon) |
| `web/src/routes/fleet.tsx` | drop WorkerCards+BatchFunnel; add OnlineStrip; reword |
| `web/src/components/fleet/launcher.tsx` | actionable success toast + `useNavigate` |

## Testing / acceptance

- Gate: `npx tsc -p tsconfig.app.json --noEmit` clean + `npm run build` ok
  (the FE has no unit-test runner; tsc + build is the standing gate).
- Manual click-through on the running server: nav shows Monitor; `/fleet`
  shows launcher + strip (and the amber 0-online state when no worker runs);
  `/monitor` shows workers + batch funnel; launching a batch toasts with a
  working "View in Monitor" action; deep-link `/monitor` loads directly.

## Out of scope (future)

- Sub-project 2: sigma-designs visual reshape (own cycle).
- Richer strip (capability-aware `gemini-api ✓`) — deferred; needs the
  backend to surface per-worker capability (option 2 from the brainstorm).
- Historical/searchable batches view (`fleet-ui-3`), richer PC cards
  (`fleet-ui-4`).
