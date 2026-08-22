import type { GuidedRegenerationDraft } from "@/lib/regeneration-draft";
import {
  type RegenerationModelRole,
  effectiveRegenerationModels,
  regenerationModelSelectionIssue,
} from "@/lib/regeneration-model-selection";
import type { LaunchDefaults, ProviderModelManifest } from "@/lib/types";
import { FRAME_OFF, FRAME_ON } from "@/lib/ui";
import { cn } from "@/lib/utils";

const ROLE_LABELS: Record<RegenerationModelRole, string> = {
  content: "Content",
  judge: "Judge",
  solver: "Solver",
  extract: "Extract",
};

export function RegenerationModelSelection({
  draft,
  defaults,
  defaultsLoadFailed,
  manifest,
  onChange,
}: {
  draft: GuidedRegenerationDraft;
  defaults: LaunchDefaults | undefined;
  defaultsLoadFailed?: boolean;
  manifest: ProviderModelManifest | undefined;
  onChange: (draft: GuidedRegenerationDraft) => void;
}) {
  const patch = (next: Partial<GuidedRegenerationDraft>) => onChange({ ...draft, ...next });
  const patchRole = (role: RegenerationModelRole, next: Partial<GuidedRegenerationDraft>) =>
    patch({
      ...next,
      modelOverrideTouchedRoles: [...new Set([...draft.modelOverrideTouchedRoles, role])],
    });
  const selected = effectiveRegenerationModels(draft, defaults);
  const issue = regenerationModelSelectionIssue(draft, defaults, manifest, defaultsLoadFailed);

  return (
    <div className="space-y-3 rounded-2xl border border-white/[0.08] bg-white/[0.025] p-4">
      <div>
        <h3 className="text-sm font-semibold text-white">Model selection</h3>
        <p className="mt-1 text-xs leading-5 text-white/45">
          Choose the models for generation and its three verification roles. Regeneration always
          uses the API transport.
        </p>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <button
          type="button"
          onClick={() => patch({ modelSelectionMode: "settings" })}
          className={cn(
            "rounded-2xl p-4 text-left",
            draft.modelSelectionMode === "settings" ? FRAME_ON : FRAME_OFF,
          )}
        >
          <span className="block text-sm font-semibold text-white">Use Settings defaults</span>
          <span className="mt-1 block text-xs leading-5 text-white/50">
            Use the four model defaults currently configured in Settings.
          </span>
        </button>
        <button
          type="button"
          onClick={() => patch({ modelSelectionMode: "override" })}
          className={cn(
            "rounded-2xl p-4 text-left",
            draft.modelSelectionMode === "override" ? FRAME_ON : FRAME_OFF,
          )}
        >
          <span className="block text-sm font-semibold text-white">Override models</span>
          <span className="mt-1 block text-xs leading-5 text-white/50">
            Choose a provider and model separately for every role.
          </span>
        </button>
      </div>

      {draft.modelSelectionMode === "settings" ? (
        <div className="space-y-2">
          <div className="grid gap-2 sm:grid-cols-2">
            {(Object.keys(ROLE_LABELS) as RegenerationModelRole[]).map((role) => (
              <div
                key={role}
                className="rounded-xl border border-white/[0.07] bg-black/10 px-3 py-2"
              >
                <span className="text-[0.68rem] font-medium uppercase tracking-wide text-white/35">
                  {ROLE_LABELS[role]}
                </span>
                <p className="mt-1 truncate text-xs text-white/75">
                  {selected[role].provider && selected[role].model
                    ? `${selected[role].provider}/${selected[role].model}`
                    : "Not configured"}
                </p>
              </div>
            ))}
          </div>
          <a href="/settings" className="inline-block text-xs text-sky-200 underline">
            Edit defaults in Settings
          </a>
        </div>
      ) : (
        <div className="grid gap-3 lg:grid-cols-2">
          <RolePicker
            modelRole="content"
            provider={draft.provider}
            model={draft.model}
            manifest={manifest}
            onProvider={(provider) => patchRole("content", { provider, model: null })}
            onModel={(model) => patchRole("content", { model })}
          />
          <RolePicker
            modelRole="judge"
            provider={draft.judgeProvider}
            model={draft.judgeModel}
            manifest={manifest}
            onProvider={(judgeProvider) =>
              patchRole("judge", { judgeProvider: judgeProvider || null, judgeModel: null })
            }
            onModel={(judgeModel) => patchRole("judge", { judgeModel })}
          />
          <RolePicker
            modelRole="solver"
            provider={draft.solverProvider}
            model={draft.solverModel}
            manifest={manifest}
            onProvider={(solverProvider) =>
              patchRole("solver", { solverProvider: solverProvider || null, solverModel: null })
            }
            onModel={(solverModel) => patchRole("solver", { solverModel })}
          />
          <RolePicker
            modelRole="extract"
            provider={draft.extractProvider}
            model={draft.extractModel}
            manifest={manifest}
            onProvider={(extractProvider) =>
              patchRole("extract", {
                extractProvider: extractProvider || null,
                extractModel: null,
              })
            }
            onModel={(extractModel) => patchRole("extract", { extractModel })}
          />
        </div>
      )}

      {issue && (
        <p className="rounded-xl border border-amber-300/20 bg-amber-300/[0.06] p-3 text-xs text-amber-100/80">
          {issue}
        </p>
      )}
    </div>
  );
}

function RolePicker({
  modelRole,
  provider,
  model,
  manifest,
  onProvider,
  onModel,
}: {
  modelRole: RegenerationModelRole;
  provider: string | null;
  model: string | null;
  manifest: ProviderModelManifest | undefined;
  onProvider: (provider: string) => void;
  onModel: (model: string | null) => void;
}) {
  const label = ROLE_LABELS[modelRole];
  const providers = Object.keys(manifest?.providers ?? {}).filter(
    (candidate) =>
      manifest?.api_supported[candidate] &&
      (modelRole !== "extract" || !manifest.api_only[candidate]),
  );
  const models = provider ? (manifest?.providers[provider] ?? []) : [];

  return (
    <div className="grid gap-2 rounded-xl border border-white/[0.07] bg-black/10 p-3 sm:grid-cols-2">
      <label className="space-y-1 text-xs text-white/50">
        {label} provider
        <select
          aria-label={`${label} provider`}
          value={provider ?? ""}
          onChange={(event) => onProvider(event.target.value)}
          className="block w-full rounded-xl border border-white/[0.1] bg-[#11131b] px-3 py-2 text-sm text-white"
        >
          <option value="">Choose a provider</option>
          {providers.map((candidate) => (
            <option key={candidate} value={candidate}>
              {candidate}
            </option>
          ))}
        </select>
      </label>
      <label className="space-y-1 text-xs text-white/50">
        {label} model
        <select
          aria-label={`${label} model`}
          value={model ?? ""}
          disabled={!provider}
          onChange={(event) => onModel(event.target.value || null)}
          className="block w-full rounded-xl border border-white/[0.1] bg-[#11131b] px-3 py-2 text-sm text-white disabled:opacity-50"
        >
          <option value="">Choose a model</option>
          {models.map((candidate) => (
            <option key={candidate} value={candidate}>
              {candidate}
            </option>
          ))}
        </select>
      </label>
    </div>
  );
}
