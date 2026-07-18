import assert from "node:assert";
import {
  coverageState,
  groupByGrade,
  progressOf,
  stuckCount,
  summarizeGrade,
  STATE_LABEL,
} from "./subject-coverage";
import type { CoverageEntry } from "./types";

const base: CoverageEntry = {
  grade: "9", subject: "biology", book_id: "b1", book_status: "toc_ready",
  source_language: "uz", original_filename: "bio.pdf", toc_validation: "verified",
  lessons_total: 10, done: 0, running: 0, pending: 0, failed: 0, cancelled: 0,
  batch_id: null, paused: false,
};
const e = (o: Partial<CoverageEntry>): CoverageEntry => ({ ...base, ...o });

// --- state machine ---
assert.strictEqual(coverageState(null), "no_textbook");
assert.strictEqual(coverageState(e({ book_status: "uploading" })), "preparing");
assert.strictEqual(coverageState(e({ book_status: "toc_extracting" })), "preparing");
assert.strictEqual(coverageState(e({ book_status: "toc_review" })), "needs_review");
assert.strictEqual(coverageState(e({ book_status: "failed" })), "textbook_problem");
// ready: textbook prepared, nothing launched
assert.strictEqual(coverageState(e({})), "ready");
// finished: every launchable lesson done
assert.strictEqual(coverageState(e({ done: 10 })), "finished");
// in progress: anything in flight
assert.strictEqual(coverageState(e({ done: 3, running: 1 })), "in_progress");
assert.strictEqual(coverageState(e({ pending: 4 })), "in_progress");
// paused beats in-flight (a paused batch is not progressing)
assert.strictEqual(coverageState(e({ pending: 4, paused: true })), "paused");
// needs attention: failures with nothing in flight
assert.strictEqual(coverageState(e({ done: 6, failed: 4 })), "needs_attention");
// partial: some done, nothing in flight, nothing failed
assert.strictEqual(coverageState(e({ done: 6 })), "partial");
// a book with no classified lessons is "ready", never "finished" (0/0 must not read as complete)
assert.strictEqual(coverageState(e({ lessons_total: 0 })), "ready");

// --- progress ---
assert.deepStrictEqual(progressOf(e({ done: 5 })), { done: 5, total: 10, pct: 50 });
assert.deepStrictEqual(progressOf(e({ lessons_total: 0 })), { done: 0, total: 0, pct: 0 });
// done can exceed the classified total (a lesson launched from a since-reclassified row) -> clamp
assert.strictEqual(progressOf(e({ done: 12 })).pct, 100);

// --- stuck ---
assert.strictEqual(stuckCount(e({ failed: 3, cancelled: 2 })), 5);

// --- every state has a human label ---
for (const s of ["no_textbook","preparing","needs_review","textbook_problem","ready",
                 "in_progress","paused","needs_attention","partial","finished"] as const) {
  assert.ok(STATE_LABEL[s] && STATE_LABEL[s].length > 0, `missing label: ${s}`);
}

// --- grouping ---
const grouped = groupByGrade([
  e({ grade: "9", subject: "biology" }),
  e({ grade: "9", subject: "physics", book_id: "b2" }),
  e({ grade: "5", subject: "biology", book_id: "b3" }),
  e({ grade: null, subject: "musiqa", book_id: "b4" }),
]);
// numeric ascending, ungraded last
assert.deepStrictEqual(grouped.map((g) => g.grade), ["5", "9", null]);
const g9 = grouped.find((g) => g.grade === "9")!;
assert.strictEqual(g9.subjects.length, 2);
// two books for one grade+subject collapse into ONE subject entry holding both
const two = groupByGrade([
  e({ grade: "7", subject: "biology", book_id: "u", source_language: "uz" }),
  e({ grade: "7", subject: "biology", book_id: "r", source_language: "ru" }),
]);
assert.strictEqual(two[0].subjects.length, 1);
assert.strictEqual(two[0].subjects[0].books.length, 2);

// --- grade summary ---
const sum = summarizeGrade({
  grade: "9",
  subjects: [
    { subject: "biology", books: [e({ done: 10 })] },                  // finished
    { subject: "physics", books: [e({ running: 1, book_id: "b2" })] }, // in progress
    { subject: "kimyo-g7-11", books: [e({ failed: 2, done: 1, book_id: "b3" })] }, // attention
    { subject: "musiqa", books: [] },                                   // no textbook
  ],
});
assert.strictEqual(sum.withTextbook, 3);
assert.strictEqual(sum.finished, 1);
assert.strictEqual(sum.inProgress, 1);
assert.strictEqual(sum.attention, 1);
assert.strictEqual(sum.missing, 1);

console.log("subject-coverage: ok");
