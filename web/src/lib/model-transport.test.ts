import assert from "node:assert/strict";
import { isApiOnlyModel, resolveTocTransport, resolveTransport } from "./model-transport";

const API_ONLY = {
  gemini: ["gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite"],
};

// ─── isApiOnlyModel ─────────────────────────────────────────────────────────
assert.equal(isApiOnlyModel("gemini", "gemini-3.6-flash", API_ONLY), true);
assert.equal(isApiOnlyModel("gemini", "gemini-3.1-pro-preview", API_ONLY), false);
assert.equal(isApiOnlyModel("gemini", null, API_ONLY), false, "null model is never api-only");
assert.equal(
  isApiOnlyModel("claude", "gemini-3.6-flash", API_ONLY),
  false,
  "the api-only set is per-provider — a same-named model on another provider isn't flagged",
);
assert.equal(
  isApiOnlyModel("gemini", "gemini-3.6-flash", undefined),
  false,
  "a missing/not-yet-loaded manifest fails open (never blocks the UI on a slow fetch)",
);

// ─── resolveTransport: an api-only model forces api even off cli ──────────
assert.deepEqual(
  resolveTransport({
    provider: "gemini",
    model: "gemini-3.6-flash",
    currentTransport: "cli",
    apiOnlyModels: API_ONLY,
  }),
  { effective: "api", forced: true },
  "an api-only model must force api even though cli was the current pick",
);

// ─── resolveTransport: a non-api-only model passes through untouched ──────
assert.deepEqual(
  resolveTransport({
    provider: "gemini",
    model: "gemini-3.1-pro-preview",
    currentTransport: "cli",
    apiOnlyModels: API_ONLY,
  }),
  { effective: "cli", forced: false },
  "a non-api-only model must not be forced",
);
assert.deepEqual(
  resolveTransport({
    provider: "claude",
    model: "claude-sonnet-4-6",
    currentTransport: "api",
    apiOnlyModels: API_ONLY,
  }),
  { effective: "api", forced: false },
  "passthrough preserves whatever transport was already in effect",
);

// ─── resolveTransport: role `inherit` + parent cli + api-only role model ──
// must still resolve to forced api — inheriting cli would be a guaranteed
// ModelNotFoundError, so the api-only fact wins regardless of parentTransport.
assert.deepEqual(
  resolveTransport({
    provider: "gemini",
    model: "gemini-3.5-flash-lite",
    currentTransport: "inherit",
    parentTransport: "cli",
    apiOnlyModels: API_ONLY,
  }),
  { effective: "api", forced: true },
  "inherit + cli parent + api-only role model must still force api",
);

// A non-api-only role left on `inherit` is untouched by this resolver — general
// inherit-resolution against the parent transport is resolveRoleTransport's job
// (lib/serveability.ts), not this one's.
assert.deepEqual(
  resolveTransport({
    provider: "gemini",
    model: "gemini-3.1-pro-preview",
    currentTransport: "inherit",
    parentTransport: "cli",
    apiOnlyModels: API_ONLY,
  }),
  { effective: "inherit", forced: false },
);

// ─── resolveTocTransport: an api-only EXTRACT model couples toc_transport ──
// mirrors app/api/v1/settings.py:108-115, which 422s an api-only extract
// model paired with toc_transport=cli — the FE must never let that combo
// reach Save in the first place.
assert.deepEqual(
  resolveTocTransport({
    extractProvider: "gemini",
    extractModel: "gemini-3.6-flash",
    currentTocTransport: "cli",
    apiOnlyModels: API_ONLY,
  }),
  { effective: "api", forced: true },
  "an api-only extract model must force toc_transport=api too",
);
assert.deepEqual(
  resolveTocTransport({
    extractProvider: "gemini",
    extractModel: "gemini-3.1-pro-preview",
    currentTocTransport: "cli",
    apiOnlyModels: API_ONLY,
  }),
  { effective: "cli", forced: false },
  "a non-api-only extract model leaves toc_transport alone",
);

console.log("model-transport.test.ts: all assertions passed");
