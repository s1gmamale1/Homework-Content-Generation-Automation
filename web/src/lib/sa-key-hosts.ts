/**
 * Union of the live worker registry with SA-key assignments, for the fleet
 * SA-keys panel host table.
 *
 * A worker's registry row is pruned once it's been dead >10 min (see
 * host-liveness.ts), but a dead host can still own key assignments — those
 * assignments must stay manageable even after the host vanishes from the
 * registry. `assignmentHosts` merges the two sources so no assigned host is
 * ever dropped from the table.
 */
import type { HostLiveness } from "./host-liveness";

export interface SaKeyHostRow extends HostLiveness {
  /** True when this host has no registry row — it's known only via an assignment. */
  assignmentOnly: boolean;
}

/**
 * Merge registry liveness with assignment hostnames into one row per
 * distinct host, sorted by hostname (matches hostLiveness()'s ordering).
 *
 * - Hosts present in `liveness` keep their own online/lastHeartbeat values,
 *   `assignmentOnly: false` — whether or not they also have an assignment.
 * - Hostnames that appear only in `assignments` are added as
 *   `{ online: false, lastHeartbeat: null, assignmentOnly: true }`.
 */
export function assignmentHosts(
  liveness: HostLiveness[],
  assignments: { hostname: string }[],
): SaKeyHostRow[] {
  const byHost = new Map<string, SaKeyHostRow>();
  for (const l of liveness) {
    byHost.set(l.host, { ...l, assignmentOnly: false });
  }
  for (const a of assignments) {
    if (!byHost.has(a.hostname)) {
      byHost.set(a.hostname, {
        host: a.hostname,
        online: false,
        lastHeartbeat: null,
        assignmentOnly: true,
      });
    }
  }
  return Array.from(byHost.values()).sort((a, b) => a.host.localeCompare(b.host));
}

/** Copy shown for an assignment-only (dead/pruned) host row, per registry fetch state. */
export function assignmentOnlyStatus(
  registry: "ready" | "loading" | "error",
): "gone" | "checking" | "registry unavailable" {
  switch (registry) {
    case "ready":
      return "gone";
    case "loading":
      return "checking";
    case "error":
      return "registry unavailable";
  }
}
