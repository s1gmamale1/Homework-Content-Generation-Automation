import { ContentStep } from "@/components/regeneration/content-step";
import { GuidedProgress } from "@/components/regeneration/guided-progress";
import { LessonStep } from "@/components/regeneration/lesson-step";
import { ReviewStep } from "@/components/regeneration/review-step";
import {
  REGENERATION_READ_RETRY_LABEL,
  type RegenerationErrorView,
  regenerationKeyedLines,
} from "@/lib/api";
import type { GuidedRegenerationDraft } from "@/lib/regeneration-draft";
import type {
  Book,
  ProviderModelManifest,
  RegenerationDestinationCheckResponse,
  RegenerationEligibleSource,
  RegenerationEstimateResponse,
  RegenerationOutputLanguage,
  RegenerationPhasePlan,
} from "@/lib/types";
import { CARD, GHOST_BTN } from "@/lib/ui";
import { cn } from "@/lib/utils";
import { CircleAlert, RotateCcw, Trash2 } from "lucide-react";

export type RegenerationDraftState = GuidedRegenerationDraft;

export function RegenerationProblem({
  view,
  onRetry,
  onOpenCampaign,
  retryLabel = REGENERATION_READ_RETRY_LABEL,
}: {
  view: RegenerationErrorView;
  onRetry?: () => void;
  onOpenCampaign?: (campaignId: string) => void;
  retryLabel?: string;
}) {
  const details = regenerationKeyedLines(view.details);
  return (
    <div className="space-y-1 rounded-xl border border-rose-300/25 bg-rose-300/[0.07] p-3 text-xs leading-5 text-rose-100/90">
      <div className="flex items-start gap-2 font-semibold">
        <CircleAlert className="mt-0.5 size-4 shrink-0" />
        <span>{view.title}</span>
      </div>
      <p className="max-w-[75ch]">{view.message}</p>
      {details.length > 0 && (
        <ul className="space-y-0.5 pl-5 [list-style:disc]">
          {details.map((row) => (
            <li key={row.key}>{row.text}</li>
          ))}
        </ul>
      )}
      {view.hint && <p className="max-w-[75ch] text-rose-100/70">{view.hint}</p>}
      {onOpenCampaign && view.campaignIds.length > 0 && (
        <div className="flex flex-wrap gap-2 pt-1">
          {view.campaignIds.map((campaignId) => (
            <button
              key={campaignId}
              type="button"
              onClick={() => onOpenCampaign(campaignId)}
              className={cn(GHOST_BTN, "px-2 py-1 text-xs")}
            >
              Open campaign {campaignId.slice(0, 8)}
            </button>
          ))}
        </div>
      )}
      {onRetry && (
        <button type="button" onClick={onRetry} className={cn(GHOST_BTN, "mt-1 px-2 py-1 text-xs")}>
          <RotateCcw className="size-3.5" />
          {retryLabel}
        </button>
      )}
    </div>
  );
}

export function RegenerationWizard({
  books,
  booksLoading,
  booksError,
  sources,
  ineligible,
  sourcesLoading,
  sourcesError,
  pickBookReason,
  phaseCatalog,
  plan,
  planLoading,
  planError,
  estimate,
  estimateError,
  destinations,
  destinationsChecking,
  destinationError,
  onCheckDestinations,
  onChooseDestination,
  manifest,
  manifestError,
  state,
  draftWarning,
  onChange,
  onDiscard,
  onCreateAndStart,
  starting,
  createError,
  onOpenCampaign,
}: {
  books: Book[] | undefined;
  booksLoading: boolean;
  booksError: RegenerationErrorView | null;
  sources: RegenerationEligibleSource[];
  ineligible: import("@/lib/types").RegenerationIneligibleLineage[];
  sourcesLoading: boolean;
  sourcesError: RegenerationErrorView | null;
  pickBookReason: string | null;
  phaseCatalog: string[];
  plan: RegenerationPhasePlan | null;
  planLoading: boolean;
  planError: RegenerationErrorView | null;
  estimate: RegenerationEstimateResponse | null;
  estimateError: RegenerationErrorView | null;
  destinations: RegenerationDestinationCheckResponse | null;
  destinationsChecking: boolean;
  destinationError: RegenerationErrorView | null;
  onCheckDestinations: () => void;
  onChooseDestination: (
    tocEntryId: string,
    outputLanguage: RegenerationOutputLanguage,
    notionLessonPageId: string,
  ) => void;
  manifest: ProviderModelManifest | undefined;
  manifestError: RegenerationErrorView | null;
  state: GuidedRegenerationDraft;
  draftWarning: string | null;
  onChange: (next: GuidedRegenerationDraft) => void;
  onDiscard: () => void;
  onCreateAndStart: () => void;
  starting: boolean;
  createError: RegenerationErrorView | null;
  onOpenCampaign: (campaignId: string) => void;
}) {
  const step = state.step === "canary" ? "review" : state.step;
  const highestReachable =
    state.selectedTocEntryIds.length === 0
      ? "lessons"
      : !state.model || !plan
        ? "content"
        : "review";
  const go = (next: GuidedRegenerationDraft["step"]) => onChange({ ...state, step: next });

  return (
    <div className={cn(CARD, "space-y-5 p-4 sm:p-5")}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-white">New regeneration campaign</h2>
          <p className="mt-1 text-xs text-white/40">
            Your choices are saved in this browser until the campaign is created.
          </p>
        </div>
        <button type="button" className={GHOST_BTN} onClick={onDiscard}>
          <Trash2 className="size-4" />
          Discard draft
        </button>
      </div>
      <GuidedProgress active={step} highestReachable={highestReachable} onSelect={go} />
      {draftWarning && (
        <p className="rounded-xl border border-amber-300/20 bg-amber-300/[0.06] p-3 text-xs leading-5 text-amber-100/80">
          {draftWarning}
        </p>
      )}

      {step === "lessons" && (
        <LessonStep
          draft={state}
          books={books}
          sources={sources}
          ineligible={ineligible}
          loading={booksLoading || sourcesLoading}
          error={
            <>
              {booksError && <RegenerationProblem view={booksError} />}
              {sourcesError && <RegenerationProblem view={sourcesError} />}
            </>
          }
          errorView={sourcesError}
          blockedReason={pickBookReason}
          onChange={onChange}
          onContinue={() => go("content")}
        />
      )}
      {step === "content" && (
        <ContentStep
          draft={state}
          canonicalPhases={phaseCatalog}
          plan={plan}
          manifest={manifest}
          planLoading={planLoading}
          planErrorView={planError}
          error={
            <>
              {planError && <RegenerationProblem view={planError} />}
              {manifestError && <RegenerationProblem view={manifestError} />}
            </>
          }
          onChange={onChange}
          onBack={() => go("lessons")}
          onContinue={() => go("review")}
        />
      )}
      {step === "review" && (
        <ReviewStep
          draft={state}
          estimate={estimate}
          destinations={destinations}
          checking={destinationsChecking}
          starting={starting}
          error={
            <>
              {estimateError && (
                <RegenerationProblem view={estimateError} onOpenCampaign={onOpenCampaign} />
              )}
              {destinationError && <RegenerationProblem view={destinationError} />}
              {createError && (
                <RegenerationProblem view={createError} onOpenCampaign={onOpenCampaign} />
              )}
            </>
          }
          onBack={() => go("content")}
          onCheckDestinations={onCheckDestinations}
          onChooseDestination={onChooseDestination}
          onChange={onChange}
          onStart={onCreateAndStart}
        />
      )}
    </div>
  );
}
