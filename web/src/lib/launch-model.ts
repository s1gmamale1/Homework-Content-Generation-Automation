/**
 * Pure content-model resolution for the fleet launcher.
 *
 * The two transports have genuinely different rules:
 *
 * - **api** REQUIRES a concrete model. The backend rejects `transport=api` with
 *   `model=null` (it would diverge between OAuth and API-key auth), so the
 *   launcher always seeds one.
 * - **cli** ALLOWS an explicit model — the provider layer emits `--model X`
 *   whenever the model is truthy — and treats `null` as "provider default"
 *   (no flag; the CLI falls back to its own configured default).
 *
 * The launcher previously forced `model = null` on cli, which made the content
 * model unreachable there: a model chosen in /settings was seeded into launcher
 * state and then immediately wiped, so codex silently ran its own config
 * default instead of the picked one.
 */

/** Radix Select cannot hold "" as an item value — sentinel for the cli
 *  "provider default" (model = null) choice. */
export const PROVIDER_DEFAULT = "__provider_default__";

/**
 * Reconcile the chosen model against the current transport + provider's models.
 * Called whenever transport, provider, or the manifest changes.
 */
export function resolveLaunchModel(
  transport: "cli" | "api",
  model: string | null,
  modelOptions: string[],
): string | null {
  const valid = model != null && modelOptions.includes(model);
  if (transport === "api") {
    // Concrete model required — keep a valid pick, else seed/repair to the first.
    return valid ? model : (modelOptions[0] ?? null);
  }
  // cli — keep a valid pick; a model belonging to another provider is stale and
  // falls back to the provider default rather than silently mis-pinning.
  return valid ? model : null;
}

/** model -> Select value (null becomes the sentinel). */
export function toSelectValue(model: string | null): string {
  return model ?? PROVIDER_DEFAULT;
}

/** Select value -> model (the sentinel becomes null). */
export function fromSelectValue(value: string): string | null {
  return value === PROVIDER_DEFAULT ? null : value;
}
