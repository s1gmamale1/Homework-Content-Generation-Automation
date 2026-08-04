/**
 * Model-level api-only resolution (Task 4, F2-FE/F4).
 *
 * `/agent/models` serves `api_only_models`: provider -> model ids that 404
 * (ModelNotFoundError) on that provider's CLI even though the provider
 * otherwise supports cli (gemini-3.6-flash / gemini-3.5-flash /
 * gemini-3.5-flash-lite today — see `app.services.agent_models.
 * GEMINI_API_ONLY_MODELS`, enforced backend-side by `validate_transport`).
 * This is distinct from the existing provider-level `api_only` map (a whole
 * provider with no cli lane at all, e.g. clodex).
 *
 * All four FE model/transport pickers (fleet launcher, section generate
 * page, the Extract/Judge role controls, and the Settings defaults page)
 * share this one resolver so a cli pick against an api-only model is forced
 * to api (and the cli option hidden) BEFORE Launch/Save, instead of only
 * failing there.
 */

import type { RoleTransport, Transport } from "./types";

/** provider -> api-only model ids for that provider. Mirrors the shape of
 *  `ProviderModelManifest.api_only_models`. */
export type ApiOnlyModels = Record<string, string[]>;

/** True if (provider, model) is in the api-only set. `model=null` (provider
 *  default) is never api-only — fail open on a missing/not-yet-loaded map so
 *  a slow /agent/models fetch never blocks the UI. */
export function isApiOnlyModel(
  provider: string,
  model: string | null,
  apiOnlyModels: ApiOnlyModels | undefined,
): boolean {
  if (!model || !apiOnlyModels) return false;
  return (apiOnlyModels[provider] ?? []).includes(model);
}

export interface ResolveTransportArgs {
  provider: string;
  model: string | null;
  /** The transport currently selected for this picker — "inherit" only makes
   *  sense for a role (extract/judge); the content pickers use Transport. */
  currentTransport: Transport | RoleTransport;
  /** The parent (job/content) transport a role's "inherit" would resolve
   *  against. Not consulted by this resolver's own branching (an api-only
   *  model forces api regardless of what the parent is) — kept on the
   *  signature so callers can document/test the inherit+cli scenario
   *  explicitly. General inherit resolution for the NON-forced case stays
   *  `resolveRoleTransport`'s job (lib/serveability.ts).
   */
  parentTransport?: Transport;
  apiOnlyModels?: ApiOnlyModels;
}

export interface ResolveTransportResult {
  effective: Transport | RoleTransport;
  forced: boolean;
}

/**
 * Resolve the effective transport for a (provider, model) pick against the
 * api-only model set.
 *
 * - api-only model → `{ effective: "api", forced: true }`, no matter what
 *   `currentTransport`/`parentTransport` were (including a role left on
 *   "inherit" whose parent transport is "cli" — inheriting cli would be a
 *   guaranteed ModelNotFoundError).
 * - otherwise → `{ effective: currentTransport, forced: false }` (pure
 *   passthrough).
 */
export function resolveTransport(args: ResolveTransportArgs): ResolveTransportResult {
  if (isApiOnlyModel(args.provider, args.model, args.apiOnlyModels)) {
    return { effective: "api", forced: true };
  }
  return { effective: args.currentTransport, forced: false };
}

/**
 * Settings-page coupling: `app/api/v1/settings.py` (PUT /launch-defaults,
 * lines ~108-115) 422s when `toc_transport` is cli but the extract
 * provider/model pair requires api (`validate_transport(extract_provider,
 * extract_model, toc_transport)`). An api-only extract model is exactly that
 * case, so the FE must force `toc_transport` to api in lockstep with the
 * extract role's own forced transport — never let the operator reach Save
 * with the incompatible combo.
 */
export function resolveTocTransport(args: {
  extractProvider: string;
  extractModel: string | null;
  currentTocTransport: Transport;
  apiOnlyModels?: ApiOnlyModels;
}): ResolveTransportResult {
  return resolveTransport({
    provider: args.extractProvider,
    model: args.extractModel,
    currentTransport: args.currentTocTransport,
    apiOnlyModels: args.apiOnlyModels,
  });
}
