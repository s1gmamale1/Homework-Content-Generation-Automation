/**
 * Shared fleet liveness helpers.
 *
 * A worker's row is keyed by pc_id = "hostname:pid", so every process restart
 * mints a NEW row — one physical host can have several rows. `hostLiveness`
 * collapses them to one entry per hostname: online if ANY row is fresh, with
 * the most-recent heartbeat as "last seen".
 */

/** Online status-dot colour (matches the Fleet worker cards). */
export const ONLINE_GREEN = "oklch(0.78 0.10 145)";

/** Compact "time since" for an ISO timestamp; "—" for null/unparseable. */
export function ago(iso: string | null): string {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";
  const secs = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (secs < 60) return "just now";
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  return `${Math.floor(secs / 86400)}d ago`;
}

export interface HostLiveness {
  host: string;
  online: boolean;
  lastHeartbeat: string | null;
}

/** One entry per distinct hostname, sorted; online if any restart-row is fresh. */
export function hostLiveness(
  workers: { pc_id: string; last_heartbeat: string | null; online: boolean }[],
): HostLiveness[] {
  const byHost = new Map<string, HostLiveness>();
  for (const w of workers) {
    const host = w.pc_id.split(":")[0];
    const prev = byHost.get(host);
    const online = (prev?.online ?? false) || w.online;
    let lastHeartbeat = prev?.lastHeartbeat ?? null;
    if (
      w.last_heartbeat &&
      (!lastHeartbeat || Date.parse(w.last_heartbeat) > Date.parse(lastHeartbeat))
    ) {
      lastHeartbeat = w.last_heartbeat;
    }
    byHost.set(host, { host, online, lastHeartbeat });
  }
  return Array.from(byHost.values()).sort((a, b) => a.host.localeCompare(b.host));
}
