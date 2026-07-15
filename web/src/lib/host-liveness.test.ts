/**
 * Plain npx-tsx-runnable test for host-liveness.ts.
 * Run: cd web && npx tsx src/lib/host-liveness.test.ts
 */
import assert from "node:assert/strict";
import { ago, hostLiveness } from "./host-liveness";

type W = { pc_id: string; last_heartbeat: string | null; online: boolean };

// A host restarts -> multiple pc_id (hostname:pid) rows. The host is ONLINE if
// ANY of its rows is fresh, and "last seen" is the most-recent heartbeat.
const rows: W[] = [
  { pc_id: "Host-20:111", last_heartbeat: "2026-07-14T10:00:00Z", online: false },
  { pc_id: "Host-20:222", last_heartbeat: "2026-07-14T12:00:00Z", online: true },
  { pc_id: "Host-12:333", last_heartbeat: "2026-07-14T09:00:00Z", online: false },
  { pc_id: "Oliver:444", last_heartbeat: null, online: false },
];
const out = hostLiveness(rows);

// sorted by host, deduped to distinct hostnames
assert.deepEqual(out.map((h) => h.host), ["Host-12", "Host-20", "Oliver"]);

const h20 = out.find((h) => h.host === "Host-20")!;
assert.equal(h20.online, true, "online if ANY restart-row is online");
assert.equal(h20.lastHeartbeat, "2026-07-14T12:00:00Z", "most-recent heartbeat wins");

const h12 = out.find((h) => h.host === "Host-12")!;
assert.equal(h12.online, false, "all rows stale -> offline");
assert.equal(h12.lastHeartbeat, "2026-07-14T09:00:00Z");

const oliver = out.find((h) => h.host === "Oliver")!;
assert.equal(oliver.online, false);
assert.equal(oliver.lastHeartbeat, null, "null heartbeat stays null");

assert.deepEqual(hostLiveness([]), [], "empty in -> empty out");

// ago(): null / unparseable -> em dash; near-now -> "just now"
assert.equal(ago(null), "—");
assert.equal(ago("not-a-date"), "—");
assert.equal(ago(new Date().toISOString()), "just now");

console.log("host-liveness.test.ts: all assertions passed");
