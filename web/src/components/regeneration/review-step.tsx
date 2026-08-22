import { clampCanarySize } from "@/lib/api";
import type { GuidedRegenerationDraft } from "@/lib/regeneration-draft";
import { effectiveRegenerationModels } from "@/lib/regeneration-model-selection";
import { formatUsd, lessonCountLabel, reviewGate } from "@/lib/regeneration-state";
import type {
  LaunchDefaults,
  RegenerationDestinationCheckResponse,
  RegenerationEstimateResponse,
  RegenerationOutputLanguage,
} from "@/lib/types";
import { GHOST_BTN, PRIMARY_BTN } from "@/lib/ui";

export function ReviewStep({
  draft,
  launchDefaults,
  estimate,
  destinations,
  checking,
  starting,
  error,
  onBack,
  onCheckDestinations,
  onChooseDestination,
  onChange,
  onStart,
}: {
  draft: GuidedRegenerationDraft;
  launchDefaults: LaunchDefaults | undefined;
  estimate: RegenerationEstimateResponse | null;
  destinations: RegenerationDestinationCheckResponse | null;
  checking: boolean;
  starting: boolean;
  error: React.ReactNode;
  onBack: () => void;
  onCheckDestinations: () => void;
  onChooseDestination: (
    tocEntryId: string,
    language: RegenerationOutputLanguage,
    pageId: string,
  ) => void;
  onChange: (draft: GuidedRegenerationDraft) => void;
  onStart: () => void;
}) {
  const destinationReady = Boolean(
    destinations?.ok && destinations.checked_target_count === destinations.target_count,
  );
  const gate = reviewGate({
    estimateReady: estimate !== null,
    workerOk: estimate?.worker_executability.ok ?? false,
    destinationsOk: destinationReady,
  });
  const totals = estimate?.estimate;
  const models = effectiveRegenerationModels(draft, launchDefaults);
  const displayModel = (role: keyof typeof models) => {
    const pair = models[role];
    return pair.provider && pair.model ? `${pair.provider}/${pair.model}` : "Not configured";
  };
  return (
    <section className="space-y-5">
      <div>
        <h2 className="text-lg font-semibold text-white">Review before spending</h2>
        <p className="mt-1 text-xs leading-5 text-white/45">
          The estimate is database-only. The Notion check is read-only and may take a few minutes
          for the complete bounded scan.
        </p>
      </div>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <ReviewValue label="Lessons" value={lessonCountLabel(draft.selectedTocEntryIds.length)} />
        <ReviewValue label="Version" value={`Homework V${draft.publicationVersion}`} />
        <ReviewValue label="Content" value={draft.mode === "full" ? "Full rebuild" : "Selective"} />
        <ReviewValue
          label="Estimated cost"
          value={
            totals ? `${formatUsd(totals.low_usd)} – ${formatUsd(totals.high_usd)}` : "Calculating…"
          }
        />
        <ReviewValue
          label="Model source"
          value={draft.modelSelectionMode === "settings" ? "Settings defaults" : "Overrides"}
        />
        <ReviewValue label="Content model" value={displayModel("content")} />
        <ReviewValue label="Judge model" value={displayModel("judge")} />
        <ReviewValue label="Solver model" value={displayModel("solver")} />
        <ReviewValue label="Extract model" value={displayModel("extract")} />
        <ReviewValue
          label="Extraction"
          value={draft.refreshExtraction ? "Refresh source text" : "Reuse source text"}
        />
        <ReviewValue
          label="Regenerated phases"
          value={totals ? String(totals.regenerated_phase_count) : "Calculating…"}
        />
        <ReviewValue
          label="Copied phases"
          value={totals ? String(totals.copied_phase_count) : "Calculating…"}
        />
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="space-y-1 text-xs text-white/50">
          Campaign version
          <span className="flex items-center gap-2 rounded-xl border border-white/[0.1] bg-white/[0.03] px-3 py-2 text-sm text-white">
            <span>Homework V</span>
            <input
              type="number"
              min={2}
              step={1}
              value={draft.publicationVersion}
              onChange={(event) =>
                onChange({
                  ...draft,
                  publicationVersion: Math.max(2, Math.trunc(Number(event.target.value) || 2)),
                  publicationVersionMode: "manual",
                })
              }
              className="w-20 bg-transparent text-white outline-none"
            />
          </span>
        </label>
        <label className="space-y-1 text-xs text-white/50">
          Canary lessons
          <input
            type="number"
            min={1}
            max={Math.max(1, draft.selectedTocEntryIds.length)}
            step={1}
            value={draft.canarySize}
            onChange={(event) =>
              onChange({
                ...draft,
                canarySize: clampCanarySize(
                  Number(event.target.value),
                  draft.selectedTocEntryIds.length,
                ),
              })
            }
            className="block w-full rounded-xl border border-white/[0.1] bg-white/[0.03] px-3 py-2 text-sm text-white"
          />
        </label>
      </div>
      <div className="rounded-xl border border-white/[0.08] bg-white/[0.03] p-3 text-xs leading-5 text-white/60">
        {estimate?.worker_executability.ok
          ? `${estimate.worker_executability.compatible_worker_ids.length} compatible worker(s) active.`
          : (estimate?.worker_executability.reason ?? "Checking worker compatibility…")}
      </div>
      <button
        type="button"
        className={GHOST_BTN}
        disabled={checking || !estimate}
        onClick={onCheckDestinations}
      >
        {checking ? "Checking Notion…" : "Check Notion destinations"}
      </button>
      {destinations && (
        <div className="space-y-2">
          <p className="text-xs text-white/45">
            Checked {destinations.checked_target_count} of {destinations.target_count} destinations.
          </p>
          <ul className="space-y-2">
            {destinations.destinations.map((destination) => (
              <li
                key={`${destination.toc_entry_id}:${destination.output_language}`}
                className="rounded-xl border border-white/[0.08] bg-white/[0.025] p-3 text-xs text-white/65"
              >
                <p className="font-medium text-white/85">{destination.lesson_title}</p>
                <p className="mt-1">
                  {destination.status === "reuse"
                    ? "Reuse the reviewed Lesson Topic"
                    : destination.status === "create"
                      ? "Create a Lesson Topic under the reviewed language container"
                      : (destination.reason ?? "Choose a safe Lesson Topic")}
                </p>
                {destination.notion_page_url && (
                  <a
                    href={destination.notion_page_url}
                    target="_blank"
                    rel="noreferrer"
                    className="mt-1 inline-block text-sky-200 underline"
                  >
                    Open reviewed Lesson Topic
                  </a>
                )}
                {destination.candidates.length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-2">
                    {destination.candidates.map((candidate) => (
                      <button
                        key={candidate.page_id}
                        type="button"
                        className={GHOST_BTN}
                        onClick={() =>
                          onChooseDestination(
                            destination.toc_entry_id,
                            destination.output_language,
                            candidate.page_id,
                          )
                        }
                      >
                        {candidate.title}
                      </button>
                    ))}
                  </div>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
      {error}
      {!gate.ok && <p className="text-xs text-amber-100/75">{gate.reason}</p>}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <button type="button" className={GHOST_BTN} onClick={onBack}>
          Back
        </button>
        <div className="text-right">
          <button
            type="button"
            className={PRIMARY_BTN}
            disabled={!gate.ok || starting}
            onClick={onStart}
          >
            {starting
              ? "Creating campaign…"
              : `Create campaign and start ${draft.canarySize} canary lesson${draft.canarySize === 1 ? "" : "s"}`}
          </button>
          <p className="mt-1 text-[0.68rem] text-white/35">First paid action</p>
        </div>
      </div>
    </section>
  );
}

function ReviewValue({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-white/[0.08] bg-white/[0.03] p-3">
      <p className="font-mono text-[0.62rem] uppercase tracking-[0.14em] text-white/35">{label}</p>
      <p className="mt-1 text-sm font-semibold text-white">{value}</p>
    </div>
  );
}
