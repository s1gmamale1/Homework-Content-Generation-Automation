# Monitor Page Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Monitor page scale to many batches: (1) drop the per-batch provider/transport "CLI" badges that clutter the cards, (4) let an operator Pause/Unpause, Cancel-all, and Retry (resume failed) a batch directly from Monitor, and (5) group batch cards by Grade and collapse the worker cards into a compact strip.

**Architecture:** All FE, confined to `web/src/components/fleet/batch-funnel.tsx`, `web/src/components/fleet/worker-cards.tsx`, and a small new pure module `web/src/lib/monitor-grouping.ts` (grade grouping + batch-action flags) that is React-free so it can be `npx tsx`-tested. Batch actions reuse the existing `api.pauseBatch/unpauseBatch/cancelBatch/resumeBatch` helpers and the launcher's confirm/toast conventions.

**Tech Stack:** React + TypeScript + react-query + sonner; Radix. No FE test runner — acceptance is `tsc --noEmit` + `npm run build` + `npx tsx` for the pure helper + in-browser eyeball (reviewer's).

---

## Approach & key decisions

- **#1 "CLI cards" = the per-`TransportRow` provider + `cli`/`API` badge cluster** (`batch-funnel.tsx:41-50`) plus the "CLI + API · N transports · done X CLI · Y API" sub-line (`:124-131`). Decision: **remove the provider+transport badge cluster and that sub-line**; KEEP the status chip (complete/in-progress/partial/failed), the `RollupBar`, the paused badge, and Show-lessons. *Reviewer confirm:* when a book has BOTH a CLI and an API batch, the two rows lose their visible transport label — we keep them visually separated by the existing `divided` top-border and add a tiny non-badge transport caption (`cli`/`api` in muted text, not a pill) ONLY when `batches.length > 1`, so the all-API common case is clean but a dual-transport book is still legible. (If the reviewer wants them fully gone even in the dual case, drop that caption.)
- **#5 grade grouping:** group the existing book-cards by `batch.grade` into sections with a `Grade N` subheader, numeric-ascending, `Ungraded` last. Workers → a single compact strip (one dense wrap of `pc_id · status · age · drain`), per the chosen "compact strip" option.
- **#4 batch controls:** add a `BatchActions` row to each `TransportRow` (actions are per `batch_id`). Show Pause when running work exists, Unpause when `paused_at`, Cancel-all when non-terminal jobs exist, Retry (resume failed) when failed/cancelled exist — same predicates the launcher uses. Cancel is destructive → `window.confirm`. Reuse the api helpers; invalidate `["batches"]` on success.
- **Load-bearing facts (verified @ tip `dd9e1e7`):**
  - Monitor = `MonitorStats` + `WorkerCards` + `BatchFunnel` (`monitor.tsx`), polling `["batches"]`/`["workers"]` every 3500ms.
  - `BatchFunnel` groups batches by `book_id` into `BookCard`s; each renders a `TransportRow` per transport (`batch-funnel.tsx:95-139`).
  - `BatchSummary` carries `grade`, `transport`, `provider`, `rollup` (`done/failed/cancelled/pending/running/cancelling/not_started`), `paused_at`, `paused_reason`, `batch_id`, `lessons_covered` (seen in `/jobs/batches` payload + `types.ts:~380`).
  - API helpers exist: `api.cancelBatch/resumeBatch/pauseBatch/unpauseBatch` (`api.ts:363-396`). The launcher's gating predicates: `hasNonTerminal` (pending+running+cancelling>0), `isBatchPaused` (`paused_at`), `hasFailedCancelled` (failed+cancelled>0) (`launcher.tsx:1006-1058`).
  - `Worker` type: `pc_id`, `status`, `online`, `last_heartbeat` (`worker-cards.tsx`).

---

### Task 1: Pure grouping + action-flag helpers (`monitor-grouping.ts`)

**Files:**
- Create: `web/src/lib/monitor-grouping.ts`
- Test: `web/src/lib/monitor-grouping.test.ts` (run via `npx tsx`, React-free)

- [ ] **Step 1: Write the failing test**

```ts
import assert from "node:assert";
import { groupBooksByGrade, batchActionFlags } from "./monitor-grouping";

// grade grouping: numeric asc, Ungraded last
const books = [
  [{ grade: "8", book_id: "b8" }],
  [{ grade: "7", book_id: "b7" }],
  [{ grade: null, book_id: "bx" }],
  [{ grade: "10", book_id: "b10" }],
] as any;
const groups = groupBooksByGrade(books);
assert.deepStrictEqual(groups.map((g) => g.grade), ["7", "8", "10", "Ungraded"]);
assert.strictEqual(groups[0].books[0][0].book_id, "b7");

// action flags
const f = batchActionFlags({ rollup: { running: 2, failed: 1 }, paused_at: null } as any);
assert.strictEqual(f.canPause, true);
assert.strictEqual(f.canCancel, true);
assert.strictEqual(f.canRetry, true);
assert.strictEqual(f.isPaused, false);

const p = batchActionFlags({ rollup: { not_started: 5 }, paused_at: "2026-06-29T00:00:00Z" } as any);
assert.strictEqual(p.isPaused, true);
assert.strictEqual(p.canRetry, false);
console.log("OK");
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd web && npx tsx src/lib/monitor-grouping.test.ts`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement the pure helpers**

```ts
import type { BatchSummary } from "./types";

export type BookGroup = BatchSummary[]; // one book's batches (CLI+API)

export interface GradeGroup { grade: string; books: BookGroup[]; }

/** Group book-groups by grade. Numeric grades ascending, "Ungraded" last. */
export function groupBooksByGrade(books: BookGroup[]): GradeGroup[] {
  const byGrade = new Map<string, BookGroup[]>();
  for (const bg of books) {
    const g = bg[0]?.grade ?? null;
    const key = g && String(g).trim() ? String(g) : "Ungraded";
    (byGrade.get(key) ?? byGrade.set(key, []).get(key)!).push(bg);
  }
  return [...byGrade.entries()]
    .sort(([a], [b]) => {
      if (a === "Ungraded") return 1;
      if (b === "Ungraded") return -1;
      return Number(a) - Number(b);
    })
    .map(([grade, books]) => ({ grade, books }));
}

export interface BatchActionFlags {
  canPause: boolean; isPaused: boolean; canCancel: boolean; canRetry: boolean;
}
export function batchActionFlags(b: BatchSummary): BatchActionFlags {
  const r = b.rollup;
  const nonTerminal = (r.pending ?? 0) + (r.running ?? 0) + (r.cancelling ?? 0) > 0;
  const isPaused = b.paused_at != null;
  const failedCancelled = (r.failed ?? 0) + (r.cancelled ?? 0) > 0;
  return {
    canPause: nonTerminal && !isPaused,
    isPaused,
    canCancel: nonTerminal,
    canRetry: failedCancelled,
  };
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd web && npx tsx src/lib/monitor-grouping.test.ts` → prints `OK`.

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/monitor-grouping.ts web/src/lib/monitor-grouping.test.ts
git commit -m "feat(monitor): pure grade-grouping + batch-action-flag helpers"
```

---

### Task 2: Remove the CLI/transport badges from batch cards (#1)

**Files:**
- Modify: `web/src/components/fleet/batch-funnel.tsx` (`TransportRow` header `:40-60`; `BookCard` sub-line `:124-131`)

- [ ] **Step 1: Trim the TransportRow header**

Replace the header `<div>` (`:40-60`) so it shows ONLY the status chip on the right (drop the left `provider` + `ApiBadge`/`cli`-pill cluster). Remove the now-unused `ApiBadge` import if nothing else uses it.

- [ ] **Step 2: Replace the dual-transport sub-line**

In `BookCard`, replace the `CLI + API · N transports · done X CLI · Y API` block (`:124-131`) with a minimal muted caption shown only when `batches.length > 1` (e.g. `done {perTransport}` without the "CLI + API · transports" prefix), or pass a tiny `cli`/`api` text label into each `TransportRow` (muted, not a pill) so dual-transport books stay legible per the Approach decision.

- [ ] **Step 3: Verify (FE acceptance)**

Run: `cd web && npx tsc -p tsconfig.app.json --noEmit && npm run build`
Expected: clean (no unused imports).

- [ ] **Step 4: Commit**

```bash
git add web/src/components/fleet/batch-funnel.tsx
git commit -m "feat(monitor): drop per-batch CLI/transport badges (#1 declutter)"
```

---

### Task 3: Batch actions from Monitor — Pause/Unpause/Cancel/Retry (#4)

**Files:**
- Create: `web/src/components/fleet/batch-actions.tsx`
- Modify: `web/src/components/fleet/batch-funnel.tsx` (render `<BatchActions batch={batch} />` in `TransportRow`, below Show-lessons)

- [ ] **Step 1: Build the BatchActions component**

A self-contained component taking `{ batch }`, computing `batchActionFlags(batch)`, and rendering the contextual buttons. Mutations call `api.pauseBatch/unpauseBatch/cancelBatch/resumeBatch`; on success `toast.success` + `qc.invalidateQueries({ queryKey: ["batches"] })`; on error `toast.error`. Cancel uses `window.confirm("Cancel all pending + running lessons in this batch?")` before mutating. Mirror the launcher's button styling (`GHOST_BTN`, rose for cancel, amber for pause). Example shape:

```tsx
export function BatchActions({ batch }: { batch: BatchSummary }) {
  const qc = useQueryClient();
  const flags = batchActionFlags(batch);
  const mk = (fn: (id: string) => Promise<unknown>, ok: string) =>
    useMutation({
      mutationFn: () => fn(batch.batch_id),
      onSuccess: () => { toast.success(ok); qc.invalidateQueries({ queryKey: ["batches"] }); },
      onError: (e) => toast.error(e instanceof Error ? e.message : "Action failed"),
    });
  const pause = mk(api.pauseBatch, "Batch paused");
  const unpause = mk(api.unpauseBatch, "Batch resumed");
  const cancel = mk(api.cancelBatch, "Cancelled");
  const retry = mk(api.resumeBatch, "Resuming failed lessons");
  // render buttons gated by flags.canPause / isPaused / canCancel / canRetry
}
```
(Hooks-in-helper: call `useMutation` four times at top level inside the component, not via `mk` if lint forbids conditional hooks — inline the four `useMutation` calls. Keep it lint-clean.)

- [ ] **Step 2: Wire it into TransportRow**

Render `<BatchActions batch={batch} />` inside `TransportRow` (e.g. in a flex row next to Show-lessons). It self-hides when no action applies (all flags false).

- [ ] **Step 3: Verify (FE acceptance)**

Run: `cd web && npx tsc -p tsconfig.app.json --noEmit && npm run build` → clean.

- [ ] **Step 4: Commit**

```bash
git add web/src/components/fleet/batch-actions.tsx web/src/components/fleet/batch-funnel.tsx
git commit -m "feat(monitor): pause/unpause/cancel/retry a batch from Monitor (#4)"
```

---

### Task 4: Group batch cards by grade (#5a)

**Files:**
- Modify: `web/src/components/fleet/batch-funnel.tsx` (`BatchFunnel` render)

- [ ] **Step 1: Apply grade grouping**

After the existing book-grouping `useMemo`, pass `books` through `groupBooksByGrade` and render a section per `GradeGroup`: a `Grade {grade}` subheader (muted, sticky-ish small heading) followed by the existing `md:grid-cols-2` grid of that grade's `BookCard`s. Keep the top-level `Batches` heading.

- [ ] **Step 2: Verify (FE acceptance)**

Run: `cd web && npx tsc -p tsconfig.app.json --noEmit && npm run build` → clean.

- [ ] **Step 3: Commit**

```bash
git add web/src/components/fleet/batch-funnel.tsx
git commit -m "feat(monitor): group batch cards by grade (#5)"
```

---

### Task 5: Collapse workers to a compact strip (#5b)

**Files:**
- Modify: `web/src/components/fleet/worker-cards.tsx` (render only — keep the drain/undrain mutations)

- [ ] **Step 1: Rewrite the render as a compact strip**

Replace the `grid sm:grid-cols-2 lg:grid-cols-3` of large cards with a single wrapping row of compact worker pills: each pill = status dot + `pc_id` (mono) + muted `status`/`age` + a small Drain/Undrain button. Keep the `Workers — online N / total` header and the empty state. Preserve `drainMut`/`undrainMut` and the draining badge treatment (can be a dot color + title).

- [ ] **Step 2: Verify (FE acceptance)**

Run: `cd web && npx tsc -p tsconfig.app.json --noEmit && npm run build` → clean.

- [ ] **Step 3: Commit**

```bash
git add web/src/components/fleet/worker-cards.tsx
git commit -m "feat(monitor): collapse worker cards into a compact strip (#5)"
```

---

### Task 6: Acceptance — eyeball handoff + helper test green

**Files:** none

- [ ] **Step 1: Re-run the pure helper test**

Run: `cd web && npx tsx src/lib/monitor-grouping.test.ts` → `OK`.

- [ ] **Step 2: Final typecheck + build**

Run: `cd web && npx tsc -p tsconfig.app.json --noEmit && npm run build` → clean.

- [ ] **Step 3: PR note for the reviewer (in-browser gate)**

Reviewer to verify on `/monitor` with live batches: (1) batch cards no longer show the provider/`cli`/`api` badges (status chip + progress remain); (4) Pause/Unpause/Cancel/Retry appear contextually per batch and act on the right batch (toast + the card updates within ~3.5s poll); (5) cards are grouped under `Grade N` headers and the workers render as one compact strip. Confirm Cancel shows a confirm dialog.

---

## Self-review notes
- **Coverage:** #1 = Task 2; #4 = Tasks 1+3; #5 = Tasks 1+4 (grade) + 5 (workers). The pure helpers (Task 1) are the only unit-testable logic; everything else is presentational → tsc+build + eyeball, per the repo's FE acceptance model.
- **Type consistency:** `groupBooksByGrade` takes `BatchSummary[][]` (book-groups) and returns `GradeGroup[]`; `batchActionFlags` reads the same rollup keys the launcher uses. `BatchActions` only ever calls the four existing `api.*Batch` helpers — no new endpoints.
- **Conflict note:** this plan and the launch-config plan touch disjoint files (this: `batch-funnel`/`worker-cards`/`monitor-grouping`; that: `launcher`/`settings`/backend) except `types.ts` is read-only here — no merge conflict expected.
- **Reviewer decision left open:** whether dual-transport (CLI+API) books keep a tiny muted `cli`/`api` caption (Approach #1) or lose the transport label entirely.
