import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2, RotateCcw } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { api } from "@/lib/api";
import type { PrepareStatus } from "@/lib/prepare-status";
import { cn } from "@/lib/utils";

const PILL_COLOR: Record<PrepareStatus["chip"]["colorFamily"], string> = {
  amber: "bg-amber-400/15 text-amber-200",
  emerald: "bg-emerald-400/15 text-emerald-300",
  blue: "bg-sky-400/15 text-sky-300",
  red: "bg-rose-500/15 text-rose-300",
};

/**
 * Shared chip + inline panel for the four "system-aware" Prepare-dialog
 * states — PREPARED / PREPARING / NEEDS REVIEW / FAILED (worklog 0144
 * task 5). Consumed by BOTH upload.tsx and launcher.tsx: BE-19's review
 * caught the same picker bug duplicated in both surfaces, so this action
 * logic (use-existing / redo-with-confirm / review deep-link / retry)
 * lives in exactly one place.
 *
 * The NO TEXTBOOK / TEXTBOOK READY states are each caller's OWN existing
 * chip markup, unchanged — this component renders nothing for those two
 * (`partPrepareStatus` never returns them the panel/actions this component
 * needs), so callers keep their current byte-preserved JSX and only swap in
 * `<PrepareStatusPanel>` for the four linked-book states.
 */
export function PrepareStatusPanel({ status }: { status: PrepareStatus }) {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [expanded, setExpanded] = useState(false);

  const retryMut = useMutation({
    mutationFn: (bookId: string) => api.retryBookToc(bookId),
    onSuccess: () => {
      toast.success("Re-preparing — extracting chapters…");
      setExpanded(false);
      // Both consumers key their availability query "notion-avail-langs"
      // (task 5) — a partial key match invalidates either shape.
      qc.invalidateQueries({ queryKey: ["notion-avail-langs"] });
      qc.invalidateQueries({ queryKey: ["books"] });
    },
    onError: (e) => {
      // The 409 race (blocked by referencing jobs between render and click)
      // is already reduced to its human `detail.message` by api.ts's
      // extractErrorMessage — no structured parsing needed here.
      toast.error(e instanceof Error ? e.message : "Retry failed");
    },
  });

  const { chip, panel, actions } = status;
  if (panel.kind === "no_textbook" || panel.kind === "textbook_ready") return null;

  function confirmAndRedo(bookId: string) {
    if (
      !window.confirm(
        "Redo TOC extraction?\n\nThis re-extracts the table of contents from the source PDF and REPLACES the current TOC rows.",
      )
    ) {
      return;
    }
    retryMut.mutate(bookId);
  }

  return (
    <div className="flex flex-col gap-2">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className={cn(
          "rounded-md px-1.5 py-0.5 text-[0.6rem] font-semibold uppercase tracking-wide transition-opacity hover:opacity-80",
          PILL_COLOR[chip.colorFamily],
          chip.pulse && "animate-pulse",
        )}
      >
        {chip.label}
      </button>

      {expanded && panel.kind === "prepared" && (
        <div className="flex flex-col gap-2 rounded-xl border border-white/[0.08] bg-white/[0.03] p-3">
          <p className="text-xs text-white/60">
            {panel.lessons} lesson{panel.lessons === 1 ? "" : "s"}
            {panel.preparedAt &&
              ` · prepared ${new Date(panel.preparedAt).toLocaleDateString()}`}
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => navigate(`/book/${panel.bookId}`)}
              className="inline-flex items-center gap-1.5 rounded-xl border border-white/[0.12] bg-white/[0.05] px-3 py-1.5 text-xs font-medium text-white/80 transition-colors hover:bg-white/[0.1] hover:text-white"
            >
              Use existing
            </button>
            <button
              type="button"
              disabled={!actions.redo || retryMut.isPending}
              title={
                panel.redo.disabledReason ??
                "Re-extracts from the PDF and replaces the current TOC rows"
              }
              onClick={() => confirmAndRedo(panel.bookId)}
              className="inline-flex items-center gap-1.5 rounded-xl border border-rose-500/30 bg-rose-500/10 px-3 py-1.5 text-xs font-medium text-rose-200 transition-colors hover:bg-rose-500/20 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {retryMut.isPending ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : (
                <RotateCcw className="size-3.5" />
              )}
              Redo TOC extraction
            </button>
          </div>
          {panel.redo.disabledReason && (
            <p className="text-[0.68rem] text-white/40">{panel.redo.disabledReason}</p>
          )}
        </div>
      )}

      {expanded && panel.kind === "preparing" && (
        <div className="flex items-center gap-2 rounded-xl border border-white/[0.08] bg-white/[0.03] p-3 text-xs text-white/60">
          <Loader2 className="size-3.5 animate-spin text-[#5b8dff]" />
          Extracting lessons…
          <Link to={`/book/${panel.bookId}`} className="text-[#9cc0ff] hover:underline">
            View progress
          </Link>
        </div>
      )}

      {expanded && panel.kind === "needs_review" && (
        <div className="flex flex-col gap-2 rounded-xl border border-amber-400/25 bg-amber-400/[0.06] p-3">
          <p className="text-xs text-amber-200/85">
            The extracted table of contents needs a look before this book is ready.
          </p>
          <Link
            to={`/book/${panel.bookId}`}
            className="inline-flex w-fit items-center gap-1.5 rounded-xl bg-gradient-to-r from-amber-500/70 to-amber-400/60 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:from-amber-500/90 hover:to-amber-400/80"
          >
            Review TOC
          </Link>
        </div>
      )}

      {expanded && panel.kind === "failed" && (
        <div className="flex flex-col gap-2 rounded-xl border border-rose-500/25 bg-rose-500/10 p-3">
          <p className="text-xs text-rose-200">TOC extraction failed for this book.</p>
          <button
            type="button"
            disabled={retryMut.isPending}
            onClick={() => retryMut.mutate(panel.bookId)}
            className="inline-flex w-fit items-center gap-1.5 rounded-xl border border-white/[0.12] bg-white/[0.05] px-3 py-1.5 text-xs font-medium text-white/80 transition-colors hover:bg-white/[0.1] hover:text-white disabled:opacity-50"
          >
            {retryMut.isPending ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : (
              <RotateCcw className="size-3.5" />
            )}
            Retry
          </button>
        </div>
      )}
    </div>
  );
}
