import assert from "node:assert";
import { sourceCheckWarnings, totalWarningCount } from "./phase-warnings";

const phases = [
  { phase_name: "extract", status: "done", validation_warnings: ["extract_coverage: 2 item(s) …", "lint:coverage_thin: …"] },
  { phase_name: "flashcards", status: "done", validation_warnings: ["lint:mixed_script: …"] },
  { phase_name: "reflection", status: "done", validation_warnings: null },
  { phase_name: "boss-arena", status: "running", validation_warnings: ["ignored — not done"] },
] as any;

// source-side checks live ONLY on the extract row; the pager hides that row.
assert.deepStrictEqual(sourceCheckWarnings(phases), [
  "extract_coverage: 2 item(s) …",
  "lint:coverage_thin: …",
]);

// the job header count must include them — that is the whole point.
assert.strictEqual(totalWarningCount(phases), 3);

// empty / missing cases
assert.deepStrictEqual(sourceCheckWarnings([] as any), []);
assert.strictEqual(totalWarningCount([] as any), 0);
assert.deepStrictEqual(
  sourceCheckWarnings([{ phase_name: "extract", status: "done", validation_warnings: null }] as any),
  [],
);

console.log("OK");
