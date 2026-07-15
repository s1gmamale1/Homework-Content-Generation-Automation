import assert from "node:assert";
import { transportRowStatus } from "./batch-status";

// in-flight (running present) -> in_progress, regardless of complete flag
assert.strictEqual(
  transportRowStatus({ complete: false, rollup: { pending: 2, running: 3 } } as any),
  "in_progress",
);

// all launched lessons done -> complete
assert.strictEqual(
  transportRowStatus({ complete: true, rollup: { done: 20 } } as any),
  "complete",
);

// done + failed mixed, nothing in flight -> partial
assert.strictEqual(
  transportRowStatus({ complete: false, rollup: { done: 18, failed: 13 } } as any),
  "partial",
);

// all failed, nothing done, nothing in flight -> failed
assert.strictEqual(
  transportRowStatus({ complete: false, rollup: { failed: 5 } } as any),
  "failed",
);

// empty rollup (nothing launched yet) -> in_progress
assert.strictEqual(
  transportRowStatus({ complete: false, rollup: {} } as any),
  "in_progress",
);

// cancelling counts as in-flight too
assert.strictEqual(
  transportRowStatus({ complete: false, rollup: { done: 5, cancelling: 1 } } as any),
  "in_progress",
);

console.log("OK");
