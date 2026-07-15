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

const p = batchActionFlags({ rollup: { pending: 5 }, paused_at: "2026-06-29T00:00:00Z" } as any);
assert.strictEqual(p.isPaused, true);
assert.strictEqual(p.canRetry, false);
assert.strictEqual(p.canPause, false);
console.log("OK");
