/**
 * Plain npx-tsx-runnable test for sa-key-hosts.ts.
 * Run: cd web && npx tsx src/lib/sa-key-hosts.test.ts
 */
import assert from "node:assert/strict";
import { assignmentHosts, assignmentOnlyStatus } from "./sa-key-hosts";
import type { HostLiveness } from "./host-liveness";

// A host present in both the registry and the assignments is NOT duplicated,
// and keeps its registry liveness (assignmentOnly: false) rather than being
// downgraded to the assignment-only placeholder.
{
  const liveness: HostLiveness[] = [
    { host: "alpha", online: true, lastHeartbeat: "2026-07-17T10:00:00Z" },
  ];
  const assignments = [{ hostname: "alpha" }];
  const rows = assignmentHosts(liveness, assignments);
  assert.equal(rows.length, 1, "host in both lists collapses to one row");
  assert.deepEqual(rows[0], {
    host: "alpha",
    online: true,
    lastHeartbeat: "2026-07-17T10:00:00Z",
    assignmentOnly: false,
  });
}

// A hostname that only appears in the assignments (its worker registry row
// was pruned — dead >10 min) is still surfaced, as an assignment-only,
// offline, never-heartbeat placeholder row — this is the whole point of the
// fix: dead hosts with key assignments must stay manageable.
{
  const liveness: HostLiveness[] = [];
  const assignments = [{ hostname: "ghost-host" }];
  const rows = assignmentHosts(liveness, assignments);
  assert.equal(rows.length, 1, "assignment-only host is still included");
  assert.deepEqual(rows[0], {
    host: "ghost-host",
    online: false,
    lastHeartbeat: null,
    assignmentOnly: true,
  });
}

// Union: registry hosts keep their own liveness values untouched, and
// assignment-only hosts are merged in alongside them (not dropped, not
// clobbering the registry entries).
{
  const liveness: HostLiveness[] = [
    { host: "bravo", online: true, lastHeartbeat: "2026-07-17T09:00:00Z" },
    { host: "delta", online: false, lastHeartbeat: "2026-07-01T00:00:00Z" },
  ];
  const assignments = [{ hostname: "bravo" }, { hostname: "charlie" }, { hostname: "delta" }];
  const rows = assignmentHosts(liveness, assignments);
  assert.equal(rows.length, 3, "3 distinct hosts total");
  const byHost = Object.fromEntries(rows.map((r) => [r.host, r]));
  assert.deepEqual(byHost.bravo, {
    host: "bravo",
    online: true,
    lastHeartbeat: "2026-07-17T09:00:00Z",
    assignmentOnly: false,
  });
  assert.deepEqual(byHost.delta, {
    host: "delta",
    online: false,
    lastHeartbeat: "2026-07-01T00:00:00Z",
    assignmentOnly: false,
  });
  assert.deepEqual(byHost.charlie, {
    host: "charlie",
    online: false,
    lastHeartbeat: null,
    assignmentOnly: true,
  });
}

// Sorted by hostname (localeCompare) across the merged union, matching the
// ordering hostLiveness() already produces — assignment-only additions must
// merge into that ordering, not get appended at the end unsorted.
{
  const liveness: HostLiveness[] = [
    { host: "zulu", online: true, lastHeartbeat: "2026-07-17T09:00:00Z" },
    { host: "alpha", online: true, lastHeartbeat: "2026-07-17T09:00:00Z" },
  ];
  const assignments = [{ hostname: "mike" }, { hostname: "zulu" }];
  const rows = assignmentHosts(liveness, assignments);
  assert.deepEqual(
    rows.map((r) => r.host),
    ["alpha", "mike", "zulu"],
    "merged rows sorted by hostname",
  );
}

// Empty inputs.
{
  assert.deepEqual(assignmentHosts([], []), [], "no liveness, no assignments -> empty");
  const liveness: HostLiveness[] = [
    { host: "solo", online: false, lastHeartbeat: null },
  ];
  assert.deepEqual(assignmentHosts(liveness, []), [
    { host: "solo", online: false, lastHeartbeat: null, assignmentOnly: false },
  ], "no assignments -> registry hosts pass through unchanged");
  assert.deepEqual(
    assignmentHosts([], [{ hostname: "only-assignment" }]),
    [{ host: "only-assignment", online: false, lastHeartbeat: null, assignmentOnly: true }],
    "no liveness -> every assignment host is assignment-only",
  );
}

// assignmentOnlyStatus: maps registry fetch state to the copy shown for an
// assignment-only (dead/pruned) host row.
assert.equal(assignmentOnlyStatus("ready"), "gone");
assert.equal(assignmentOnlyStatus("loading"), "checking");
assert.equal(assignmentOnlyStatus("error"), "registry unavailable");

console.log("sa-key-hosts.test.ts: all assertions passed");
