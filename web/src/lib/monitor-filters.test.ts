import assert from "node:assert";
import { summarizeByLanguage, bookMatchesStatus } from "./monitor-filters";

const batches = [
  { output_language: "uz", rollup: { done: 3, failed: 1 }, paused_at: null },
  { output_language: "uz", rollup: { running: 2 }, paused_at: null },
  { output_language: "en", rollup: { done: 5 }, paused_at: null },
] as any;
const sums = summarizeByLanguage(batches);
assert.deepStrictEqual(sums.map((s) => s.lang), ["uz", "en"]); // ru has no batches → omitted
const uz = sums.find((s) => s.lang === "uz")!;
assert.strictEqual(uz.done, 3); assert.strictEqual(uz.failed, 1); assert.strictEqual(uz.running, 2);

const failedBook = [{ output_language: "uz", rollup: { failed: 1 }, paused_at: null }] as any;
assert.strictEqual(bookMatchesStatus(failedBook, "attention"), true);
assert.strictEqual(bookMatchesStatus(failedBook, "failed"), true);
assert.strictEqual(bookMatchesStatus(failedBook, "running"), false);
assert.strictEqual(bookMatchesStatus(failedBook, "all"), true);
console.log("OK");
