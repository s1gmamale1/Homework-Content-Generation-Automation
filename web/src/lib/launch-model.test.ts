/**
 * Plain npx-tsx-runnable test for launch-model.ts helpers.
 * Run: cd web && npx tsx src/lib/launch-model.test.ts
 */
import assert from "node:assert/strict";
import {
  PROVIDER_DEFAULT,
  resolveLaunchModel,
  toSelectValue,
  fromSelectValue,
} from "./launch-model";

const CODEX = ["gpt-5.5", "gpt-5.6-sol", "gpt-5.4-mini", "gpt-5.3-codex-spark"];
const GEMINI = ["gemini-3.1-pro-preview", "gemini-2.5-flash"];

// ─── cli: an explicit model is ALLOWED (the bug this fixes) ────────────────
// Regression: the launcher used to force setModel(null) on cli, so a model
// picked in /settings (seeded into launcher state) was silently discarded and
// the CLI fell back to its own config default. cli must KEEP a valid pick.
assert.equal(
  resolveLaunchModel("cli", "gpt-5.3-codex-spark", CODEX),
  "gpt-5.3-codex-spark",
  "cli must keep an explicit model that is valid for the provider",
);

// cli: a model belonging to a DIFFERENT provider is stale -> provider default.
assert.equal(
  resolveLaunchModel("cli", "gpt-5.3-codex-spark", GEMINI),
  null,
  "cli must drop a model that isn't in this provider's manifest list",
);

// cli: null stays null — "provider default" is a legitimate cli choice
// (no --model flag; the CLI uses its own configured default).
assert.equal(resolveLaunchModel("cli", null, CODEX), null, "cli null = provider default");

// ─── api: a CONCRETE model is REQUIRED ─────────────────────────────────────
// The backend 400s on transport=api + model=null (it would diverge between
// OAuth and API-key auth), so the launcher must always seed one.
assert.equal(
  resolveLaunchModel("api", null, GEMINI),
  "gemini-3.1-pro-preview",
  "api must seed the first model when none is chosen",
);
assert.equal(
  resolveLaunchModel("api", "gpt-5.5", GEMINI),
  "gemini-3.1-pro-preview",
  "api must repair a stale cross-provider model to the first option",
);
assert.equal(
  resolveLaunchModel("api", "gemini-2.5-flash", GEMINI),
  "gemini-2.5-flash",
  "api must keep a valid model",
);
assert.equal(
  resolveLaunchModel("api", null, []),
  null,
  "api with no options yields null (launch button stays disabled)",
);

// ─── Select value <-> model round-trip ─────────────────────────────────────
// Radix Select cannot hold "" as an item value, so null uses a sentinel.
assert.equal(toSelectValue(null), PROVIDER_DEFAULT);
assert.equal(toSelectValue("gpt-5.5"), "gpt-5.5");
assert.equal(fromSelectValue(PROVIDER_DEFAULT), null);
assert.equal(fromSelectValue("gpt-5.5"), "gpt-5.5");

console.log("launch-model.test.ts: all assertions passed");
