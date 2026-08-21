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
  regenerationErrorView,
  regenerationListPollMs,
  regenerationPollDecision,
} from "@/lib/api";
import type {
  RegenerationCampaignDetail,
  RegenerationCampaignDraft,
  RegenerationPhasePlanRequest,
  RegenerationTargetReport,
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
 * human gate, and on a target parked waiting for an operator.
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
      book_ids: [],
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
    canary_size: draft.canarySize,
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

  const eligible = useQuery({
    queryKey: ["regeneration", "eligible"],
    queryFn: () => api.listRegenerationEligible(),
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

  const sources = eligible.data?.sources ?? [];
  /** The plan is per SUBJECT. A campaign may legitimately span subjects; the
   *  wizard previews the first one and the estimate returns a plan per subject. */
  const subject = useMemo(() => {
    const chosen = sources.find(
      (s) =>
        s.output_language === draft.language && draft.selectedTocEntryIds.includes(s.toc_entry_id),
    );
    return chosen?.subject ?? sources.find((s) => s.output_language === draft.language)?.subject;
  }, [sources, draft.language, draft.selectedTocEntryIds]);

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

  const canaryMut = useMutation({
    mutationFn: (campaignId: string) => api.launchRegenerationCanary(campaignId),
    onSuccess: (fresh) => {
      adopt(fresh);
      toast.success("Canary started. It will wait here for your review.");
    },
  });

  const approveMut = useMutation({
    mutationFn: (campaignId: string) => api.approveRegenerationCampaign(campaignId, { actor: "" }),
    onSuccess: (fresh) => {
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
    },
    onSuccess: (result) => {
      qc.invalidateQueries({ queryKey: campaignKey(result.campaign_id) });
      qc.invalidateQueries({ queryKey: CAMPAIGNS_KEY });
      toast.success(result.target.reason);
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
              sources={sources}
              ineligible={eligible.data?.ineligible ?? []}
              sourcesLoading={eligible.isLoading}
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
