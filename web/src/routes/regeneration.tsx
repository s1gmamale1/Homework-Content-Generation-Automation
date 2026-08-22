import { CampaignList } from "@/components/regeneration/campaign-list";
import { CanaryStep } from "@/components/regeneration/canary-step";
import { GuidedProgress } from "@/components/regeneration/guided-progress";
import {
  type RegenerationDraftState,
  RegenerationProblem,
  RegenerationWizard,
} from "@/components/regeneration/regeneration-wizard";
import { SpaceBackdrop } from "@/components/space-backdrop";
import {
  REGENERATION_NO_SPEND_NOTE,
  type RegenerationActionKind,
  api,
  clampCanarySize,
  mergeReleasedFailures,
  regenerationDestinationSignature,
  regenerationDetailView,
  regenerationDraftSignature,
  regenerationEligibleQuery,
  regenerationErrorView,
  regenerationLatestMutationError,
  regenerationListPollMs,
  regenerationMutationView,
  regenerationPollDecision,
  regenerationRetryAudit,
  regenerationSelectablePhases,
} from "@/lib/api";
import {
  clearRegenerationDraft,
  defaultGuidedRegenerationDraft,
  displayedPublicationVersion,
  effectiveSelectedPhases,
  initializeDraftModel,
  loadRegenerationDraft,
  pruneRegenerationDraft,
  saveRegenerationDraft,
} from "@/lib/regeneration-draft";
import {
  regenerationLaunchContract,
  regenerationModelSelectionIssue,
} from "@/lib/regeneration-model-selection";
import { createAndStartCanary } from "@/lib/regeneration-state";
import type {
  LaunchDefaults,
  RegenerationCampaignDetail,
  RegenerationCampaignDraft,
  RegenerationDestinationCheckRequest,
  RegenerationDestinationCheckResponse,
  RegenerationDestinationOverride,
  RegenerationEstimateRequest,
  RegenerationPhasePlanRequest,
  RegenerationTargetReport,
  RegenerationWaveFailure,
} from "@/lib/types";
import { CARD } from "@/lib/ui";
import { cn } from "@/lib/utils";
/**
 * Regeneration area — a dedicated workspace, deliberately NOT the Fleet batch
 * launcher, reached only when the bundle was built with
 * `VITE_REGENERATION_ENABLED=1`. That flag is cosmetic: the backend's
 * `REGENERATION_ENABLED` is the real gate and answers 404 for every route
 * below when it is off, which is exactly what `regenerationErrorView` renders.
 *
 * Server state is authoritative here. This page holds one piece of local
 * state — the DRAFT the operator is composing — and reads everything else from
 * TanStack Query. Mutations never patch a campaign optimistically: each one
 * returns the refreshed report, which is written straight into the cache.
 *
 * Polling follows work, not screens: `regenerationPollDecision` refreshes a
 * campaign only while generation, publication or the publisher's own bounded
 * retries can move it, and stops dead on a terminal campaign, on the canary's
 * human gate, on a target parked waiting for an operator, and on an approved
 * campaign whose release never landed — which polling could never fix.
 *
 * Discovery is BOUNDED. `/eligible` is never called unfiltered: the books list
 * (~246 rows) is the first step, subject and grade narrow it, and only an
 * explicitly chosen book switches the lesson query on.
 *
 * Two payloads exist ONLY on a mutation response and are therefore held here
 * as transient per-campaign / per-target state: `released_failures` (the
 * lessons a release could not start) and the `previous_publication_*` audit a
 * publication retry clears. Both would otherwise be erased by the very
 * refetch that follows the mutation.
 *
 * FAILURE IS A FIRST-CLASS STATE HERE. Each of the four things this page can
 * be doing — nothing selected, loading, failed, loaded — renders as itself. A
 * failed campaign list is not an empty one, a campaign that has not arrived is
 * not an unselected one, and a refusal belongs to the campaign or the lesson
 * whose own mutation variables produced it, never to whatever happens to be on
 * screen when it lands.
 *
 * ACCEPTED, DELIBERATE: `actor` is posted blank and `app_git_revision` null.
 * The backend stores both for audit, but this product exposes no operator
 * identity and no build SHA anywhere in its frontend contract — there is no
 * per-user login, only a shared bearer token, and the bundle carries no commit
 * stamp. Inventing either here would write a meaningless value into a frozen,
 * immutable campaign row. Both stay in one place, on `campaignDraft`, so they
 * are one edit away if the contract ever grows them.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

const CAMPAIGNS_KEY = ["regeneration", "campaigns"] as const;
/** The PREFIX, so one invalidation covers whichever book is loaded. */
const ELIGIBLE_KEY = ["regeneration", "eligible"] as const;
const campaignKey = (id: string) => ["regeneration", "campaign", id] as const;

/** The phase-plan probe. `/phase-plan` refuses an empty selection outright, so
 *  discovering "which phases does this subject even have" asks for the extract
 *  refresh and reads ONLY `canonical_phases` off the answer. Still a pure
 *  preview: no row, no job, no spend. */
function catalogRequest(subject: string): RegenerationPhasePlanRequest {
  return {
    subject,
    selected_phases: [],
    excluded_affected_phases: [],
    refresh_extraction: true,
    exclusion_acknowledged: true,
  };
}

function selectedPhases(draft: RegenerationDraftState, canonicalPhases: string[]): string[] {
  return effectiveSelectedPhases(draft, regenerationSelectablePhases(canonicalPhases));
}

function planRequest(
  subject: string,
  draft: RegenerationDraftState,
  canonicalPhases: string[],
): RegenerationPhasePlanRequest {
  return {
    subject,
    selected_phases: selectedPhases(draft, canonicalPhases),
    excluded_affected_phases: draft.excludedPhases,
    refresh_extraction: draft.refreshExtraction,
    exclusion_acknowledged: draft.acknowledged,
  };
}

function selection(draft: RegenerationDraftState) {
  return {
    // The filters AND server-side, so naming the book alongside the lessons
    // narrows nothing away — it records WHICH book was regenerated on the
    // frozen campaign's `selection_spec`.
    book_ids: draft.bookId ? [draft.bookId] : [],
    toc_entry_ids: draft.selectedTocEntryIds,
    output_languages: [draft.language],
  };
}

function destinationOverrides(draft: RegenerationDraftState): RegenerationDestinationOverride[] {
  return draft.destinationOverrides.map((override) => ({
    toc_entry_id: override.tocEntryId,
    output_language: override.outputLanguage,
    notion_lesson_page_id: override.notionLessonPageId,
  }));
}

function destinationCheckRequest(
  draft: RegenerationDraftState,
): RegenerationDestinationCheckRequest {
  return {
    publication_version: draft.publicationVersion,
    selection: selection(draft),
    destination_overrides: destinationOverrides(draft),
  };
}

function estimateRequest(
  draft: RegenerationDraftState,
  canonicalPhases: string[],
  defaults: LaunchDefaults | undefined,
): RegenerationEstimateRequest | null {
  const contract = regenerationLaunchContract(draft, defaults);
  if (!contract) return null;
  return {
    publication_version: draft.publicationVersion,
    selection: selection(draft),
    contract,
    selected_phases: selectedPhases(draft, canonicalPhases),
    excluded_affected_phases: draft.excludedPhases,
    refresh_extraction: draft.refreshExtraction,
    exclusion_acknowledged: draft.acknowledged,
    canary_size: clampCanarySize(draft.canarySize, draft.selectedTocEntryIds.length),
  };
}

function campaignDraft(
  draft: RegenerationDraftState,
  canonicalPhases: string[],
  approvedDestinationDigest: string,
  estimateLow: number | null,
  estimateHigh: number | null,
  defaults: LaunchDefaults | undefined,
): RegenerationCampaignDraft | null {
  const estimate = estimateRequest(draft, canonicalPhases, defaults);
  if (!estimate) return null;
  return {
    ...estimate,
    destination_overrides: destinationOverrides(draft),
    approved_destination_digest: approvedDestinationDigest,
    // Echoed back so the frozen campaign records the figure that was SHOWN,
    // not one recomputed at insert time.
    estimated_cost_low_usd: estimateLow,
    estimated_cost_high_usd: estimateHigh,
    app_git_revision: null,
    actor: "",
    notes: {},
  };
}

export function RegenerationPage() {
  const qc = useQueryClient();
  const [initialLoad] = useState(() => loadRegenerationDraft(window.localStorage));
  const [draft, setDraft] = useState<RegenerationDraftState>(initialLoad.draft);
  const [draftWarning, setDraftWarning] = useState<string | null>(initialLoad.warning);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [createCanaryFailure, setCreateCanaryFailure] = useState<{
    campaignId: string;
    message: string;
  } | null>(null);
  const launchDefaultsApplied = useRef(false);
  const lastPrunedInputs = useRef<string | null>(null);
  /** The draft signature a create refusal belongs to. A refusal is rendered
   *  only while the draft still matches it. */
  const [createErrorFor, setCreateErrorFor] = useState<string | null>(null);
  /** A destination digest is valid only for the exact lesson/language/version
   *  and override inputs that produced it. The server response is transient;
   *  only the operator's override choices belong in the persisted draft. */
  const [destinationReview, setDestinationReview] = useState<{
    signature: string;
    response: RegenerationDestinationCheckResponse;
  } | null>(null);
  const [pendingByTarget, setPendingByTarget] = useState<
    Record<string, RegenerationActionKind | null>
  >({});
  /** Wave failures live ONLY on a mutation response; the next GET returns an
   *  empty list, so holding them here is what keeps them readable. Keyed by
   *  campaign, and cleared only when a later release for that campaign
   *  SUCCEEDS — see `forgetFailures`. */
  const [releaseFailures, setReleaseFailures] = useState<Record<string, RegenerationWaveFailure[]>>(
    {},
  );
  /** Same problem, per target: `retry_publication` CLEARS the error it
   *  retried, and `previous_*` is the only surviving copy. */
  const [retryAuditByTarget, setRetryAuditByTarget] = useState<Record<string, string>>({});

  const rememberFailures = (campaignId: string, failures: RegenerationWaveFailure[]) => {
    if (failures.length === 0) return;
    setReleaseFailures((prev) => ({
      ...prev,
      [campaignId]: mergeReleasedFailures(failures, prev[campaignId]),
    }));
  };

  /** Books are the bounded first step — ~246 rows, held under a dedicated key.
   *  `/eligible` is asked for ONE book's lessons and nothing wider. */
  const books = useQuery({ queryKey: ["books", "all"], queryFn: api.listAllBooks });

  const eligibleQuery = regenerationEligibleQuery(draft.bookId);
  const eligible = useQuery({
    queryKey: [...ELIGIBLE_KEY, draft.bookId],
    queryFn: () => api.listRegenerationEligible(eligibleQuery.filters),
    enabled: eligibleQuery.enabled,
  });

  const campaigns = useQuery({
    queryKey: CAMPAIGNS_KEY,
    queryFn: () => api.listRegenerationCampaigns(),
    refetchInterval: (query) => regenerationListPollMs(query.state.data?.campaigns),
  });

  const detail = useQuery({
    queryKey: campaignKey(selectedId ?? ""),
    queryFn: () => api.getRegenerationCampaign(selectedId ?? ""),
    enabled: selectedId !== null,
    refetchInterval: (query) => regenerationPollDecision(query.state.data).intervalMs,
  });

  const manifest = useQuery({ queryKey: ["agent-models"], queryFn: api.getAgentModels });
  const launchDefaults = useQuery({
    queryKey: ["settings", "launch-defaults"],
    queryFn: api.getLaunchDefaults,
  });

  useEffect(() => {
    const saved = saveRegenerationDraft(window.localStorage, draft);
    if (saved.warning) setDraftWarning(saved.warning);
  }, [draft]);

  useEffect(() => {
    if (launchDefaultsApplied.current || !launchDefaults.data) return;
    launchDefaultsApplied.current = true;
    setDraft((current) => initializeDraftModel(current, launchDefaults.data));
  }, [launchDefaults.data]);

  const sources = useMemo(() => eligible.data?.sources ?? [], [eligible.data]);
  const selectedSources = useMemo(
    () =>
      sources.filter(
        (source) =>
          source.output_language === draft.language &&
          draft.selectedTocEntryIds.includes(source.toc_entry_id),
      ),
    [draft.language, draft.selectedTocEntryIds, sources],
  );
  const automaticPublicationVersion = displayedPublicationVersion(draft, selectedSources);

  useEffect(() => {
    if (!eligible.data) return;
    if (
      draft.publicationVersionMode !== "automatic" ||
      draft.publicationVersion === automaticPublicationVersion
    ) {
      return;
    }
    setDraft((current) =>
      current.publicationVersionMode === "automatic"
        ? { ...current, publicationVersion: automaticPublicationVersion }
        : current,
    );
  }, [
    automaticPublicationVersion,
    draft.publicationVersion,
    draft.publicationVersionMode,
    eligible.data,
  ]);

  /** The plan is per SUBJECT. Scoping to one book makes that unambiguous: the
   *  selected book's own subject wins, and the eligible rows are the fallback
   *  while the books list is still loading. */
  const subject = useMemo(() => {
    const fromBook = books.data?.find((b) => b.id === draft.bookId)?.subject;
    if (fromBook) return fromBook;
    const chosen = sources.find(
      (s) =>
        s.output_language === draft.language && draft.selectedTocEntryIds.includes(s.toc_entry_id),
    );
    return chosen?.subject ?? sources.find((s) => s.output_language === draft.language)?.subject;
  }, [books.data, draft.bookId, sources, draft.language, draft.selectedTocEntryIds]);

  const catalog = useQuery({
    queryKey: ["regeneration", "phase-catalog", subject],
    queryFn: () => api.previewRegenerationPhasePlan(catalogRequest(subject ?? "")),
    enabled: Boolean(subject),
  });

  const canonicalPhases = catalog.data?.canonical_phases ?? [];

  useEffect(() => {
    if (!draft.bookId || !eligible.data || !catalog.data || !manifest.data) return;
    const validModelRefs = new Set<string>();
    for (const [provider, models] of Object.entries(manifest.data.providers)) {
      if (!manifest.data.api_supported[provider]) continue;
      for (const model of models) validModelRefs.add(`${provider}/${model}`);
    }
    const eligibleTocEntryIds = new Set(eligible.data.sources.map((source) => source.toc_entry_id));
    const validPhaseNames = new Set(regenerationSelectablePhases(catalog.data.canonical_phases));
    const signature = JSON.stringify({
      bookId: draft.bookId,
      eligible: [...eligibleTocEntryIds].sort(),
      models: [...validModelRefs].sort(),
      phases: [...validPhaseNames].sort(),
    });
    if (lastPrunedInputs.current === signature) return;
    lastPrunedInputs.current = signature;
    setDraft((current) => {
      const pruned = pruneRegenerationDraft(current, {
        eligibleTocEntryIds,
        validModelRefs,
        validPhaseNames,
      });
      if (pruned.removedLessonCount > 0) {
        setDraftWarning(
          `${pruned.removedLessonCount} saved lesson${pruned.removedLessonCount === 1 ? " is" : "s are"} no longer eligible and ${pruned.removedLessonCount === 1 ? "was" : "were"} removed from this draft.`,
        );
      }
      return pruned.draft;
    });
  }, [catalog.data, draft.bookId, eligible.data, manifest.data]);
  const effectivePhases = selectedPhases(draft, canonicalPhases);
  const hasPhaseSelection = effectivePhases.length > 0 || draft.refreshExtraction;
  const phasePlanBody = planRequest(subject ?? "", draft, canonicalPhases);
  const plan = useQuery({
    queryKey: ["regeneration", "phase-plan", subject, phasePlanBody],
    queryFn: () => api.previewRegenerationPhasePlan(phasePlanBody),
    enabled: Boolean(subject) && hasPhaseSelection,
  });

  const modelIssue = regenerationModelSelectionIssue(draft, launchDefaults.data, manifest.data);
  const estimateBody = estimateRequest(draft, canonicalPhases, launchDefaults.data);
  const canEstimate =
    draft.selectedTocEntryIds.length > 0 &&
    hasPhaseSelection &&
    modelIssue === null &&
    estimateBody !== null;
  const estimate = useQuery({
    queryKey: ["regeneration", "estimate", estimateBody],
    queryFn: () => {
      if (!estimateBody) throw new Error("regeneration model selection is incomplete");
      return api.estimateRegeneration(estimateBody);
    },
    enabled: canEstimate,
  });

  const destinationBody = destinationCheckRequest(draft);
  const destinationSignature = regenerationDestinationSignature(destinationBody);
  const currentDestinations =
    destinationReview?.signature === destinationSignature ? destinationReview.response : null;

  const destinationMut = useMutation({
    mutationFn: (vars: {
      request: RegenerationDestinationCheckRequest;
      signature: string;
    }) => api.checkRegenerationDestinations(vars.request),
    onSuccess: (response, vars) => setDestinationReview({ signature: vars.signature, response }),
  });

  /** Write the refreshed report the mutation returned straight into the cache,
   *  then invalidate so the next read still comes from the server. The API is
   *  the authority for campaign state; nothing here guesses it. */
  const adopt = (fresh: RegenerationCampaignDetail) => {
    qc.setQueryData(campaignKey(fresh.id), fresh);
    // Do this BEFORE invalidating: the refetch that follows returns
    // `released_failures: []`, because only the mutation route reports them.
    rememberFailures(fresh.id, fresh.released_failures);
    qc.invalidateQueries({ queryKey: CAMPAIGNS_KEY });
    qc.invalidateQueries({ queryKey: campaignKey(fresh.id) });
  };

  const createMut = useMutation({
    // The draft this refusal will be ABOUT. `useMutation` keeps `error` until
    // the next `mutate()`, so without this an `active_lineage_conflict` naming
    // three lessons stays on screen after the operator has deselected them —
    // describing a draft that no longer exists and blaming a selection that is
    // no longer there.
    onMutate: () => setCreateErrorFor(regenerationDraftSignature(draft)),
    mutationFn: (body: RegenerationCampaignDraft) =>
      createAndStartCanary({
        request: body,
        create: api.createRegenerationCampaign,
        launch: api.launchRegenerationCanary,
        onCampaignCreated: (fresh) => {
          const cleared = clearRegenerationDraft(window.localStorage);
          setDraftWarning(cleared.warning);
          setDraft(
            launchDefaults.data
              ? initializeDraftModel(defaultGuidedRegenerationDraft(), launchDefaults.data)
              : defaultGuidedRegenerationDraft(),
          );
          setCreateCanaryFailure(null);
          adopt(fresh);
          qc.invalidateQueries({ queryKey: ELIGIBLE_KEY });
          setDestinationReview(null);
          setSelectedId(fresh.id);
        },
      }),
    onSuccess: (result) => {
      if (result.canaryStarted) {
        forgetFailures(result.campaign.id);
        adopt(result.campaign);
        toast.success("Campaign created and canary started. Review it here when it finishes.");
      } else {
        setCreateCanaryFailure({
          campaignId: result.campaign.id,
          message:
            "Campaign created; canary not started. Retry this campaign's canary below — do not create it again.",
        });
        toast.error("Campaign created; canary not started.", {
          description: "Use Retry canary on this campaign. Do not create it again.",
        });
      }
    },
  });

  /** A release that SUCCEEDS is the one intentional boundary at which the
   *  previous attempt's failures stop being the current truth. Deliberately
   *  not `onMutate`: a retry that is refused (publisher off, stale state) must
   *  leave the record of the wave that failed on screen, and clearing it
   *  before the round trip would delete exactly the list the operator is
   *  acting on. Both updates are functional, so the clear lands before the
   *  merge in `adopt`. */
  const forgetFailures = (campaignId: string) =>
    setReleaseFailures((prev) => {
      if (!(campaignId in prev)) return prev;
      const next = { ...prev };
      delete next[campaignId];
      return next;
    });

  const canaryMut = useMutation({
    mutationFn: (campaignId: string) => api.launchRegenerationCanary(campaignId),
    onSuccess: (fresh) => {
      setCreateCanaryFailure((current) => (current?.campaignId === fresh.id ? null : current));
      forgetFailures(fresh.id);
      adopt(fresh);
      toast.success("Canary started. It will wait here for your review.");
    },
  });

  const approveMut = useMutation({
    mutationFn: (campaignId: string) => api.approveRegenerationCampaign(campaignId, { actor: "" }),
    onSuccess: (fresh) => {
      forgetFailures(fresh.id);
      adopt(fresh);
      toast.success("Approved. Remaining lessons release and successful versions publish.");
    },
  });

  const rejectMut = useMutation({
    mutationFn: (vars: { campaignId: string; reason: string }) =>
      api.rejectRegenerationCampaign(vars.campaignId, { actor: "", reason: vars.reason }),
    onSuccess: (fresh) => {
      adopt(fresh);
      toast.success("Rejected. No Notion page was created and no version was consumed.");
    },
  });

  const cancelMut = useMutation({
    mutationFn: (vars: { campaignId: string; reason: string }) =>
      api.cancelRegenerationCampaign(vars.campaignId, { actor: "", reason: vars.reason }),
    onSuccess: (fresh) => {
      adopt(fresh);
      toast.success("Cancelled. Pages that already published stay published.");
    },
  });

  const targetMut = useMutation({
    mutationFn: (vars: {
      kind: RegenerationActionKind;
      target: RegenerationTargetReport;
      reason: string;
    }) => {
      if (vars.kind === "retry-generation") {
        return api.retryRegenerationGeneration(vars.target.id);
      }
      if (vars.kind === "retry-publication") {
        return api.retryRegenerationPublication(vars.target.id);
      }
      return api.abandonRegenerationTarget(vars.target.id, {
        actor: "",
        reason: vars.reason,
      });
    },
    onMutate: (vars) => {
      setPendingByTarget((prev) => ({ ...prev, [vars.target.id]: vars.kind }));
      // A new action on this target supersedes the note the previous one left.
      setRetryAuditByTarget((prev) => {
        if (!(vars.target.id in prev)) return prev;
        const next = { ...prev };
        delete next[vars.target.id];
        return next;
      });
    },
    onSuccess: (result) => {
      // `retry_publication` clears the error it retried; `previous_*` is the
      // only remaining record of what prompted it, so it is kept on screen
      // rather than spent on a toast that disappears.
      const audit = regenerationRetryAudit(result);
      if (audit) {
        setRetryAuditByTarget((prev) => ({ ...prev, [result.target.id]: audit }));
      }
      // A target action can release a wave too, and those failures are just as
      // mutation-only as the campaign ones.
      rememberFailures(result.campaign_id, result.released_failures);
      qc.invalidateQueries({ queryKey: campaignKey(result.campaign_id) });
      qc.invalidateQueries({ queryKey: CAMPAIGNS_KEY });
      toast.success(result.target.reason, audit ? { description: audit } : undefined);
    },
    onSettled: (_result, _error, vars) => {
      setPendingByTarget((prev) => ({ ...prev, [vars.target.id]: null }));
    },
  });

  const view = (error: unknown) => (error ? regenerationErrorView(error) : null);

  /** "Nothing is selected", "it has not arrived yet", "the read failed" and
   *  "here it is" are four different screens; only the first is an invitation
   *  to pick a campaign. */
  const detailView = regenerationDetailView({
    selectedId,
    data: detail.data,
    error: detail.error,
  });
  const selected = detailView.detail;
  const poll = regenerationPollDecision(selected);

  /**
   * Every campaign/target mutation is read through its own VARIABLES.
   *
   * A mutation object outlives the selection: approve campaign A, get a 409,
   * click campaign B — and B rendered A's refusal and A's pending spinner,
   * because neither lives on the campaign. `variables` records which campaign
   * or lesson the operator actually acted on, so it is what decides who the
   * result belongs to. Nothing here calls `reset()`: that would fix the
   * attribution by deleting the evidence, and it has to be driven from the
   * selection handler, which is the same place this page deliberately keeps
   * state a refetch would erase.
   */
  const ownsCampaign = (id: string) => id === selectedId;
  const ownsCampaignVars = (vars: { campaignId: string }) => vars.campaignId === selectedId;
  const canaryView = regenerationMutationView(canaryMut, ownsCampaign);
  const approveView = regenerationMutationView(approveMut, ownsCampaign);
  const rejectView = regenerationMutationView(rejectMut, ownsCampaignVars);
  const cancelView = regenerationMutationView(cancelMut, ownsCampaignVars);
  const targetView = regenerationMutationView(
    targetMut,
    (vars: { target: RegenerationTargetReport }) => vars.target.campaign_id === selectedId,
  );

  // The newest submitted gate action owns the visible result. A stale approve
  // refusal must not mask the reject the operator just attempted, and a newer
  // success supersedes every older refusal.
  const campaignActionError = regenerationLatestMutationError([
    { submittedAt: canaryMut.submittedAt, error: canaryView.error },
    { submittedAt: approveMut.submittedAt, error: approveView.error },
    { submittedAt: rejectMut.submittedAt, error: rejectView.error },
  ]);
  // A target refusal is about ONE lesson, so it renders on that lesson's row.
  const targetError =
    targetView.error && targetMut.variables
      ? { targetId: targetMut.variables.target.id, view: targetView.error }
      : null;

  return (
    <div className="relative">
      <SpaceBackdrop />

      <div className="relative z-10 space-y-7">
        <header className="flex items-start gap-4">
          <span className="grid size-14 shrink-0 place-items-center rounded-2xl border border-white/[0.12] bg-gradient-to-br from-[#7c5cff]/40 to-[#4d9bff]/30 shadow-[0_18px_40px_-18px_rgba(124,92,255,0.8)]">
            <RefreshCw className="size-7 text-white" />
          </span>
          <div>
            <h1 className="text-4xl font-semibold tracking-tight text-white sm:text-[2.75rem]">
              Regeneration
            </h1>
            <p className="mt-2 max-w-[62ch] text-sm leading-6 text-white/55">
              Rebuild phases of homework that is already published, review a canary, and release a
              new version beside the original. This workspace is separate from the Fleet launcher on
              purpose — nothing here starts a first-run batch, and nothing here replaces a page that
              already exists.
            </p>
          </div>
        </header>

        <div className={cn(CARD, "text-xs leading-5 text-white/45")}>
          {REGENERATION_NO_SPEND_NOTE}
        </div>

        <div className="space-y-5">
          <details className={cn(CARD, "p-4")}>
            <summary className="cursor-pointer text-sm font-semibold text-white/70">
              Previous campaigns
            </summary>
            <div className="mt-4">
              <CampaignList
                campaigns={campaigns.data?.campaigns ?? []}
                count={campaigns.data?.count ?? null}
                limit={campaigns.data?.limit ?? 0}
                offset={campaigns.data?.offset ?? 0}
                selectedId={selectedId}
                onSelect={setSelectedId}
                isLoading={campaigns.isLoading}
                error={campaigns.error}
                onRetry={() => campaigns.refetch()}
              />
            </div>
          </details>

          {selectedId === null ? (
            <RegenerationWizard
              books={books.data}
              booksLoading={books.isLoading}
              booksError={view(books.error)}
              sources={sources}
              ineligible={eligible.data?.ineligible ?? []}
              sourcesLoading={eligibleQuery.enabled && eligible.isLoading}
              sourcesError={view(eligible.error)}
              pickBookReason={eligibleQuery.blockedReason}
              phaseCatalog={catalog.data?.canonical_phases ?? []}
              plan={plan.data ?? null}
              planLoading={plan.isFetching || catalog.isFetching}
              planError={view(plan.error ?? catalog.error)}
              estimate={estimate.data ?? null}
              estimateError={view(estimate.error)}
              destinations={currentDestinations}
              destinationsChecking={destinationMut.isPending}
              destinationError={
                destinationMut.variables?.signature === destinationSignature
                  ? view(destinationMut.error)
                  : null
              }
              onCheckDestinations={() =>
                destinationMut.mutate({
                  request: destinationBody,
                  signature: destinationSignature,
                })
              }
              onChooseDestination={(tocEntryId, outputLanguage, notionLessonPageId) =>
                setDraft((current) => ({
                  ...current,
                  destinationOverrides: [
                    ...current.destinationOverrides.filter(
                      (override) =>
                        override.tocEntryId !== tocEntryId ||
                        override.outputLanguage !== outputLanguage,
                    ),
                    { tocEntryId, outputLanguage, notionLessonPageId },
                  ],
                }))
              }
              manifest={manifest.data}
              launchDefaults={launchDefaults.data}
              manifestError={view(manifest.error)}
              state={draft}
              draftWarning={draftWarning}
              onChange={setDraft}
              onDiscard={() => {
                const cleared = clearRegenerationDraft(window.localStorage);
                setDraft(
                  launchDefaults.data
                    ? initializeDraftModel(defaultGuidedRegenerationDraft(), launchDefaults.data)
                    : defaultGuidedRegenerationDraft(),
                );
                setDraftWarning(cleared.warning);
                setDestinationReview(null);
              }}
              onCreateAndStart={() => {
                if (!currentDestinations) return;
                const body = campaignDraft(
                  draft,
                  canonicalPhases,
                  currentDestinations.destination_digest,
                  estimate.data?.estimate?.low_usd ?? null,
                  estimate.data?.estimate?.high_usd ?? null,
                  launchDefaults.data,
                );
                if (body) createMut.mutate(body);
              }}
              starting={createMut.isPending}
              createError={
                createErrorFor === regenerationDraftSignature(draft) ? view(createMut.error) : null
              }
              onOpenCampaign={setSelectedId}
            />
          ) : (
            <div className="space-y-4">
              <GuidedProgress active="canary" highestReachable="canary" readOnly />
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <h2 className="text-sm font-semibold text-white/75">
                    Canary review and campaign report
                  </h2>
                  <p className="mt-1 text-xs text-white/40">
                    This is the only content approval gate. Successful results publish automatically
                    after approval.
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => setSelectedId(null)}
                  className="rounded-xl border border-white/[0.1] bg-white/[0.04] px-3 py-2 text-xs font-medium text-white/70 hover:bg-white/[0.08]"
                >
                  New campaign
                </button>
              </div>
              {detailView.error && (
                <RegenerationProblem view={detailView.error} onRetry={() => detail.refetch()} />
              )}
              {detailView.message && (
                <p className={cn(CARD, "text-xs text-white/40")}>{detailView.message}</p>
              )}
              {createCanaryFailure?.campaignId === selectedId && (
                <p className="rounded-xl border border-amber-300/25 bg-amber-300/[0.07] p-3 text-xs leading-5 text-amber-100/85">
                  {createCanaryFailure.message}
                </p>
              )}

              {selected && (
                <>
                  <p className="px-1 text-[0.68rem] leading-5 text-white/35">{poll.reason}</p>
                  <CanaryStep
                    campaign={selected}
                    releasedFailures={mergeReleasedFailures(
                      selected.released_failures,
                      releaseFailures[selected.id],
                    )}
                    retryAuditByTarget={retryAuditByTarget}
                    pendingByTarget={pendingByTarget}
                    onLaunchCanary={() => canaryMut.mutate(selected.id)}
                    onApprove={() => approveMut.mutate(selected.id)}
                    onReject={(reason) =>
                      rejectMut.mutateAsync({ campaignId: selected.id, reason })
                    }
                    onTargetAction={(kind, target, reason) =>
                      targetMut.mutateAsync({ kind, target, reason })
                    }
                    onCancel={(reason) =>
                      cancelMut.mutateAsync({ campaignId: selected.id, reason })
                    }
                    launching={canaryView.pending}
                    approving={approveView.pending}
                    rejecting={rejectView.pending}
                    cancelling={cancelView.pending}
                    actionError={campaignActionError ?? cancelView.error}
                    targetError={targetError}
                  />
                </>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
