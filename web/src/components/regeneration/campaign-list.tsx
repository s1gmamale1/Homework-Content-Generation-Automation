import { regenerationCampaignStatusLabel } from "@/lib/api";
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
  selectedId,
  onSelect,
  isLoading = false,
}: {
  campaigns: RegenerationCampaignSummary[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  isLoading?: boolean;
}) {
  return (
    <section className={cn(CARD, "space-y-2")}>
      <header className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-white">Campaigns</h2>
        <span className="font-mono text-[0.65rem] uppercase tracking-[0.14em] text-white/35">
          {campaigns.length} total
        </span>
      </header>

      {isLoading && campaigns.length === 0 && (
        <p className="text-xs text-white/40">Loading campaigns…</p>
      )}
      {!isLoading && campaigns.length === 0 && (
        <p className="text-xs text-white/40">No regeneration campaigns yet.</p>
      )}

      <ul className="space-y-1">
        {campaigns.map((c) => (
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
                  {c.requested_phases.length > 0
                    ? c.requested_phases.join(", ")
                    : "extract refresh"}
                  {c.refresh_extraction && c.requested_phases.length > 0 ? " + extract" : ""}
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
