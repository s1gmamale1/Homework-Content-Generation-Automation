import type { GuidedRegenerationDraft, RegenerationModelRole } from "./regeneration-draft";
import type { LaunchDefaults, ProviderModelManifest, RegenerationLaunchContract } from "./types";

export type { RegenerationModelRole } from "./regeneration-draft";

export interface RegenerationModelPair {
  provider: string | null;
  model: string | null;
}

export type EffectiveRegenerationModels = Record<RegenerationModelRole, RegenerationModelPair>;

const ROLE_LABELS: Record<RegenerationModelRole, string> = {
  content: "Content",
  judge: "Judge",
  solver: "Solver",
  extract: "Extract",
};

export function effectiveRegenerationModels(
  draft: GuidedRegenerationDraft,
  defaults: LaunchDefaults | undefined,
): EffectiveRegenerationModels {
  if (draft.modelSelectionMode === "settings") {
    return {
      content: {
        provider: defaults?.content_provider ?? null,
        model: defaults?.content_model ?? null,
      },
      judge: {
        provider: defaults?.judge_provider ?? null,
        model: defaults?.judge_model ?? null,
      },
      solver: {
        provider: defaults?.solver_provider ?? null,
        model: defaults?.solver_model ?? null,
      },
      extract: {
        provider: defaults?.extract_provider ?? null,
        model: defaults?.extract_model ?? null,
      },
    };
  }
  return {
    content: { provider: draft.provider, model: draft.model },
    judge: { provider: draft.judgeProvider, model: draft.judgeModel },
    solver: { provider: draft.solverProvider, model: draft.solverModel },
    extract: { provider: draft.extractProvider, model: draft.extractModel },
  };
}

export function regenerationModelSelectionIssue(
  draft: GuidedRegenerationDraft,
  defaults: LaunchDefaults | undefined,
  manifest: ProviderModelManifest | undefined,
  defaultsLoadFailed = false,
): string | null {
  if (draft.modelSelectionMode === "settings" && defaults === undefined) {
    return defaultsLoadFailed
      ? "Settings defaults could not be loaded. Retry the Settings request below."
      : "Settings defaults are still loading.";
  }
  const selected = effectiveRegenerationModels(draft, defaults);
  for (const role of ["content", "judge", "solver", "extract"] as const) {
    const label = ROLE_LABELS[role];
    const { provider, model } = selected[role];
    const source = draft.modelSelectionMode === "settings" ? " in Settings" : "";
    if (!provider) return `${label} provider is not configured${source}.`;
    if (!model) return `${label} model is not configured${source}.`;
    if (!manifest) return "Model catalog is still loading.";
    if (!manifest.api_supported[provider]) {
      return `${label} provider ${provider} cannot run regeneration through the API.`;
    }
    if (role === "extract" && manifest.api_only[provider]) {
      return `Extract provider ${provider} cannot run extraction safely.`;
    }
    if (!(manifest.providers[provider] ?? []).includes(model)) {
      return `${label} model ${model} is no longer available for ${provider}.`;
    }
  }
  return null;
}

function completePair(pair: RegenerationModelPair): pair is { provider: string; model: string } {
  return pair.provider !== null && pair.model !== null;
}

export function regenerationLaunchContract(
  draft: GuidedRegenerationDraft,
  defaults: LaunchDefaults | undefined,
): RegenerationLaunchContract | null {
  const selected = effectiveRegenerationModels(draft, defaults);
  if (
    !completePair(selected.content) ||
    !completePair(selected.judge) ||
    !completePair(selected.solver) ||
    !completePair(selected.extract)
  ) {
    return null;
  }
  return {
    provider: selected.content.provider,
    model: selected.content.model,
    transport: "api",
    extract_transport: "api",
    extract_provider: selected.extract.provider,
    extract_model: selected.extract.model,
    judge_transport: "api",
    judge_provider: selected.judge.provider,
    judge_model: selected.judge.model,
    solver_transport: "api",
    solver_provider: selected.solver.provider,
    solver_model: selected.solver.model,
    session_limit_strategy: "inherit",
  };
}
