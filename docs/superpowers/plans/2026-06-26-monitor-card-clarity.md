# Monitor card clarity: honest transport chip + per-transport split note (monitor-card-clarity-1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the monitor's multi-transport batch card from labelling an all-failed transport "complete" (green), and surface the per-transport done counts when a book is split across CLI+API — without ever summing into a misleading combined total.

**Architecture:** FE-only, no backend, no migration. A pure exported `transportRowStatus(batch)` helper turns the binary `complete?` chip in `TransportRow` into a 4-way outcome verdict (complete/in progress/failed/partial). The multi-transport header subtitle gains a per-transport `done X CLI · Y API` note (each transport's own count, never summed).

**Tech Stack:** React 18, TypeScript, Tailwind. No FE test runner → `tsc --noEmit` + `vite build` is the structural gate; the pure helper is also exercised by a standalone tsx test.

---

## Approach & key decisions

- **Item 1 — outcome-aware chip:** the chip is binary today — `batch.complete ? "complete" : "in progress"` (`batch-funnel.tsx:38-49`). Server `complete` = "all jobs terminal" (`app/api/v1/batch.py:71-76`), which is also true when **everything failed** → an all-failed transport reads green "complete", contradicting the red RollupBar. Fix: a pure exported `transportRowStatus(b)` that splits the terminal state by outcome (all-success → complete/green, none-done → failed/red, mixed → partial/amber), and a 4-variant chip driven by it. RollupBar already shows the numbers, so the chip is just the one-word verdict.
- **Item 2(a) — per-transport done note (no sum):** the multi-transport subtitle is just `CLI + API · {n} transports` (`:114-118`). Append each transport's own done count: `done 18 CLI · 13 API`. **Hard rule — never sum done across transports into one total.** Verified in live data (book `62865c70`: `cli_done=1 + api_done=2` but only 2 *distinct* lessons → a summed `3/2` is >100%). The existing `TransportRow` doc-comment already records this ("a lesson can be done on CLI yet not-started on API — summing the two rollups would double-count"); reporting per-transport is accurate, summing is not. Single-transport cards (`batches.length === 1`) are unchanged (the one row already shows its count).
- **Rejected — a combined "X/31 distinct" total in the header (item 2(b)):** needs a backend book-level dedup rollup (distinct lessons done on *any* transport) — that's roadmap **R18** (already filed for the related Fleet-tray rollup). Out of scope here; refresh R18 to note this is item 2(b), the accurate version of the header note.
- **Helper placement (minor deviation from the spec, better serves its goal):** the spec sketched `transportRowStatus` inline in `batch-funnel.tsx`. Instead it lives in a new pure module `web/src/lib/batch-status.ts` (`RowStatus` + `transportRowStatus`, no React) — matching the repo's pure-helper pattern (`serveability.ts`, `launcher-config.ts`) and making it reliably importable by a standalone tsx test (an inline `.tsx` export would drag the whole JSX/React import chain into the test). The presentational chip-style map (`ROW_CHIP`, uses `CSSProperties` + Tailwind classes) stays in the component. Behavior is identical to the spec's helper.
- **Verified facts (tip `39c0985`):** chip block `batch-funnel.tsx:38-49` inside `TransportRow` (receives `batch: BatchSummary`); header block `:114-118` in the parent (`batches`/`head` in scope); `BatchRollup = Partial<Record<JobStatus | "not_started", number>>` (`types.ts:363`) so `b.rollup.done/failed/cancelled` are valid optional numbers; `JobStatus` = pending|running|done|failed|cancelling|cancelled (`types.ts:117-124`); `cn` imported from `@/lib/utils`. Server `complete` flag + `RollupBar` are left untouched (FE-only).

---

### Task 1: outcome-aware chip in `TransportRow`

**Files:**
- Create: `web/src/lib/batch-status.ts` (pure `RowStatus` + `transportRowStatus`)
- Modify: `web/src/components/fleet/batch-funnel.tsx` (import helper + `CSSProperties`; add `ROW_CHIP`; replace the chip block; compute `chip` in `TransportRow`)

- [ ] **Step 1: Create the pure helper module**

Create `web/src/lib/batch-status.ts`:

```typescript
/**
 * Pure batch-outcome verdict (monitor-card-clarity-1). No React — typecheck-clean
 * and unit-testable. The server `complete` flag only means "all jobs terminal",
 * which is ALSO true when everything failed; this splits the terminal state by
 * outcome so the monitor chip can tell the truth (the RollupBar shows the numbers).
 */
import type { BatchSummary } from "./types";

export type RowStatus = "in_progress" | "complete" | "failed" | "partial";

export function transportRowStatus(b: BatchSummary): RowStatus {
  if (!b.complete) return "in_progress"; // pending/running still present
  const done = b.rollup.done ?? 0;
  const failed = b.rollup.failed ?? 0;
  const cancelled = b.rollup.cancelled ?? 0;
  if (failed === 0 && cancelled === 0) return "complete"; // all succeeded → green
  if (done === 0) return "failed"; // nothing succeeded → red
  return "partial"; // mixed (e.g. 18 done / 13 failed) → amber
}
```

- [ ] **Step 2: Run tsc to confirm the new module typechecks**

Run: `cd web && npx tsc -p tsconfig.app.json --noEmit`
Expected: clean (types resolve from `./types`).

- [ ] **Step 3: Wire it into `batch-funnel.tsx` — imports + chip-style map**

(a) Change the React import:
```typescript
import { useMemo, useState } from "react";
```
to:
```typescript
import { type CSSProperties, useMemo, useState } from "react";
```

(b) Add the helper import alongside the other `@/lib` imports (near the `import type { BatchSummary } from "@/lib/types";` line):
```typescript
import { type RowStatus, transportRowStatus } from "@/lib/batch-status";
```

(c) Add the presentational chip map at module scope, immediately BEFORE the `function TransportRow({` declaration / its doc-comment:
```typescript
const ROW_CHIP: Record<RowStatus, { label: string; className: string; style?: CSSProperties }> = {
  complete: {
    label: "complete",
    className: "text-white/90",
    style: { background: "oklch(0.78 0.10 145 / 0.25)" },
  },
  in_progress: { label: "in progress", className: "bg-white/[0.07] text-white/55" },
  failed: { label: "failed", className: "bg-red-500/20 text-red-200" },
  partial: { label: "partial", className: "bg-amber-500/20 text-amber-200" },
};
```

- [ ] **Step 4: Compute `chip` inside `TransportRow`**

Inside `TransportRow`, just after the existing `const Chevron = expanded ? ChevronDown : ChevronRight;` line, add:

```typescript
  const chip = ROW_CHIP[transportRowStatus(batch)];
```

- [ ] **Step 5: Replace the binary chip block**

Replace this block (currently `batch-funnel.tsx:38-49`):

```tsx
        {batch.complete ? (
          <span
            className="shrink-0 rounded-full px-2 py-0.5 text-[0.7rem] font-medium text-white/90"
            style={{ background: "oklch(0.78 0.10 145 / 0.25)" }}
          >
            complete
          </span>
        ) : (
          <span className="shrink-0 rounded-full bg-white/[0.07] px-2 py-0.5 text-[0.7rem] text-white/55">
            in progress
          </span>
        )}
```

with:

```tsx
        <span
          className={cn(
            "shrink-0 rounded-full px-2 py-0.5 text-[0.7rem] font-medium",
            chip.className,
          )}
          style={chip.style}
        >
          {chip.label}
        </span>
```

- [ ] **Step 6: Typecheck + build (the structural gate)**

Run: `cd web && npx tsc -p tsconfig.app.json --noEmit && npm run build`
Expected: tsc clean (exit 0); build writes `web/dist/` (only the pre-existing >500 kB chunk advisory is acceptable).

- [ ] **Step 7: Commit**

```bash
cd /Users/macmini5/Documents/HCGA-monitor-clarity
git add web/src/lib/batch-status.ts web/src/components/fleet/batch-funnel.tsx
git commit -m "monitor-card-clarity: outcome-aware transport chip (complete/failed/partial/in progress)"
```

---

### Task 2: per-transport done split in the multi-transport header

**Files:**
- Modify: `web/src/components/fleet/batch-funnel.tsx` (the `batches.length > 1` subtitle)

- [ ] **Step 1: Replace the subtitle**

Replace this block (currently `batch-funnel.tsx:114-118`):

```tsx
        {batches.length > 1 && (
          <div className="mt-0.5 text-[0.72rem] text-white/40">
            CLI + API · {batches.length} transports
          </div>
        )}
```

with (append each transport's own done count — NEVER a summed total):

```tsx
        {batches.length > 1 && (
          <div className="mt-0.5 text-[0.72rem] text-white/40">
            CLI + API · {batches.length} transports · done{" "}
            {batches
              .map((b) => `${b.rollup.done ?? 0} ${b.transport.toUpperCase()}`)
              .join(" · ")}
          </div>
        )}
```

This renders e.g. `CLI + API · 2 transports · done 18 CLI · 13 API`. No summed `X/31` total — lessons can be done on both transports (real overlap), so a sum over-counts (>100%).

- [ ] **Step 2: Typecheck + build**

Run: `cd web && npx tsc -p tsconfig.app.json --noEmit && npm run build`
Expected: clean.

- [ ] **Step 3: Commit**

```bash
cd /Users/macmini5/Documents/HCGA-monitor-clarity
git add web/src/components/fleet/batch-funnel.tsx
git commit -m "monitor-card-clarity: per-transport done split in multi-transport header (no sum)"
```

---

### Task 3: helper behavioral test + R18 refresh + Finish

**Files:**
- Modify: `docs/memory/ROADMAP.md` (refresh R18), `docs/memory/MASTER_MEMORY.md`, `docs/memory/INDEX.md`
- Move: this plan → `docs/superpowers/plans/shipped/`

- [ ] **Step 1: Behavioral test of the pure verdict helper (controller runs)**

The whole correctness of Item 1 lives in `transportRowStatus`. Prove it deterministically with a standalone tsx script (no browser needed):

Run from `web/`:
```bash
cat > /tmp/trs_test.mjs <<'EOF'
import { transportRowStatus } from "/Users/macmini5/Documents/HCGA-monitor-clarity/web/src/lib/batch-status.ts";
const mk = (complete, rollup) => ({ complete, rollup });
let pass = 0, fail = 0;
const eq = (got, want, m) => (got === want ? (pass++, console.log("  ok", m, "→", got)) : (fail++, console.log("  FAIL", m, "got", got, "want", want)));
eq(transportRowStatus(mk(false, { pending: 5 })), "in_progress", "pending present");
eq(transportRowStatus(mk(false, { running: 1, done: 3 })), "in_progress", "running present");
eq(transportRowStatus(mk(true, { failed: 22, done: 0 })), "failed", "English-CLI 22 failed/0 done");
eq(transportRowStatus(mk(true, { done: 18, failed: 13 })), "partial", "Algebra 18 done/13 failed");
eq(transportRowStatus(mk(true, { done: 31 })), "complete", "all done");
eq(transportRowStatus(mk(true, { done: 5, cancelled: 2 })), "partial", "done + cancelled → partial");
eq(transportRowStatus(mk(true, { cancelled: 4, done: 0 })), "failed", "all cancelled, none done → failed");
console.log(`\n${fail === 0 ? "ALL PASS" : "FAILURES"}: ${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);
EOF
npx tsx /tmp/trs_test.mjs
```
Expected: ALL PASS (7/7). (`batch-status.ts` is a pure module — no React/JSX imports — so tsx loads it cleanly.)

- [ ] **Step 2: In-browser eyeball (controller, on the live server)**

Confirm on the live monitor: English-CLI (22 failed/0 done) → **failed** (red); Algebra-CLI (18 done/13 failed) → **partial** (amber); a clean all-done batch → **complete** (green); a running batch → **in progress**; a multi-transport header shows `done 18 CLI · 13 API` with no >100%. (Behavioral, no money — read-only monitor view.)

- [ ] **Step 3: Refresh R18 in `docs/memory/ROADMAP.md`**

Append a bullet to the R18 section (`ROADMAP.md:64-68`) noting item 2(b): the monitor card's **accurate combined** book total (`X/31` = distinct lessons done on *any* transport) needs the same book-level per-lesson dedup rollup as the Fleet-tray split — `monitor-card-clarity-1` shipped only the honest per-transport note (2a); the deduped combined total (2b) is deferred here because a naive sum over-counts on real cross-transport overlap.

- [ ] **Step 4: Rebase-check before finishing**

```bash
cd /Users/macmini5/Documents/HCGA-monitor-clarity
git fetch origin
git log HEAD..origin/Nggaev-v2 --oneline   # if non-empty: rebase onto origin/Nggaev-v2, re-run tsc+build, then continue
```

- [ ] **Step 5: Finish — worklog + INDEX + plan move**

- Worklog entry in `docs/memory/MASTER_MEMORY.md` (verify next-free — `0092` is current highest → likely `0093`) + a row in `docs/memory/INDEX.md`.
- De-stale reference docs only if they describe the monitor chip/header (grep `docs/CODE_MAP.md` for `batch-funnel` — the capability-gate line mentions it; add a brief note if warranted).
- `git mv docs/superpowers/plans/2026-06-26-monitor-card-clarity.md docs/superpowers/plans/shipped/`
- Commit with staged files only (never `git add -A`).

- [ ] **Step 6: PR to the gatekeeper (no self-merge)**

Push `monitor-card-clarity`, open a PR targeting `Nggaev-v2`, route it back to the gate.
