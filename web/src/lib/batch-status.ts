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
