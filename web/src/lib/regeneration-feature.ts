/**
 * Build-time UI gate for the versioned-homework regeneration area (Task 4).
 *
 * This is the SECOND, cosmetic gate. The authoritative safety boundary is the
 * backend `REGENERATION_ENABLED` flag, which refuses the API regardless of what
 * this bundle renders — turning this flag on does not turn the feature on.
 *
 * Mirrors `viewer.ts` (`VITE_VIEWER === "1"`) but reads `import.meta.env`
 * defensively: `npm test` runs `node --import tsx --test`, where `import.meta`
 * has no `env`, and importing this module there must not throw.
 */

export const REGENERATION_ROUTE_PATH = "/regeneration";
export const REGENERATION_NAV_LABEL = "Regeneration";

/** Pure: on only for the exact string "1". Absent/empty/"0"/"true" are all off. */
export function isRegenerationEnabled(env: Record<string, string>): boolean {
  return env.VITE_REGENERATION_ENABLED === "1";
}

const buildEnv: Record<string, string> =
  (import.meta as ImportMeta & { env?: Record<string, string> }).env ?? {};

/** Derived once at module load; drives route + nav registration. */
export const IS_REGENERATION_ENABLED = isRegenerationEnabled(buildEnv);
