/**
 * Pure batch-outcome verdict (monitor-card-clarity-1). No React — typecheck-clean
 * and unit-testable. Derived wholly from `rollup` (launched lessons only, since
 * BE-03): the server `complete` flag now means "every launched lesson is done"
 * (`sum(rollup) > 0 && rollup.done === sum`), so a halted batch (some
 * failed/cancelled, nothing in flight) reports `complete=false` — it is NOT
 * "in progress" and must not be reported as such. This splits the terminal
 * state by outcome so the monitor chip can tell the truth (the RollupBar shows
 * the numbers).
 */
import type { BatchSummary } from "./types";

export type RowStatus = "in_progress" | "complete" | "failed" | "partial";

export function transportRowStatus(b: BatchSummary): RowStatus {
  const pending = b.rollup.pending ?? 0;
  const running = b.rollup.running ?? 0;
  const cancelling = b.rollup.cancelling ?? 0;
  const done = b.rollup.done ?? 0;
  const failed = b.rollup.failed ?? 0;
  const cancelled = b.rollup.cancelled ?? 0;
  const sum = pending + running + cancelling + done + failed + cancelled;

  if (pending + running + cancelling > 0 || sum === 0) return "in_progress";
  // All-terminal: split by outcome.
  if (done === sum) return "complete"; // all succeeded → green
  if (done === 0) return "failed"; // nothing succeeded → red
  return "partial"; // mixed (e.g. 18 done / 13 failed) → amber
}
