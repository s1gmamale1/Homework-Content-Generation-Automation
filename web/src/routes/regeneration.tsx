import { CampaignList } from "@/components/regeneration/campaign-list";
import { CampaignReport } from "@/components/regeneration/campaign-report";
import { CanaryReview } from "@/components/regeneration/canary-review";
import {
  type RegenerationDraftState,
  RegenerationWizard,
  defaultRegenerationDraft,
} from "@/components/regeneration/regeneration-wizard";
import { SpaceBackdrop } from "@/components/space-backdrop";
import {
  REGENERATION_NO_SPEND_NOTE,
  type RegenerationActionKind,
  api,
  clampCanarySize,
  mergeReleasedFailures,
  regenerationEligibleQuery,
  regenerationErrorView,
  regenerationListPollMs,
  regenerationPollDecision,
  regenerationRetryAudit,
} from "@/lib/api";
import type {
  RegenerationCampaignDetail,
  RegenerationCampaignDraft,
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
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";

const CAMPAIGNS_KEY = ["regeneration", "campaigns"] as const;
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

function planRequest(subject: string, draft: RegenerationDraftState): RegenerationPhasePlanRequest {
  return {
    subject,
    selected_phases: draft.selectedPhases,
    excluded_affected_phases: draft.excludedPhases,
    refresh_extraction: draft.refreshExtraction,
    exclusion_acknowledged: draft.acknowledged,
  };
}

function campaignDraft(
  draft: RegenerationDraftState,
  estimateLow: number | null,
  estimateHigh: number | null,
): RegenerationCampaignDraft {
  return {
    selection: {
      // The filters AND server-side, so naming the book alongside the lessons
      // narrows nothing away — it records WHICH book was regenerated on the
      // frozen campaign's `selection_spec`.
      book_ids: draft.bookId ? [draft.bookId] : [],
      toc_entry_ids: draft.selectedTocEntryIds,
      output_languages: [draft.language],
    },
    contract: {
      provider: draft.provider,
      model: draft.model,
      // Regeneration is api-only server-side; sending anything else is a
      // guaranteed `non_api_transport` refusal, so it is pinned here.
      transport: "api",
      extract_transport: "inherit",
      extract_provider: null,
      extract_model: null,
      judge_transport: "inherit",
      judge_provider: null,
      judge_model: null,
      solver_transport: "inherit",
      solver_provider: null,
      solver_model: null,
      session_limit_strategy: "inherit",
    },
    selected_phases: draft.selectedPhases,
    excluded_affected_phases: draft.excludedPhases,
    refresh_extraction: draft.refreshExtraction,
    exclusion_acknowledged: draft.acknowledged,
    // Clamped again at the boundary: `canary_size` has a `ge=1` server refusal
    // that arrives as a raw validation payload, and deselecting lessons after
    // sizing the canary is the ordinary way to get there.
    canary_size: clampCanarySize(draft.canarySize, draft.selectedTocEntryIds.length),
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
  const [draft, setDraft] = useState<RegenerationDraftState>(defaultRegenerationDraft);
  const [selectedId, setSelectedId] = useState<string | null>(null);
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

  /** Books are the bounded first step — ~246 rows, shared with Fleet's cache.
   *  `/eligible` is asked for ONE book's lessons and nothing wider. */
  const books = useQuery({ queryKey: ["books"], queryFn: api.listBooks });

  const eligibleQuery = regenerationEligibleQuery(draft.bookId);
  const eligible = useQuery({
    queryKey: ["regeneration", "eligible", draft.bookId],
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

  const sources = useMemo(() => eligible.data?.sources ?? [], [eligible.data]);
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

  const hasPhaseSelection = draft.selectedPhases.length > 0 || draft.refreshExtraction;
  const plan = useQuery({
    queryKey: ["regeneration", "phase-plan", subject, planRequest(subject ?? "", draft)],
    queryFn: () => api.previewRegenerationPhasePlan(planRequest(subject ?? "", draft)),
    enabled: Boolean(subject) && hasPhaseSelection,
  });

  const canEstimate =
    draft.selectedTocEntryIds.length > 0 && hasPhaseSelection && draft.model !== null;
  const estimateBody = campaignDraft(draft, null, null);
  const estimate = useQuery({
    queryKey: ["regeneration", "estimate", estimateBody],
    queryFn: () => api.estimateRegeneration(estimateBody),
    enabled: canEstimate,
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
    mutationFn: () =>
      api.createRegenerationCampaign(
        campaignDraft(
          draft,
          estimate.data?.estimate?.low_usd ?? null,
          estimate.data?.estimate?.high_usd ?? null,
        ),
      ),
    onSuccess: (fresh) => {
      adopt(fresh);
      setSelectedId(fresh.id);
      toast.success("Campaign frozen. Nothing has been spent or published yet.");
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

  const selected = detail.data ?? null;
  const poll = regenerationPollDecision(selected);

  const view = (error: unknown) => (error ? regenerationErrorView(error) : null);
  const campaignActionError = view(
    canaryMut.error ?? approveMut.error ?? rejectMut.error ?? detail.error,
  );

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

        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
          <div className="space-y-4">
            <h2 className="text-sm font-semibold text-white/70">New campaign</h2>
            <RegenerationWizard
              books={books.data}
              booksLoading={books.isLoading}
              booksError={view(books.error)}
              sources={sources}
              ineligible={eligible.data?.ineligible ?? []}
              sourcesLoading={eligibleQuery.enabled && eligible.isLoading}
              pickBookReason={eligibleQuery.blockedReason}
              phaseCatalog={catalog.data?.canonical_phases ?? []}
              plan={plan.data ?? null}
              planError={view(plan.error ?? catalog.error ?? eligible.error)}
              estimate={estimate.data ?? null}
              estimateLoading={estimate.isFetching}
              estimateError={view(estimate.error)}
              manifest={manifest.data}
              state={draft}
              onChange={setDraft}
              onCreate={() => createMut.mutate()}
              creating={createMut.isPending}
              createError={view(createMut.error)}
            />
          </div>

          <div className="space-y-4">
            <CampaignList
              campaigns={campaigns.data?.campaigns ?? []}
              selectedId={selectedId}
              onSelect={setSelectedId}
              isLoading={campaigns.isLoading}
            />

            {selected && (
              <>
                <p className="px-1 text-[0.68rem] leading-5 text-white/35">{poll.reason}</p>
                <CanaryReview
                  detail={selected}
                  onLaunchCanary={() => canaryMut.mutate(selected.id)}
                  onApprove={() => approveMut.mutate(selected.id)}
                  onReject={(reason) => rejectMut.mutate({ campaignId: selected.id, reason })}
                  launching={canaryMut.isPending}
                  approving={approveMut.isPending}
                  rejecting={rejectMut.isPending}
                  actionError={campaignActionError}
                />
                <CampaignReport
                  detail={selected}
                  releasedFailures={mergeReleasedFailures(
                    selected.released_failures,
                    releaseFailures[selected.id],
                  )}
                  retryAuditByTarget={retryAuditByTarget}
                  pendingByTarget={pendingByTarget}
                  onAction={(kind, target, reason) => targetMut.mutate({ kind, target, reason })}
                  onCancelCampaign={(reason) =>
                    cancelMut.mutate({ campaignId: selected.id, reason })
                  }
                  cancelling={cancelMut.isPending}
                  actionError={view(targetMut.error ?? cancelMut.error)}
                />
              </>
            )}

            {!selected && (
              <p className={cn(CARD, "text-xs text-white/40")}>
                Pick a campaign to read its canary and its report, or freeze a new one on the left.
              </p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
