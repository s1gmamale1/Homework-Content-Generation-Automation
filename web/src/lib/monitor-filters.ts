import type { BatchSummary } from "./types";
import { transportRowStatus } from "./batch-status";

export const LANGUAGES = ["uz", "en", "ru"] as const;
export type Lang = (typeof LANGUAGES)[number];
export const STATUS_FILTERS = ["attention", "all", "running", "failed", "paused", "complete"] as const;
export type StatusFilter = (typeof STATUS_FILTERS)[number];

export interface LangSummary { lang: Lang; lessons: number; done: number; running: number; failed: number; paused: number; }

/** Per-language summary counts; only languages that actually have batches are returned. */
export function summarizeByLanguage(batches: BatchSummary[]): LangSummary[] {
  return LANGUAGES.map((lang) => {
    const bs = batches.filter((b) => b.output_language === lang);
    const sum = (k: string) => bs.reduce((a, b) => a + ((b.rollup as Record<string, number>)[k] ?? 0), 0);
    const lessons = bs.reduce(
      (a, b) => a + Object.values(b.rollup).reduce((x, n) => x + (n ?? 0), 0), 0);
    return { lang, lessons, done: sum("done"),
      running: sum("running") + sum("pending") + sum("cancelling"),
      failed: sum("failed"), paused: bs.filter((b) => b.paused_at != null).length };
  }).filter((s) => s.lessons > 0);
}

/** Does ANY of a book's batches match the status filter? (book = the batches for one book_id) */
export function bookMatchesStatus(book: BatchSummary[], f: StatusFilter): boolean {
  if (f === "all") return true;
  const r = (b: BatchSummary) => b.rollup as Record<string, number>;
  const anyRunning = book.some((b) => (r(b).pending ?? 0) + (r(b).running ?? 0) + (r(b).cancelling ?? 0) > 0);
  const anyFailed = book.some((b) => (r(b).failed ?? 0) > 0);
  const anyPaused = book.some((b) => b.paused_at != null);
  const allComplete = book.every((b) => transportRowStatus(b) === "complete");
  switch (f) {
    case "attention": return anyFailed || anyPaused || anyRunning;
    case "running": return anyRunning;
    case "failed": return anyFailed;
    case "paused": return anyPaused;
    case "complete": return allComplete;
  }
}
