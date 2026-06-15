import type { BatchRollup } from "@/lib/types";
import { cn } from "@/lib/utils";
import { colorFor, STATUS_ORDER } from "./status";

export function RollupBar({
  rollup,
  covered,
}: {
  rollup: BatchRollup;
  covered: number;
}) {
  const total = Object.values(rollup).reduce((a, b) => a + (b ?? 0), 0);
  const pct = total ? Math.round(((rollup.done ?? 0) / total) * 100) : 0;

  // Non-zero statuses, in canonical order (so cancelled/cancelling always
  // surface when present — never a fixed 4-status assumption).
  const segments = STATUS_ORDER.filter((s) => (rollup[s] ?? 0) > 0);

  return (
    <div className="space-y-1.5">
      <div className="flex h-2 overflow-hidden rounded-full">
        {total === 0 ? (
          <div className="h-full w-full bg-white/[0.06]" />
        ) : (
          segments.map((status) => (
            <div
              key={status}
              className={cn(status === "running" && "seg-now")}
              style={{
                flex: rollup[status] ?? 0,
                background: colorFor(status),
                color: colorFor(status),
              }}
            />
          ))
        )}
      </div>

      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-[0.72rem] text-white/45">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          {segments.map((status) => (
            <span key={status} className="inline-flex items-center gap-1.5">
              <span
                aria-hidden
                className="size-2 shrink-0 rounded-full"
                style={{ background: colorFor(status) }}
              />
              <span>
                {status} {rollup[status]}
              </span>
            </span>
          ))}
        </div>
        <span className="ml-auto shrink-0 font-mono text-white/40">
          {covered} / {total} · {pct}%
        </span>
      </div>
    </div>
  );
}
