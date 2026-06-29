import type { BatchSummary } from "./types";

export type BookGroup = BatchSummary[]; // one book's batches (CLI+API)

export interface GradeGroup { grade: string; books: BookGroup[]; }

/** Group book-groups by grade. Numeric grades ascending, "Ungraded" last. */
export function groupBooksByGrade(books: BookGroup[]): GradeGroup[] {
  const byGrade = new Map<string, BookGroup[]>();
  for (const bg of books) {
    const g = bg[0]?.grade ?? null;
    const key = g && String(g).trim() ? String(g) : "Ungraded";
    const arr = byGrade.get(key);
    if (arr) arr.push(bg);
    else byGrade.set(key, [bg]);
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
