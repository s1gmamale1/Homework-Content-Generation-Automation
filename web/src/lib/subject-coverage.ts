/**
 * Pure status mapping for the /dashboard page (subject-coverage-1). No React —
 * typecheck-clean and unit-testable, following the batch-status.ts convention.
 *
 * The vocabulary here is deliberately plain-language: this page is read by
 * non-technical people, so states are things like "Ready to start" and
 * "Needs attention", never job statuses or transport names.
 */
import type { CoverageEntry } from "./types";

export type CoverageState =
  | "no_textbook"
  | "preparing"
  | "needs_review"
  | "textbook_problem"
  | "ready"
  | "in_progress"
  | "paused"
  | "needs_attention"
  | "partial"
  | "finished";

/** Plain-language labels. These are the words a non-technical viewer reads. */
export const STATE_LABEL: Record<CoverageState, string> = {
  no_textbook: "No textbook yet",
  preparing: "Preparing textbook",
  needs_review: "Textbook needs review",
  textbook_problem: "Textbook problem",
  ready: "Ready to start",
  in_progress: "In progress",
  paused: "Paused",
  needs_attention: "Needs attention",
  partial: "Started, not running",
  finished: "Finished",
};

/** Visual tone per state — consumed by the row component for chip colour. */
export const STATE_TONE: Record<CoverageState, "good" | "busy" | "warn" | "idle"> = {
  no_textbook: "idle",
  preparing: "busy",
  needs_review: "warn",
  textbook_problem: "warn",
  ready: "idle",
  in_progress: "busy",
  paused: "warn",
  needs_attention: "warn",
  partial: "warn",
  finished: "good",
};

/** Sort order: the things needing a human come first, finished work last. */
export const STATE_ORDER: CoverageState[] = [
  "needs_attention",
  "textbook_problem",
  "needs_review",
  "paused",
  "partial",
  "in_progress",
  "preparing",
  "ready",
  "finished",
  "no_textbook",
];

export function coverageState(entry: CoverageEntry | null): CoverageState {
  if (!entry) return "no_textbook";
  switch (entry.book_status) {
    case "uploading":
    case "toc_extracting":
      return "preparing";
    case "toc_review":
      return "needs_review";
    case "failed":
      return "textbook_problem";
  }
  const inFlight = entry.running + entry.pending;
  // A book whose TOC yielded no classified lessons has nothing to finish —
  // report it as ready, never as "finished" off a 0/0 division.
  if (entry.lessons_total > 0 && entry.done >= entry.lessons_total) return "finished";
  if (entry.paused) return "paused";
  if (inFlight > 0) return "in_progress";
  if (entry.failed > 0) return "needs_attention";
  if (entry.done > 0) return "partial";
  return "ready";
}

export function progressOf(entry: CoverageEntry): {
  done: number;
  total: number;
  pct: number;
} {
  const total = entry.lessons_total;
  const done = entry.done;
  if (total <= 0) return { done: 0, total: 0, pct: 0 };
  return { done, total, pct: Math.min(100, Math.round((done / total) * 100)) };
}

/** Lessons a human may need to look at (failed + cancelled). */
export function stuckCount(entry: CoverageEntry): number {
  return entry.failed + entry.cancelled;
}

export interface SubjectCoverage {
  subject: string;
  /** usually 1; >1 when a grade+subject has several textbook editions */
  books: CoverageEntry[];
}

/** Books whose textbook source language matches the selected tab float first;
 *  everything else keeps its original order (stable, input not mutated).
 *
 *  Why: the language tabs scope HOMEWORK output language, not textbook
 *  language — a subject with ru+uz editions rendered its twins in ingest
 *  order, so on the Русский tab the uz edition could sit on top and a click
 *  on the wrong near-identical bar read as a language redirect (traced live
 *  on G9 algebra / G7 algebra, 2026-07-18). */
export function sortBooksForLang(
  books: CoverageEntry[],
  lang: string,
): CoverageEntry[] {
  return [...books].sort(
    (a, b) =>
      Number(b.source_language === lang) - Number(a.source_language === lang),
  );
}

export interface GradeCoverage {
  grade: string | null; // null = ungraded bucket
  subjects: SubjectCoverage[];
}

/** Should this book appear on the given language tab AT ALL?
 *
 *  A tab is a per-language view for a non-technical reader: it lists a book
 *  only when the book has something IN that language — the textbook itself is
 *  that language, OR homework in that language exists/is in flight (the
 *  entry's job counts are already scoped to the tab's output language by the
 *  endpoint, so any nonzero count is that-language activity). Books failing
 *  both drop into the collapsed "Nothing in <lang> yet" bucket instead of
 *  rendering as a wall of alien-language "Ready to start" rows — which is
 *  what made the Русский tab read as mis-routed (user report, 2026-07-18). */
export function visibleForLang(entry: CoverageEntry, lang: string): boolean {
  if (entry.source_language === lang) return true;
  return (
    entry.done + entry.running + entry.pending + entry.failed + entry.cancelled > 0
  );
}

/** Numeric grade ascending; the ungraded bucket always last. */
export function groupByGrade(entries: CoverageEntry[]): GradeCoverage[] {
  const byGrade = new Map<string, Map<string, CoverageEntry[]>>();
  for (const e of entries) {
    const gk = e.grade ?? "__null__";
    const subjects = byGrade.get(gk) ?? new Map<string, CoverageEntry[]>();
    subjects.set(e.subject, [...(subjects.get(e.subject) ?? []), e]);
    byGrade.set(gk, subjects);
  }
  const out: GradeCoverage[] = [...byGrade.entries()].map(([gk, subjects]) => ({
    grade: gk === "__null__" ? null : gk,
    subjects: [...subjects.entries()].map(([subject, books]) => ({ subject, books })),
  }));
  return out.sort((a, b) => {
    if (a.grade === null) return 1;
    if (b.grade === null) return -1;
    const na = Number(a.grade);
    const nb = Number(b.grade);
    if (Number.isNaN(na) || Number.isNaN(nb)) return a.grade.localeCompare(b.grade);
    return na - nb;
  });
}

export interface GradeSummary {
  withTextbook: number;
  finished: number;
  inProgress: number;
  attention: number;
  missing: number;
}

const ATTENTION: CoverageState[] = [
  "needs_attention",
  "textbook_problem",
  "needs_review",
  "paused",
];

/** Headline counts for a grade card. A subject counts once, by its worst book. */
export function summarizeGrade(grade: GradeCoverage): GradeSummary {
  let withTextbook = 0;
  let finished = 0;
  let inProgress = 0;
  let attention = 0;
  let missing = 0;
  for (const s of grade.subjects) {
    if (s.books.length === 0) {
      missing += 1;
      continue;
    }
    withTextbook += 1;
    const states = s.books.map(coverageState);
    const worst = STATE_ORDER.find((st) => states.includes(st)) ?? "ready";
    if (ATTENTION.includes(worst)) attention += 1;
    else if (worst === "finished") finished += 1;
    else if (worst === "in_progress" || worst === "preparing" || worst === "partial")
      inProgress += 1;
  }
  return { withTextbook, finished, inProgress, attention, missing };
}
