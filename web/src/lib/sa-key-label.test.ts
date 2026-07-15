/**
 * Plain npx-tsx-runnable test for sa-key-label.ts.
 * Run: cd web && npx tsx src/lib/sa-key-label.test.ts
 */
import assert from "node:assert/strict";
import { keyLabel } from "./sa-key-label";

// The owning Gmail is not in the key JSON — it only arrives via the uploaded
// filename (convention: "<gmail>.json"). Surface that as the label.
assert.equal(keyLabel("info@gmail.com.json", "project-abc"), "info@gmail.com");
assert.equal(
  keyLabel("abdscorpion00@gmail.com.json", "project-43a667fb"),
  "abdscorpion00@gmail.com",
);
// Case-insensitive .json strip.
assert.equal(keyLabel("owner@gmail.com.JSON", "project-x"), "owner@gmail.com");

// Fallbacks — a filename that isn't an email is useless as an owner label, so
// fall back to the project id rather than showing "key" or "".
assert.equal(keyLabel("key.json", "project-x"), "project-x", "default upload name -> project id");
assert.equal(keyLabel("", "project-x"), "project-x", "empty filename -> project id");
assert.equal(keyLabel("credentials.json", "project-x"), "project-x", "non-email name -> project id");

// Filename without .json but still an email is accepted.
assert.equal(keyLabel("owner@gmail.com", "project-x"), "owner@gmail.com");

console.log("sa-key-label.test.ts: all assertions passed");
