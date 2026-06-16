import { ChevronDown, ChevronRight } from "lucide-react";
import { useState } from "react";
import type { BatchSummary } from "@/lib/types";
import { subjectLabel } from "@/lib/subjects";
import { CARD, GHOST_BTN } from "@/lib/ui";
import { cn } from "@/lib/utils";
import { ApiBadge } from "./launcher";
import { BatchLessonList } from "./batch-lesson-list";
import { RollupBar } from "./rollup-bar";

function BatchCard({ batch }: { batch: BatchSummary }) {
  const [expanded, setExpanded] = useState(false);
  const Chevron = expanded ? ChevronDown : ChevronRight;

  return (
    <div className={cn(CARD, "space-y-3", !batch.complete && "glow-rim")}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold text-white">
            {subjectLabel(batch.subject)}
            {batch.grade ? (
              <span className="font-normal text-white/45"> · grade {batch.grade}</span>
            ) : null}
          </div>
          <div className="mt-0.5 flex items-center gap-2 font-mono text-[0.72rem] text-white/40">
            <span>{batch.provider}</span>
            {batch.transport === "api" && <ApiBadge />}
          </div>
        </div>
        {batch.complete ? (
          <span
            className="shrink-0 rounded-full px-2 py-0.5 text-[0.7rem] font-medium text-white/90"
            style={{ background: "oklch(0.78 0.10 145 / 0.25)" }}
          >
            complete
          </span>
        ) : (
          <span className="shrink-0 rounded-full bg-white/[0.07] px-2 py-0.5 text-[0.7rem] text-white/55">
            in progress
          </span>
        )}
      </div>

      <RollupBar rollup={batch.rollup} covered={batch.lessons_covered} />

      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className={cn(GHOST_BTN, "px-2 py-1.5 text-xs")}
      >
        <Chevron className="size-4" />
        {expanded ? "Hide lessons" : "Show lessons"}
      </button>

      {expanded && (
        <BatchLessonList batchId={batch.batch_id} enabled={expanded} />
      )}
    </div>
  );
}

export function BatchFunnel({ batches }: { batches?: BatchSummary[] }) {
  return (
    <div className="space-y-3">
      <h2 className="text-sm font-semibold tracking-tight text-white">Batches</h2>
      {!batches || batches.length === 0 ? (
        <div className={cn(CARD, "text-sm text-white/50")}>
          No batches launched yet.
        </div>
      ) : (
        <div className="space-y-3">
          {batches.map((b) => (
            <BatchCard key={b.batch_id} batch={b} />
          ))}
        </div>
      )}
    </div>
  );
}
