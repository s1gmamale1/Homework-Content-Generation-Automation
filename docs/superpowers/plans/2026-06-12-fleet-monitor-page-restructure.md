# Fleet → Monitor Page Restructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the overloaded `/fleet` page — move worker cards + the batch funnel onto a new `/monitor` page, leaving `/fleet` as a launch-focused page with a minimal liveness strip and an actionable post-launch toast.

**Architecture:** Pure frontend restructure. No backend change, no new endpoints. Components (`WorkerCards`, `BatchFunnel`) are relocated, not rewritten. One new presentational component (`OnlineStrip`) that self-owns the `["workers"]` query (react-query dedupes it against `/monitor`'s copy). One new page (`monitor.tsx`) registered in the centralized `App.tsx` router. The launcher's existing `sonner` success toast gains a "View in Monitor" action.

**Tech Stack:** React + TypeScript + Vite, react-router-dom, @tanstack/react-query, sonner, lucide-react, Tailwind.

**Testing note:** the frontend has no unit-test runner; the standing gate is `npx tsc -p tsconfig.app.json --noEmit` (compile) + `npm run build` per the project. Each task below verifies with `tsc --noEmit` (fast); the final task additionally runs `npm run build` and a manual clickthrough. All commands run from `web/`.

**Spec:** `docs/superpowers/specs/2026-06-12-fleet-monitor-page-restructure-design.md`

**Architecture refinement vs spec:** the spec said `/fleet` "keeps the workers query." This plan makes `OnlineStrip` self-own the `["workers"]` query instead, so `fleet.tsx` drops its workers query entirely (cleaner component boundary; identical behavior — react-query dedupes the shared key with `/monitor`).

---

## File Structure

| File | Responsibility |
|------|----------------|
| `web/src/components/fleet/online-strip.tsx` | NEW — one-line worker-liveness indicator, links to `/monitor` |
| `web/src/routes/monitor.tsx` | NEW — Monitor page: `WorkerCards` + `BatchFunnel` |
| `web/src/App.tsx` | MODIFY — register `/monitor` route + import |
| `web/src/components/layout.tsx` | MODIFY — add Monitor nav item (Activity icon) |
| `web/src/routes/fleet.tsx` | MODIFY — drop WorkerCards+BatchFunnel+workers query; add OnlineStrip; reword |
| `web/src/components/fleet/launcher.tsx` | MODIFY — actionable success toast + `useNavigate` |

---

### Task 1: `OnlineStrip` component

**Files:**
- Create: `web/src/components/fleet/online-strip.tsx`

- [ ] **Step 1: Create the component**

```tsx
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { api } from "@/lib/api";

/** One-line worker-liveness indicator for the launch page. Self-owns the
 *  ["workers"] query (react-query dedupes it against the Monitor page). Links
 *  to /monitor for detail. The 0-online amber state is the anti-footgun:
 *  launching with no worker just queues forever. */
export function OnlineStrip() {
  const workers = useQuery({
    queryKey: ["workers"],
    queryFn: api.listWorkers,
    refetchInterval: 3500,
  });
  const data = workers.data;

  if (!data) {
    return <p className="text-xs text-white/45">checking workers…</p>;
  }

  if (data.online === 0) {
    return (
      <Link
        to="/monitor"
        className="inline-flex items-center gap-1.5 text-xs text-amber-300/90 transition-colors hover:text-amber-200"
      >
        <span className="size-1.5 rounded-full bg-amber-400" />
        no machines online — launches will queue until a worker starts
      </Link>
    );
  }

  return (
    <Link
      to="/monitor"
      className="inline-flex items-center gap-1.5 text-xs text-emerald-400/90 transition-colors hover:text-emerald-300"
    >
      <span className="size-1.5 rounded-full bg-emerald-400" />
      {data.online} {data.online === 1 ? "machine" : "machines"} online
    </Link>
  );
}
```

- [ ] **Step 2: Verify it compiles**

Run (from `web/`): `npx tsc -p tsconfig.app.json --noEmit`
Expected: no errors. (`api.listWorkers` returns `{ workers, online, total, stale_after_seconds }` — `online` is a direct field.)

- [ ] **Step 3: Commit**

```bash
git add web/src/components/fleet/online-strip.tsx
git commit -m "feat(fleet): OnlineStrip liveness indicator (chunk-3 T1)"
```

---

### Task 2: Monitor page + route + nav item

**Files:**
- Create: `web/src/routes/monitor.tsx`
- Modify: `web/src/App.tsx`
- Modify: `web/src/components/layout.tsx`

- [ ] **Step 1: Create the Monitor page**

```tsx
import { useQuery } from "@tanstack/react-query";

import { BatchFunnel } from "@/components/fleet/batch-funnel";
import { WorkerCards } from "@/components/fleet/worker-cards";
import { SpaceBackdrop } from "@/components/space-backdrop";
import { api } from "@/lib/api";

export function MonitorPage() {
  const batches = useQuery({ queryKey: ["batches"], queryFn: api.listBatches, refetchInterval: 3500 });
  const workers = useQuery({ queryKey: ["workers"], queryFn: api.listWorkers, refetchInterval: 3500 });

  return (
    <>
      <SpaceBackdrop />
      <div className="relative z-10 space-y-5">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Monitor</h1>
          <p className="mt-1 text-white/55">Workers and live batch progress.</p>
        </div>
        <WorkerCards data={workers.data} />
        <BatchFunnel batches={batches.data} />
      </div>
    </>
  );
}
```

- [ ] **Step 2: Register the route in `App.tsx`**

Add the import alongside the other `@/routes/*` imports (after the `FleetPage` import line):

```tsx
import { MonitorPage } from "@/routes/monitor";
```

Add the route inside the protected `<Route>` group, immediately after the `/fleet` line:

```tsx
            <Route path="/fleet" element={<FleetPage />} />
            <Route path="/monitor" element={<MonitorPage />} />
```

- [ ] **Step 3: Add the nav item in `layout.tsx`**

Add `Activity` to the existing lucide import:

```tsx
import { Activity, Gauge, Library, Moon, Plus, Rocket } from "lucide-react";
```

Add the nav item immediately after the Fleet `NavItem`, inside `<nav aria-label="Primary" …>`:

```tsx
              <NavItem to="/fleet" icon={<Rocket className="size-4" />}>
                Fleet
              </NavItem>
              <NavItem to="/monitor" icon={<Activity className="size-4" />}>
                Monitor
              </NavItem>
```

- [ ] **Step 4: Verify it compiles**

Run (from `web/`): `npx tsc -p tsconfig.app.json --noEmit`
Expected: no errors. (`WorkerCards` takes `data?: { workers; online; total }`; `BatchFunnel` takes `batches?: BatchSummary[]` — both match.)

- [ ] **Step 5: Commit**

```bash
git add web/src/routes/monitor.tsx web/src/App.tsx web/src/components/layout.tsx
git commit -m "feat(fleet): Monitor page + route + nav item (chunk-3 T2)"
```

---

### Task 3: Recompose `/fleet`

**Files:**
- Modify: `web/src/routes/fleet.tsx`

- [ ] **Step 1: Replace the page body**

Replace the entire contents of `web/src/routes/fleet.tsx` with:

```tsx
import { useQuery } from "@tanstack/react-query";

import { FleetLauncher } from "@/components/fleet/launcher";
import { OnlineStrip } from "@/components/fleet/online-strip";
import { SpaceBackdrop } from "@/components/space-backdrop";
import { api } from "@/lib/api";

export function FleetPage() {
  const batches = useQuery({ queryKey: ["batches"], queryFn: api.listBatches, refetchInterval: 3500 });
  const books = useQuery({ queryKey: ["books"], queryFn: api.listBooks, refetchInterval: 3500 });

  return (
    <>
      <SpaceBackdrop />
      <div className="relative z-10 space-y-5">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Fleet</h1>
          <p className="mt-1 text-white/55">Launch a whole subject. Watch progress in Monitor.</p>
        </div>
        <OnlineStrip />
        <FleetLauncher books={books.data} batches={batches.data} />
      </div>
    </>
  );
}
```

(Removed: the `workers` query, the `WorkerCards`/`BatchFunnel` imports + renders, and the two-column grid wrapper — the launcher is now full-width.)

- [ ] **Step 2: Verify it compiles**

Run (from `web/`): `npx tsc -p tsconfig.app.json --noEmit`
Expected: no errors, and no "unused import" complaints (WorkerCards/BatchFunnel imports are gone).

- [ ] **Step 3: Commit**

```bash
git add web/src/routes/fleet.tsx
git commit -m "feat(fleet): /fleet = launcher + OnlineStrip, monitoring moved out (chunk-3 T3)"
```

---

### Task 4: Actionable post-launch toast

**Files:**
- Modify: `web/src/components/fleet/launcher.tsx`

- [ ] **Step 1: Add the `useNavigate` import**

Add to the top imports of `web/src/components/fleet/launcher.tsx`:

```tsx
import { useNavigate } from "react-router-dom";
```

- [ ] **Step 2: Get a `navigate` handle inside `ReadyRow`**

`ReadyRow` is the component that owns the launch mutation (the `onSuccess` at ~line 330). Add this near its other hooks (e.g. right after `const qc = useQueryClient();`):

```tsx
  const navigate = useNavigate();
```

- [ ] **Step 3: Make the success toast actionable**

Replace the existing success-toast line in the `launch` mutation's `onSuccess`:

```tsx
      toast.success(`Launched ${r.jobs_created} lessons`);
```

with:

```tsx
      toast.success(`Launched ${r.jobs_created} lessons`, {
        action: { label: "View in Monitor", onClick: () => navigate("/monitor") },
      });
```

(Leave the rest of `onSuccess` — `setChoosing(false)`, `setSelected(new Set())`, the two `qc.invalidateQueries` — unchanged.)

- [ ] **Step 4: Verify it compiles**

Run (from `web/`): `npx tsc -p tsconfig.app.json --noEmit`
Expected: no errors. (`sonner`'s `toast.success` accepts a second `{ action: { label, onClick } }` arg.)

- [ ] **Step 5: Commit**

```bash
git add web/src/components/fleet/launcher.tsx
git commit -m "feat(fleet): actionable 'View in Monitor' launch toast (chunk-3 T4)"
```

---

### Task 5: Build + manual clickthrough (acceptance)

**Files:** none (verification only)

- [ ] **Step 1: Production build**

Run (from `web/`): `npm run build`
Expected: build succeeds, writes `web/dist/` (chunk-size warning is pre-existing and fine).

- [ ] **Step 2: Manual clickthrough** (server running on :8000, hard-refresh)

- Navbar shows **Monitor** after Fleet; clicking it loads `/monitor` with worker cards + batch funnel.
- `/fleet` shows the title, the OnlineStrip line, then the launcher (full-width); no worker cards / funnel.
- With **no worker running**, the strip shows the amber "no machines online" state; with a worker up, green "N machine(s) online".
- Launching a batch shows a toast with a working **View in Monitor** button that navigates to `/monitor` and the batch is visible there.
- Deep-link `http://localhost:8000/monitor` loads directly (route registered).

- [ ] **Step 3: (no commit — verification task)**

---

## Self-Review

**Spec coverage:**
- New Monitor page (batches + workers) → Task 2. ✓
- `/fleet` = launcher + minimal strip → Task 3 + Task 1. ✓
- Nav item A "Monitor" (Activity) → Task 2 Step 3. ✓
- App.tsx route registration (named step, 404 consequence) → Task 2 Step 2. ✓
- OnlineStrip states (loading / 0-online amber / ≥1 green) → Task 1. ✓
- Post-launch actionable toast, stay-on-/fleet → Task 4. ✓
- Gate tsc + build + clickthrough → each task + Task 5. ✓
- Non-goals (no sigma-designs, no backend, no funnel/worker-card internals) → respected; only `launcher.tsx` is edited and only additively. ✓

**Placeholder scan:** none — every step has exact code/commands.

**Type consistency:** `OnlineStrip` (no props) used in `fleet.tsx`; `MonitorPage`/`FleetPage` named exports match `App.tsx` imports; `WorkerCards data={workers.data}` and `BatchFunnel batches={batches.data}` match their existing signatures; `navigate("/monitor")` path matches the registered route.
