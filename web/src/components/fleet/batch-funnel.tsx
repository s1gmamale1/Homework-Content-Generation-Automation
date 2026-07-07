import { PauseCircle } from "lucide-react";
import { type CSSProperties, useEffect, useMemo, useState } from "react";
import type { BatchSummary } from "@/lib/types";
import { type RowStatus, transportRowStatus } from "@/lib/batch-status";
import { type StatusFilter, bookMatchesStatus } from "@/lib/monitor-filters";
import { groupBooksByGrade } from "@/lib/monitor-grouping";
import { subjectLabelWithVariant } from "@/lib/subjects";
import { CARD, FRAME_OFF, FRAME_ON, GHOST_BTN, PRESSABLE } from "@/lib/ui";
import { cn } from "@/lib/utils";
import { BatchActions } from "./batch-actions";
import { BatchLessonList } from "./batch-lesson-list";
import { MonitorDrawer } from "./monitor-drawer";
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
  onShowLessons,
}: {
  batch: BatchSummary;
  divided: boolean;
  /** Shown next to the status chip only when the book has >1 transport. */
  transportLabel?: string;
  onShowLessons: (batchId: string, title: string) => void;
}) {
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

      {batch.archived + batch.unarchived > 0 && (
        <div className="text-[0.7rem] text-white/45">
          Notion archive · {batch.archived}/{batch.archived + batch.unarchived}
          {batch.stale > 0 && (
            <span className="text-amber-400"> · {batch.stale} stale</span>
          )}
        </div>
      )}

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
          onClick={() => onShowLessons(batch.batch_id, batch.batch_id)}
          className={cn(GHOST_BTN, PRESSABLE, FRAME_OFF, "px-2 py-1.5 text-xs")}
        >
          Show lessons
        </button>

        <BatchActions batch={batch} />
      </div>
    </div>
  );
}

/** One card per BOOK. All of a book's API batches (one per output language —
 *  `UNIQUE(book_id, transport, output_language)`) collapse into a single card
 *  with one shared header and one TransportRow each, instead of separate cards
 *  for the same subject. (cli batches are filtered out upstream in monitor.tsx.) */
function BookCard({
  batches,
  onShowLessons,
}: {
  batches: BatchSummary[];
  onShowLessons: (batchId: string, title: string) => void;
}) {
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

  const subjectGradeTitle = [
    subjectLabelWithVariant(head.subject, head.subject_variant),
    head.grade ? `grade ${head.grade}` : null,
  ]
    .filter(Boolean)
    .join(" · ");

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
          onShowLessons={(batchId) => onShowLessons(batchId, subjectGradeTitle)}
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
  const [drawer, setDrawer] = useState<{ batchId: string; title: string } | null>(null);
  const [gradeFilter, setGradeFilter] = useState<string | null>(null);

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

  // Keep gradeFilter valid as data/filters change: reset to null if the
  // selected grade is no longer present in gradeGroups.
  useEffect(() => {
    if (gradeFilter && !gradeGroups.some((g) => g.grade === gradeFilter)) {
      setGradeFilter(null);
    }
  }, [gradeGroups, gradeFilter]);

  // When a specific grade is selected, show only that grade's group.
  const visibleGroups = gradeFilter
    ? gradeGroups.filter((g) => g.grade === gradeFilter)
    : gradeGroups;

  return (
    <div className="space-y-3">
      <h2 className="text-sm font-semibold tracking-tight text-white">Batches</h2>

      {/* Grade filter strip — only when there is something to filter */}
      {gradeGroups.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {/* "All grades" chip */}
          <button
            type="button"
            onClick={() => setGradeFilter(null)}
            className={cn(
              "rounded-xl px-3 py-1 text-xs font-medium",
              PRESSABLE,
              gradeFilter === null ? FRAME_ON : FRAME_OFF,
            )}
          >
            All grades
          </button>

          {gradeGroups.map((group) => {
            const failed = group.books.reduce(
              (a, book) =>
                a +
                book.reduce(
                  (x, b) => x + ((b.rollup as Record<string, number>).failed ?? 0),
                  0,
                ),
              0,
            );
            const label =
              group.grade === "Ungraded" ? "Ungraded" : `Grade ${group.grade}`;
            return (
              <button
                key={group.grade}
                type="button"
                onClick={() => setGradeFilter(group.grade)}
                className={cn(
                  "rounded-xl px-3 py-1 text-xs font-medium",
                  PRESSABLE,
                  gradeFilter === group.grade ? FRAME_ON : FRAME_OFF,
                )}
              >
                {label}
                {failed > 0 && (
                  <span className="ml-1 text-red-300">· {failed} failed</span>
                )}
              </button>
            );
          })}
        </div>
      )}

      {books.length === 0 ? (
        <div className={cn(CARD, "text-sm text-white/50")}>
          No batches launched yet.
        </div>
      ) : (
        <div className="space-y-5">
          {visibleGroups.map(({ grade, books: gradeBooks }) => (
            <div key={grade} className="space-y-3">
              <p className="text-[0.7rem] font-medium uppercase tracking-[0.12em] text-white/35">
                {grade === "Ungraded" ? "Ungraded" : `Grade ${grade}`}
              </p>
              <div className="grid items-start gap-3 md:grid-cols-2">
                {gradeBooks.map((group) => (
                  <BookCard
                    key={group[0].book_id}
                    batches={group}
                    onShowLessons={(batchId, title) => setDrawer({ batchId, title })}
                  />
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      <MonitorDrawer
        open={!!drawer}
        title={drawer?.title}
        onClose={() => setDrawer(null)}
      >
        {drawer && <BatchLessonList batchId={drawer.batchId} enabled />}
      </MonitorDrawer>
    </div>
  );
}
