import {
  type OutcomeStatus,
  type TargetOutcome,
  bucketReport,
  outcomeReason,
  reportActions,
} from "@/lib/regeneration-state";
import { CARD, GHOST_BTN } from "@/lib/ui";
import { cn } from "@/lib/utils";
/**
 * Campaign outcome report (Task 4 shell).
 *
 * Five buckets, always rendered in the documented order even when empty, and
 * every row carries prose from `outcomeReason` rather than an internal status
 * token. Retry and abandon sit at their real positions but are genuinely
 * `disabled` — the wiring lands in Task 10, and the hint says so on hover.
 */
import { Ban, RotateCcw } from "lucide-react";

const BUCKET_TONE: Record<OutcomeStatus, string> = {
  published: "border-emerald-300/25 bg-emerald-300/[0.07] text-emerald-100",
  publication_pending: "border-sky-300/25 bg-sky-300/[0.07] text-sky-100",
  publication_failed: "border-amber-300/25 bg-amber-300/[0.08] text-amber-100",
  generation_failed: "border-rose-300/25 bg-rose-300/[0.08] text-rose-100",
  abandoned: "border-white/[0.12] bg-white/[0.04] text-white/60",
};

export function CampaignReport({ outcomes }: { outcomes: TargetOutcome[] }) {
  const buckets = bucketReport(outcomes);

  return (
    <section className={cn(CARD, "space-y-3")}>
      <header>
        <h2 className="text-sm font-semibold text-white">Campaign report</h2>
        <p className="mt-1 max-w-[70ch] text-xs leading-5 text-white/45">
          Every lesson lands in exactly one bucket, with a plain-language reason. Empty buckets stay
          visible so nothing quietly disappears.
        </p>
      </header>

      <div className="space-y-3">
        {buckets.map((bucket) => (
          <div key={bucket.status} className="space-y-1.5">
            <div className="flex items-center gap-2">
              <span
                className={cn(
                  "rounded-lg border px-2 py-1 text-[0.68rem] font-medium",
                  BUCKET_TONE[bucket.status],
                )}
              >
                {bucket.label}
              </span>
              <span className="font-mono text-[0.66rem] text-white/35">{bucket.count}</span>
            </div>

            {bucket.count === 0 ? (
              <p className="pl-1 text-xs text-white/30">None.</p>
            ) : (
              <ul className="space-y-1">
                {bucket.targets.map((outcome) => (
                  <li
                    key={outcome.lessonId}
                    className="rounded-xl border border-white/[0.07] bg-white/[0.02] px-3 py-2"
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="min-w-0 flex-1 truncate text-sm text-white/85">
                        {outcome.lessonTitle}
                      </span>
                      <span className="font-mono text-[0.64rem] text-white/35">
                        {outcome.language.toUpperCase()}
                        {outcome.publishedVersion != null && ` · V${outcome.publishedVersion}`}
                      </span>
                    </div>
                    <p className="mt-1 max-w-[75ch] text-xs leading-5 text-white/50">
                      {outcomeReason(outcome)}
                    </p>
                    {reportActions(outcome).length > 0 && (
                      <div className="mt-1.5 flex flex-wrap gap-1">
                        {reportActions(outcome).map((action) => (
                          <button
                            key={action.kind}
                            type="button"
                            disabled={!action.enabled}
                            title={action.hint}
                            className={cn(GHOST_BTN, "px-2 py-1 text-xs")}
                          >
                            {action.kind === "retry" ? (
                              <RotateCcw className="size-3.5" />
                            ) : (
                              <Ban className="size-3.5" />
                            )}
                            {action.label}
                          </button>
                        ))}
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}
