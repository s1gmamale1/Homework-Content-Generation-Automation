import assert from "node:assert/strict";
import { normalizeProviderTransport } from "./transport-policy";

assert.equal(
  normalizeProviderTransport({
    transport: "cli",
    apiSupported: true,
    apiOnly: true,
    apiFleetOk: true,
  }),
  "api",
  "API-only providers must pin API",
);
assert.equal(
  normalizeProviderTransport({
    transport: "api",
    apiSupported: true,
    apiOnly: true,
    apiFleetOk: false,
  }),
  "api",
  "an unkeyed fleet must not silently downgrade an API-only provider",
);
assert.equal(
  normalizeProviderTransport({
    transport: "api",
    apiSupported: true,
    apiOnly: false,
    apiFleetOk: false,
  }),
  "cli",
  "dual-lane providers may fall back when the fleet lacks API creds",
);
assert.equal(
  normalizeProviderTransport({
    transport: "api",
    apiSupported: false,
    apiOnly: false,
    apiFleetOk: true,
  }),
  "cli",
  "CLI-only providers must reject stale API state",
);

console.log("transport-policy.test.ts: all assertions passed");
