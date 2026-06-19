import { ChevronDown, ChevronRight, PauseCircle } from "lucide-react";
import { useMemo, useState } from "react";
import type { BatchSummary } from "@/lib/types";
import { subjectLabelWithVariant } from "@/lib/subjects";
import { CARD, GHOST_BTN } from "@/lib/ui";
import { cn } from "@/lib/utils";
import { ApiBadge } from "./launcher";
import { BatchLessonList } from "./batch-lesson-list";
import { RollupBar } from "./rollup-bar";

/** One transport's progress within a book card: provider/badge, status,
 *  its own rollup bar, and a per-transport lessons drill-in. Kept separate
 *  per transport (never merged) because a lesson can be done on CLI yet
 *  not-started on API — summing the two rollups would double-count. */
function TransportRow({
  batch,
  divided,
}: {
  batch: BatchSummary;
  divided: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const Chevron = expanded ? ChevronDown : ChevronRight;

  return (
    <div className={cn("space-y-3", divided && "border-t border-white/[0.06] pt-3")}>
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 font-mono text-[0.72rem] text-white/45">
          <span>{batch.provider}</span>
          {batch.transport === "api" ? (
            <ApiBadge />
          ) : (
            <span className="rounded-full bg-white/[0.06] px-1.5 py-0.5 text-[0.62rem] uppercase tracking-wide text-white/45">
              cli
            </span>
          )}
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

      {/* Paused badge — shown when the budget monitor (C4) has gated this batch.
          A polished cost-$ spend dashboard defers to C6. */}
      {batch.paused_at && (
        <div className="flex items-center gap-1.5 rounded-lg border border-amber-500/30 bg-amber-500/[0.08] px-2.5 py-1.5 text-xs text-amber-300">
          <PauseCircle className="size-3.5 shrink-0" />
          <span>
            Paused — budget cap reached
            {batch.paused_reason ? ` (${batch.paused_reason})` : ""}
          </span>
        </div>
      )}

      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className={cn(GHOST_BTN, "px-2 py-1.5 text-xs")}
      >
        <Chevron className="size-4" />
        {expanded ? "Hide lessons" : "Show lessons"}
      </button>

      {expanded && <BatchLessonList batchId={batch.batch_id} enabled={expanded} />}
    </div>
  );
}

/** One card per BOOK. A book has at most a CLI batch and an API batch (the
 *  `UNIQUE(book_id, transport)` constraint), so CLI+API collapse into a single
 *  card with one shared header and one TransportRow each — instead of two
 *  separate cards for the same subject. */
function BookCard({ batches }: { batches: BatchSummary[] }) {
  const head = batches[0];
  const anyIncomplete = batches.some((b) => !b.complete);

  return (
    <div
      className={cn(
        CARD,
        "space-y-3 transition-transform hover:-translate-y-0.5",
        anyIncomplete && "glow-rim",
      )}
    >
      <div className="min-w-0">
        <div className="truncate text-sm font-semibold text-white">
          {subjectLabelWithVariant(head.subject, head.subject_variant)}
          {head.grade ? (
            <span className="font-normal text-white/45"> · grade {head.grade}</span>
          ) : null}
        </div>
        {batches.length > 1 && (
          <div className="mt-0.5 text-[0.72rem] text-white/40">
            CLI + API · {batches.length} transports
          </div>
        )}
      </div>

      {batches.map((b, i) => (
        <TransportRow key={b.batch_id} batch={b} divided={i > 0} />
      ))}
    </div>
  );
}

export function BatchFunnel({ batches }: { batches?: BatchSummary[] }) {
  // Group batches by book so CLI+API for one subject share a single card.
  // Map preserves insertion order; batches arrive newest-first, so the book
  // with the most recent batch leads. Within a book, CLI sorts before API.
  const books = useMemo(() => {
    const byBook = new Map<string, BatchSummary[]>();
    for (const b of batches ?? []) {
      const arr = byBook.get(b.book_id);
      if (arr) arr.push(b);
      else byBook.set(b.book_id, [b]);
    }
    for (const arr of byBook.values()) {
      arr.sort(
        (a, b) =>
          (a.transport === "api" ? 1 : 0) - (b.transport === "api" ? 1 : 0),
      );
    }
    return [...byBook.values()];
  }, [batches]);

  return (
    <div className="space-y-3">
      <h2 className="text-sm font-semibold tracking-tight text-white">Batches</h2>
      {books.length === 0 ? (
        <div className={cn(CARD, "text-sm text-white/50")}>
          No batches launched yet.
        </div>
      ) : (
        <div className="grid items-start gap-3 md:grid-cols-2">
          {books.map((group) => (
            <BookCard key={group[0].book_id} batches={group} />
          ))}
        </div>
      )}
    </div>
  );
}
