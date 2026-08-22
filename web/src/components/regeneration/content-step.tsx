import {
  type RegenerationErrorView,
  cascadeFromPlan,
  phaseSelectionFromPlan,
  regenerationPlanBlockedReason,
  regenerationPlanStepView,
  regenerationSelectablePhases,
} from "@/lib/api";
import type { GuidedRegenerationDraft } from "@/lib/regeneration-draft";
import { regenerationModelSelectionIssue } from "@/lib/regeneration-model-selection";
import { exclusionWarning } from "@/lib/regeneration-state";
import type { LaunchDefaults, ProviderModelManifest, RegenerationPhasePlan } from "@/lib/types";
import { FRAME_OFF, FRAME_ON, GHOST_BTN, PRIMARY_BTN } from "@/lib/ui";
import { cn, formatPhaseName } from "@/lib/utils";
import { RegenerationModelSelection } from "./model-selection";

export function ContentStep({
  draft,
  canonicalPhases,
  plan,
  manifest,
  launchDefaults,
  launchDefaultsFailed,
  planLoading,
  planErrorView,
  error,
  onChange,
  onBack,
  onContinue,
}: {
  draft: GuidedRegenerationDraft;
  canonicalPhases: string[];
  plan: RegenerationPhasePlan | null;
  manifest: ProviderModelManifest | undefined;
  launchDefaults: LaunchDefaults | undefined;
  launchDefaultsFailed: boolean;
  planLoading: boolean;
  planErrorView: RegenerationErrorView | null;
  error: React.ReactNode;
  onChange: (draft: GuidedRegenerationDraft) => void;
  onBack: () => void;
  onContinue: () => void;
}) {
  const phases = regenerationSelectablePhases(canonicalPhases);
  const selection = plan ? phaseSelectionFromPlan(plan) : null;
  const cascade = plan ? cascadeFromPlan(plan) : null;
  const warning = selection ? exclusionWarning(selection) : null;
  const planStep = regenerationPlanStepView({
    plan,
    hasSelection:
      draft.mode === "full"
        ? phases.length > 0
        : draft.selectedPhases.length > 0 || draft.refreshExtraction,
    isLoading: planLoading,
    error: planErrorView,
  });
  const planBlockedReason = regenerationPlanBlockedReason(planStep);
  const modelIssue = regenerationModelSelectionIssue(
    draft,
    launchDefaults,
    manifest,
    launchDefaultsFailed,
  );
  const patch = (next: Partial<GuidedRegenerationDraft>) => onChange({ ...draft, ...next });
  const toggle = (values: string[], value: string) =>
    values.includes(value) ? values.filter((item) => item !== value) : [...values, value];

  return (
    <section className="space-y-5">
      <div>
        <h2 className="text-lg font-semibold text-white">Choose what to rebuild</h2>
        <p className="mt-1 text-xs leading-5 text-white/45">
          Full rebuild is the safe default. Selective regeneration is available when you
          intentionally want a smaller downstream slice.
        </p>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <button
          type="button"
          onClick={() =>
            patch({ mode: "full", selectedPhases: [], excludedPhases: [], acknowledged: false })
          }
          className={cn("rounded-2xl p-4 text-left", draft.mode === "full" ? FRAME_ON : FRAME_OFF)}
        >
          <span className="block text-sm font-semibold text-white">Full rebuild</span>
          <span className="mt-1 block text-xs leading-5 text-white/50">
            Rebuilds all {phases.length} content phases with the current prompts.
          </span>
        </button>
        <button
          type="button"
          onClick={() => patch({ mode: "selective", acknowledged: false })}
          className={cn(
            "rounded-2xl p-4 text-left",
            draft.mode === "selective" ? FRAME_ON : FRAME_OFF,
          )}
        >
          <span className="block text-sm font-semibold text-white">Selective</span>
          <span className="mt-1 block text-xs leading-5 text-white/50">
            Choose phases; affected downstream phases are included automatically.
          </span>
        </button>
      </div>

      {draft.mode === "selective" && (
        <div className="space-y-2">
          <p className="text-xs font-medium text-white/60">Requested phases</p>
          <div className="flex flex-wrap gap-2">
            {phases.map((phase) => (
              <button
                key={phase}
                type="button"
                className={cn(GHOST_BTN, draft.selectedPhases.includes(phase) && FRAME_ON)}
                onClick={() =>
                  patch({
                    selectedPhases: toggle(draft.selectedPhases, phase),
                    excludedPhases: [],
                    acknowledged: false,
                  })
                }
              >
                {formatPhaseName(phase)}
              </button>
            ))}
          </div>
          {cascade && (
            <p className="rounded-xl border border-white/[0.08] bg-white/[0.03] p-3 text-xs text-white/60">
              {cascade.headline}. {cascade.detail}
            </p>
          )}
        </div>
      )}

      <RegenerationModelSelection
        draft={draft}
        defaults={launchDefaults}
        defaultsLoadFailed={launchDefaultsFailed}
        manifest={manifest}
        onChange={onChange}
      />

      <details className="rounded-xl border border-white/[0.08] bg-white/[0.02] p-3">
        <summary className="cursor-pointer text-sm font-medium text-white/65">Advanced</summary>
        <div className="mt-3 space-y-3 text-xs text-white/55">
          <label className="flex items-start gap-3">
            <input
              type="checkbox"
              checked={draft.refreshExtraction}
              onChange={(event) =>
                patch({ refreshExtraction: event.target.checked, acknowledged: false })
              }
              className="mt-0.5 size-4 accent-[#7c5cff]"
            />
            <span>
              <b className="text-white/80">Re-extract source text</b>
              <br />
              Off by default. Enable only when the textbook extraction itself must be rebuilt.
            </span>
          </label>
          {draft.mode === "selective" && (plan?.auto_included_phases.length ?? 0) > 0 && (
            <div>
              <p className="mb-2">
                Exclude an automatically affected phase only if you accept an inconsistent packet:
              </p>
              <div className="flex flex-wrap gap-2">
                {plan?.auto_included_phases
                  .filter((phase) => !draft.selectedPhases.includes(phase))
                  .map((phase) => (
                    <button
                      key={phase}
                      type="button"
                      className={cn(
                        GHOST_BTN,
                        draft.excludedPhases.includes(phase) &&
                          "border-amber-300/35 text-amber-100",
                      )}
                      onClick={() =>
                        patch({
                          excludedPhases: toggle(draft.excludedPhases, phase),
                          acknowledged: false,
                        })
                      }
                    >
                      {formatPhaseName(phase)}
                    </button>
                  ))}
              </div>
            </div>
          )}
          {warning && (
            <label className="flex items-start gap-3 rounded-xl border border-amber-300/25 bg-amber-300/[0.07] p-3 text-amber-100/85">
              <input
                type="checkbox"
                checked={draft.acknowledged}
                onChange={(event) => patch({ acknowledged: event.target.checked })}
                className="mt-0.5 size-4"
              />
              <span>
                {warning.message}
                <br />
                <b>{warning.acknowledgementLabel}</b>
              </span>
            </label>
          )}
        </div>
      </details>
      {planLoading && <p className="text-xs text-white/40">Calculating downstream phases…</p>}
      {planStep.message && <p className="text-xs text-white/45">{planStep.message}</p>}
      {error}
      {planBlockedReason && <p className="text-xs text-amber-100/70">{planBlockedReason}</p>}
      <div className="flex justify-between">
        <button type="button" className={GHOST_BTN} onClick={onBack}>
          Back
        </button>
        <button
          type="button"
          className={PRIMARY_BTN}
          disabled={modelIssue !== null || planBlockedReason !== null}
          onClick={onContinue}
        >
          Review campaign
        </button>
      </div>
    </section>
  );
}
