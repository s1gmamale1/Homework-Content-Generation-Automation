import {
  type Campaign,
  campaignStatusLabel,
  formatUsd,
  lessonCountLabel,
} from "@/lib/regeneration-state";
import { CARD, FRAME_OFF, FRAME_ON, PRESSABLE } from "@/lib/ui";
import { cn } from "@/lib/utils";
/**
 * Campaign picker for the regeneration area (Task 4 shell). Fixture-driven —
 * Task 10 swaps the array for the API list. Status wording comes from
 * `campaignStatusLabel` so the report and the list can never drift apart, and
 * so the flow keeps its own vocabulary instead of borrowing Fleet's.
 */
import { ChevronRight, Clock3 } from "lucide-react";

const STATUS_TONE: Record<string, string> = {
  awaiting_approval: "border-amber-300/30 bg-amber-300/[0.09] text-amber-100",
  canary_regenerating: "border-sky-300/30 bg-sky-300/[0.09] text-sky-100",
  regenerating: "border-sky-300/30 bg-sky-300/[0.09] text-sky-100",
  publishing: "border-sky-300/30 bg-sky-300/[0.09] text-sky-100",
  completed: "border-emerald-300/30 bg-emerald-300/[0.09] text-emerald-100",
  rejected: "border-white/[0.12] bg-white/[0.05] text-white/60",
  abandoned: "border-white/[0.12] bg-white/[0.05] text-white/60",
  failed: "border-rose-300/30 bg-rose-300/[0.09] text-rose-100",
};

export function CampaignList({
  campaigns,
  selectedId,
  onSelect,
}: {
  campaigns: Campaign[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  return (
    <section className={cn(CARD, "space-y-2")}>
      <header className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-white">Campaigns</h2>
        <span className="font-mono text-[0.65rem] uppercase tracking-[0.14em] text-white/35">
          {campaigns.length} total
        </span>
      </header>

      {campaigns.length === 0 && (
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
                <span className="block truncate text-sm font-medium text-white">{c.name}</span>
                <span className="mt-0.5 flex flex-wrap items-center gap-2 font-mono text-[0.64rem] text-white/40">
                  <span className="inline-flex items-center gap-1">
                    <Clock3 className="size-3" />
                    {c.createdAt.slice(0, 10)}
                  </span>
                  <span>
                    {lessonCountLabel(c.targets.length)} · canary {c.canarySize}
                  </span>
                  <span>
                    {formatUsd(c.estimate.costLowUsd)} – {formatUsd(c.estimate.costHighUsd)} est.
                  </span>
                </span>
              </span>
              <span
                className={cn(
                  "shrink-0 rounded-lg border px-2 py-1 text-[0.68rem] font-medium",
                  STATUS_TONE[c.status] ?? "border-white/[0.12] bg-white/[0.05] text-white/60",
                )}
              >
                {campaignStatusLabel(c.status)}
              </span>
              <ChevronRight className="size-4 shrink-0 text-white/30" />
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
