import { RegenerationProblem } from "@/components/regeneration/regeneration-wizard";
import {
  regenerationCampaignCountLabel,
  regenerationCampaignListView,
  regenerationCampaignStatusLabel,
} from "@/lib/api";
import { formatUsd, lessonCountLabel } from "@/lib/regeneration-state";
import type { RegenerationCampaignStatus, RegenerationCampaignSummary } from "@/lib/types";
import { CARD, FRAME_OFF, FRAME_ON, PRESSABLE } from "@/lib/ui";
import { cn } from "@/lib/utils";
/**
 * Campaign picker for the regeneration area, over the real `GET
 * /api/v1/regeneration/campaigns` rollups.
 *
 * The list route deliberately does NOT roll up per campaign server-side — a
 * list poll must never take a campaign write lock — so `status_counts` here is
 * the last rolled-up view, and the detail screen is what refreshes a campaign.
 *
 * Status wording comes from `regenerationCampaignStatusLabel` so the list and
 * the report can never drift apart, and so this flow keeps its own vocabulary
 * instead of borrowing Fleet's.
 */
import { ChevronRight, Clock3, TriangleAlert } from "lucide-react";

const STATUS_TONE: Record<RegenerationCampaignStatus, string> = {
  draft: "border-white/[0.12] bg-white/[0.05] text-white/60",
  canary_running: "border-sky-300/30 bg-sky-300/[0.09] text-sky-100",
  awaiting_canary_approval: "border-amber-300/30 bg-amber-300/[0.09] text-amber-100",
  approved: "border-sky-300/30 bg-sky-300/[0.09] text-sky-100",
  bulk_running: "border-sky-300/30 bg-sky-300/[0.09] text-sky-100",
  attention_required: "border-rose-300/30 bg-rose-300/[0.09] text-rose-100",
  completed: "border-emerald-300/30 bg-emerald-300/[0.09] text-emerald-100",
  completed_with_abandonments: "border-emerald-300/20 bg-emerald-300/[0.06] text-emerald-100/80",
  rejected: "border-white/[0.12] bg-white/[0.05] text-white/60",
  cancelled: "border-white/[0.12] bg-white/[0.05] text-white/60",
};

/** "$1.20 – $3.00 est." — or an honest blank when nothing was recorded. */
function estimateText(campaign: RegenerationCampaignSummary): string {
  const { estimated_cost_low_usd: low, estimated_cost_high_usd: high } = campaign;
  if (low == null && high == null) return "no estimate recorded";
  if (low == null || high == null) return `${formatUsd(low ?? high ?? 0)} est.`;
  return `${formatUsd(low)} – ${formatUsd(high)} est.`;
}

export function CampaignList({
  campaigns,
  count = null,
  limit = 0,
  offset = 0,
  selectedId,
  onSelect,
  isLoading = false,
  error = null,
  onRetry,
}: {
  campaigns: RegenerationCampaignSummary[];
  /** `CampaignListOut.count` — every campaign matching the filter, not just the
   *  ones on this page. `null` when the read failed. */
  count?: number | null;
  /** `CampaignListOut.limit` / `.offset`, so a capped page can say so. */
  limit?: number;
  offset?: number;
  selectedId: string | null;
  onSelect: (id: string) => void;
  isLoading?: boolean;
  /** Re-runs the campaigns query. A read that failed is worth another try
   *  without reloading the app — and the rows already in the cache stay on
   *  screen while it runs. */
  onRetry?: () => void;
  /** The raw `campaigns` query error. This list is the only regeneration query
   *  that runs unconditionally, so it is where a server-side `REGENERATION_
   *  ENABLED=false` becomes visible — no book has to be picked first. */
  error?: unknown;
}) {
  // Which of loading / empty / failed is true is a decision, not a render
  // detail: a failed read used to fall through to "no campaigns yet".
  const view = regenerationCampaignListView({ campaigns, isLoading, error });
  // `GET /campaigns` is PAGED. Labelling the rendered rows "N total" turns a
  // capped first page into a claim about the whole database — and that number
  // is what an operator reads to decide no campaign is missing.
  const total = regenerationCampaignCountLabel({
    shown: view.campaigns.length,
    count: view.mode === "error" ? null : count,
    limit,
    offset,
  });
  return (
    <section className={cn(CARD, "space-y-2")}>
      <header className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-white">Campaigns</h2>
        <span className="font-mono text-[0.65rem] uppercase tracking-[0.14em] text-white/35">
          {total}
        </span>
      </header>

      {view.error && <RegenerationProblem view={view.error} onRetry={onRetry} />}
      {view.message && <p className="text-xs text-white/40">{view.message}</p>}

      <ul className="space-y-1">
        {view.campaigns.map((c) => (
          <li key={c.id}>
            <button
              type="button"
              onClick={() => onSelect(c.id)}
              className={cn(
                "flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-left",
                PRESSABLE,
                selectedId === c.id ? FRAME_ON : FRAME_OFF,
              )}
            >
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium text-white">
                  {c.identity.title}
                </span>
                <span className="mt-0.5 block truncate text-xs text-white/60">
                  {c.identity.subtitle}
                </span>
                <span className="mt-0.5 flex flex-wrap items-center gap-2 font-mono text-[0.64rem] text-white/40">
                  <span className="inline-flex items-center gap-1">
                    <Clock3 className="size-3" />
                    {(c.created_at ?? "").slice(0, 10) || "—"}
                  </span>
                  <span>
                    {lessonCountLabel(c.target_count)} · canary {c.canary_size}
                  </span>
                  <span>{estimateText(c)}</span>
                </span>
              </span>
              {c.attention_required && (
                <TriangleAlert
                  className="size-4 shrink-0 text-rose-200"
                  aria-label="needs a decision"
                />
              )}
              <span
                className={cn(
                  "shrink-0 rounded-lg border px-2 py-1 text-[0.68rem] font-medium",
                  STATUS_TONE[c.status] ?? "border-white/[0.12] bg-white/[0.05] text-white/60",
                )}
              >
                {regenerationCampaignStatusLabel(c.status)}
              </span>
              <ChevronRight className="size-4 shrink-0 text-white/30" />
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
