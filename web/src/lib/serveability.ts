/**
 * Pure fleet-serveability helpers.
 * No React, no fetch — safe to call from anywhere (launcher, section picker,
 * role pickers).  Task 6 wires these into the UI components.
 */

import type { FleetCapability } from "./types";

/**
 * Can the fleet serve a job with the given provider + transport?
 *
 * Fail-open: if the fleet block is absent or the fleet is offline we return
 * ok=true so the UI doesn't block the user before the first status poll lands.
 */
export function serveability(
  fleet: FleetCapability | undefined,
  provider: string,
  transport: "cli" | "api",
): { ok: boolean; reason: string | null } {
  if (!fleet || !fleet.online) return { ok: true, reason: null };
  const ok =
    transport === "api" ? !!fleet.api[provider] : !!fleet.cli[provider];
  if (ok) return { ok: true, reason: null };
  const reason =
    transport === "api" ? "no API creds on fleet" : "CLI not on any worker";
  return { ok: false, reason };
}

/**
 * Is the provider serveable in at least one transport mode?
 * Used to disable a provider option entirely in the launcher.
 */
export function providerServeableAnyMode(
  fleet: FleetCapability | undefined,
  provider: string,
): boolean {
  return !fleet?.online || !!fleet?.cli[provider] || !!fleet?.api[provider];
}

/**
 * Resolves an `inherit` role transport to the job's effective transport.
 * Call this before passing to `serveability` for extract / judge roles.
 */
export function resolveRoleTransport(
  roleTransport: "inherit" | "cli" | "api",
  jobTransport: "cli" | "api",
): "cli" | "api" {
  return roleTransport === "inherit" ? jobTransport : roleTransport;
}
