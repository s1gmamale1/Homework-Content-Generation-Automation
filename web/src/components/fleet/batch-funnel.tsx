import { ChevronDown, ChevronRight, PauseCircle } from "lucide-react";
import { type CSSProperties, useMemo, useState } from "react";
import type { BatchSummary } from "@/lib/types";
import { type RowStatus, transportRowStatus } from "@/lib/batch-status";
import { type StatusFilter, bookMatchesStatus } from "@/lib/monitor-filters";
import { groupBooksByGrade } from "@/lib/monitor-grouping";
import { subjectLabelWithVariant } from "@/lib/subjects";
import { CARD, GHOST_BTN } from "@/lib/ui";
import { cn } from "@/lib/utils";
import { BatchActions } from "./batch-actions";
import { BatchLessonList } from "./batch-lesson-list";
import { RollupBar } from "./rollup-bar";

const ROW_CHIP: Record<RowStatus, { label: string; className: string; style?: CSSProperties }> = {
  complete: {
    label: "complete",
    className: "text-white/90",
    style: { background: "oklch(0.78 0.10 145 / 0.25)" },
  },
  in_progress: { label: "in progress", className: "bg-white/[0.07] text-white/55" },
  failed: { label: "failed", className: "bg-red-500/20 text-red-200" },
  partial: { label: "partial", className: "bg-amber-500/20 text-amber-200" },
};

/** One batch's progress within a book card: status chip, rollup bar, and a
 *  per-batch lessons drill-in. Kept separate per batch (never merged) because
 *  the same lesson can be done in one batch yet not-started in another —
 *  summing rollups would double-count. (The Monitor is API-only; cli batches
 *  are filtered out upstream in monitor.tsx.) */
function TransportRow({
  batch,
  divided,
  transportLabel,
}: {
  batch: BatchSummary;
  divided: boolean;
  /** Shown next to the status chip only when the book has >1 transport. */
  transportLabel?: string;
}) {
  const [expanded, setExpanded] = useState(false);
  const Chevron = expanded ? ChevronDown : ChevronRight;
  const chip = ROW_CHIP[transportRowStatus(batch)];

  return (
    <div className={cn("space-y-3", divided && "border-t border-white/[0.06] pt-3")}>
      <div className="flex items-center justify-between gap-3">
        {transportLabel && (
          <span className="font-mono text-[0.62rem] uppercase tracking-wide text-white/35">
            {transportLabel}
          </span>
        )}
        <span
          className={cn(
            "ml-auto shrink-0 rounded-full px-2 py-0.5 text-[0.7rem] font-medium",
            chip.className,
          )}
          style={chip.style}
        >
          {chip.label}
        </span>
      </div>

      <RollupBar rollup={batch.rollup} covered={batch.lessons_covered} />

      {/* Paused badge — shown when the budget monitor (C4) has gated this batch.
          A polished cost-$ spend dashboard defers to C6. */}
      {batch.paused_at && (
        <div className="flex items-center gap-1.5 rounded-lg border border-amber-500/30 bg-amber-500/[0.08] px-2.5 py-1.5 text-xs text-amber-300">
          <PauseCircle className="size-3.5 shrink-0" />
          <span>
            {batch.paused_reason === "manual"
              ? "Paused by operator"
              : `Paused — budget cap reached${batch.paused_reason ? ` (${batch.paused_reason})` : ""}`}
          </span>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className={cn(GHOST_BTN, "px-2 py-1.5 text-xs")}
        >
          <Chevron className="size-4" />
          {expanded ? "Hide lessons" : "Show lessons"}
        </button>

        <BatchActions batch={batch} />
      </div>

      {expanded && <BatchLessonList batchId={batch.batch_id} enabled={expanded} />}
    </div>
  );
}

/** One card per BOOK. All of a book's API batches (one per output language —
 *  `UNIQUE(book_id, transport, output_language)`) collapse into a single card
 *  with one shared header and one TransportRow each, instead of separate cards
 *  for the same subject. (cli batches are filtered out upstream in monitor.tsx.) */
function BookCard({ batches }: { batches: BatchSummary[] }) {
  const head = batches[0];
  // Glow ("in progress" treatment) only while work is ACTUALLY in flight —
  // queued/running/cancelling jobs. NOT `!complete`: a partial/subset launch
  // that finished everything it launched is idle (nothing running) even though
  // the book isn't 100% covered, so it must not keep pulsing as in-progress.
  const anyInFlight = batches.some(
    (b) =>
      (b.rollup.pending ?? 0) +
        (b.rollup.running ?? 0) +
        (b.rollup.cancelling ?? 0) >
      0,
  );

  return (
    <div
      className={cn(
        CARD,
        "space-y-3 transition-transform hover:-translate-y-0.5",
        anyInFlight && "glow-rim",
      )}
    >
      <div className="min-w-0">
        <div className="truncate text-sm font-semibold text-white">
          {subjectLabelWithVariant(head.subject, head.subject_variant)}
          {head.grade ? (
            <span className="font-normal text-white/45"> · grade {head.grade}</span>
          ) : null}
        </div>
      </div>

      {batches.map((b, i) => (
        <TransportRow
          key={b.batch_id}
          batch={b}
          divided={i > 0}
          transportLabel={batches.length > 1 ? b.transport : undefined}
        />
      ))}
    </div>
  );
}

export function BatchFunnel({
  batches,
  statusFilter = "all",
}: {
  batches?: BatchSummary[];
  statusFilter?: StatusFilter;
}) {
  // Group batches by book so a subject's batches share a single card. The list
  // is already API-scoped by monitor.tsx (cli filtered there so stats + cards
  // stay consistent), so a cli-only book never appears here. After grouping,
  // drop books that don't match the status filter BEFORE groupBooksByGrade so
  // grade sections only contain matching books.
  const books = useMemo(() => {
    const byBook = new Map<string, BatchSummary[]>();
    for (const b of batches ?? []) {
      const arr = byBook.get(b.book_id);
      if (arr) arr.push(b);
      else byBook.set(b.book_id, [b]);
    }
    return [...byBook.values()].filter((book) =>
      bookMatchesStatus(book, statusFilter),
    );
  }, [batches, statusFilter]);

  const gradeGroups = useMemo(() => groupBooksByGrade(books), [books]);

  return (
    <div className="space-y-3">
      <h2 className="text-sm font-semibold tracking-tight text-white">Batches</h2>
      {books.length === 0 ? (
        <div className={cn(CARD, "text-sm text-white/50")}>
          No batches launched yet.
        </div>
      ) : (
        <div className="space-y-5">
          {gradeGroups.map(({ grade, books: gradeBooks }) => (
            <div key={grade} className="space-y-3">
              <p className="text-[0.7rem] font-medium uppercase tracking-[0.12em] text-white/35">
                {grade === "Ungraded" ? "Ungraded" : `Grade ${grade}`}
              </p>
              <div className="grid items-start gap-3 md:grid-cols-2">
                {gradeBooks.map((group) => (
                  <BookCard key={group[0].book_id} batches={group} />
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
